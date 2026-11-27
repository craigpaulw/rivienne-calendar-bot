"""Pure rules for the Rivienne Calendar Bot. No Discord, no network, easy to test."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}

FIRST_YEAR = 2027      # prompt set 1 is used in 2027
CLAIM_DAYS = 14        # a scene must start within 14 days of the custom
PRESS_DAYS = 7         # a claim waits 7 days for a second player
VOTE_DELAY_DAYS = 7    # vote opens a week after the window closes
VOTE_DAYS = 7
LINE_DAYS = 7          # players get a week to write the Chronicle line

JOB_RANK2 = 8          # witnessed Job scenes for the second rank
JOB_RANK3 = 32         # witnessed Job scenes for Master

BIOMES = ["Woods", "Marsh", "Hill", "Beach", "Glacier", "Tropics",
          "Mountain", "Volcano", "Desert", "City"]


@dataclass
class Custom:
    name: str
    where: str
    post: str
    status: str
    twist: str
    start: tuple          # (day, month, year or None)
    end: tuple | None
    every: int            # 1 = every year, 4 = every four years
    fixed_year: int | None


@dataclass
class BynameRule:
    custom: str           # custom name, or "" for Island Hand
    byname: str
    mode: str             # "three" | "first" | "all"


def parse_dm(text: str):
    """'19 Jan' -> (19, 1, None); "'27 Oct 2027" -> (27, 10, 2027); '' -> None."""
    if not text:
        return None
    m = re.match(r"^\s*'?(\d{1,2})\s+([A-Za-z]{3})[A-Za-z]*\.?(?:\s+(\d{4}))?\s*$", text)
    if not m:
        return None
    mon = MONTHS.get(m.group(2).lower())
    if not mon:
        return None
    return int(m.group(1)), mon, int(m.group(3)) if m.group(3) else None


def make_custom(row: dict) -> Custom | None:
    start = parse_dm(row.get("start", ""))
    if not start or not row.get("name"):
        return None
    end = parse_dm(row.get("end", ""))
    rep = (row.get("repeats") or "").lower()
    every = 4 if "4 years" in rep else 1
    return Custom(row["name"].strip(), row.get("where", ""), row.get("post", ""),
                  (row.get("status") or "").strip(), (row.get("twist") or "").strip(),
                  start, end, every, start[2])


def occurrence(c: Custom, year: int):
    """(start_date, end_date) of the custom in this year, or None if it doesn't happen."""
    if c.fixed_year is not None:
        if year < c.fixed_year or (year - c.fixed_year) % c.every:
            return None
    d, m, _ = c.start
    s = date(year, m, d)
    if c.end:
        ed, em, _ = c.end
        e = date(year, em, ed)
    else:
        e = s
    return s, e


def prompt_set(c: Custom, year: int) -> int:
    """Which of the four prompt sets (1-4) applies this year."""
    if c.fixed_year is not None:
        idx = (year - c.fixed_year) // c.every
    else:
        idx = year - FIRST_YEAR
    return idx % 4 + 1


def active_occurrences(customs, today: date):
    """Customs whose claim window (start to end + 14 days) includes today."""
    out = []
    for c in customs:
        for y in (today.year, today.year - 1):
            occ = occurrence(c, y)
            if occ and occ[0] <= today <= occ[1] + timedelta(days=CLAIM_DAYS):
                out.append((c, y, occ[0], occ[1]))
    return out


def thread_in_window(start: date, end: date, thread_day: date) -> bool:
    return start <= thread_day <= end + timedelta(days=CLAIM_DAYS)


def rung_for(total: int, ladder):
    """ladder = [(stamps, name)] ascending. Returns (stamps, name) or None."""
    best = None
    for n, name in ladder:
        if total >= n:
            best = (n, name)
    return best


def next_rung(total: int, ladder):
    for n, name in ladder:
        if total < n:
            return n, name
    return None


def claim_passes(presses, claimant: int) -> bool:
    """presses = [(user_id, counts_toward_pass)]. Needs one other counted player."""
    return any(uid != claimant and counts for uid, counts in presses)


def job_rank(scenes: int) -> int:
    """Which rank a count of witnessed Job scenes has reached: 1, 2 or 3."""
    if scenes >= JOB_RANK3:
        return 3
    if scenes >= JOB_RANK2:
        return 2
    return 1


def job_next(scenes: int):
    """(scenes still needed, the rank it brings), or None at Master."""
    if scenes < JOB_RANK2:
        return JOB_RANK2 - scenes, 2
    if scenes < JOB_RANK3:
        return JOB_RANK3 - scenes, 3
    return None


def week_key(d: date) -> str:
    """The real-world week a date falls in, Monday to Sunday. '2026-W48'."""
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def custom_bynames(dated: set, standing_stamped: set, standing_all: set, rules):
    """dated = {(custom, year)}; returns the set of custom Byname names earned."""
    earned = set()
    for r in rules:
        if r.mode == "three":
            years = {y for (n, y) in dated if n == r.custom}
            if any(y in years and y + 1 in years and y + 2 in years for y in years):
                earned.add(r.byname)
        elif r.mode == "first":
            if any(n == r.custom for (n, _) in dated):
                earned.add(r.byname)
        elif r.mode == "all":
            if standing_all and standing_all <= standing_stamped:
                earned.add(r.byname)
    return earned
