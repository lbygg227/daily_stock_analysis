# -*- coding: utf-8 -*-
"""One-off industry classification sync (no financial re-sync)."""

from __future__ import annotations

import os
import sys

# Clear system/session proxy before any network imports (domestic data sources).
for key in (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "http_proxy",
    "https_proxy",
    "ALL_PROXY",
    "all_proxy",
):
    os.environ.pop(key, None)
os.environ["NO_PROXY"] = "*"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.services.fundamental_sync import FundamentalSyncService  # noqa: E402


def main() -> int:
    include_boards = "--boards" in sys.argv
    svc = FundamentalSyncService()
    result = {
        "exchange": svc.enrich_industry_from_exchange(),
        "quote": svc.enrich_industry_from_em_quote(),
    }
    remaining = len(svc.repo.get_stocks_without_industry())
    if remaining > 0 and include_boards:
        result["boards"] = svc.enrich_industry_from_ths()
    else:
        result["boards"] = 0
    result["remaining"] = len(svc.repo.get_stocks_without_industry())
    print(f"DONE industry_enrich={result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
