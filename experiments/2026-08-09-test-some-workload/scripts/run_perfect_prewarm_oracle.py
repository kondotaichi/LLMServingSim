#!/usr/bin/env python3
"""Run serving with a perfect proactive-KV-prewarm oracle.

Every redirect that would normally migrate reusable KV is treated as if the
same prefix had already been placed at the selected destination.  Routing and
all subsequent scheduling remain live, so queueing feedback is simulated.
This intentionally optimistic mode is an upper bound, not an implementable
predictor.
"""

from __future__ import annotations

from pathlib import Path
import sys


REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from serving.__main__ import main
from serving.core.router import Router


_apply_kv_migration = Router._apply_kv_migration_if_needed


def _apply_perfect_prewarm(self, req_data, sched):
    failover = req_data.get("failover")
    if failover and failover.get("failover_mode") == "migrate_kv":
        # local_kv follows the same prefix-seeding path but charges no
        # request-visible transfer latency, representing a completed prewarm.
        failover["failover_mode"] = "local_kv"
        geo = req_data.setdefault("geo", {})
        geo["perfect_prewarm_oracle"] = 1
    return _apply_kv_migration(self, req_data, sched)


Router._apply_kv_migration_if_needed = _apply_perfect_prewarm


if __name__ == "__main__":
    main()
