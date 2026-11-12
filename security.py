"""本地安全存储：加密数据库连接配置与管理员登录凭据。

约定：
- `secret.key`：Fernet 对称密钥，首次运行自动生成，勿外泄、勿提交版本库。
- `db_config.json`：内容为 Fernet 密文（整文件加密），明文永不落盘。
- 管理员密码只存 PBKDF2-HMAC-SHA256 摘要与盐，不存明文。
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

BASE_DIR = Path(__file__).resolve().parent
KEY_FILE = BASE_DIR / "secret.key"
CONFIG_FILE = BASE_DIR / "db_config.json"
PBKDF2_ITERATIONS = 200_000

_DEFAULT_CONFIG: dict = {
    "db": {"host": "", "port": "", "dbname": "", "user": "", "password": ""},
    "admin": {},  # {"username", "salt", "password_hash"}，为空表示首次使用
}


# ---------------------------------------------------------------- 密钥与加解密

def load_key() -> bytes:
    """读取或首次生成 Fernet 密钥；同一密钥亦用作 Flask 会话签名密钥。"""
    if KEY_FILE.exists():
        return KEY_FILE.read_bytes().strip()
    key = Fernet.generate_key()
    KEY_FILE.write_bytes(key)
    try:
        os.chmod(KEY_FILE, 0o600)
    except OSError:  # noqa: BLE001 Windows 等场景
        pass
    return key


def _fernet() -> Fernet:
    return Fernet(load_key())


def encrypt_config(data: dict) -> bytes:
    return _fernet().encrypt(json.dumps(data, ensure_ascii=False).encode("utf-8"))


def decrypt_config(token: bytes) -> dict:
    try:
        raw = _fernet().decrypt(token)
    except InvalidToken as exc:
        raise RuntimeError("配置文件解密失败：密钥与 db_config.json 不匹配") from exc
    return json.loads(raw.decode("utf-8"))


# ---------------------------------------------------------------- 配置读写

def load_config() -> dict:
    if not CONFIG_FILE.exists():
        return json.loads(json.dumps(_DEFAULT_CONFIG))
    try:
        data = decrypt_config(CONFIG_FILE.read_bytes())
    except Exception:  # noqa: BLE001 文件缺失或损坏时回退默认
        return json.loads(json.dumps(_DEFAULT_CONFIG))
    merged = json.loads(json.dumps(_DEFAULT_CONFIG))
    merged.update(data)
    return merged


def save_config(data: dict) -> None:
    CONFIG_FILE.write_bytes(encrypt_config(data))
    try:
        os.chmod(CONFIG_FILE, 0o600)
    except OSError:  # noqa: BLE001
        pass


# ---------------------------------------------------------------- 数据库配置

def get_db_config() -> dict:
    return dict(load_config()["db"])


def set_db_config(db: dict) -> None:
    config = load_config()
    config["db"] = dict(db)
    save_config(config)


# ---------------------------------------------------------------- 管理员凭据

def hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    """返回 (salt_hex, hash_hex)。"""
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS
    )
    return salt.hex(), digest.hex()


def verify_password(password: str, salt_hex: str, expected_hex: str) -> bool:
    try:
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        return False
    _, digest = hash_password(password, salt)
    return secrets.compare_digest(digest, expected_hex)


def get_admin() -> dict | None:
    admin = load_config()["admin"]
    return admin or None


def set_admin(username: str, password: str) -> None:
    salt_hex, hash_hex = hash_password(password)
    config = load_config()
    config["admin"] = {"username": username, "salt": salt_hex, "password_hash": hash_hex}
    save_config(config)


def verify_admin(username: str, password: str) -> bool:
    admin = get_admin()
    if not admin:
        return False
    return (
        secrets.compare_digest(username, admin.get("username", ""))
        and verify_password(password, admin.get("salt", ""), admin.get("password_hash", ""))
    )
