"""Reads the Rivienne Calendar Bot Content sheet. Read-only; the bot never writes to it."""
import csv
import io
import re
import time
import urllib.request
from dataclasses import dataclass, field

import logic

TTL = 300  # seconds; edits to the sheet show up within five minutes

# The tab ids in the sheet's own web address (the number after gid=). The sheet must be
# shared as "Anyone with the link: Viewer", the same way the Character Sheet is.
GIDS = {"Calendar": "88751899", "Standing customs": "848536518", "Bot settings": "1537421422",
        "Prompts": "2057473676", "Bynames": "5452181"}


@dataclass
class Content:
    customs: list = field(default_factory=list)
    standing: list = field(default_factory=list)       # [{"name","where","text","status"}]
    prompts: dict = field(default_factory=dict)        # (custom, set number) -> [p1, p2, p3]
    settings: dict = field(default_factory=dict)
    ladder: list = field(default_factory=list)         # [(stamps, name)]
    colours: dict = field(default_factory=dict)        # name -> int
    rules: list = field(default_factory=list)          # [BynameRule]

    def custom(self, name):
        return next((c for c in self.customs if c.name == name), None)

    def standing_names(self):
        return {s["name"] for s in self.standing if s["status"].lower() == "defined"}


def _col(rows, header):
    idx = {h.strip().lower(): i for i, h in enumerate(rows[0])}
    return idx


def _get(row, i):
    return row[i].strip() if i is not None and i < len(row) else ""


def parse(tabs: dict) -> Content:
    """tabs = {tab name: list of rows (list of strings)}. Pure, so it can be tested."""
    c = Content()

    rows = tabs.get("Calendar", [])
    if rows:
        h = _col(rows, 0)
        for r in rows[1:]:
            item = {
                "start": _get(r, h.get("starts")), "end": _get(r, h.get("ends")),
                "repeats": _get(r, h.get("repeats")), "name": _get(r, h.get("custom")),
                "where": _get(r, h.get("where")), "post": _get(r, h.get("what it is (the post)")),
                "status": _get(r, h.get("status")),
                "twist": _get(r, h.get("this year's twist (optional one line)")),
            }
            cu = logic.make_custom(item)
            if cu:
                c.customs.append(cu)

    rows = tabs.get("Standing customs", [])
    for r in rows[1:]:
        if _get(r, 0):
            c.standing.append({"name": _get(r, 0), "where": _get(r, 1), "text": _get(r, 2), "status": _get(r, 3)})

    rows = tabs.get("Prompts", [])
    for r in rows[1:]:
        name, year = _get(r, 0), _get(r, 1)
        if name and year.isdigit():
            c.prompts[(name, int(year))] = [p for p in (_get(r, 2), _get(r, 3), _get(r, 4)) if p]

    rows = tabs.get("Bot settings", [])
    for r in rows[1:]:
        if _get(r, 0):
            c.settings[_get(r, 0).lower()] = _get(r, 1)

    rows = tabs.get("Bynames", [])
    for r in rows[1:]:
        kind, earned, byname, colour = _get(r, 0).lower(), _get(r, 1), _get(r, 2), _get(r, 3)
        if not byname:
            continue
        if kind == "island":
            m = re.match(r"(\d+)\s*stamps?", earned, re.I)
            if m:
                c.ladder.append((int(m.group(1)), byname))
                hexm = re.search(r"#([0-9A-Fa-f]{6})", colour)
                if hexm:
                    c.colours[byname] = int(hexm.group(1), 16)
        elif kind == "custom":
            if earned.lower().startswith("every standing"):
                c.rules.append(logic.BynameRule("", byname, "all"))
            else:
                custom = earned.split(",")[0].strip()
                mode = "first" if "first time" in earned.lower() else "three"
                c.rules.append(logic.BynameRule(custom, byname, mode))
    c.ladder.sort()
    return c


class Sheet:
    def __init__(self, sheet_id):
        self.sheet_id = sheet_id
        self._cache, self._at = None, 0

    def _fetch(self):
        out = {}
        for name, gid in GIDS.items():
            url = f"https://docs.google.com/spreadsheets/d/{self.sheet_id}/export?format=csv&gid={gid}"
            with urllib.request.urlopen(url, timeout=30) as r:
                data = r.read().decode("utf-8")
            if data.lstrip().lower().startswith("<"):
                raise RuntimeError("The sheet is not shared as 'Anyone with the link: Viewer'.")
            out[name] = list(csv.reader(io.StringIO(data)))
        return out

    def get(self) -> Content:
        if self._cache is None or time.time() - self._at > TTL:
            try:
                self._cache = parse(self._fetch())
                self._at = time.time()
            except Exception:
                if self._cache is None:
                    raise
                self._at = time.time() - TTL + 60   # keep the old copy, retry in a minute
        return self._cache
