"""Every coaching threshold lives here (coaching-rules.md Part A). Tune here, never in logic."""

from __future__ import annotations

from decimal import Decimal

# A.1: a set below this fraction of the session top set weight is a warm-up.
WARMUP_FRACTION = Decimal("0.60")

# A.2: e1RM uses sets in this rep range only.
E1RM_MIN_REPS = 1
E1RM_MAX_REPS = 10
EPLEY_REP_DIVISOR = Decimal(30)
