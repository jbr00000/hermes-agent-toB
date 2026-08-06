from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from typing import Any

from redis import Redis
from redis.exceptions import RedisError

from .repository import tenant_id

logger = logging.getLogger(__name__)


class RuntimeStore:
    def __init__(self) -> None:
        self._url = os.environ.get("HERMES_REDIS_URL", "").strip()
        self._redis = Redis.from_url(self._url, decode_responses=True) if self._url else None
        self._local_guard = threading.Lock()
        self._local_locks: dict[str, str] = {}
        self._local_cancelled: set[str] = set()

    def _key(self, *parts: str) -> str:
        return ":".join(("hermes", tenant_id(), *parts))

    def health(self) -> bool | None:
        if self._redis is None:
            return None
        try:
            return bool(self._redis.ping())
        except RedisError:
            return False

    def acquire_conversation(self, conversation_id: str, ttl_seconds: int = 300) -> str | None:
        token = str(uuid.uuid4())
        key = self._key("conversation", conversation_id, "lock")
        if self._redis is not None:
            try:
                if self._redis.set(key, token, nx=True, ex=ttl_seconds):
                    return token
                return None
            except RedisError as exc:
                logger.warning("Redis lock unavailable, using process-local lock: %s", exc)
        with self._local_guard:
            if key in self._local_locks:
                return None
            self._local_locks[key] = token
            return token

    def release_conversation(self, conversation_id: str, token: str) -> None:
        key = self._key("conversation", conversation_id, "lock")
        if self._redis is not None:
            try:
                self._redis.eval(
                    "if redis.call('get', KEYS[1]) == ARGV[1] then "
                    "return redis.call('del', KEYS[1]) else return 0 end",
                    1,
                    key,
                    token,
                )
                return
            except RedisError as exc:
                logger.warning("Redis lock release failed: %s", exc)
        with self._local_guard:
            if self._local_locks.get(key) == token:
                self._local_locks.pop(key, None)

    def mark_request(self, request_id: str, state: str, ttl_seconds: int = 3600) -> None:
        if self._redis is None:
            return
        try:
            self._redis.set(self._key("request", request_id, "state"), state, ex=ttl_seconds)
        except RedisError as exc:
            logger.warning("Redis request state write failed: %s", exc)

    def cancel_request(self, request_id: str, ttl_seconds: int = 3600) -> None:
        key = self._key("request", request_id, "cancel")
        if self._redis is not None:
            try:
                self._redis.set(key, "1", ex=ttl_seconds)
                return
            except RedisError as exc:
                logger.warning("Redis cancellation write failed: %s", exc)
        with self._local_guard:
            self._local_cancelled.add(key)

    def is_cancelled(self, request_id: str) -> bool:
        key = self._key("request", request_id, "cancel")
        if self._redis is not None:
            try:
                return self._redis.get(key) == "1"
            except RedisError as exc:
                logger.warning("Redis cancellation read failed: %s", exc)
        with self._local_guard:
            return key in self._local_cancelled

    def append_event(
        self,
        request_id: str,
        event_id: int,
        event: str,
        data: dict[str, Any],
        ttl_seconds: int = 3600,
    ) -> None:
        if self._redis is None:
            return
        key = self._key("stream", request_id, "events")
        payload = json.dumps(
            {"id": event_id, "event": event, "data": data, "created_at": time.time()},
            ensure_ascii=False,
        )
        try:
            pipe = self._redis.pipeline()
            pipe.rpush(key, payload)
            pipe.ltrim(key, -500, -1)
            pipe.expire(key, ttl_seconds)
            pipe.execute()
        except RedisError as exc:
            logger.warning("Redis SSE buffer write failed: %s", exc)


_runtime_store: RuntimeStore | None = None
_runtime_lock = threading.Lock()


def get_runtime_store() -> RuntimeStore:
    global _runtime_store
    configured_url = os.environ.get("HERMES_REDIS_URL", "").strip()
    if _runtime_store is None or _runtime_store._url != configured_url:
        with _runtime_lock:
            if _runtime_store is None or _runtime_store._url != configured_url:
                _runtime_store = RuntimeStore()
    return _runtime_store


def reset_runtime_store_for_tests() -> None:
    global _runtime_store
    with _runtime_lock:
        _runtime_store = None
