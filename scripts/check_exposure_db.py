# -*- coding: utf-8 -*-
"""Quick exposure graph DB check for watchlist."""
from src.config import Config
from src.repositories.exposure_repo import ExposureRepository
from src.repositories.event_signal_repo import EventSignalRepository
from src.storage import DatabaseManager

def main() -> None:
    Config.reset_instance()
    db = DatabaseManager.get_instance()
    exposure = ExposureRepository(db)
    signals = EventSignalRepository(db)
    codes = [c.strip() for c in (Config.get_instance().stock_list or []) if str(c).strip()]
    print("STOCK_LIST:", codes)
    print("entity_alias:", len(exposure.list_entity_aliases()))
    rows, total = exposure.list_exposures(limit=500, include_disabled=True)
    print("company_exposure:", total)
    pending = signals.list_by_status("pending", limit=10)
    print("event_signal pending:", len(pending))
    for code in codes:
        prof = exposure.get_company_profile(code)
        exps = exposure.get_exposures_by_code(code, active_only=False)
        base = exposure.get_baseline_cache(code)
        name = prof.name if prof else None
        print(f"{code}: profile={name!r} exposures={len(exps)} baseline={bool(base)}")
        for edge in exps[:5]:
            print(f"  -> {edge.target_entity_id} ({edge.link_type}, {edge.source})")

if __name__ == "__main__":
    main()
