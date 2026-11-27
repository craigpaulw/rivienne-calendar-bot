"""Drives handle_job_press with stand-in Discord objects, to prove the witness rules."""
import asyncio
import os
import tempfile
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DISCORD_TOKEN", "x")
os.environ.setdefault("GUILD_ID", "1")
os.environ.setdefault("DB_PATH", os.path.join(tempfile.mkdtemp(), "p.db"))

import bot
import jobs
import logic
from store import Store
from test_jobs import ROWS, via_csv

CRAIG, NELL, FINN, MASTER = 1, 2, 3, 9


class Perms:
    administrator = False


class User:
    def __init__(self, uid, staff=False):
        self.id = uid
        self.display_name = f"user{uid}"
        self.bot = False
        self.roles = [type("R", (), {"id": 777})()] if staff else []
        self.guild_permissions = Perms()

    @property
    def mention(self):
        return f"<@{self.id}>"


class Msg:
    def __init__(self):
        self.content = None

    async def edit(self, content=None, **kw):
        self.content = content


class Resp:
    def __init__(self):
        self.deferred = False
        self.sent = []
        self.view = None
        self.embed = None

    async def defer(self, **kw):
        self.deferred = True

    async def send_message(self, content=None, view=None, embed=None, **kw):
        self.sent.append(content or "")
        self.view = view
        self.embed = embed


class Followup:
    def __init__(self):
        self.sent = []

    async def send(self, text, **kw):
        self.sent.append(text)


class Interaction:
    def __init__(self, user, channel_id=500):
        self.user = user
        self.guild = type("G", (), {"id": 1})()
        self.response = Resp()
        self.followup = Followup()
        self.message = Msg()
        self.channel = type("Ch", (), {"id": channel_id})()

    async def original_response(self):
        m = Msg()
        m.id = 9999
        return m

    def last(self):
        return self.followup.sent[-1] if self.followup.sent else ""

    def said(self):
        return self.response.sent[-1] if self.response.sent else ""


class FakeSheet:
    def __init__(self, settings):
        self._c = type("C", (), {"settings": settings})()

    def get(self):
        return self._c


class FakeJobs:
    def __init__(self):
        self._d = jobs.parse(via_csv(ROWS))

    def get(self):
        return self._d


said = []


async def fake_say(channel_id, **kw):
    said.append(kw.get("content", ""))
    return Msg()


def setup(settings=None):
    """Fresh store and stubs for each test."""
    said.clear()
    path = os.path.join(tempfile.mkdtemp(), "p.db")
    bot.store = Store(path)
    bot.sheet = FakeSheet(settings or {"time zone": "Europe/London",
                                       "staff who also play": ""})
    bot.jobsheet = FakeJobs()
    bot.say = fake_say
    bot.CALENDAR_CHANNEL = 4242
    bot.STAFF_ROLE = 777
    bot.store.set_job(CRAIG, "Press", "")
    return bot.store


def claim(s, thread=500, job="Press", claimant=CRAIG, age_days=0):
    cid = s.new_job_claim(thread, claimant, job)
    s.add_job_press(cid, claimant, True)
    if age_days:
        when = (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat()
        s.db.execute("UPDATE job_claims SET created_at=? WHERE id=?", (when, cid))
        s.db.commit()
    return cid


def press(cid, user):
    i = Interaction(user)
    asyncio.run(bot.handle_job_press(i, cid))
    return i


def test_first_witness_counts():
    s = setup()
    i = press(claim(s), User(NELL))
    assert "1 witnessed" in i.last(), i.last()
    assert s.job_scene_count(CRAIG, "Press") == 1
    assert "Job scene, witnessed" in i.message.content
    assert "<@2>" in i.message.content


def test_cannot_witness_your_own():
    s = setup()
    i = press(claim(s), User(CRAIG))
    assert "your own claim" in i.last()
    assert s.job_scene_count(CRAIG, "Press") == 0


def test_same_partner_same_week_is_refused():
    s = setup()
    press(claim(s, thread=500), User(NELL))
    i = press(claim(s, thread=501), User(NELL))
    assert "already counted" in i.last(), i.last()
    assert s.job_scene_count(CRAIG, "Press") == 1


def test_different_partner_same_week_counts():
    s = setup()
    press(claim(s, thread=500), User(NELL))
    i = press(claim(s, thread=501), User(FINN))
    assert "2 witnessed" in i.last(), i.last()
    assert s.job_scene_count(CRAIG, "Press") == 2


def test_two_witnesses_one_scene_counts_once():
    s = setup()
    cid = claim(s, thread=500)
    press(cid, User(NELL))
    i = press(cid, User(FINN))
    assert "already counted" in i.last() or "stays at 1" in i.last(), i.last()
    assert s.job_scene_count(CRAIG, "Press", per_scene=True) == 1
    assert s.job_scene_count(CRAIG, "Press", per_scene=False) == 2


def test_per_partner_mode_counts_both():
    s = setup({"time zone": "Europe/London", "staff who also play": "",
               "job scene credit": "per partner"})
    cid = claim(s, thread=500)
    press(cid, User(NELL))
    i = press(cid, User(FINN))
    assert "2 witnessed" in i.last(), i.last()


def test_contest_master_does_not_count():
    s = setup()
    i = press(claim(s), User(MASTER, staff=True))
    assert "does not count as the witness" in i.last(), i.last()
    assert s.job_scene_count(CRAIG, "Press") == 0


def test_staff_who_also_plays_does_count():
    # the sheet lists real Discord IDs, which are 15+ digits
    craig = 123456789012345678
    s = setup({"time zone": "Europe/London", "staff who also play": str(craig)})
    i = press(claim(s), User(craig, staff=True))
    assert "1 witnessed" in i.last(), i.last()


def test_lapsed_claim():
    s = setup()
    i = press(claim(s, age_days=logic.PRESS_DAYS + 1), User(NELL))
    assert "lapsed" in i.last()
    assert s.job_scene_count(CRAIG, "Press") == 0


def test_rank_two_is_announced_at_eight():
    s = setup()
    for n in range(8):
        # a different partner each time, so every scene counts
        press(claim(s, thread=600 + n), User(100 + n))
    assert s.job_scene_count(CRAIG, "Press") == 8
    assert any("Correspondent" in m for m in said), said
    # and it is not announced twice
    said.clear()
    press(claim(s, thread=700), User(200))
    assert not any("Correspondent" in m for m in said), said


def test_master_is_announced_at_thirtytwo():
    s = setup()
    for n in range(32):
        press(claim(s, thread=800 + n), User(300 + n))
    assert s.job_scene_count(CRAIG, "Press") == 32
    assert any("Editor" in m and "Master" in m for m in said), said


def test_unknown_claim():
    setup()
    i = press(99999, User(NELL))
    assert "no longer open" in i.last()


# ---------- /jobscene choosing a Job ----------

def run_jobscene(user, job=None, channel_id=500):
    bot.in_scenes = lambda ch: True          # stand in for a real Scenes thread
    i = Interaction(user, channel_id)
    asyncio.run(bot.jobscene_cmd.callback(i, job=job))
    return i


def test_jobscene_asks_when_you_have_no_job():
    s = setup()
    s.clear_job(CRAIG)
    i = run_jobscene(User(CRAIG))
    assert "Which Job was the scene?" in i.said(), i.said()
    assert s.job_choice(CRAIG) is None       # it asked, it did not guess


def test_jobscene_sets_the_job_when_you_name_one():
    s = setup()
    s.clear_job(CRAIG)
    i = run_jobscene(User(CRAIG), job="Chef")
    assert "Your Job is now **Chef**" in i.said(), i.said()
    assert "witnessed **Chef** Job scene" in i.said()
    assert s.job_choice(CRAIG)["job"] == "Chef"


def test_jobscene_uses_your_stored_job_without_asking():
    setup()                                  # Craig is already Press
    i = run_jobscene(User(CRAIG))
    assert "witnessed **Press** Job scene" in i.said(), i.said()
    assert "Your Job is now" not in i.said()


def test_jobscene_refuses_a_job_you_do_not_hold():
    s = setup()                               # Craig is Press
    i = run_jobscene(User(CRAIG), job="Chef")
    assert "Your Job is **Press**" in i.said(), i.said()
    assert "change it with" in i.said().lower()
    assert s.job_choice(CRAIG)["job"] == "Press"     # not switched behind his back
    assert s.job_claim(1) is None                    # and no claim was opened


def test_jobscene_naming_your_own_job_is_fine():
    setup()
    i = run_jobscene(User(CRAIG), job="press")       # case-insensitive
    assert "witnessed **Press** Job scene" in i.said(), i.said()


def test_jobscene_unknown_job():
    s = setup()
    s.clear_job(CRAIG)
    i = run_jobscene(User(CRAIG), job="Plumber")
    assert "no Job called **Plumber**" in i.said(), i.said()
    assert s.job_choice(CRAIG) is None


def test_jobscene_innkeeper_mentions_the_biome():
    s = setup()
    s.clear_job(CRAIG)
    i = run_jobscene(User(CRAIG), job="Innkeeper")
    assert "one biome of your choice" in i.said(), i.said()
    assert s.job_choice(CRAIG)["job"] == "Innkeeper"


# ---------- the Chronicle's list now comes off the sheet ----------

def test_entry_types_come_from_the_sheet():
    setup()
    kinds = bot.entry_types()
    assert kinds[:2] == ["Landmark (founders)", "Master Contest win"]
    assert len(kinds) == 17, kinds
    assert "Press - Editor" in kinds
    assert not any(k.startswith("Rancher") for k in kinds)


def test_entry_types_fall_back_when_the_sheet_is_unreachable():
    setup()

    class Broken:
        def get(self):
            raise RuntimeError("no sheet")

    bot.jobsheet = Broken()
    kinds = bot.entry_types()
    assert len(kinds) == 17, kinds
    assert "Press - Editor" in kinds


def run_chronicle(user, kind, entry="a long enough line"):
    i = Interaction(user)
    asyncio.run(bot.chronicle_cmd.callback(i, kind=kind, entry=entry))
    return i


def test_chronicle_rejects_a_kind_not_on_the_list():
    setup()
    i = run_chronicle(User(CRAIG), "Press - Supreme Overlord")
    assert "not one of the Chronicle's entry kinds" in i.said(), i.said()
    assert i.response.view is None            # no confirm button, so nothing can be written


def test_chronicle_accepts_a_kind_from_the_sheet():
    setup()
    i = run_chronicle(User(CRAIG), "press - editor")      # typed in lower case
    assert "**Press - Editor**" in i.said(), i.said()     # normalised to the sheet's spelling
    assert i.response.view is not None


def test_chronicle_rejects_a_board_only_job():
    setup()
    i = run_chronicle(User(CRAIG), "Rancher - Master of the Yard")
    assert "not one of the Chronicle's entry kinds" in i.said(), i.said()


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("pass", name)
            except Exception as e:
                fails += 1
                print("FAIL", name, "-", type(e).__name__, e)
    print("\n%d failed" % fails)
    raise SystemExit(1 if fails else 0)
