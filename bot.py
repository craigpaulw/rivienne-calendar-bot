"""Rivienne Calendar Bot: daily custom posts, Stamps, Bynames and the Chronicle."""
import logging
import os
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv

import logic
from content import Sheet
from store import Store

load_dotenv()
log = logging.getLogger("rivbot")


def env_int(name):
    v = os.getenv(name, "").strip()
    return int(v) if v.isdigit() else None


TOKEN = os.getenv("DISCORD_TOKEN", "")
GUILD_ID = env_int("GUILD_ID")
CALENDAR_CHANNEL = env_int("CALENDAR_CHANNEL_ID")
CHRONICLE_CHANNEL = env_int("CHRONICLE_CHANNEL_ID")
SCENES_CHANNEL = env_int("SCENES_CHANNEL_ID")
STAFF_ROLE = env_int("STAFF_ROLE_ID")
GUILD = discord.Object(id=GUILD_ID) if GUILD_ID else None

sheet = Sheet(os.getenv("SHEET_ID", ""))
store = Store(os.getenv("DB_PATH", "stamps.db"))

intents = discord.Intents.default()
intents.message_content = True      # lets the bot read the one-line Chronicle replies
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

STAR = "⭐"


# ---------- helpers ----------

def zone(c):
    return ZoneInfo(c.settings.get("time zone") or "Europe/London")


def now_local(c):
    return datetime.now(zone(c))


def post_time(c):
    m = re.match(r"(\d{1,2}):(\d{2})", c.settings.get("post time", "08:00"))
    return (int(m.group(1)), int(m.group(2))) if m else (8, 0)


def is_staff(member) -> bool:
    if member is None:
        return False
    perms = getattr(member, "guild_permissions", None)
    if perms and perms.administrator:
        return True
    return STAFF_ROLE is not None and any(r.id == STAFF_ROLE for r in getattr(member, "roles", []))


async def say(channel_id, **kw):
    ch = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
    return await ch.send(**kw)


def scene_is_public(thread) -> bool:
    if thread.is_private():
        return False
    parent = thread.parent
    if isinstance(parent, discord.ForumChannel):
        names = {t.name.lower() for t in getattr(thread, "applied_tags", [])}
        if "private" in names:
            return False
    return True


# ---------- the daily post ----------

def build_post(c, custom, year, start, end):
    n = logic.prompt_set(custom, year)
    prompts = c.prompts.get((custom.name, n), [])
    when = f"{start.day} {start.strftime('%B')}" + (f" to {end.day} {end.strftime('%B')}" if end != start else "")
    parts = [custom.post]
    last = store.chron(custom.name, year - custom.every)
    if last and last["status"] == "done" and last["line"]:
        parts.append(f"*Last time:* {last['line']}")
    if custom.twist:
        parts.append(f"*This year:* {custom.twist}")
    if prompts:
        parts.append("**Prompts**\n" + "\n".join(f"{i}. {p}" for i, p in enumerate(prompts, 1)))
    closing = c.settings.get("closing line", "")
    parts.append((closing + " " if closing else "") + "Play it in a scene with another player, then `/stamp` it.")
    emb = discord.Embed(title=f"{custom.name}", description="\n\n".join(parts)[:4000], colour=0x7B3FE4)
    emb.set_footer(text=f"{when} · {custom.where}")
    return emb


async def daily_post(c, now):
    today = now.date()
    h, m = post_time(c)
    if (now.hour, now.minute) < (h, m) or not CALENDAR_CHANNEL:
        return
    for cu in c.customs:
        if cu.status.lower() != c.settings.get("only post if status is", "Defined").lower():
            continue
        occ = logic.occurrence(cu, today.year)
        if not occ:
            continue
        s, e = occ
        if today == s and not store.get(f"posted:{cu.name}:{today.year}"):
            store.put(f"posted:{cu.name}:{today.year}", "1")
            await say(CALENDAR_CHANNEL, embed=build_post(c, cu, today.year, s, e))
        elif (today == e and e != s and c.settings.get("closing reminder", "").lower() in ("yes", "on", "true")
              and not store.get(f"closed:{cu.name}:{today.year}")):
            store.put(f"closed:{cu.name}:{today.year}", "1")
            await say(CALENDAR_CHANNEL, content=f"Last day of **{cu.name}**. Play it in a scene and `/stamp` it.")


# ---------- roles, Bynames, announcements ----------

async def get_role(guild, name, colour):
    role = discord.utils.get(guild.roles, name=name)
    if role is None:
        role = await guild.create_role(name=name, colour=discord.Colour(colour or 0), reason="Rivienne Byname")
    return role


async def sync_member(guild, user_id):
    """Fix the Island Byname role, and announce new Island and custom Bynames."""
    c = sheet.get()
    total = store.count(user_id)
    rung = logic.rung_for(total, c.ladder)
    try:
        member = guild.get_member(user_id) or await guild.fetch_member(user_id)
    except discord.NotFound:
        return
    ladder_names = {n for _, n in c.ladder}
    want = rung[1] if rung else None
    for r in list(member.roles):
        if r.name in ladder_names and r.name != want:
            await member.remove_roles(r, reason="Byname ladder")
    if want and not any(r.name == want for r in member.roles):
        await member.add_roles(await get_role(guild, want, c.colours.get(want)), reason="Byname ladder")
    prev = store.get(f"rung:{user_id}")
    if want and want != prev:
        store.put(f"rung:{user_id}", want)
        if CALENDAR_CHANNEL:
            await say(CALENDAR_CHANNEL, content=f"{member.mention} is now a **{want}**.")
    earned = logic.custom_bynames(store.dated_set(user_id), store.standing_set(user_id),
                                  c.standing_names(), c.rules)
    for b in sorted(earned):
        if not store.has_byname(user_id, b):
            store.add_byname(user_id, b)
            if CALENDAR_CHANNEL:
                await say(CALENDAR_CHANNEL, content=f"{member.mention} has earned the Byname **{b}**.")


# ---------- what a player can claim here ----------

def options_for(user_id, thread, c, today):
    """Each option: dict(label, value, kind, key, year)."""
    out = []
    tday = thread.created_at.astimezone(zone(c)).date()
    for cu, year, s, e in logic.active_occurrences(c.customs, today):
        if cu.status.lower() != "defined":
            continue
        if logic.thread_in_window(s, e, tday) and not store.has_stamp(user_id, cu.name, year):
            out.append(dict(label=f"{cu.name} {year}", value=f"d|{cu.name}|{year}", kind="dated", key=cu.name, year=year))
    for s in c.standing:
        if s["status"].lower() == "defined" and not store.has_stamp(user_id, "standing:" + s["name"], 0):
            out.append(dict(label=f"{s['name']} (standing custom)", value=f"s|{s['name']}", kind="standing",
                            key="standing:" + s["name"], year=0))
    for o in store.occasions():
        opened = datetime.fromisoformat(o["created_at"]).astimezone(zone(c)).date()
        closes = datetime.fromisoformat(o["closes_at"]).astimezone(zone(c)).date()
        key = f"occasion:{o['id']}"
        if opened <= tday <= closes and not store.has_stamp(user_id, key, 0):
            out.append(dict(label=f"{o['name']} (Stamp Event)", value=f"o|{o['id']}", kind="occasion", key=key, year=0))
    return out


def in_scenes(channel) -> bool:
    return isinstance(channel, discord.Thread) and (SCENES_CHANNEL is None or channel.parent_id == SCENES_CHANNEL)


async def grant(guild, user_id, claim):
    ok = store.add_stamp(user_id, claim["kind"], claim["key"], claim["year"], claim["label"], str(claim["id"]))
    if ok:
        store.add_scene_player(claim["thread_id"], user_id)
        await sync_member(guild, user_id)
    return ok


# ---------- the I was there button ----------

class PressButton(discord.ui.DynamicItem[discord.ui.Button], template=r"iwas:(?P<cid>\d+)"):
    def __init__(self, cid: int):
        super().__init__(discord.ui.Button(label="I was there", style=discord.ButtonStyle.success,
                                           custom_id=f"iwas:{cid}"))
        self.cid = cid

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["cid"]))

    async def callback(self, interaction: discord.Interaction):
        await handle_press(interaction, self.cid)


def claim_text(claim, names):
    who = ", ".join(names)
    return f"**{claim['label']}** Stamp. Stamped: {who}"


async def handle_press(interaction, cid):
    await interaction.response.defer(ephemeral=True)
    claim = store.claim(cid)
    if not claim:
        return await interaction.followup.send("This claim is no longer open.", ephemeral=True)
    user = interaction.user
    age = datetime.now(timezone.utc) - datetime.fromisoformat(claim["created_at"])
    if not claim["passed"] and age > timedelta(days=logic.PRESS_DAYS):
        return await interaction.followup.send("This claim has lapsed. Ask for a new one with /stamp.", ephemeral=True)
    if store.has_stamp(user.id, claim["key"], claim["year"]):
        return await interaction.followup.send("You already hold this Stamp.", ephemeral=True)
    counts = not is_staff(user)
    store.add_press(cid, user.id, counts)
    guild = interaction.guild
    if claim["passed"]:
        await grant(guild, user.id, claim)
        await interaction.followup.send("Stamped. Welcome to the scene.", ephemeral=True)
    elif logic.claim_passes(store.presses(cid), claim["claimant"]):
        store.mark_passed(cid)
        if claim["kind"] == "dated" and claim["public"]:
            store.add_scene(claim["thread_id"], claim["key"], claim["year"])
        for uid, _ in store.presses(cid):
            await grant(guild, uid, claim)
        await interaction.followup.send("Stamped.", ephemeral=True)
    else:
        return await interaction.followup.send(
            "Noted. The Stamp is given once another player who was in the scene has pressed too. "
            "A Contest Master does not count for that.", ephemeral=True)
    names = [f"<@{u}>" for u, _ in store.presses(cid)]
    try:
        await interaction.message.edit(content=claim_text(claim, names))
    except discord.HTTPException:
        pass


# ---------- player commands ----------

async def custom_autocomplete(interaction: discord.Interaction, current: str):
    ch = interaction.channel
    if not isinstance(ch, discord.Thread):
        return []
    c = sheet.get()
    opts = options_for(interaction.user.id, ch, c, now_local(c).date())
    cur = current.lower()
    return [app_commands.Choice(name=o["label"][:100], value=o["value"]) for o in opts if cur in o["label"].lower()][:25]


@tree.command(name="stamp", description="Claim a Stamp for the scene in this thread", guild=GUILD)
@app_commands.describe(custom="The custom you played")
@app_commands.autocomplete(custom=custom_autocomplete)
async def stamp_cmd(interaction: discord.Interaction, custom: str):
    ch = interaction.channel
    if not in_scenes(ch):
        return await interaction.response.send_message("Use /stamp inside a scene thread in the Scenes channel.", ephemeral=True)
    c = sheet.get()
    opt = next((o for o in options_for(interaction.user.id, ch, c, now_local(c).date()) if o["value"] == custom), None)
    if not opt:
        return await interaction.response.send_message(
            "You can't claim that here. Either you already hold it, or this scene was started outside its window.", ephemeral=True)
    if store.open_claim(ch.id, interaction.user.id, opt["key"], opt["year"], logic.PRESS_DAYS):
        return await interaction.response.send_message("You already have an open claim for that in this thread.", ephemeral=True)
    cid = store.new_claim(ch.id, interaction.user.id, opt["kind"], opt["key"], opt["year"], opt["label"], scene_is_public(ch))
    claim = store.claim(cid)
    store.add_press(cid, interaction.user.id, True)
    view = discord.ui.View(timeout=None)
    view.add_item(PressButton(cid))
    await interaction.response.send_message(
        f"{interaction.user.mention} is claiming the **{opt['label']}** Stamp for this scene.\n"
        f"Another player who was in it, press the button. A Contest Master or NPC does not count. "
        f"The claim stays open for {logic.PRESS_DAYS} days.", view=view)
    msg = await interaction.original_response()
    store.set_claim_message(cid, msg.id)


@tree.command(name="passport", description="See your Stamps, Bynames and your next Byname", guild=GUILD)
async def passport_cmd(interaction: discord.Interaction):
    c = sheet.get()
    uid = interaction.user.id
    total = store.count(uid)
    rung = logic.rung_for(total, c.ladder)
    nxt = logic.next_rung(total, c.ladder)
    emb = discord.Embed(title=f"{interaction.user.display_name}'s passport", colour=c.colours.get(rung[1], 0x7B3FE4) if rung else 0x7B3FE4)
    emb.add_field(name="Stamps", value=str(total))
    emb.add_field(name="Byname", value=rung[1] if rung else "None yet")
    emb.add_field(name="Next", value=f"{nxt[0] - total} more for **{nxt[1]}**" if nxt else "You've reached the top")
    custom_b = store.bynames(uid)
    emb.add_field(name="Custom Bynames", value=", ".join(custom_b) if custom_b else "None yet", inline=False)
    st = store.standing_set(uid)
    emb.add_field(name="Standing customs", value=f"{len(st)} of {len(c.standing_names())}")
    rows = store.stamps(uid)
    dated = [f"{r['label']}" for r in rows if r["kind"] == "dated"][-10:]
    if dated:
        emb.add_field(name="Recent dated customs", value="\n".join(dated), inline=False)
    await interaction.response.send_message(embed=emb, ephemeral=True)


# ---------- staff commands ----------

def staff_only(interaction):
    return is_staff(interaction.user)


@tree.command(name="award", description="Staff: give a Stamp directly to players", guild=GUILD)
@app_commands.describe(players="Mention the players, e.g. @Ash @Misty", reason="Why the Stamp is given")
async def award_cmd(interaction: discord.Interaction, players: str, reason: str):
    if not staff_only(interaction):
        return await interaction.response.send_message("Staff only.", ephemeral=True)
    ids = [int(x) for x in dict.fromkeys(re.findall(r"<@!?(\d+)>", players))]
    if not ids:
        return await interaction.response.send_message("Mention at least one player, like @Name.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    key = "award:" + uuid.uuid4().hex[:8]
    for uid in ids:
        store.add_stamp(uid, "award", key, 0, reason[:100], "award")
        await sync_member(interaction.guild, uid)
    if CALENDAR_CHANNEL:
        await say(CALENDAR_CHANNEL, content=f"A Stamp for {' '.join(f'<@{u}>' for u in ids)}: {reason}")
    await interaction.followup.send(f"Stamp given to {len(ids)} player(s).", ephemeral=True)


@tree.command(name="occasion", description="Staff: open a Stamp Event for a major scene", guild=GUILD)
@app_commands.describe(name="Name of the event", days="How many days scenes can be started for it")
async def occasion_cmd(interaction: discord.Interaction, name: str, days: app_commands.Range[int, 1, 60] = 14):
    if not staff_only(interaction):
        return await interaction.response.send_message("Staff only.", ephemeral=True)
    closes = datetime.now(timezone.utc) + timedelta(days=days)
    store.add_occasion(name, closes.isoformat(), interaction.user.id)
    if CALENDAR_CHANNEL:
        await say(CALENDAR_CHANNEL, embed=discord.Embed(
            title=f"Stamp Event: {name}",
            description=f"Play a scene for this in the next {days} days and `/stamp` it to earn a Stamp.", colour=0xA68000))
    await interaction.response.send_message("Stamp Event opened.", ephemeral=True)


class ConfirmReset(discord.ui.View):
    def __init__(self, target, by):
        super().__init__(timeout=60)
        self.target, self.by = target, by

    @discord.ui.button(label="Reset to zero", style=discord.ButtonStyle.danger)
    async def go(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.by:
            return await interaction.response.send_message("Not yours to confirm.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        n = store.reset_user(self.target.id, self.by)
        c = sheet.get()
        names = {nm for _, nm in c.ladder} | {r.byname for r in c.rules}
        member = interaction.guild.get_member(self.target.id)
        if member:
            for r in member.roles:
                if r.name in names:
                    await member.remove_roles(r, reason="Trainer retired")
        await interaction.followup.send(f"Reset {self.target.display_name}: {n} Stamps cleared.", ephemeral=True)
        self.stop()


@tree.command(name="reset", description="Staff: set a player's Stamps back to zero (retired Trainer)", guild=GUILD)
@app_commands.describe(player="The player whose Trainer has retired")
async def reset_cmd(interaction: discord.Interaction, player: discord.Member):
    if not staff_only(interaction):
        return await interaction.response.send_message("Staff only.", ephemeral=True)
    n = store.count(player.id)
    await interaction.response.send_message(
        f"This clears {player.display_name}'s {n} Stamps, Bynames and open claims. Chronicle lines stay. Sure?",
        view=ConfirmReset(player, interaction.user.id), ephemeral=True)


# ---------- the Chronicle: every entry goes through here ----------

ENTRY_MAX = 280
ENTRY_TYPES = [
    "Landmark (founders)", "Master Contest win", "Press - Editor", "Artist - Laureate",
    "Nurse - Physician", "Costumer - Designer", "Chef - Head Cook", "Mystic - Oracle",
    "Coastguard - Lifesaver", "Ranger - Old Hand", "Researcher - Chair", "Shrinewarden - Elder",
    "Mountaineer - Pathfinder", "Archaeologist - Old Bones", "Safety Officer - Chief Inspector",
    "HR Manager - Head of People", "Innkeeper - Host",
]


async def post_entry(kind, text, author_id, named_ids):
    """Posts one entry to the Chronicle channel. Entries are never edited or removed."""
    emb = discord.Embed(title=kind, description=text, colour=0x7B3FE4)
    if author_id:
        emb.set_footer(text="Written through the Chronicle command")
    named = [n for n in dict.fromkeys(named_ids)]
    content = None
    if named:
        content = ("Named in this entry: " + " ".join(f"<@{n}>" for n in named) +
                   ". You may take a Byname from it.")
    msg = await say(CHRONICLE_CHANNEL, content=content, embed=emb,
                    allowed_mentions=discord.AllowedMentions(users=True))
    store.add_entry(kind, text, author_id or 0, named, msg.id)
    return msg


class ConfirmEntry(discord.ui.View):
    def __init__(self, author, kind, text, named):
        super().__init__(timeout=180)
        self.author, self.kind, self.text, self.named = author, kind, text, named

    @discord.ui.button(label="Add to the Chronicle", style=discord.ButtonStyle.success)
    async def go(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author:
            return await interaction.response.send_message("Not yours to confirm.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        msg = await post_entry(self.kind, self.text, self.author, self.named)
        await interaction.followup.send(f"Written. {msg.jump_url}", ephemeral=True)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def no(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Cancelled. Nothing was written.", ephemeral=True)
        self.stop()


@tree.command(name="chronicle", description="Write an entry in the Chronicle", guild=GUILD)
@app_commands.describe(kind="What you are recording", entry="Your two lines. Entries are never edited, so read it twice",
                       named1="Someone named in the entry", named2="Another person named", named3="Another person named")
@app_commands.choices(kind=[app_commands.Choice(name=n, value=n) for n in ENTRY_TYPES])
async def chronicle_cmd(interaction: discord.Interaction, kind: app_commands.Choice[str],
                        entry: app_commands.Range[str, 10, ENTRY_MAX],
                        named1: discord.Member = None, named2: discord.Member = None, named3: discord.Member = None):
    named = [m.id for m in (named1, named2, named3) if m and not m.bot]
    preview = f"**{kind.value}**\n{entry}\n\n" + (f"Named: {' '.join(f'<@{n}>' for n in named)}\n\n" if named else "")
    await interaction.response.send_message(
        preview + "This goes into the Chronicle for good. It can't be edited or removed afterwards.",
        view=ConfirmEntry(interaction.user.id, kind.value, entry, named), ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none())


# ---------- the Chronicle loop ----------

def parse_dt(s):
    return datetime.fromisoformat(s) if s else None


async def chronicle_step(c, cu, year, s, e, now_utc):
    key = cu.name
    closes = datetime.combine(e + timedelta(days=logic.CLAIM_DAYS + logic.VOTE_DELAY_DAYS), datetime.min.time(),
                              tzinfo=zone(c)).astimezone(timezone.utc)
    row = store.chron(key, year)
    if not row:
        if now_utc < closes or not CHRONICLE_CHANNEL:
            return
        threads = store.scenes_for(key, year)
        if not threads:
            return store.chron_set(key, year, status="none")
        await say(CHRONICLE_CHANNEL, content=f"**{key}, {year}.** Which scene did you love most? Star it with a {STAR}. "
                  f"Voting is open for {logic.VOTE_DAYS} days. You can't star your own scene.")
        for tid in threads:
            th = bot.get_channel(tid) or await bot.fetch_channel(tid)
            msg = await say(CHRONICLE_CHANNEL, content=f"{th.jump_url}")
            await msg.add_reaction(STAR)
            store.add_vote_post(key, year, tid, msg.channel.id, msg.id)
        return store.chron_set(key, year, status="voting",
                               vote_close=(now_utc + timedelta(days=logic.VOTE_DAYS)).isoformat())
    if row["status"] == "voting" and now_utc >= parse_dt(row["vote_close"]):
        best, best_n = None, 0
        for vp in store.vote_posts(key, year):
            ch = bot.get_channel(vp["channel_id"]) or await bot.fetch_channel(vp["channel_id"])
            msg = await ch.fetch_message(vp["message_id"])
            players = set(store.scene_players(vp["thread_id"]))
            n = 0
            for r in msg.reactions:
                if str(r.emoji) == STAR:
                    async for u in r.users():
                        if not u.bot and u.id not in players:
                            n += 1
            if n > best_n:
                best, best_n = vp["thread_id"], n
        if best is None:
            return store.chron_set(key, year, status="none")
        th = bot.get_channel(best) or await bot.fetch_channel(best)
        ping = " ".join(f"<@{u}>" for u in store.scene_players(best))
        ask = await th.send(f"{ping} The island favoured your scene for **{key} {year}**! "
                            f"Reply to this message with a line or two that tells what happened, and it goes into the Chronicle. "
                            f"You have {logic.LINE_DAYS} days.")
        return store.chron_set(key, year, status="asking", winner_thread=best, ask_message=ask.id,
                               ask_deadline=(now_utc + timedelta(days=logic.LINE_DAYS)).isoformat())
    if row["status"] == "asking" and (row["line"] or now_utc >= parse_dt(row["ask_deadline"])):
        line = row["line"]
        if not line:
            th = bot.get_channel(row["winner_thread"]) or await bot.fetch_channel(row["winner_thread"])
            names = []
            guild = bot.get_guild(GUILD_ID)
            for u in store.scene_players(row["winner_thread"]):
                m = guild.get_member(u) if guild else None
                names.append(m.display_name if m else "a Trainer")
            line = f"In {year}, {', '.join(names)} played out \"{th.name}\" and the island remembered it."
        await post_entry("Favoured scene", f"**{key}, {year}.** {line}", None, store.scene_players(row["winner_thread"]))
        store.chron_set(key, year, status="done", line=line)


async def chronicle_all(c, now_utc):
    today = now_utc.astimezone(zone(c)).date()
    for cu in c.customs:
        for y in (today.year, today.year - 1):
            occ = logic.occurrence(cu, y)
            if occ:
                await chronicle_step(c, cu, y, occ[0], occ[1], now_utc)


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.reference or not message.reference.message_id:
        return
    row = store.chron_by_ask(message.reference.message_id)
    if not row or row["line"]:
        return
    if message.author.id not in store.scene_players(row["winner_thread"]):
        return
    text = message.content.strip()
    if 5 <= len(text) <= ENTRY_MAX:
        store.chron_set(row["key"], row["year"], line=text)
        await message.add_reaction("✅")


@tasks.loop(minutes=1)
async def tick():
    try:
        c = sheet.get()
        now = now_local(c)
        await daily_post(c, now)
        await chronicle_all(c, now.astimezone(timezone.utc))
        last = store.get("last_backup")
        if not last or datetime.now(timezone.utc) - datetime.fromisoformat(last) > timedelta(days=7):
            store.backup(os.getenv("BACKUP_DIR", "backups"))
            store.put("last_backup", datetime.now(timezone.utc).isoformat())
    except Exception:
        log.exception("tick failed")


@tick.before_loop
async def before_tick():
    await bot.wait_until_ready()


@bot.event
async def setup_hook():
    bot.add_dynamic_items(PressButton)
    if GUILD:
        await tree.sync(guild=GUILD)
    tick.start()


@bot.event
async def on_ready():
    log.info("Logged in as %s", bot.user)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if not TOKEN:
        raise SystemExit("Put DISCORD_TOKEN in the .env file first.")
    bot.run(TOKEN)
