from __future__ import annotations

import atexit
import os
import re
from pathlib import Path
from typing import Any

import logging
import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


DATABASE_URL = (os.environ.get("DATABASE_URL") or "").strip()
POSTGRES_SCHEMA_PATH = Path(__file__).with_name("postgres_schema.sql")

REPLACE_CONFLICT_COLUMNS = {
    "ai_query_cache": ("cache_key",),
    "discovery_cache": ("cache_key",),
    "discovery_hits": ("id",),
    "query_products": ("normalized_query", "product_id", "page_number"),
    "reviews": ("id",),
    "user_recommendations": ("user_id", "product_id"),
}

INSERT_OR_REPLACE_PATTERN = re.compile(
    r"INSERT\s+OR\s+REPLACE\s+INTO\s+([a-zA-Z_][\w]*)\s*\((.*?)\)\s*VALUES\s*\((.*?)\)",
    re.IGNORECASE | re.DOTALL,
)
NAMED_PLACEHOLDER_PATTERN = re.compile(r"(?<!:):([a-zA-Z_][a-zA-Z0-9_]*)")

_POOL: ConnectionPool | None = None


def postgres_enabled() -> bool:
    return bool(DATABASE_URL)


def _pool() -> ConnectionPool:
    global _POOL
    if _POOL is None:
        if not DATABASE_URL:
            raise RuntimeError("DATABASE_URL is not configured")
        _POOL = ConnectionPool(
            conninfo=DATABASE_URL,
            min_size=1,
            max_size=20,
            kwargs={"row_factory": dict_row, "prepare_threshold": None},
            open=True,
        )
    return _POOL


def _close_pool() -> None:
    global _POOL
    if _POOL is not None:
        _POOL.close()
        _POOL = None


atexit.register(_close_pool)


def _replace_placeholders(sql: str) -> str:
    result: list[str] = []
    in_single = False
    in_double = False
    index = 0
    while index < len(sql):
        char = sql[index]
        if char == "'" and not in_double:
            result.append(char)
            if in_single and index + 1 < len(sql) and sql[index + 1] == "'":
                result.append(sql[index + 1])
                index += 2
                continue
            in_single = not in_single
            index += 1
            continue
        if char == '"' and not in_single:
            in_double = not in_double
            result.append(char)
            index += 1
            continue
        if char == "?" and not in_single and not in_double:
            result.append("%s")
        else:
            result.append(char)
        index += 1
    return "".join(result)


def _replace_named_placeholders(sql: str) -> str:
    return NAMED_PLACEHOLDER_PATTERN.sub(r"%(\1)s", sql)


def _rewrite_insert_or_replace(sql: str) -> str:
    match = INSERT_OR_REPLACE_PATTERN.search(sql)
    if not match:
        return sql
    table_name = match.group(1)
    conflict_columns = REPLACE_CONFLICT_COLUMNS.get(table_name.lower())
    if not conflict_columns:
        return sql.replace("INSERT OR REPLACE", "INSERT")
    column_names = [column.strip() for column in match.group(2).split(",")]
    update_columns = [column for column in column_names if column not in conflict_columns]
    conflict_target = ", ".join(conflict_columns)
    if update_columns:
        updates = ", ".join(f"{column} = EXCLUDED.{column}" for column in update_columns)
        replacement = (
            f"INSERT INTO {table_name} ({match.group(2)}) VALUES ({match.group(3)}) "
            f"ON CONFLICT ({conflict_target}) DO UPDATE SET {updates}"
        )
    else:
        replacement = (
            f"INSERT INTO {table_name} ({match.group(2)}) VALUES ({match.group(3)}) "
            f"ON CONFLICT ({conflict_target}) DO NOTHING"
        )
    return INSERT_OR_REPLACE_PATTERN.sub(replacement, sql, count=1)


def translate_sql(sql: str) -> str:
    translated = sql
    if "INSERT OR REPLACE" in translated.upper():
        translated = _rewrite_insert_or_replace(translated)
    translated = _replace_named_placeholders(translated)
    translated = _replace_placeholders(translated)
    translated = re.sub(r"\bis_active\s*=\s*1\b", "is_active = TRUE", translated, flags=re.IGNORECASE)
    translated = re.sub(r"\bis_active\s*=\s*0\b", "is_active = FALSE", translated, flags=re.IGNORECASE)
    return translated


class BufferedResult:
    def __init__(self, rows: list[dict[str, Any]], rowcount: int):
        self._rows = rows
        self.rowcount = rowcount

    def fetchone(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self._rows)


class PostgresConnectionWrapper:
    def __init__(self, connection: psycopg.Connection, pool: ConnectionPool):
        self._connection = connection
        self._pool = pool

    def execute(self, sql: str, params: Any = ()) -> BufferedResult:
        translated = translate_sql(sql)
        if isinstance(params, dict):
            params = {
                key: (bool(value) if key == "is_active" and value is not None else value)
                for key, value in params.items()
            }
        with self._connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(translated, params)
            rows = cursor.fetchall() if cursor.description else []
            return BufferedResult(rows, cursor.rowcount)

    def executescript(self, sql_text: str) -> None:
        statements = [statement.strip() for statement in sql_text.split(";\n") if statement.strip()]
        with self._connection.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        if self._connection.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
            self._connection.rollback()
        self._pool.putconn(self._connection)

    def __enter__(self) -> "PostgresConnectionWrapper":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type:
            self.rollback()
        else:
            self.commit()
        self.close()


def get_connection() -> PostgresConnectionWrapper:
    pool = _pool()
    logging.getLogger(__name__).debug("Requesting connection from pool")
    conn = pool.getconn()
    logging.getLogger(__name__).debug("Obtained connection from pool")
    return PostgresConnectionWrapper(conn, pool)
