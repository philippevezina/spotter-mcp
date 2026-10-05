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

# A.3: progression. Loads in lb.
LINEAR_STEP_UPPER_LB = Decimal(5)
LINEAR_STEP_LOWER_LB = Decimal(10)
RIR_BIG_JUMP_ABOVE = 2  # reported RIR >= target + 2 -> add 2 increments
RIR_BIG_JUMP_INCREMENTS = 2
RIR_LOW_BELOW = 1  # reported RIR < target - 1 is harder than planned
# A.3 double_progression step 1 (decisions.md): add load unless RIR < target - 1.
DOUBLE_RIR_TOLERANCE = 1

# A.4: stalls. Consecutive misses at the same working weight.
STALL_WARNING_MISSES = 2
STALL_RESET_MISSES = 3
RESET_FRACTION = Decimal("0.10")

# A.6: readiness modifiers.
RED_RIR_ADD = 1
RED_SETS_REMOVE = 1
RED_MIN_SETS = 2

# A.9: deload.
DELOAD_FRACTION = Decimal("0.10")
DELOAD_RIR_ADD = 2

# A.11: rounding.
BARBELL_MIN_LB = Decimal(45)

# A.12: first exposure. No exposure in this many days -> needs_calibration.
HISTORY_DAYS = 84

# A.8: volume guardrails (create_plan warnings).
MUSCLES = frozenset(
    {
        "chest",
        "back",
        "shoulders",
        "biceps",
        "triceps",
        "quads",
        "hamstrings",
        "glutes",
        "calves",
        "core",
    }
)
WEEKLY_SETS_LOW = 8
WEEKLY_SETS_HIGH = 20
NEW_PLAN_SETS_LOW = 10
NEW_PLAN_SETS_HIGH = 12
SESSION_SETS_HIGH = 25
SECONDARY_SET_WEIGHT = Decimal("0.5")

# A.10: plan length and end criteria.
BLOCK_WEEKS_MIN = 4
BLOCK_WEEKS_MAX = 8
DEFAULT_MAX_WEEKS = 6
DEFAULT_STALL_LIFTS = 2
DEFAULT_MIN_ADHERENCE = Decimal("0.7")
DEFAULT_ADHERENCE_WINDOW_WEEKS = 2
