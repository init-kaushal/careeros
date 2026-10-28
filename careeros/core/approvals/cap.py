"""The weekly invite cap (outreach spec section 6.4). Pure: `now` is a parameter, nothing here reads a clock or a file.

Neutral module of the approvals package: `approval begin` and `careeros outreach cap` share these functions, and approvals
imports nothing from the outreach package. Only the leaf `careeros.core.fold` (the profile fold marker) is used besides
approvals modules.

* `parse_cap_setting(profile_text)`: the text above the fold, the `## Outreach` section, exactly one line
  `Weekly invite cap: N` (a list marker and bold markup are tolerated, the label is case-insensitive). Absent (no heading,
  or the heading without the line, or the line outside the section or below the fold) is `CapSetting(None, None)`. Any
  line in the section that starts with the label but is not a valid setting, a value outside 0 to 1000, and a second such
  line are a `problem`: a typo never silently changes enforcement.
* `invite_count(states, now, exclude=None)`: every `linkedin_invite` item that is `in_flight` (any age) plus every one
  that is `consumed` with `now < consumed_at + 7 days`. An item is in one state at a time, so it is never counted twice;
  the count uses `ItemState.consumed_at`, never `started_at` or `last_ts`. A `consumed_at` that does not parse counts
  (fail closed), without a leave date. Released/voided/expired/declined/superseded/pending/approved items do not count.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from careeros.core.approvals import rules
from careeros.core.approvals.fold import parse_ts
from careeros.core.approvals.model import ItemState, State
from careeros.core.fold import above_fold

CAP_WINDOW_DAYS = 7
DEFAULT_WEEKLY_CAP = 15
MAX_WEEKLY_CAP = 1000
CAP_CONFIG_INVALID = "cap_config_invalid"
FIX_TEXT = "write exactly one line `Weekly invite cap: N` (a whole number from 0 to 1000) under `## Outreach` in profile.md"

_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_HEADING = re.compile(r"^ {0,3}##[ \t]+outreach[ \t]*#*[ \t]*$", re.I)
_ANY_H1_H2 = re.compile(r"^ {0,3}#{1,2}(?:[ \t]|$)")
_MARKER = re.compile(r"^(?:[-*+]|\d+[.)])[ \t]+")
_LABEL = re.compile(r"^weekly[ \t]+invite[ \t]+cap\b", re.I)
_MAX_LINE = 200           # a valid setting line is far shorter; longer lines are never fed to the regexes
_VALID = re.compile(r"^weekly[ \t]+invite[ \t]+cap[*_`]*[ \t]*:[ \t]*[*_`]*[ \t]*([0-9]{1,4})[ \t]*[*_`]*$", re.I | re.ASCII)


@dataclass(frozen=True)
class CapSetting:
    value: int | None = None        # the configured cap; None when absent or invalid
    problem: str | None = None      # set when the setting is present but invalid


@dataclass(frozen=True)
class CapRefusal:
    code: str                       # CAP_CONFIG_INVALID
    message: str


@dataclass(frozen=True)
class CapCount:
    in_flight: int
    consumed_in_window: int
    oldest_leaves: str | None = None    # when the oldest counted consumed invite leaves the window; None for in_flight only

    @property
    def total(self) -> int:
        return self.in_flight + self.consumed_in_window


def _normalised(line: str) -> str:
    text = _MARKER.sub("", line.strip(), count=1).replace("**", "").replace("__", "")
    return text.strip(" \t*_`")


def parse_cap_setting(profile_text: str) -> CapSetting:
    """The cap setting in `profile.md` text (see the module docstring). Never raises. A line is limited to 200 characters once
    stripped: a longer line that starts with the label is an invalid setting, any other longer line is ignored, and a longer
    `##` heading never opens the section (but still closes one)."""
    text = profile_text if isinstance(profile_text, str) else ""
    inside = False
    setting_lines: list[str] = []
    for raw in above_fold(text.lstrip("\ufeff")).splitlines():
        line = raw.strip()
        if _ANY_H1_H2.match(raw):
            inside = len(raw) <= _MAX_LINE and bool(_HEADING.match(raw))
            continue
        if inside and _LABEL.match(_normalised(line[:_MAX_LINE])):
            setting_lines.append(line if len(line) <= _MAX_LINE else line[:60] + "...")
    if not setting_lines:
        return CapSetting()
    if len(setting_lines) > 1:
        quoted = "; ".join(repr(s) for s in setting_lines)
        return CapSetting(None, f"more than one `Weekly invite cap` line under `## Outreach`: {quoted}")
    found = _VALID.match(_normalised(setting_lines[0])) if len(setting_lines[0]) <= _MAX_LINE else None
    if found is None:
        return CapSetting(None, f"the `Weekly invite cap` line {setting_lines[0]!r} is not `Weekly invite cap: N` with a whole number")
    value = int(found[1])           # at most 4 digits
    if value > MAX_WEEKLY_CAP:
        return CapSetting(None, f"the `Weekly invite cap` line {setting_lines[0]!r} is above {MAX_WEEKLY_CAP}")
    return CapSetting(value, None)


def effective_cap(setting: CapSetting) -> int | CapRefusal:
    """The cap to enforce: the configured value, 15 when absent, or a `cap_config_invalid` refusal when invalid."""
    if setting.problem is not None:
        return CapRefusal(CAP_CONFIG_INVALID, f"{setting.problem}; {FIX_TEXT}")
    return DEFAULT_WEEKLY_CAP if setting.value is None else setting.value


def invite_count(states: Mapping[str, ItemState], now: str, *, exclude: str | None = None) -> CapCount:
    """The cap count of the folded approval `states` (item id -> ItemState) at `now`; `exclude` omits that item id (the
    item being begun). Raises ValueError when `now` is unreadable."""
    current = parse_ts(now)
    if current is None:
        raise ValueError(f"unreadable now {now!r}")
    window = timedelta(days=CAP_WINDOW_DAYS)
    in_flight = consumed = 0
    leaves: list[datetime] = []
    for item_id, st in states.items():
        if item_id == exclude or st.kind != rules.LINKEDIN_INVITE:
            continue
        if st.state is State.IN_FLIGHT:
            in_flight += 1
        elif st.state is State.CONSUMED:
            at = parse_ts(st.consumed_at)
            if at is None:
                consumed += 1                   # fail closed: an unreadable ts still counts
            elif current < at + window:
                consumed += 1
                leaves.append(at + window)
    return CapCount(in_flight, consumed, min(leaves).strftime(_TS_FORMAT) if leaves else None)


def remaining(cap: int, count: CapCount) -> int:
    """`max(0, cap - count.total)`."""
    return max(0, cap - count.total)
