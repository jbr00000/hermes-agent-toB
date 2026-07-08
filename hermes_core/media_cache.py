"""Media cache and local media path validation helpers.

This module holds the non-platform-specific pieces that used to live in the
messaging gateway adapter base class.
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import Optional

from hermes_constants import get_default_hermes_root, get_hermes_dir, get_hermes_home

DEFAULT_INBOUND_MEDIA_MAX_BYTES = 128 * 1024 * 1024

IMAGE_CACHE_DIR = get_hermes_dir("cache/images", "image_cache")
AUDIO_CACHE_DIR = get_hermes_dir("cache/audio", "audio_cache")
VIDEO_CACHE_DIR = get_hermes_dir("cache/videos", "video_cache")
DOCUMENT_CACHE_DIR = get_hermes_dir("cache/documents", "document_cache")
SCREENSHOT_CACHE_DIR = get_hermes_dir("cache/screenshots", "browser_screenshots")

_CACHE_DIR_IMPORT_DEFAULTS = {
    "IMAGE_CACHE_DIR": IMAGE_CACHE_DIR,
    "AUDIO_CACHE_DIR": AUDIO_CACHE_DIR,
    "VIDEO_CACHE_DIR": VIDEO_CACHE_DIR,
    "DOCUMENT_CACHE_DIR": DOCUMENT_CACHE_DIR,
    "SCREENSHOT_CACHE_DIR": SCREENSHOT_CACHE_DIR,
}

MEDIA_DELIVERY_ALLOW_DIRS_ENV = "HERMES_MEDIA_ALLOW_DIRS"
MEDIA_DELIVERY_TRUST_RECENT_SECONDS_ENV = "HERMES_MEDIA_TRUST_RECENT_SECONDS"
MEDIA_DELIVERY_STRICT_ENV = "HERMES_MEDIA_DELIVERY_STRICT"
_MEDIA_DELIVERY_TRUST_RECENT_DEFAULT_SECONDS = 600
_HERMES_HOME = get_hermes_home()
_HERMES_ROOT = get_default_hermes_root()


def _resolve_cache_dir(constant_name: str, new_subpath: str, old_name: str) -> Path:
    fresh = get_hermes_dir(new_subpath, old_name)
    current = globals().get(constant_name)
    default = _CACHE_DIR_IMPORT_DEFAULTS.get(constant_name)
    if current is not None and default is not None and current != default:
        return Path(current)
    return fresh


def get_image_cache_dir() -> Path:
    path = _resolve_cache_dir("IMAGE_CACHE_DIR", "cache/images", "image_cache")
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_audio_cache_dir() -> Path:
    path = _resolve_cache_dir("AUDIO_CACHE_DIR", "cache/audio", "audio_cache")
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_video_cache_dir() -> Path:
    path = _resolve_cache_dir("VIDEO_CACHE_DIR", "cache/videos", "video_cache")
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_document_cache_dir() -> Path:
    path = _resolve_cache_dir("DOCUMENT_CACHE_DIR", "cache/documents", "document_cache")
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_inbound_media_max_bytes() -> int:
    try:
        from hermes_cli.config import load_config

        cfg = load_config()
        gateway_cfg = cfg.get("gateway", {}) if isinstance(cfg, dict) else {}
        if isinstance(gateway_cfg, dict) and "max_inbound_media_bytes" in gateway_cfg:
            return int(gateway_cfg["max_inbound_media_bytes"])
    except Exception:
        pass
    return DEFAULT_INBOUND_MEDIA_MAX_BYTES


def validate_inbound_media_size(
    size: int,
    *,
    media_type: str = "media",
    max_bytes: Optional[int] = None,
) -> None:
    limit = get_inbound_media_max_bytes() if max_bytes is None else max_bytes
    if limit and size > limit:
        raise ValueError(
            f"Inbound {media_type} payload is too large "
            f"({size} bytes > {limit} bytes)"
        )


def _looks_like_image(data: bytes) -> bool:
    if len(data) < 4:
        return False
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return True
    if data[:3] == b"\xff\xd8\xff":
        return True
    if data[:6] in {b"GIF87a", b"GIF89a"}:
        return True
    if data[:2] == b"BM":
        return True
    return data[:4] == b"RIFF" and len(data) >= 12 and data[8:12] == b"WEBP"


def cache_image_from_bytes(data: bytes, ext: str = ".jpg") -> str:
    validate_inbound_media_size(len(data), media_type="image")
    if not _looks_like_image(data):
        snippet = data[:80].decode("utf-8", errors="replace")
        raise ValueError(
            f"Refusing to cache non-image data as {ext} "
            f"(starts with: {snippet!r})"
        )
    filepath = get_image_cache_dir() / f"img_{uuid.uuid4().hex[:12]}{ext}"
    filepath.write_bytes(data)
    return str(filepath)


def cache_audio_from_bytes(data: bytes, ext: str = ".ogg") -> str:
    validate_inbound_media_size(len(data), media_type="audio")
    filepath = get_audio_cache_dir() / f"audio_{uuid.uuid4().hex[:12]}{ext}"
    filepath.write_bytes(data)
    return str(filepath)


def cache_video_from_bytes(data: bytes, ext: str = ".mp4") -> str:
    validate_inbound_media_size(len(data), media_type="video")
    filepath = get_video_cache_dir() / f"video_{uuid.uuid4().hex[:12]}{ext}"
    filepath.write_bytes(data)
    return str(filepath)


def cache_document_from_bytes(data: bytes, filename: str) -> str:
    cache_dir = get_document_cache_dir()
    safe_name = Path(filename).name if filename else "document"
    safe_name = safe_name.replace("\x00", "").strip()
    if not safe_name or safe_name in {".", ".."}:
        safe_name = "document"
    filepath = cache_dir / f"doc_{uuid.uuid4().hex[:12]}_{safe_name}"
    if not filepath.resolve().is_relative_to(cache_dir.resolve()):
        raise ValueError(f"Path traversal rejected: {filename!r}")
    filepath.write_bytes(data)
    return str(filepath)


def cleanup_image_cache(max_age_hours: int = 24) -> int:
    return _cleanup_cache_dir(get_image_cache_dir(), max_age_hours)


def cleanup_document_cache(max_age_hours: int = 24) -> int:
    return _cleanup_cache_dir(get_document_cache_dir(), max_age_hours)


def _cleanup_cache_dir(cache_dir: Path, max_age_hours: int) -> int:
    cutoff = time.time() - (max_age_hours * 3600)
    removed = 0
    for path in cache_dir.iterdir():
        if path.is_file() and path.stat().st_mtime < cutoff:
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
    return removed


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _profile_cache_roots() -> list[Path]:
    roots: list[Path] = []
    profiles_dir = _HERMES_ROOT / "profiles"
    try:
        profile_dirs = [path for path in profiles_dir.iterdir() if path.is_dir()]
    except OSError:
        return roots
    for profile_dir in profile_dirs:
        for subdir in ("images", "audio", "videos", "documents", "screenshots"):
            roots.append(profile_dir / "cache" / subdir)
    return roots


def _media_delivery_allowed_roots() -> list[Path]:
    roots = [
        IMAGE_CACHE_DIR,
        AUDIO_CACHE_DIR,
        VIDEO_CACHE_DIR,
        DOCUMENT_CACHE_DIR,
        SCREENSHOT_CACHE_DIR,
        _HERMES_HOME / "cache" / "images",
        _HERMES_HOME / "cache" / "audio",
        _HERMES_HOME / "cache" / "videos",
        _HERMES_HOME / "cache" / "documents",
        _HERMES_HOME / "cache" / "screenshots",
    ]
    roots.extend(_profile_cache_roots())
    extra = os.environ.get(MEDIA_DELIVERY_ALLOW_DIRS_ENV, "")
    for chunk in extra.split(os.pathsep):
        for raw_root in chunk.split(","):
            raw_root = raw_root.strip()
            if raw_root:
                root = Path(os.path.expanduser(raw_root))
                if root.is_absolute():
                    roots.append(root)
    return roots


def _media_delivery_denied_paths() -> list[Path]:
    denied = [Path(p) for p in ("/etc", "/proc", "/sys", "/dev", "/root", "/boot")]
    home = Path(os.path.expanduser("~"))
    for subdir in (".ssh", ".aws", ".gnupg", ".kube", ".docker", ".config"):
        denied.append(home / subdir)
    for root in (_HERMES_HOME, _HERMES_ROOT):
        for rel in (
            ".env",
            "auth.json",
            "credentials",
            "config.yaml",
            ".anthropic_oauth.json",
            "google_token.json",
            "pairing",
            "mcp-tokens",
        ):
            denied.append(root / rel)
    return denied


def _path_under_denied_prefix(resolved: Path) -> bool:
    try:
        home = Path(os.path.expanduser("~")).resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        home = None
    for denied in _media_delivery_denied_paths():
        try:
            resolved_denied = denied.expanduser().resolve(strict=False)
        except (OSError, RuntimeError, ValueError):
            continue
        if not (_path_is_within(resolved, resolved_denied) or resolved == resolved_denied):
            continue
        if home is not None and resolved_denied == home:
            continue
        return True
    return False


def _file_is_recently_produced(resolved: Path) -> bool:
    raw = os.environ.get(MEDIA_DELIVERY_TRUST_RECENT_SECONDS_ENV, "").strip()
    try:
        window = max(0.0, float(raw)) if raw else _MEDIA_DELIVERY_TRUST_RECENT_DEFAULT_SECONDS
    except (TypeError, ValueError):
        window = _MEDIA_DELIVERY_TRUST_RECENT_DEFAULT_SECONDS
    if window <= 0:
        return False
    try:
        return (time.time() - resolved.stat().st_mtime) <= window
    except OSError:
        return False


def validate_media_delivery_path(path: str) -> Optional[str]:
    """Return a safe absolute file path for native media delivery, else None."""
    if not path:
        return None
    candidate = str(path).strip()
    if len(candidate) >= 2 and candidate[0] == candidate[-1] and candidate[0] in "`\"'":
        candidate = candidate[1:-1].strip()
    candidate = candidate.lstrip("`\"'").rstrip("`\"',.;:)}]")
    if not candidate:
        return None
    try:
        expanded = Path(os.path.expanduser(candidate))
    except (OSError, RuntimeError, ValueError):
        return None
    if not expanded.is_absolute():
        return None
    try:
        resolved = expanded.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return None
    if not resolved.is_file():
        return None

    for root in _media_delivery_allowed_roots():
        try:
            resolved_root = root.expanduser().resolve(strict=False)
        except (OSError, RuntimeError, ValueError):
            continue
        if _path_is_within(resolved, resolved_root):
            return str(resolved)

    if _path_under_denied_prefix(resolved):
        return None
    if os.environ.get(MEDIA_DELIVERY_STRICT_ENV, "").strip().lower() in {"1", "true", "yes", "on"}:
        return str(resolved) if _file_is_recently_produced(resolved) else None
    return str(resolved)
