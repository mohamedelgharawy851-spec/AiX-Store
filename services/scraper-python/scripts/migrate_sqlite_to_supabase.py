from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

from _supabase_utils import (
    DEFAULT_SQLITE_DB,
    POSTGRES_SCHEMA_PATH,
    connect_postgres,
    env_value,
    execute_sql_script,
    json_ready,
    supabase_admin_request,
)


JSONB_COLUMNS = {
    "products": {"category_scores_json", "matched_terms_json", "image_gallery_json", "variant_attributes_json", "tags_json", "raw_json"},
    "queries": {"next_page_token_json", "query_variants_json"},
    "reviews": {"raw_json"},
    "user_events": {"product_snapshot_json", "metadata_json"},
    "user_favorites": {"product_snapshot_json"},
    "ai_query_cache": {"rewrite_json"},
    "ai_runs": {"input_json", "output_json"},
    "discovery_queries": {"request_json", "engines_json"},
    "discovery_cache": {"payload_json"},
}

BOOL_COLUMNS = {
    "products": {"is_active"},
}

UPSERT_KEYS = {
    "profiles": ("id",),
    "products": ("id",),
    "queries": ("normalized_query",),
    "query_products": ("normalized_query", "product_id", "page_number"),
    "reviews": ("id",),
    "related_products": ("product_id", "related_product_id"),
    "user_events": ("id",),
    "user_favorites": ("user_id", "product_id"),
    "user_affinities": ("user_id", "affinity_type", "affinity_key"),
    "user_recommendations": ("user_id", "product_id"),
    "ai_query_cache": ("cache_key",),
    "ai_runs": ("id",),
    "discovery_queries": ("context_key", "variant_text"),
    "discovery_hits": ("id",),
    "discovery_cache": ("cache_key",),
    "discovery_suppression": ("normalized_url",),
}

TABLE_ORDER = [
    "products",
    "queries",
    "query_products",
    "reviews",
    "related_products",
    "discovery_queries",
    "discovery_hits",
    "discovery_cache",
    "discovery_suppression",
    "ai_query_cache",
    "ai_runs",
    "profiles",
    "user_affinities",
    "user_events",
    "user_favorites",
    "user_recommendations",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate the local SQLite catalog DB into Supabase-backed PostgreSQL.")
    parser.add_argument("--sqlite-db", default=str(DEFAULT_SQLITE_DB), help="Path to the local SQLite database.")
    parser.add_argument("--database-url", default="", help="Overrides DATABASE_URL.")
    parser.add_argument("--supabase-url", default="", help="Overrides SUPABASE_URL.")
    parser.add_argument("--service-role-key", default="", help="Overrides SUPABASE_SERVICE_ROLE_KEY.")
    parser.add_argument("--apply-schema", action="store_true", help="Apply the PostgreSQL schema before migrating data.")
    parser.add_argument("--skip-auth", action="store_true", help="Skip auth/profiles migration and any user-linked tables.")
    parser.add_argument("--skip-recovery", action="store_true", help="Do not trigger Supabase password recovery emails.")
    parser.add_argument("--report-path", default="", help="Optional path for the migration report JSON.")
    return parser.parse_args()


def sqlite_connection(sqlite_db: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(sqlite_db)
    connection.row_factory = sqlite3.Row
    return connection


def sqlite_rows(connection: sqlite3.Connection, table_name: str) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(f"SELECT * FROM {table_name}").fetchall()]


def prepare_row(table_name: str, row: dict[str, Any], user_id_map: dict[str, str]) -> dict[str, Any]:
    prepared: dict[str, Any] = {}
    for key, value in row.items():
        if key == "user_id" and value is not None:
            mapped = user_id_map.get(str(value))
            if not mapped:
                raise ValueError(f"Missing migrated user mapping for legacy user_id={value!r} in table {table_name}")
            prepared[key] = mapped
            continue
        if key in JSONB_COLUMNS.get(table_name, set()):
            parsed = json_ready(value)
            if parsed is None:
                stripped = str(value).strip()
                if stripped.startswith("{"):
                    parsed = {}
                elif stripped.startswith("["):
                    parsed = []
            prepared[key] = Jsonb(parsed) if parsed is not None else None
            continue
        if key in BOOL_COLUMNS.get(table_name, set()):
            prepared[key] = bool(int(value or 0))
            continue
        prepared[key] = value
    return prepared


def build_exists_sql(table_name: str, row: dict[str, Any]) -> tuple[str, list[Any]]:
    keys = UPSERT_KEYS[table_name]
    clause = " AND ".join(f"{key} = %s" for key in keys)
    return f"SELECT 1 FROM public.{table_name} WHERE {clause} LIMIT 1", [row[key] for key in keys]


def build_upsert_sql(table_name: str, row: dict[str, Any]) -> tuple[str, list[Any]]:
    columns = list(row.keys())
    keys = UPSERT_KEYS[table_name]
    update_columns = [column for column in columns if column not in keys]
    quoted_columns = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))
    conflict_target = ", ".join(keys)
    if update_columns:
        update_set = ", ".join(f"{column} = EXCLUDED.{column}" for column in update_columns)
        sql = (
            f"INSERT INTO public.{table_name} ({quoted_columns}) VALUES ({placeholders}) "
            f"ON CONFLICT ({conflict_target}) DO UPDATE SET {update_set}"
        )
    else:
        sql = f"INSERT INTO public.{table_name} ({quoted_columns}) VALUES ({placeholders}) ON CONFLICT ({conflict_target}) DO NOTHING"
    return sql, [row[column] for column in columns]


def existing_key_set(postgres_conn, table_name: str) -> set[tuple[Any, ...]]:
    keys = UPSERT_KEYS[table_name]
    select_sql = f"SELECT {', '.join(keys)} FROM public.{table_name}"
    with postgres_conn.cursor() as cursor:
        rows = cursor.execute(select_sql).fetchall()
    result: set[tuple[Any, ...]] = set()
    for row in rows:
        if isinstance(row, dict):
            result.add(tuple(row[key] for key in keys))
        else:
            result.add(tuple(row))
    return result


async def list_supabase_users(supabase_url: str, service_role_key: str) -> dict[str, dict[str, Any]]:
    by_email: dict[str, dict[str, Any]] = {}
    page = 1
    while True:
        response = await supabase_admin_request(
            method="GET",
            supabase_url=supabase_url,
            service_role_key=service_role_key,
            path="/auth/v1/admin/users",
            query={"page": page, "per_page": 200},
        )
        payload = response.json() or {}
        users = payload.get("users") or []
        for user in users:
            email = str(user.get("email") or "").strip().lower()
            if email:
                by_email[email] = user
        if len(users) < 200:
            break
        page += 1
    return by_email


async def ensure_supabase_user(email: str, supabase_url: str, service_role_key: str, existing_users: dict[str, dict[str, Any]]) -> dict[str, Any]:
    normalized_email = email.strip().lower()
    existing = existing_users.get(normalized_email)
    if existing:
        return existing
    response = await supabase_admin_request(
        method="POST",
        supabase_url=supabase_url,
        service_role_key=service_role_key,
        path="/auth/v1/admin/users",
        json_body={
            "email": normalized_email,
            "password": secrets.token_urlsafe(24),
            "email_confirm": True,
            "user_metadata": {"migration_source": "sqlite"},
            "app_metadata": {"provider": "email"},
        },
    )
    payload = response.json() or {}
    user = payload.get("user") or payload
    existing_users[normalized_email] = user
    return user


async def trigger_password_recovery(email: str, supabase_url: str, service_role_key: str) -> None:
    await supabase_admin_request(
        method="POST",
        supabase_url=supabase_url,
        service_role_key=service_role_key,
        path="/auth/v1/recover",
        json_body={"email": email},
    )


async def migrate_profiles_and_auth(
    sqlite_rows_users: list[dict[str, Any]],
    *,
    postgres_conn,
    supabase_url: str,
    service_role_key: str,
    skip_recovery: bool,
) -> tuple[dict[str, str], dict[str, Any]]:
    existing_users = await list_supabase_users(supabase_url, service_role_key)
    user_id_map: dict[str, str] = {}
    summary = {"authUsersProcessed": 0, "recoveryRequested": 0, "profilesInserted": 0, "profilesUpdated": 0, "failed": []}

    with postgres_conn.cursor() as cursor:
        for sqlite_user in sqlite_rows_users:
            email = str(sqlite_user["email"]).strip().lower()
            legacy_user_id = str(sqlite_user["id"])
            try:
                auth_user = await ensure_supabase_user(email, supabase_url, service_role_key, existing_users)
                auth_user_id = str(auth_user["id"])
                user_id_map[legacy_user_id] = auth_user_id
                exists_row = cursor.execute("SELECT 1 FROM public.profiles WHERE id = %s", (auth_user_id,)).fetchone()
                cursor.execute(
                    """
                    INSERT INTO public.profiles (
                      id, legacy_user_id, email, role, level, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                      legacy_user_id = EXCLUDED.legacy_user_id,
                      email = EXCLUDED.email,
                      updated_at = EXCLUDED.updated_at
                    """,
                    (
                        auth_user_id,
                        legacy_user_id,
                        email,
                        "user",
                        1,
                        sqlite_user.get("created_at"),
                        sqlite_user.get("updated_at") or sqlite_user.get("created_at"),
                    ),
                )
                if exists_row:
                    summary["profilesUpdated"] += 1
                else:
                    summary["profilesInserted"] += 1
                summary["authUsersProcessed"] += 1
                if not skip_recovery:
                    await trigger_password_recovery(email, supabase_url, service_role_key)
                    summary["recoveryRequested"] += 1
            except Exception as exc:
                summary["failed"].append({"legacyUserId": legacy_user_id, "email": email, "error": str(exc)})
        postgres_conn.commit()
    return user_id_map, summary


def migrate_table(
    *,
    postgres_conn,
    table_name: str,
    rows: list[dict[str, Any]],
    user_id_map: dict[str, str],
) -> dict[str, int]:
    if not rows:
        return {"inserted": 0, "updated": 0, "skipped": 0, "failed": 0}

    prepared_rows: list[dict[str, Any]] = []
    failed = 0
    for source_row in rows:
        try:
            prepared_rows.append(prepare_row(table_name, source_row, user_id_map))
        except Exception:
            failed += 1

    if not prepared_rows:
        return {"inserted": 0, "updated": 0, "skipped": 0, "failed": failed}

    existing_keys = existing_key_set(postgres_conn, table_name)
    keys = UPSERT_KEYS[table_name]
    inserted = 0
    updated = 0
    upsert_sql, _ = build_upsert_sql(table_name, prepared_rows[0])
    columns = list(prepared_rows[0].keys())
    param_rows: list[list[Any]] = []
    for row in prepared_rows:
        row_key = tuple(row[key] for key in keys)
        if row_key in existing_keys:
            updated += 1
        else:
            inserted += 1
        param_rows.append([row[column] for column in columns])

    batch_size = 200
    with postgres_conn.cursor() as cursor:
        for index in range(0, len(param_rows), batch_size):
            cursor.executemany(upsert_sql, param_rows[index : index + batch_size])
            postgres_conn.commit()
    return {"inserted": inserted, "updated": updated, "skipped": 0, "failed": failed}


def apply_schema(connection) -> None:
    execute_sql_script(connection, POSTGRES_SCHEMA_PATH.read_text("utf-8"))


def default_report_path(sqlite_db: Path) -> Path:
    return sqlite_db.parent / "migration-audit" / "latest-supabase-migration-report.json"


def main() -> int:
    args = parse_args()
    sqlite_db = Path(args.sqlite_db).expanduser().resolve()
    if not sqlite_db.exists():
        raise SystemExit(f"SQLite database not found: {sqlite_db}")

    database_url = env_value("DATABASE_URL", args.database_url)
    supabase_url = env_value("SUPABASE_URL", args.supabase_url)
    service_role_key = env_value("SUPABASE_SERVICE_ROLE_KEY", args.service_role_key)
    report_path = Path(args.report_path).expanduser().resolve() if args.report_path else default_report_path(sqlite_db)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    report: dict[str, Any] = {"tables": {}, "auth": {}, "skipped": {}, "sqliteDb": str(sqlite_db)}

    sqlite_conn = sqlite_connection(sqlite_db)
    try:
        with connect_postgres(database_url) as postgres_conn:
            if args.apply_schema:
                print("Applying PostgreSQL schema...", flush=True)
                apply_schema(postgres_conn)
                report["schemaApplied"] = True

            user_id_map: dict[str, str] = {}
            sqlite_users = sqlite_rows(sqlite_conn, "users")
            if args.skip_auth:
                report["auth"] = {"skipped": True}
                report["skipped"]["users"] = len(sqlite_users)
                report["skipped"]["sessions"] = len(sqlite_rows(sqlite_conn, "sessions"))
            else:
                print("Migrating Supabase auth users and profiles...", flush=True)
                user_id_map, auth_summary = asyncio.run(
                    migrate_profiles_and_auth(
                        sqlite_users,
                        postgres_conn=postgres_conn,
                        supabase_url=supabase_url,
                        service_role_key=service_role_key,
                        skip_recovery=args.skip_recovery,
                    )
                )
                report["auth"] = auth_summary
                report["skipped"]["sessions"] = len(sqlite_rows(sqlite_conn, "sessions"))

            for table_name in TABLE_ORDER:
                if args.skip_auth and table_name.startswith("user_"):
                    report["skipped"][table_name] = len(sqlite_rows(sqlite_conn, table_name))
                    continue
                if table_name == "profiles":
                    continue
                table_rows = sqlite_rows(sqlite_conn, table_name)
                print(f"Migrating {table_name} ({len(table_rows)} rows)...", flush=True)
                report["tables"][table_name] = migrate_table(
                    postgres_conn=postgres_conn,
                    table_name=table_name,
                    rows=table_rows,
                    user_id_map=user_id_map,
                )

            with postgres_conn.cursor() as cursor:
                postgres_counts: dict[str, int] = {}
                for table_name in TABLE_ORDER:
                    if table_name == "profiles":
                        table_ref = "public.profiles"
                    else:
                        table_ref = f"public.{table_name}"
                    postgres_counts[table_name] = int(cursor.execute(f"SELECT COUNT(*) AS count FROM {table_ref}").fetchone()["count"])
                report["postgresCounts"] = postgres_counts
                postgres_conn.commit()
    finally:
        sqlite_conn.close()

    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=True), "utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
