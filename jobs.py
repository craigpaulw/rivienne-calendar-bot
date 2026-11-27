"""Reads the twenty Jobs from the master Character Sheet. Read-only; the bot never writes to it.

The Jobs live on the Character Sheet, not on the Calendar Bot's own content sheet, because the
Player Handbook and the Character Sheet are the master copy. Keeping a second list in here would
drift the first time a Remit is reworded.
"""
import csv
import io
import time
import urllib.request
from dataclasses import dataclass, field

TTL = 1800          # seconds. Jobs change rarely, so this is read every half hour.
GID = "1166939416"  # the Game Data tab
MAX_ROWS = 40       # never read further than this past the header

BIOME_CHOICE = "one biome of your choice"


NO_CHRONICLE = ("does not go in the chronicle", "not go in the chronicle",
                "nothing is written to the chronicle", "never goes in the chronicle")


def writes_to_chronicle(effect: str) -> bool:
    """Only used when a Job's Chronicle entry cell is blank. Reads it off the effect text so a
    newly added Job still behaves sensibly before anybody fills the column in."""
    e = (effect or "").lower()
    if "chronicle" not in e:
        return False
    return not any(n in e for n in NO_CHRONICLE)


@dataclass
class Job:
    name: str
    where: str
    rank1: str
    rank2: str
    master: str
    effect: str
    remit: str
    chronicle: bool = True

    @property
    def picks_biome(self) -> bool:
        """True for a Job whose Where is 'One biome of your choice' (the Innkeeper)."""
        return BIOME_CHOICE in self.where.lower()

    def title(self, rank: int) -> str:
        return {1: self.rank1, 2: self.rank2, 3: self.master}.get(rank, self.rank1)


@dataclass
class JobData:
    jobs: list = field(default_factory=list)

    def job(self, name):
        low = (name or "").strip().lower()
        return next((j for j in self.jobs if j.name.lower() == low), None)

    def names(self):
        return [j.name for j in self.jobs]

    def chronicle_kinds(self):
        """The Chronicle entry kinds the Jobs contribute, named off the sheet so a renamed
        Master title cannot leave the Chronicle's list stale."""
        return [f"{j.name} - {j.master}" for j in self.jobs if j.chronicle and j.master]


def _cell(row, i):
    return row[i].strip() if i < len(row) else ""


def parse(rows) -> JobData:
    """rows = the Game Data tab as a list of lists. Pure, so it can be tested.

    Finds the Job header row rather than trusting a row number, so inserting rows above the
    Job block on the sheet cannot silently empty the list.
    """
    d = JobData()
    start = None
    for i, r in enumerate(rows):
        if _cell(r, 0).lower() == "job" and _cell(r, 1).lower() == "where":
            start = i + 1
            break
    if start is None:
        raise RuntimeError("Could not find the Job header row (Job | Where) on the Game Data tab.")
    for r in rows[start:start + MAX_ROWS]:
        name = _cell(r, 0)
        if not name:
            break
        effect = _cell(r, 5)
        flag = _cell(r, 7).lower()
        if flag in ("yes", "y", "true"):
            chron = True
        elif flag in ("no", "n", "false"):
            chron = False
        else:
            chron = writes_to_chronicle(effect)
        d.jobs.append(Job(name, _cell(r, 1), _cell(r, 2), _cell(r, 3),
                          _cell(r, 4), effect, _cell(r, 6), chron))
    if not d.jobs:
        raise RuntimeError("Found the Job header row but no Jobs under it.")
    return d


class Jobs:
    """The Job list, cached. Falls back to the last good copy if a read fails."""

    def __init__(self, sheet_id):
        self.sheet_id = sheet_id
        self._cache, self._at = None, 0

    def _fetch(self):
        url = f"https://docs.google.com/spreadsheets/d/{self.sheet_id}/export?format=csv&gid={GID}"
        with urllib.request.urlopen(url, timeout=30) as r:
            data = r.read().decode("utf-8")
        if data.lstrip().lower().startswith("<"):
            raise RuntimeError("The Character Sheet is not shared as 'Anyone with the link: Viewer'.")
        return list(csv.reader(io.StringIO(data)))

    def get(self) -> JobData:
        if not self.sheet_id:
            raise RuntimeError("CHARACTER_SHEET_ID is not set, so the Jobs cannot be read.")
        if self._cache is None or time.time() - self._at > TTL:
            try:
                self._cache = parse(self._fetch())
                self._at = time.time()
            except Exception:
                if self._cache is None:
                    raise
                self._at = time.time() - TTL + 60   # keep the old copy, retry in a minute
        return self._cache
