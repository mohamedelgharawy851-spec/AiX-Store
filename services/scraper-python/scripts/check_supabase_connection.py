from __future__ import annotations

import argparse
import asyncio
import json

from _supabase_utils import connect_postgres, env_value, supabase_admin_request


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify Supabase Postgres and Auth connectivity.")
    parser.add_argument("--database-url", default="", help="Overrides DATABASE_URL.")
    parser.add_argument("--supabase-url", default="", help="Overrides SUPABASE_URL.")
    parser.add_argument("--service-role-key", default="", help="Overrides SUPABASE_SERVICE_ROLE_KEY.")
    return parser.parse_args()


async def check_http(supabase_url: str, service_role_key: str) -> dict[str, object]:
    users_response = await supabase_admin_request(
        method="GET",
        supabase_url=supabase_url,
        service_role_key=service_role_key,
        path="/auth/v1/admin/users",
        query={"page": 1, "per_page": 1},
    )
    jwks_response = await supabase_admin_request(
        method="GET",
        supabase_url=supabase_url,
        service_role_key=service_role_key,
        path="/auth/v1/.well-known/jwks.json",
    )
    return {
        "adminUsersStatus": users_response.status_code,
        "jwksStatus": jwks_response.status_code,
        "jwksKeys": len((jwks_response.json() or {}).get("keys") or []),
    }


def main() -> int:
    args = parse_args()
    database_url = env_value("DATABASE_URL", args.database_url)
    supabase_url = env_value("SUPABASE_URL", args.supabase_url)
    service_role_key = env_value("SUPABASE_SERVICE_ROLE_KEY", args.service_role_key)

    summary: dict[str, object] = {"postgres": {}, "auth": {}}

    with connect_postgres(database_url) as connection:
        row = connection.execute("select current_database() as name, current_user as username, version() as version").fetchone()
        summary["postgres"] = {
            "connected": True,
            "database": row["name"],
            "user": row["username"],
            "version": row["version"],
        }

    summary["auth"] = asyncio.run(check_http(supabase_url, service_role_key))
    print(json.dumps(summary, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
