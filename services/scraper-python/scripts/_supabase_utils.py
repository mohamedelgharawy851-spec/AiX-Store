from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx
import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row


BASE_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = Path(__file__).resolve().parents[3]
DEFAULT_SQLITE_DB = BASE_DIR / "data" / "catalog.db"
POSTGRES_SCHEMA_PATH = BASE_DIR / "app" / "storage" / "postgres_schema.sql"

load_dotenv(ROOT_DIR / ".env")


def env_value(name: str, explicit: str | None = None) -> str:
    value = (explicit or os.environ.get(name) or "").strip()
    if not value:
        raise ValueError(f"Missing required setting: {name}")
    return value


def connect_postgres(database_url: str):
    return psycopg.connect(database_url, row_factory=dict_row, prepare_threshold=None)


def execute_sql_script(connection: psycopg.Connection, sql_text: str) -> None:
    statements = [statement.strip() for statement in sql_text.split(";\n") if statement.strip()]
    with connection.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)
    connection.commit()


def json_ready(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "ignore")
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        if stripped[0] in "[{":
            try:
                return json.loads(stripped)
            except Exception:
                return value
        return value
    return value


async def supabase_admin_request(
    *,
    method: str,
    supabase_url: str,
    service_role_key: str,
    path: str,
    json_body: dict[str, Any] | None = None,
    query: dict[str, Any] | None = None,
    bearer_token: str | None = None,
) -> httpx.Response:
    headers = {
        "apikey": service_role_key,
        "Authorization": f"Bearer {bearer_token or service_role_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        response = await client.request(
            method,
            f"{supabase_url.rstrip('/')}{path}",
            headers=headers,
            json=json_body,
            params=query,
        )
        response.raise_for_status()
        return response
