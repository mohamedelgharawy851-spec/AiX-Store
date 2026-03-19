from __future__ import annotations

import argparse
import asyncio
import json

from _supabase_utils import connect_postgres, env_value, supabase_admin_request


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Trigger Supabase password-recovery emails for all migrated profiles.")
    parser.add_argument("--database-url", default="", help="Overrides DATABASE_URL.")
    parser.add_argument("--supabase-url", default="", help="Overrides SUPABASE_URL.")
    parser.add_argument("--service-role-key", default="", help="Overrides SUPABASE_SERVICE_ROLE_KEY.")
    return parser.parse_args()


async def trigger_recovery(email: str, supabase_url: str, service_role_key: str) -> None:
    await supabase_admin_request(
        method="POST",
        supabase_url=supabase_url,
        service_role_key=service_role_key,
        path="/auth/v1/recover",
        json_body={"email": email},
    )


def main() -> int:
    args = parse_args()
    database_url = env_value("DATABASE_URL", args.database_url)
    supabase_url = env_value("SUPABASE_URL", args.supabase_url)
    service_role_key = env_value("SUPABASE_SERVICE_ROLE_KEY", args.service_role_key)

    with connect_postgres(database_url) as connection:
        rows = connection.execute(
            """
            SELECT email
            FROM public.profiles
            WHERE email IS NOT NULL AND email <> ''
            ORDER BY created_at ASC, email ASC
            """
        ).fetchall()

    sent = 0
    failed: list[dict[str, str]] = []
    for row in rows:
        email = str(row["email"]).strip().lower()
        if not email:
            continue
        try:
            asyncio.run(trigger_recovery(email, supabase_url, service_role_key))
            sent += 1
        except Exception as exc:
            failed.append({"email": email, "error": str(exc)})

    print(json.dumps({"sent": sent, "failed": failed}, indent=2, ensure_ascii=True))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
