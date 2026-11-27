"""Tests for the Job list, the rank thresholds and the once-a-week-per-partner cap."""
import csv
import io
import os
import tempfile
from datetime import date

import jobs
import logic
from store import Store

# A faithful slice of the Game Data tab: the note row, the header, the twenty Jobs,
# then a blank row and whatever follows. Only the columns the bot reads are filled in.
HEADER_NOTE = "Jobs - the twenty, fixed. Rank names are the Job's own;"
ROWS = [
    ["", "", "", "", "", "", "", ""],
    [HEADER_NOTE, "", "", "", "", "", "", ""],
    ["Job", "Where", "Rank 1", "Rank 2", "Master", "Master effect", "Remit", "Chronicle entry"],
    ["Press", "Anywhere", "Stringer", "Correspondent", "Editor", "e", "r", "Yes"],
    ["Artist", "Anywhere", "Sketcher", "Painter", "Laureate", "e", "r", "Yes"],
    ["Nurse", "Anywhere", "Aide", "Attendant", "Physician", "e", "r", "Yes"],
    ["Stagehand", "Anywhere", "Hand", "Rigger", "Stage Master", "e", "r", "No"],
    ["Costumer", "Anywhere", "Cutter", "Tailor", "Designer", "e", "r", "Yes"],
    ["Caller", "Anywhere", "Bell", "Voice", "Herald", "e", "r", "No"],
    ["Chef", "Anywhere", "Pot-washer", "Cook", "Head Cook", "e", "r", "Yes"],
    ["Mystic", "Anywhere", "Reader", "Diviner", "Oracle", "e", "r", "Yes"],
    ["Skywatcher", "Anywhere", "Watch", "Weatherwise", "Stormcaller", "e", "r", "No"],
    ["Coastguard", "Beach, Glacier, Tropics, Marsh", "Crew", "Coxswain", "Lifesaver", "e", "r", "Yes"],
    ["Ranger", "Anywhere but City", "Apprentice", "Tracker", "Old Hand", "e", "r", "Yes"],
    ["Shrinewarden", "Anywhere but City", "Novice", "Tender", "Elder", "e", "r", "Yes"],
    ["Mountaineer", "Hill, Glacier, Mountain, Volcano", "Porter", "Guide", "Pathfinder", "e", "r", "Yes"],
    ["Archaeologist", "Woods, Hill, Mountain, Volcano, Desert", "Sifter", "Digger", "Old Bones", "e", "r", "Yes"],
    ["Researcher", "Anywhere", "Assistant", "Fellow", "Chair", "e", "r", "Yes"],
    ["Rancher", "Woods, Hill, City", "Yardhand", "Groom", "Master of the Yard", "e", "r", "No"],
    ["Safety Officer", "Anywhere", "Spotter", "Inspector", "Chief Inspector", "e", "r", "Yes"],
    ["HR Manager", "Anywhere", "Clerk", "Recruiter", "Head of People", "e", "r", "Yes"],
    ["Musician", "Anywhere", "Busker", "Accompanist", "Bandleader", "e", "r", "No"],
    ["Innkeeper", "One biome of your choice", "Doorkeeper", "Housekeeper", "Host", "e", "r", "Yes"],
    [],
    ["Bond shapes", "", "", "", "", "", "", ""],
    ["Rival (mutual, any Trainer)", "", "", "", "", "", "", ""],
]

# The five Jobs whose Master effect posts to the Board, or writes nothing.
NOT_IN_CHRONICLE = {"Stagehand", "Caller", "Skywatcher", "Rancher", "Musician"}


def via_csv(rows):
    """Round-trip through CSV, the way the bot really receives it."""
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    return list(csv.reader(io.StringIO(buf.getvalue())))


def test_parse():
    d = jobs.parse(via_csv(ROWS))
    assert len(d.jobs) == 20, len(d.jobs)
    assert d.names()[0] == "Press" and d.names()[-1] == "Innkeeper"
    assert d.job("press").name == "Press"            # case-insensitive lookup
    assert d.job("Nope") is None
    # it stopped at the blank row and did not swallow the Bond shapes below
    assert "Bond shapes" not in d.names()
    assert not any("Rival" in n for n in d.names())


def test_ranks_and_titles():
    d = jobs.parse(via_csv(ROWS))
    press = d.job("Press")
    assert (press.title(1), press.title(2), press.title(3)) == ("Stringer", "Correspondent", "Editor")
    assert not press.picks_biome
    assert d.job("Innkeeper").picks_biome


def test_where_values():
    d = jobs.parse(via_csv(ROWS))
    anywhere = [j.name for j in d.jobs if j.where == "Anywhere"]
    assert len(anywhere) == 13, anywhere          # thirteen plainly Anywhere
    rooted = [j.name for j in d.jobs if j.where != "Anywhere"]
    assert len(rooted) == 7, rooted               # the other seven are constrained
    assert d.job("Coastguard").where == "Beach, Glacier, Tropics, Marsh"
    # every biome named in a Where is a real biome
    for j in d.jobs:
        if j.where in ("Anywhere", "Anywhere but City", "One biome of your choice"):
            continue
        for part in j.where.split(","):
            assert part.strip() in logic.BIOMES, (j.name, part)


def test_header_moves():
    """Inserting rows above the Job block must not break the read."""
    shifted = [["x"] * 7] * 15 + ROWS
    assert len(jobs.parse(via_csv(shifted)).jobs) == 20


def test_missing_header_is_loud():
    try:
        jobs.parse(via_csv([["a", "b"], ["c", "d"]]))
    except RuntimeError as e:
        assert "header" in str(e).lower()
    else:
        raise AssertionError("a missing header should raise, not return an empty list")


def test_chronicle_kinds():
    d = jobs.parse(via_csv(ROWS))
    kinds = d.chronicle_kinds()
    assert len(kinds) == 15, kinds
    assert "Press - Editor" in kinds
    assert "Innkeeper - Host" in kinds
    assert "Safety Officer - Chief Inspector" in kinds
    for name in NOT_IN_CHRONICLE:
        assert not any(k.startswith(name + " -") for k in kinds), name
    # every kind is "Job - Master title", matching the sheet
    for j in d.jobs:
        assert (f"{j.name} - {j.master}" in kinds) == (j.name not in NOT_IN_CHRONICLE), j.name


def test_renaming_a_master_title_follows():
    """The whole point of reading the list: rename a Master title and the Chronicle follows."""
    rows = [list(r) for r in ROWS]
    for r in rows:
        if r and r[0] == "Press":
            r[4] = "Editor-in-Chief"
    kinds = jobs.parse(via_csv(rows)).chronicle_kinds()
    assert "Press - Editor-in-Chief" in kinds
    assert "Press - Editor" not in kinds


def test_flag_beats_the_effect_text():
    """An explicit Yes/No wins over whatever the effect text happens to say."""
    rows = [list(r) for r in ROWS]
    for r in rows:
        if r and r[0] == "Rancher":
            r[5] = "you record it in the Chronicle"   # text says yes
            r[7] = "No"                               # the column says no
        if r and r[0] == "Press":
            r[5] = "Post it to the Board; it does not go in the Chronicle."
            r[7] = "Yes"
    kinds = jobs.parse(via_csv(rows)).chronicle_kinds()
    assert not any(k.startswith("Rancher") for k in kinds)
    assert "Press - Editor" in kinds


def test_blank_flag_falls_back_to_the_text():
    """A Job added without filling the column in still behaves sensibly."""
    rows = [list(r) for r in ROWS]
    for r in rows:
        if r and r[0] == "Press":
            r[5], r[7] = "you publish an edition to the Chronicle", ""
        if r and r[0] == "Stagehand":
            r[5], r[7] = "Post it to the Board; it does not go in the Chronicle.", ""
        if r and r[0] == "Chef":
            r[5], r[7] = "Nothing is written to the Chronicle.", ""
        if r and r[0] == "Mystic":
            r[5], r[7] = "you name the tune of the Season", ""   # no mention at all
    kinds = jobs.parse(via_csv(rows)).chronicle_kinds()
    assert "Press - Editor" in kinds
    assert not any(k.startswith("Stagehand") for k in kinds)
    assert not any(k.startswith("Chef") for k in kinds)
    assert not any(k.startswith("Mystic") for k in kinds)


def test_writes_to_chronicle_helper():
    assert jobs.writes_to_chronicle("you record it in the Chronicle") is True
    assert jobs.writes_to_chronicle("Post it to the Board; it does not go in the Chronicle.") is False
    assert jobs.writes_to_chronicle("Nothing is written to the Chronicle.") is False
    assert jobs.writes_to_chronicle("you name the tune of the Season") is False
    assert jobs.writes_to_chronicle("") is False


def test_rank_thresholds():
    assert [logic.job_rank(n) for n in (0, 1, 7, 8, 31, 32, 99)] == [1, 1, 1, 2, 2, 3, 3]
    assert logic.job_next(0) == (8, 2)
    assert logic.job_next(7) == (1, 2)
    assert logic.job_next(8) == (24, 3)
    assert logic.job_next(31) == (1, 3)
    assert logic.job_next(32) is None


def test_week_key():
    # Monday starts the week, so Sunday and the Monday after differ
    assert logic.week_key(date(2026, 11, 23)) == logic.week_key(date(2026, 11, 29))
    assert logic.week_key(date(2026, 11, 29)) != logic.week_key(date(2026, 11, 30))


def fresh_store():
    path = os.path.join(tempfile.mkdtemp(), "t.db")
    return Store(path)


def test_partner_week_cap():
    s = fresh_store()
    CRAIG, NELL, FINN = 1, 2, 3
    w1, w2 = "2026-W48", "2026-W49"
    # first scene with Nell counts
    assert s.add_job_scene(CRAIG, "Press", NELL, w1, 100) is True
    # a second scene with Nell the same week does not, even in a different thread
    assert s.add_job_scene(CRAIG, "Press", NELL, w1, 101) is False
    # a different partner the same week does
    assert s.add_job_scene(CRAIG, "Press", FINN, w1, 101) is True
    # Nell again next week does
    assert s.add_job_scene(CRAIG, "Press", NELL, w2, 102) is True


def test_counting_modes():
    s = fresh_store()
    CRAIG, NELL, FINN = 1, 2, 3
    # one scene, two witnesses
    s.add_job_scene(CRAIG, "Press", NELL, "2026-W48", 100)
    s.add_job_scene(CRAIG, "Press", FINN, "2026-W48", 100)
    assert s.job_scene_count(CRAIG, "Press", per_scene=True) == 1
    assert s.job_scene_count(CRAIG, "Press", per_scene=False) == 2


def test_progress_is_kept_per_job():
    s = fresh_store()
    CRAIG = 1
    s.add_job_scene(CRAIG, "Press", 2, "2026-W48", 100)
    s.add_job_scene(CRAIG, "Press", 3, "2026-W49", 101)
    s.add_job_scene(CRAIG, "Chef", 2, "2026-W50", 102)
    s.set_job(CRAIG, "Chef", "")
    assert s.job_choice(CRAIG)["job"] == "Chef"
    assert s.job_scene_count(CRAIG, "Press") == 2    # kept after leaving Press
    assert s.job_scene_count(CRAIG, "Chef") == 1
    assert {r["job"]: r["scenes"] for r in s.job_history(CRAIG)} == {"Press": 2, "Chef": 1}


def test_innkeeper_biome_is_stored():
    s = fresh_store()
    s.set_job(7, "Innkeeper", "Marsh")
    assert s.job_choice(7)["biome"] == "Marsh"
    s.set_job(7, "Press", "")
    assert s.job_choice(7)["biome"] == ""


def test_job_claim_flow():
    s = fresh_store()
    cid = s.new_job_claim(500, 1, "Press")
    assert s.job_claim(cid)["passed"] == 0
    assert s.open_job_claim(500, 1, "Press", 7) is not None
    assert s.open_job_claim(500, 1, "Chef", 7) is None
    s.add_job_press(cid, 1, True)
    assert s.add_job_press(cid, 1, True) is False      # no double press
    s.add_job_press(cid, 2, True)
    assert sorted(u for u, _ in s.job_presses(cid)) == [1, 2]
    s.mark_job_passed(cid)
    assert s.job_claim(cid)["passed"] == 1
    assert s.open_job_claim(500, 1, "Press", 7) is None


def test_reset_clears_jobs():
    s = fresh_store()
    s.set_job(1, "Press", "")
    s.add_job_scene(1, "Press", 2, "2026-W48", 100)
    s.put("jobrank:1:Press", "2")
    s.reset_user(1, 99)
    assert s.job_choice(1) is None
    assert s.job_scene_count(1, "Press") == 0
    assert s.get("jobrank:1:Press") is None


def test_stamps_still_work():
    """The Job tables must not disturb the Stamp side."""
    s = fresh_store()
    assert s.add_stamp(1, "dated", "Sinking Day", 2027, "Sinking Day 2027") is True
    assert s.add_stamp(1, "dated", "Sinking Day", 2027, "Sinking Day 2027") is False
    assert s.count(1) == 1
    assert s.has_stamp(1, "Sinking Day", 2027)


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("pass", name)
            except Exception as e:
                fails += 1
                print("FAIL", name, "-", e)
    print("\n%d failed" % fails)
    raise SystemExit(1 if fails else 0)
