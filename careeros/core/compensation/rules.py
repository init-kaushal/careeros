"""Constants of the compensation record (spec sections 4, 5.1 and 8). Pure; no I/O, no imports.

`KINDS` is ordered highest trust first (`posted` over `recruiter` over `estimate`); the order is part of the rules, not
of the agent's confidence. `OBSERVATION_STALE_DAYS` feeds only the `CMP100` warning and never reaches a score.
"""

COMPENSATION_FILE = "compensation.md"
SCHEMA = 1
KINDS = ("posted", "recruiter", "estimate")
PERIODS = ("year", "month")
CONFIDENCES = ("high", "medium", "low")
FX_WINDOW_DAYS = 30
OBSERVATION_STALE_DAYS = 90
