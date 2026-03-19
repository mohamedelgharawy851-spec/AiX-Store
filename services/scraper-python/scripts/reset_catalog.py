from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import urllib.error
import urllib.request

from app.jobs import job_runner
from app.storage.db import category_counts, count_products, initialize_database, reset_product_linked_state


def _health_ok(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1.5) as response:
            return int(getattr(response, "status", 0) or 0) == 200
    except (OSError, urllib.error.URLError):
        return False


def _warn_if_runtime_active() -> None:
    python_port = int(os.environ.get("SHOPEASE_PYTHON_PORT", "8790"))
    runtime_port = int(os.environ.get("SHOPEASE_RUNTIME_PORT", "8787"))
    active_services: list[str] = []
    if _health_ok(python_port):
        active_services.append(f"python:{python_port}")
    if _health_ok(runtime_port):
        active_services.append(f"runtime:{runtime_port}")
    if active_services:
        print(
            "Warning: reset is intended as an offline maintenance action, "
            f"but active services were detected ({', '.join(active_services)}).",
            file=sys.stderr,
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reset ShopEase product-linked state and optionally reseed the catalog.")
    parser.add_argument("--reseed", action="store_true", help="Rebuild the catalog after the reset.")
    parser.add_argument("--full-baseline", action="store_true", help="Run the full baseline reseed flow.")
    parser.add_argument("--per-category-target", type=int, default=24, help="Target minimum active products per category.")
    parser.add_argument("--gallery-concurrency", type=int, default=4, help="Concurrency for forced gallery enrichment.")
    parser.add_argument(
        "--drop-search-history",
        action="store_true",
        help="Also delete preserved non-product search history rows from user_events.",
    )
    return parser


async def _run(args: argparse.Namespace) -> dict[str, object]:
    initialize_database()
    reset_summary = reset_product_linked_state(preserve_search_history=not args.drop_search_history)
    reseed_summary: dict[str, object] | None = None
    backfill_summary: dict[str, object] | None = None

    should_reseed = bool(args.reseed or args.full_baseline)
    if should_reseed:
        reseed_summary = await job_runner.reseed_full_catalog_baseline(per_category_target=max(1, args.per_category_target))
        backfill_summary = await job_runner.backfill_product_galleries(concurrency=max(1, args.gallery_concurrency))

    return {
        "reset": reset_summary,
        "reseed": reseed_summary,
        "galleryBackfill": backfill_summary,
        "categoryCounts": category_counts(),
        "totalActiveProducts": count_products(),
    }


def _validate_thresholds(summary: dict[str, object], *, minimum_category_count: int = 12, minimum_total_count: int = 120) -> bool:
    reseed_summary = summary.get("reseed")
    if not isinstance(reseed_summary, dict):
        return True
    final_category_counts = summary.get("categoryCounts") if isinstance(summary.get("categoryCounts"), dict) else {}
    targets = reseed_summary.get("targets") if isinstance(reseed_summary.get("targets"), dict) else {}
    for category_id in targets.keys():
        if int(final_category_counts.get(category_id, 0) or 0) < minimum_category_count:
            return False
    return int(summary.get("totalActiveProducts") or 0) >= minimum_total_count


def main() -> int:
    args = _build_parser().parse_args()
    _warn_if_runtime_active()
    summary = asyncio.run(_run(args))
    print(json.dumps(summary, indent=2, ensure_ascii=True))
    if not _validate_thresholds(summary):
        print("Reset completed, but the reseed did not meet the minimum baseline thresholds.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
