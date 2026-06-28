# -*- coding: utf-8 -*-
"""CLI wrapper for cold-start theme pack import (optional seed, not runtime config)."""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.services.theme_pack_importer import import_theme_pack  # noqa: E402


def main() -> int:
    # 默认 changxin_chain 仅为冷启动示例；生产推荐 --extract-exposure-edges
    arg = sys.argv[1] if len(sys.argv) > 1 else "changxin_chain"
    if str(arg).lower().endswith((".yaml", ".yml")):
        stats = import_theme_pack(path=arg)
    else:
        stats = import_theme_pack(pack_id=arg)
    print(stats)
    return 0 if stats.get("errors", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
