"""Which leagues and teams get picks. The Settings tab's pick toggles.

    data/teams.json    every team per league, grouped (conference, division or soccer league), with
                       "out": true for teams already eliminated when the league is in its postseason
    data/filters.json  {"updated": iso, "off_leagues": [...], "off_teams": {"MLB": ["<espn team id>", ...]}}

The app saves the toggles on the device and sends them two ways: to the artifact database (collection
"prefs", doc "filters"; the daily run copies it here) and to the private ntfy topic "<topic>-prefs"
(the website). The newest copy wins. Everything is on by default, so a new team is never skipped.
"""
import json
import urllib.request
from datetime import datetime, timedelta, timezone

from . import config, sources

TEAMS_FILE = config.DATA / "teams.json"
FILTERS_FILE = config.DATA / "filters.json"
STANDINGS = "https://site.api.espn.com/apis/v2/sports"
SINGLE_GAME = ("NFL", "NCAAF", "NCAAB")  # one loss and you're out


# ---------- team list ----------

def _groups(node: dict, league: str, top: str | None = None, out: dict | None = None) -> dict:
    """Walk ESPN standings: every team under the root's direct children is grouped by that child."""
    out = {} if out is None else out
    for e in node.get("standings", {}).get("entries", []):
        t = e["team"]
        out.setdefault(top or node.get("name") or league, {})[str(t["id"])] = {
            "id": str(t["id"]), "abbr": t.get("abbreviation"), "name": t.get("displayName") or t.get("name")}
    for child in node.get("children", []):
        if top:
            name = top
        elif league == "NCAAF":
            name = config.CFB_CONFERENCES.get(str(child.get("id"))) or child.get("abbreviation") or child.get("name")
        elif league == "NCAAB":
            name = child.get("abbreviation") or child.get("shortName") or child.get("name")
        else:
            name = child.get("name")
        _groups(child, league, name, out)
    return out


def _paths(league):
    return [f"soccer/{s}" for s in config.SOCCER_LEAGUES] if league == "SOCCER" else [config.LEAGUES[league][0]]


def _league(league: str) -> dict:
    groups = {}
    for p in _paths(league):
        params = {"group": "80"} if league == "NCAAF" else {"group": "50"} if league == "NCAAB" else None
        data = sources.get_json(f"{STANDINGS}/{p}/standings", params, ttl=6 * 3600)
        g = _groups(data, league)
        if league == "SOCCER":  # one group per soccer league, named after it
            g = {data.get("name") or p: {k: v for grp in g.values() for k, v in grp.items()}}
        for name, teams in g.items():
            groups.setdefault(name, {}).update(teams)
    post, alive = postseason(league)
    out = []
    for name, teams in groups.items():
        rows = sorted(teams.values(), key=lambda t: t["name"] or "")
        for t in rows:
            if post and t["id"] not in alive:
                t["out"] = True
        out.append({"name": name, "teams": rows})
    out.sort(key=lambda g: g["name"])
    return {"postseason": post, "groups": out}


def postseason(league: str, today: str | None = None) -> tuple[bool, set]:
    """(in the postseason?, ids of teams still alive). A team is alive if it played or is scheduled in a
    postseason game in the last 3 weeks or next week and hasn't lost a finished series (or, for one-game
    rounds, its last game)."""
    if league == "SOCCER":
        return False, set()
    from .run import today as _today
    day = datetime.fromisoformat(today or _today())
    path = config.LEAGUES[league][0]
    params = {"dates": day.strftime("%Y%m%d")}
    if league in ("NCAAF", "NCAAB"):
        params["groups"] = "80" if league == "NCAAF" else "50"
    board = sources.get_json(f"{sources.ESPN}/{path}/scoreboard", params, ttl=3600)
    stype = ((board.get("leagues") or [{}])[0].get("season") or {}).get("type") or {}
    if str(stype.get("type")) != "3":
        return False, set()
    last = {}  # team id -> (date, eliminated?)
    for off in range(-21, 8):
        d = day + timedelta(days=off)
        p = dict(params, dates=d.strftime("%Y%m%d"))
        try:
            data = sources.get_json(f"{sources.ESPN}/{path}/scoreboard", p, ttl=3600 if off >= -1 else 7 * 86400)
        except sources.SourceError:
            continue
        for ev in data.get("events", []):
            if str((ev.get("season") or {}).get("type")) != "3":
                continue
            comp = ev["competitions"][0]
            done = ev["status"]["type"].get("completed", False)
            series = comp.get("series") or {}
            for c in comp["competitors"]:
                tid = str(c["team"]["id"])
                out = False
                if done and series.get("completed"):
                    need = series.get("totalCompetitions", 1) // 2 + 1
                    mine = next((s for s in series.get("competitors", []) if str(s.get("id")) == tid), {})
                    out = mine.get("wins", 0) < need
                elif done and league in SINGLE_GAME and not series:
                    out = c.get("winner") is False
                if tid not in last or ev["date"] >= last[tid][0]:
                    last[tid] = (ev["date"], out)
    return True, {tid for tid, (_, out) in last.items() if not out}


def build() -> dict:
    """Refresh data/teams.json. A league whose feed fails keeps its previous list."""
    try:
        old = json.loads(TEAMS_FILE.read_text()).get("leagues", {})
    except (FileNotFoundError, ValueError):
        old = {}
    leagues = {}
    for league in config.LEAGUES:
        try:
            leagues[league] = _league(league)
        except (sources.SourceError, KeyError, TypeError) as e:
            print(f"teams: {league}: {e}")
            if league in old:
                leagues[league] = old[league]
    data = {"generated": datetime.now(timezone.utc).isoformat(timespec="minutes"), "leagues": leagues}
    TEAMS_FILE.write_text(json.dumps(data, separators=(",", ":")) + "\n")
    return data


def load_teams() -> dict | None:
    try:
        return json.loads(TEAMS_FILE.read_text())
    except (FileNotFoundError, ValueError):
        return None


# ---------- filters ----------

def _clean(f: dict) -> dict:
    return {"updated": str(f.get("updated") or ""),
            "off_leagues": sorted({x for x in f.get("off_leagues", []) if x in config.LEAGUES}),
            "off_teams": {k: sorted({str(t) for t in v}) for k, v in (f.get("off_teams") or {}).items()
                          if k in config.LEAGUES and v}}


def _ntfy_latest() -> dict | None:
    from .live import notify_topic
    topic = notify_topic()
    if not topic:
        return None
    try:
        with urllib.request.urlopen(urllib.request.Request(f"https://ntfy.sh/{topic}-prefs/json?poll=1&since=all",
                                                           headers=sources.UA), timeout=15) as r:
            msgs = [json.loads(x) for x in r.read().decode().splitlines() if x.strip()]
    except Exception as e:
        print(f"filters: {e}")
        return None
    best = None
    for m in msgs:
        try:
            body = m.get("message") or "{}"
            if m.get("attachment", {}).get("url"):  # ntfy turns messages over 4 KB into a file
                with urllib.request.urlopen(urllib.request.Request(m["attachment"]["url"], headers=sources.UA), timeout=15) as r:
                    body = r.read().decode()
            f = json.loads(body)
        except Exception:
            continue
        if isinstance(f, dict) and f.get("key") == "filters" and (best is None or str(f.get("updated")) > str(best.get("updated"))):
            best = f
    return best


def load_filters(remote: bool = True) -> dict:
    """The saved toggles, updated from the website's newest copy on ntfy when it is newer."""
    try:
        f = _clean(json.loads(FILTERS_FILE.read_text()))
    except (FileNotFoundError, ValueError):
        f = _clean({})
    if remote:
        n = _ntfy_latest()
        if n and str(n.get("updated")) > f["updated"]:
            f = _clean(n)
            save_filters(f)
    return f


def save_filters(f: dict) -> dict:
    f = _clean(f)
    FILTERS_FILE.write_text(json.dumps(f, indent=2) + "\n")
    return f


def allowed(game: dict, f: dict) -> bool:
    """Picks for a game unless its league is off, or both teams are toggled off (a game with one team you
    follow still gets picks)."""
    league = game["league"]
    if league in f.get("off_leagues", []):
        return False
    off = set(f.get("off_teams", {}).get(league, []))
    ids = [str(game.get("home_id") or ""), str(game.get("away_id") or "")]
    return not off or not all(i and i in off for i in ids)
