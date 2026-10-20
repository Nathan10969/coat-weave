"""psycopg2 连接池包装 + pgvector 类型注册。"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

import psycopg2
from psycopg2.extensions import connection as PgConnection
from psycopg2.pool import SimpleConnectionPool

from ..config import SETTINGS

try:
    from pgvector.psycopg2 import register_vector  # type: ignore[import-not-found]
except ImportError:  # 部分 sandbox 没装 pgvector
    register_vector = None  # type: ignore[assignment]


logger = logging.getLogger(__name__)


_POOL: SimpleConnectionPool | None = None


def get_pool(minconn: int = 1, maxconn: int = 5) -> SimpleConnectionPool:
    """返回（懒初始化的）全局连接池。"""
    global _POOL
    if _POOL is None:
        _POOL = SimpleConnectionPool(
            minconn,
            maxconn,
            host=SETTINGS.db.host,
            port=SETTINGS.db.port,
            dbname=SETTINGS.db.name,
            user=SETTINGS.db.user,
            password=SETTINGS.db.password,
        )
    return _POOL


@contextmanager
def get_conn() -> Iterator[PgConnection]:
    """从池借一个 conn，并注册 vector 类型。"""
    pool = get_pool()
    conn = pool.getconn()
    try:
        if register_vector is not None:
            try:
                register_vector(conn)
            except psycopg2.Error as exc:
                # 最常见：DB 还没 CREATE pgvector extension。
                # conn 仍可跑非 vector 查询 (e.g. setup-db)，但后面任何
                # vector 列读写会因类型错误炸掉 — 这里把根因吵出来。
                logger.error(
                    "register_vector failed (%s). "
                    "Run `python -m coating_kg setup-db` to apply schema.sql "
                    "(which CREATEs the vector extension) before any ingest.",
                    exc,
                )
        yield conn
    finally:
        pool.putconn(conn)


def close_pool() -> None:
    global _POOL
    if _POOL is not None:
        _POOL.closeall()
        _POOL = None
