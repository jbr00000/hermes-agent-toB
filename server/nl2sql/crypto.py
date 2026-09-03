"""数据源密码的 Fernet 加解密。

密钥优先级：环境变量 ``NL2SQL_DS_KEY``（secret，放 $HERMES_HOME/.env）→
缺省时自动生成并持久化到 ``$HERMES_HOME/nl2sql_ds.key``（与 auth.py 的
jwt.key 同一模式，零配置可跑）。

密文只进 ``nl2sql_datasource.password_enc`` 列；API 层永不回传明文，
解密仅发生在「测试连接 / 抓 DDL / 问数执行」的内存中。
"""
from __future__ import annotations

import os
import threading

from cryptography.fernet import Fernet

from hermes_constants import get_hermes_home

_KEY: Fernet | None = None
_KEY_LOCK = threading.Lock()


def _fernet() -> Fernet:
    global _KEY
    if _KEY is not None:
        return _KEY
    with _KEY_LOCK:
        if _KEY is not None:
            return _KEY
        configured = os.environ.get("NL2SQL_DS_KEY", "").strip()
        if configured:
            _KEY = Fernet(configured.encode("utf-8"))
            return _KEY
        key_file = get_hermes_home() / "nl2sql_ds.key"
        if key_file.exists():
            raw = key_file.read_text(encoding="utf-8").strip()
        else:
            raw = Fernet.generate_key().decode("utf-8")
            key_file.parent.mkdir(parents=True, exist_ok=True)
            key_file.write_text(raw, encoding="utf-8")
        _KEY = Fernet(raw.encode("utf-8"))
        return _KEY


def encrypt_password(plain: str) -> str:
    """明文 → Fernet 密文（urlsafe base64 文本，可直接落 Text 列）。"""
    return _fernet().encrypt(plain.encode("utf-8")).decode("utf-8")


def decrypt_password(encrypted: str | None) -> str:
    """密文 → 明文；NULL/空串 → 空串（数据源允许无密码的场景由连接层兜底报错）。"""
    if not encrypted:
        return ""
    return _fernet().decrypt(encrypted.encode("utf-8")).decode("utf-8")
