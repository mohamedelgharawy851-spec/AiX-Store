from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from _supabase_utils import DEFAULT_SQLITE_DB


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit and export the local SQLite catalog DB for Supabase migration.")
    parser.add_argument("--sqlite-db", default=str(DEFAULT_SQLITE_DB), help="Path to the local SQLite database.")
    parser.add_argument(
        "--output-dir",
        default="",
        help="Optional output directory. Defaults to services/scraper-python/data/migration-audit/<timestamp>/",
    )
    return parser.parse_args()


def sqlite_tables(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [str(row[0]) for row in rows]


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), "utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True))
            handle.write("\n")


def build_schema_summary(table_name: str, columns: list[dict], foreign_keys: list[dict], indexes: list[dict], row_count: int) -> str:
    lines = [f"## `{table_name}`", "", f"- Row count: `{row_count}`", ""]
    lines.append("| Column | Type | Not Null | Default | PK |")
    lines.append("| --- | --- | --- | --- | --- |")
    for column in columns:
        lines.append(
            f"| `{column['name']}` | `{column['type']}` | `{bool(column['notnull'])}` | `{column['dflt_value']}` | `{column['pk']}` |"
        )
    lines.append("")
    if foreign_keys:
        lines.append("Foreign keys:")
        for fk in foreign_keys:
            lines.append(
                f"- `{fk['from']}` -> `{fk['table']}.{fk['to']}` (on_update={fk['on_update']}, on_delete={fk['on_delete']})"
            )
        lines.append("")
    if indexes:
        lines.append("Indexes:")
        for index in indexes:
            lines.append(
                f"- `{index['name']}` unique={bool(index['unique'])} origin={index['origin']} partial={bool(index['partial'])}"
            )
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    sqlite_db = Path(args.sqlite_db).expanduser().resolve()
    if not sqlite_db.exists():
        raise SystemExit(f"SQLite database not found: {sqlite_db}")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = (
        Path(args.output_dir).expanduser().resolve()
        if args.output_dir
        else sqlite_db.parent / "migration-audit" / timestamp
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(sqlite_db)
    connection.row_factory = sqlite3.Row
    try:
        tables = sqlite_tables(connection)
        counts: dict[str, int] = {}
        schema_summary_parts: list[str] = ["# SQLite Migration Audit", "", f"- Database: `{sqlite_db}`", f"- Generated at: `{timestamp}`", ""]
        fk_graph: dict[str, list[dict]] = defaultdict(list)

        for table_name in tables:
            row_count = int(connection.execute(f"SELECT COUNT(*) AS count FROM {table_name}").fetchone()["count"])
            counts[table_name] = row_count
            columns = [dict(row) for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()]
            foreign_keys = [dict(row) for row in connection.execute(f"PRAGMA foreign_key_list({table_name})").fetchall()]
            indexes = [dict(row) for row in connection.execute(f"PRAGMA index_list({table_name})").fetchall()]
            rows = [dict(row) for row in connection.execute(f"SELECT * FROM {table_name}").fetchall()]

            for fk in foreign_keys:
                fk_graph[table_name].append(
                    {
                        "from": fk["from"],
                        "to_table": fk["table"],
                        "to_column": fk["to"],
                        "on_update": fk["on_update"],
                        "on_delete": fk["on_delete"],
                    }
                )

            write_json(output_dir / "schema" / f"{table_name}.json", {
                "columns": columns,
                "foreign_keys": foreign_keys,
                "indexes": indexes,
            })
            write_jsonl(output_dir / "rows" / f"{table_name}.jsonl", rows)
            write_json(output_dir / "samples" / f"{table_name}.json", rows[:5])
            schema_summary_parts.append(build_schema_summary(table_name, columns, foreign_keys, indexes, row_count))

        write_json(output_dir / "table_counts.json", counts)
        write_json(output_dir / "foreign_key_graph.json", fk_graph)
        (output_dir / "schema_summary.md").write_text("\n".join(schema_summary_parts), "utf-8")
    finally:
        connection.close()

    print(f"SQLite audit written to: {output_dir}")
    for table_name, count in sorted(counts.items()):
        print(f"{table_name}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
