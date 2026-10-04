"""Every coaching threshold lives here (coaching-rules.md Part A). Tune here, never in logic."""

from __future__ import annotations

from decimal import Decimal

# A.1: a set below this fraction of the session top set weight is a warm-up.
WARMUP_FRACTION = Decimal("0.60")

# A.2: e1RM uses sets in this rep range only.
E1RM_MIN_REPS = 1
E1RM_MAX_REPS = 10
EPLEY_REP_DIVISOR = Decimal(30)

# A.5: readiness classification. Scores are Garmin's 0-100 scales; HRV in ms.
HRV_STATUS_RED = frozenset({"LOW", "POOR"})
READINESS_RED_BELOW = 25
READINESS_AMBER_BELOW = 50
SLEEP_RED_BELOW = 50  # red only together with low Body Battery
BODY_BATTERY_MAX_RED_BELOW = 40
SLEEP_AMBER_BELOW = 60

# A.7: endurance interference. Durations in seconds, training effect on Garmin's 0-5 scale.
RUN_TYPES = frozenset(
    {"running", "trail_running", "treadmill_running", "track_running", "street_running"}
)
RIDE_TYPES = frozenset(
    {
        "cycling",
        "road_biking",
        "indoor_cycling",
        "virtual_ride",
        "mountain_biking",
        "gravel_cycling",
        "cyclocross",
    }
)
RUN_LONG_S = 75 * 60
RUN_AEROBIC_TE_HARD = Decimal("4.0")
RUN_ANAEROBIC_TE_HARD = Decimal("3.0")
RIDE_LONG_S = 120 * 60
RIDE_AEROBIC_TE_HARD = Decimal("4.0")
# "48 h before" a planned day: the planned date and this many dates before it (decisions.md).
INTERFERENCE_LOOKBACK_DAYS = 2
LOWER_BODY_PATTERNS = frozenset({"squat", "hinge", "lunge"})

# Exercise history trend (not a coaching rule; display only). Changes smaller than this
# fraction of the first value read as flat.
TREND_FLAT_FRACTION = Decimal("0.01")
TREND_MIN_POINTS = 2
