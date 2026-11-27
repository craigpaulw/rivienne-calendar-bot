"""The bot's little database. Nothing here needs hand maintenance."""
import glob
import os
import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS stamps(
  id INTEGER PRIMARY KEY, user_id INTEGER, kind TEXT, key TEXT, year INTEGER,
  label TEXT, ref TEXT, granted_at TEXT, UNIQUE(user_id, key, year));
CREATE TABLE IF NOT EXISTS claims(
  id INTEGER PRIMARY KEY, message_id INTEGER, thread_id INTEGER, claimant INTEGER,
  kind TEXT, key TEXT, year INTEGER, label TEXT, created_at TEXT,
  passed INTEGER DEFAULT 0, public INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS presses(
  claim_id INTEGER, user_id INTEGER, counts INTEGER, PRIMARY KEY(claim_id, user_id));
CREATE TABLE IF NOT EXISTS occasions(
  id INTEGER PRIMARY KEY, name TEXT, created_at TEXT, closes_at TEXT, created_by INTEGER);
CREATE TABLE IF NOT EXISTS scenes(
  thread_id INTEGER, key TEXT, year INTEGER, PRIMARY KEY(thread_id, key, year));
CREATE TABLE IF NOT EXISTS scene_players(
  thread_id INTEGER, user_id INTEGER, PRIMARY KEY(thread_id, user_id));
CREATE TABLE IF NOT EXISTS chronicle(
  key TEXT, year INTEGER, status TEXT, vote_close TEXT, winner_thread INTEGER,
  ask_message INTEGER, ask_deadline TEXT, line TEXT, PRIMARY KEY(key, year));
CREATE TABLE IF NOT EXISTS vote_posts(
  key TEXT, year INTEGER, thread_id INTEGER, channel_id INTEGER, message_id INTEGER);
CREATE TABLE IF NOT EXISTS bynames(
  user_id INTEGER, byname TEXT, awarded_at TEXT, PRIMARY KEY(user_id, byname));
CREATE TABLE IF NOT EXISTS entries(
  id INTEGER PRIMARY KEY, kind TEXT, text TEXT, author INTEGER, named TEXT, message_id INTEGER, at TEXT);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS resets(
  user_id INTEGER, by_user INTEGER, stamps INTEGER, at TEXT);
CREATE TABLE IF NOT EXISTS job_choice(
  user_id INTEGER PRIMARY KEY, job TEXT, biome TEXT, chosen_at TEXT);
CREATE TABLE IF NOT EXISTS job_scenes(
  user_id INTEGER, job TEXT, partner_id INTEGER, week TEXT, thread_id INTEGER, at TEXT,
  PRIMARY KEY(user_id, job, partner_id, week));
CREATE TABLE IF NOT EXISTS job_claims(
  id INTEGER PRIMARY KEY, message_id INTEGER, thread_id INTEGER, claimant INTEGER,
  job TEXT, created_at TEXT, passed INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS job_presses(
  claim_id INTEGER, user_id INTEGER, counts INTEGER, PRIMARY KEY(claim_id, user_id));
"""


def now_iso():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path):
        self.path = path
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    # meta
    def get(self, k, default=None):
        r = self.db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return r["v"] if r else default

    def put(self, k, v):
        self.db.execute("INSERT OR REPLACE INTO meta(k,v) VALUES(?,?)", (k, v))
        self.db.commit()

    def drop(self, k):
        self.db.execute("DELETE FROM meta WHERE k=?", (k,))
        self.db.commit()

    # stamps
    def has_stamp(self, user, key, year):
        return self.db.execute("SELECT 1 FROM stamps WHERE user_id=? AND key=? AND year=?",
                               (user, key, year)).fetchone() is not None

    def add_stamp(self, user, kind, key, year, label, ref=""):
        try:
            self.db.execute(
                "INSERT INTO stamps(user_id,kind,key,year,label,ref,granted_at) VALUES(?,?,?,?,?,?,?)",
                (user, kind, key, year, label, ref, now_iso()))
            self.db.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def count(self, user):
        return self.db.execute("SELECT COUNT(*) c FROM stamps WHERE user_id=?", (user,)).fetchone()["c"]

    def stamps(self, user):
        return self.db.execute("SELECT * FROM stamps WHERE user_id=? ORDER BY granted_at", (user,)).fetchall()

    def dated_set(self, user):
        return {(r["key"], r["year"]) for r in self.db.execute(
            "SELECT key,year FROM stamps WHERE user_id=? AND kind='dated'", (user,))}

    def standing_set(self, user):
        return {r["key"].split(":", 1)[1] for r in self.db.execute(
            "SELECT key FROM stamps WHERE user_id=? AND kind='standing'", (user,))}

    # claims
    def new_claim(self, thread_id, claimant, kind, key, year, label, public):
        cur = self.db.execute(
            "INSERT INTO claims(thread_id,claimant,kind,key,year,label,created_at,public) VALUES(?,?,?,?,?,?,?,?)",
            (thread_id, claimant, kind, key, year, label, now_iso(), 1 if public else 0))
        self.db.commit()
        return cur.lastrowid

    def set_claim_message(self, cid, mid):
        self.db.execute("UPDATE claims SET message_id=? WHERE id=?", (mid, cid))
        self.db.commit()

    def claim(self, cid):
        return self.db.execute("SELECT * FROM claims WHERE id=?", (cid,)).fetchone()

    def open_claim(self, thread_id, claimant, key, year, max_age_days):
        cutoff = datetime.now(timezone.utc).timestamp() - max_age_days * 86400
        for r in self.db.execute(
                "SELECT * FROM claims WHERE thread_id=? AND claimant=? AND key=? AND year=? AND passed=0",
                (thread_id, claimant, key, year)):
            if datetime.fromisoformat(r["created_at"]).timestamp() >= cutoff:
                return r
        return None

    def mark_passed(self, cid):
        self.db.execute("UPDATE claims SET passed=1 WHERE id=?", (cid,))
        self.db.commit()

    def add_press(self, cid, user, counts):
        try:
            self.db.execute("INSERT INTO presses(claim_id,user_id,counts) VALUES(?,?,?)", (cid, user, 1 if counts else 0))
            self.db.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def presses(self, cid):
        return [(r["user_id"], bool(r["counts"])) for r in
                self.db.execute("SELECT user_id,counts FROM presses WHERE claim_id=?", (cid,))]

    # scenes
    def add_scene(self, thread_id, key, year):
        self.db.execute("INSERT OR IGNORE INTO scenes(thread_id,key,year) VALUES(?,?,?)", (thread_id, key, year))
        self.db.commit()

    def add_scene_player(self, thread_id, user):
        self.db.execute("INSERT OR IGNORE INTO scene_players(thread_id,user_id) VALUES(?,?)", (thread_id, user))
        self.db.commit()

    def scenes_for(self, key, year):
        return [r["thread_id"] for r in self.db.execute(
            "SELECT thread_id FROM scenes WHERE key=? AND year=? ORDER BY rowid", (key, year))]

    def scene_players(self, thread_id):
        return [r["user_id"] for r in self.db.execute(
            "SELECT user_id FROM scene_players WHERE thread_id=?", (thread_id,))]

    # occasions
    def add_occasion(self, name, closes_at, by):
        cur = self.db.execute("INSERT INTO occasions(name,created_at,closes_at,created_by) VALUES(?,?,?,?)",
                              (name, now_iso(), closes_at, by))
        self.db.commit()
        return cur.lastrowid

    def occasions(self):
        return self.db.execute("SELECT * FROM occasions ORDER BY id").fetchall()

    # chronicle
    def chron(self, key, year):
        return self.db.execute("SELECT * FROM chronicle WHERE key=? AND year=?", (key, year)).fetchone()

    def chron_set(self, key, year, **fields):
        if not self.chron(key, year):
            self.db.execute("INSERT INTO chronicle(key,year,status) VALUES(?,?,?)", (key, year, fields.get("status", "new")))
        for k, v in fields.items():
            self.db.execute(f"UPDATE chronicle SET {k}=? WHERE key=? AND year=?", (v, key, year))
        self.db.commit()

    def chron_by_ask(self, message_id):
        return self.db.execute("SELECT * FROM chronicle WHERE ask_message=? AND status='asking'", (message_id,)).fetchone()

    def add_vote_post(self, key, year, thread_id, channel_id, message_id):
        self.db.execute("INSERT INTO vote_posts VALUES(?,?,?,?,?)", (key, year, thread_id, channel_id, message_id))
        self.db.commit()

    def vote_posts(self, key, year):
        return self.db.execute("SELECT * FROM vote_posts WHERE key=? AND year=?", (key, year)).fetchall()

    # Chronicle entries (every entry ever posted is kept here)
    def add_entry(self, kind, text, author, named, message_id):
        self.db.execute("INSERT INTO entries(kind,text,author,named,message_id,at) VALUES(?,?,?,?,?,?)",
                        (kind, text, author, ",".join(str(n) for n in named), message_id, now_iso()))
        self.db.commit()

    # bynames
    def has_byname(self, user, byname):
        return self.db.execute("SELECT 1 FROM bynames WHERE user_id=? AND byname=?", (user, byname)).fetchone() is not None

    def add_byname(self, user, byname):
        self.db.execute("INSERT OR IGNORE INTO bynames VALUES(?,?,?)", (user, byname, now_iso()))
        self.db.commit()

    def bynames(self, user):
        return [r["byname"] for r in self.db.execute("SELECT byname FROM bynames WHERE user_id=? ORDER BY awarded_at", (user,))]

    # ---------- Jobs ----------

    def set_job(self, user, job, biome=""):
        self.db.execute("INSERT OR REPLACE INTO job_choice(user_id,job,biome,chosen_at) VALUES(?,?,?,?)",
                        (user, job, biome or "", now_iso()))
        self.db.commit()

    def job_choice(self, user):
        return self.db.execute("SELECT * FROM job_choice WHERE user_id=?", (user,)).fetchone()

    def clear_job(self, user):
        self.db.execute("DELETE FROM job_choice WHERE user_id=?", (user,))
        self.db.commit()

    def job_scene_count(self, user, job, per_scene=True):
        """Witnessed Job scenes in this Job. per_scene counts distinct scenes; otherwise
        it counts partner-weeks, so one scene with three witnesses counts three."""
        q = ("SELECT COUNT(DISTINCT thread_id) c FROM job_scenes WHERE user_id=? AND job=?"
             if per_scene else
             "SELECT COUNT(*) c FROM job_scenes WHERE user_id=? AND job=?")
        return self.db.execute(q, (user, job)).fetchone()["c"]

    def add_job_scene(self, user, job, partner, week, thread_id):
        """True if this partner-week was free, False if the pair already counted that week."""
        try:
            self.db.execute(
                "INSERT INTO job_scenes(user_id,job,partner_id,week,thread_id,at) VALUES(?,?,?,?,?,?)",
                (user, job, partner, week, thread_id, now_iso()))
            self.db.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def job_history(self, user):
        """Every Job this Trainer has scenes in, so progress in a Job they left is kept."""
        return self.db.execute(
            "SELECT job, COUNT(DISTINCT thread_id) scenes, COUNT(*) pairs "
            "FROM job_scenes WHERE user_id=? GROUP BY job ORDER BY job", (user,)).fetchall()

    def new_job_claim(self, thread_id, claimant, job):
        cur = self.db.execute(
            "INSERT INTO job_claims(thread_id,claimant,job,created_at) VALUES(?,?,?,?)",
            (thread_id, claimant, job, now_iso()))
        self.db.commit()
        return cur.lastrowid

    def job_claim(self, cid):
        return self.db.execute("SELECT * FROM job_claims WHERE id=?", (cid,)).fetchone()

    def set_job_claim_message(self, cid, mid):
        self.db.execute("UPDATE job_claims SET message_id=? WHERE id=?", (mid, cid))
        self.db.commit()

    def open_job_claim(self, thread_id, claimant, job, max_age_days):
        cutoff = datetime.now(timezone.utc).timestamp() - max_age_days * 86400
        for r in self.db.execute(
                "SELECT * FROM job_claims WHERE thread_id=? AND claimant=? AND job=? AND passed=0",
                (thread_id, claimant, job)):
            if datetime.fromisoformat(r["created_at"]).timestamp() >= cutoff:
                return r
        return None

    def mark_job_passed(self, cid):
        self.db.execute("UPDATE job_claims SET passed=1 WHERE id=?", (cid,))
        self.db.commit()

    def add_job_press(self, cid, user, counts):
        try:
            self.db.execute("INSERT INTO job_presses(claim_id,user_id,counts) VALUES(?,?,?)",
                            (cid, user, 1 if counts else 0))
            self.db.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def job_presses(self, cid):
        return [(r["user_id"], bool(r["counts"])) for r in
                self.db.execute("SELECT user_id,counts FROM job_presses WHERE claim_id=?", (cid,))]

    # reset a retired Trainer
    def reset_user(self, user, by):
        n = self.count(user)
        d = self.db
        d.execute("DELETE FROM stamps WHERE user_id=?", (user,))
        d.execute("DELETE FROM bynames WHERE user_id=?", (user,))
        d.execute("DELETE FROM presses WHERE user_id=?", (user,))
        d.execute("DELETE FROM scene_players WHERE user_id=?", (user,))
        d.execute("DELETE FROM claims WHERE claimant=? AND passed=0", (user,))
        d.execute("DELETE FROM meta WHERE k=?", (f"rung:{user}",))
        d.execute("DELETE FROM job_choice WHERE user_id=?", (user,))
        d.execute("DELETE FROM job_scenes WHERE user_id=?", (user,))
        d.execute("DELETE FROM job_presses WHERE user_id=?", (user,))
        d.execute("DELETE FROM job_claims WHERE claimant=? AND passed=0", (user,))
        d.execute("DELETE FROM meta WHERE k LIKE ?", (f"jobrank:{user}:%",))
        d.execute("INSERT INTO resets VALUES(?,?,?,?)", (user, by, n, now_iso()))
        d.commit()
        return n

    # weekly backup, keeps the last 8
    def backup(self, folder):
        os.makedirs(folder, exist_ok=True)
        dest = os.path.join(folder, "stamps-" + datetime.now().strftime("%Y%m%d") + ".db")
        out = sqlite3.connect(dest)
        self.db.backup(out)
        out.close()
        for old in sorted(glob.glob(os.path.join(folder, "stamps-*.db")))[:-8]:
            os.remove(old)
        return dest
