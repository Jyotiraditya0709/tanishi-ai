"""
Dream scheduler loop: nightly extraction + weekly consolidation.
"""

from __future__ import annotations

import os
import time
from datetime import datetime

from tanishi.core import get_config
from tanishi.memory.dream import DreamCycle
from tanishi.memory.manager import MemoryManager


def run_dream_loop(memory_manager=None, config=None):
    """
    Daemon thread: every 30 min checks if it's dream hour.
    - Nightly extraction at DREAM_HOUR (default 4 AM)
    - Weekly consolidation on Sundays at dream hour
    """
    cfg = config or get_config()
    mm = memory_manager
    if mm is None:
        try:
            mm = MemoryManager(cfg.db_path)
        except Exception:
            mm = None

    dream = DreamCycle(mm, cfg)
    dream_hour = int(os.getenv("DREAM_HOUR", "4"))
    last_extract_date = None
    last_consolidation_date = None

    print(f"[dream] dream cycle armed (hour={dream_hour:02d}:00)")

    while True:
        try:
            now = datetime.now()
            should_run = now.hour == dream_hour
            if should_run and last_extract_date != now.date():
                memories = dream.run_extraction(hours_back=24)
                print(f"[dream] extracted {len(memories)} memories from last 24h")
                last_extract_date = now.date()

                # Sunday consolidation (weekday: Monday=0 ... Sunday=6)
                if now.weekday() == 6 and last_consolidation_date != now.date():
                    consolidated = dream.run_consolidation()
                    n = sum(
                        len(v)
                        for v in consolidated.values()
                        if isinstance(v, list)
                    ) if isinstance(consolidated, dict) else 0
                    print(f"[dream] consolidated {n} entries into core knowledge")
                    last_consolidation_date = now.date()
        except Exception as e:
            print(f"[dream] loop error: {e}")

        time.sleep(30 * 60)
