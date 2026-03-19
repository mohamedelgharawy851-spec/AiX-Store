from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path


def main() -> int:
    output_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("services/scraper-python/data/ai-training.jsonl")
    from app.storage import db as db_module

    output_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_module.DB_PATH, timeout=30, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    rows: list[dict] = []

    for row in connection.execute("SELECT * FROM ai_runs ORDER BY created_at ASC").fetchall():
        rows.append(
            {
                "kind": "ai_run",
                "runType": row["run_type"],
                "mode": row["mode"],
                "triggerReason": row["trigger_reason"],
                "modelId": row["model_id"],
                "promptVersion": row["prompt_version"],
                "input": json.loads(row["input_json"]),
                "output": json.loads(row["output_json"]) if row["output_json"] else None,
                "status": row["status"],
                "latencyMs": row["latency_ms"],
                "errorText": row["error_text"],
                "createdAt": row["created_at"],
            }
        )

    for row in connection.execute(
        """
        SELECT ue.*, p.title, p.category_id, p.provider
        FROM user_events ue
        LEFT JOIN products p ON p.id = ue.product_id
        ORDER BY ue.created_at ASC
        """
    ).fetchall():
        rows.append(
            {
                "kind": "user_event",
                "type": row["event_type"],
                "userId": row["user_id"],
                "productId": row["product_id"],
                "productTitle": row["title"],
                "categoryId": row["category_id"],
                "provider": row["provider"],
                "queryText": row["query_text"],
                "sourceUrl": row["source_url"],
                "metadata": json.loads(row["metadata_json"]) if row["metadata_json"] else {},
                "createdAt": row["created_at"],
            }
        )

    output_path.write_text("\n".join(json.dumps(row, ensure_ascii=True) for row in rows) + ("\n" if rows else ""), "utf-8")
    print(f"Wrote {len(rows)} rows to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
