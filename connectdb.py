"""PostgreSQL 连接配置。

连接参数保存在加密配置文件（security.set_db_config），也可用环境变量覆盖：

    PV_DB_HOST     默认空（未配置）
    PV_DB_PORT     默认 5432
    PV_DB_NAME     默认空（未配置）
    PV_DB_USER     默认空（未配置）
    PV_DB_PASSWORD 默认空

默认数据库信息为空：首次使用需在「设置」页填写并保存（自动加密存储）。
"""

from __future__ import annotations

import os
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

import security


def db_settings() -> dict:
    """当前生效的连接参数：加密配置文件为基础，环境变量可覆盖。"""
    cfg = security.get_db_config()

    def pick(env: str, key: str, default: str = "") -> str:
        return os.getenv(env) or str(cfg.get(key) or "") or default

    return {
        "host": pick("PV_DB_HOST", "host"),
        "port": int(pick("PV_DB_PORT", "port", "5432") or 5432),
        "dbname": pick("PV_DB_NAME", "dbname"),
        "user": pick("PV_DB_USER", "user"),
        "password": pick("PV_DB_PASSWORD", "password"),
    }


def is_configured() -> bool:
    s = db_settings()
    return bool(s["host"] and s["dbname"] and s["user"])


def dsn() -> str:
    s = db_settings()
    auth = s["user"] if not s["password"] else f"{s['user']}:{s['password']}"
    return f"postgresql://{auth}@{s['host']}:{s['port']}/{s['dbname']}"


@contextmanager
def get_conn():
    """获取一个自动提交/回滚的连接。"""
    if not is_configured():
        raise RuntimeError("数据库未配置，请到「设置」页填写连接信息")
    conn = psycopg.connect(**db_settings(), row_factory=dict_row)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def check_connection() -> tuple[bool, str]:
    """探测数据库是否可用，供界面提示使用。"""
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("select 1")
            cur.fetchone()
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, str(exc).strip()
