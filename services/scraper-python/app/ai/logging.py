from __future__ import annotations

import json
import secrets
from typing import Any

from ..utils import now_iso


def _get_connection():
    from ..storage import db as db_module

    return db_module.get_connection()


def start_ai_run(
    *,
    run_type: str,
    mode: str,
    trigger_reason: str,
    model_id: str,
    prompt_version: str,
    input_payload: dict[str, Any],
) -> str | None:
    run_id = secrets.token_hex(12)
    connection = _get_connection()
    try:
        connection.execute(
            """
            INSERT INTO ai_runs (
              id, run_type, mode, trigger_reason, model_id, prompt_version, input_json, output_json, status, latency_ms, error_text, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 'skipped', NULL, NULL, ?)
            """,
            (
                run_id,
                run_type,
                mode,
                trigger_reason,
                model_id,
                prompt_version,
                json.dumps(input_payload, ensure_ascii=True, separators=(",", ":")),
                now_iso(),
            ),
        )
        connection.commit()
    except Exception:
        return None
    finally:
        connection.close()
    return run_id


def finish_ai_run(
    run_id: str | None,
    *,
    status: str,
    output_payload: dict[str, Any] | None = None,
    latency_ms: int | None = None,
    error_text: str | None = None,
) -> None:
    if not run_id:
        return
    connection = _get_connection()
    try:
        connection.execute(
            """
            UPDATE ai_runs
            SET status = ?, output_json = ?, latency_ms = ?, error_text = ?
            WHERE id = ?
            """,
            (
                status,
                json.dumps(output_payload, ensure_ascii=True, separators=(",", ":")) if output_payload is not None else None,
                latency_ms,
                error_text,
                run_id,
            ),
        )
        connection.commit()
    except Exception:
        return
    finally:
        connection.close()
