"""Running research notes per team, so a team's next game starts from what we already learned.

    data/games_index.json             espn event id -> date, league, teams (filled by every scan)
    data/team_notes/<LEAGUE>/<ABBR>.md one file per team

Each file has a "Standing notes" section that research writes by hand (coaching tendencies, home field,
key players, depth, what keeps proving true) and an auto-built "Game log" from every analyst file on
that team: the summary, the research, the leg notes, our picks and how they graded. Rebuilding keeps
the standing notes as they are.
"""
import json
import re

from . import config

INDEX = config.DATA / "games_index.json"
NOTES = config.DATA / "team_notes"
ANALYST = config.DATA / "analyst"
MARK = "## Game log"
STANDING = ("## Standing notes\n\n_Research adds what stays true across games here: coaching tendencies, "
            "home field, key players and depth, injury history, betting trends._\n\n")


def _load_index() -> dict:
    try:
        return json.loads(INDEX.read_text())
    except FileNotFoundError:
        return {}


def record(games: list[dict], league: str) -> None:
    """Remember who played in each scanned game (called from run.scan)."""
    idx = _load_index()
    for g in games:
        idx[str(g["id"])] = {"date": g["start"][:10], "start": g["start"], "league": league,
                             "home": g["home"], "away": g["away"],
                             "home_name": g.get("home_name", ""), "away_name": g.get("away_name", "")}
    INDEX.write_text(json.dumps(idx, indent=1, sort_keys=True))


def _game(eid: str, date: str, idx: dict, by_event: dict) -> dict | None:
    if eid in idx:
        return idx[eid]
    b = by_event.get(eid)
    if b and " @ " in b.get("game", ""):
        away, home = b["game"].split(" @ ", 1)
        return {"date": date, "league": b["league"], "home": home, "away": away}
    return None


def _graded(bets: list[dict], eid: str) -> list[str]:
    out = []
    for b in bets:
        if b.get("event_id") == eid or any(l.get("event_id") == eid for l in b.get("legs", [])):
            legs = [l for l in b["legs"] if b.get("event_id") == eid or l.get("event_id") == eid]
            res = " / ".join(f"{l['desc']} ({l['odds']:+d}): {l.get('status') or 'pending'}" for l in legs)
            out.append(f"- {b['play']}{' (lean)' if b.get('lean') else ''}: {res} -> **{b['status']}**")
    return out


def build() -> int:
    """Rebuild every team file. Returns how many teams have notes."""
    idx = _load_index()
    bets = json.loads(config.BETS_FILE.read_text())["bets"]
    by_event = {b["event_id"]: b for b in bets if b.get("event_id")}
    finals = {b["event_id"]: b["final"] for b in bets if b.get("event_id") and b.get("final")}
    log: dict[tuple, list] = {}
    for f in sorted(ANALYST.glob("*.json"), reverse=True):
        m = re.match(r"(\d{4}-\d{2}-\d{2})-(\d+)$", f.stem)
        if not m:
            continue
        date, eid = m.groups()
        g = _game(eid, date, idx, by_event)
        if not g:
            continue
        a = json.loads(f.read_text())
        notes = [f"- {l['desc']}: {l.get('note', '')}" for l in a.get("legs", []) + a.get("extra_legs", []) if l.get("note")]
        for side, opp, where in (("home", "away", "vs"), ("away", "home", "at")):
            name = g.get(f"{opp}_name") or g[opp]
            block = [f"### {date}: {where} {name}" + (f" (final: {finals[eid]})" if eid in finals else "")]
            if a.get("summary"):
                block.append(a["summary"])
            if a.get("research"):
                r = a["research"]
                block.append("\n".join(r) if isinstance(r, list) else str(r))
            if notes:
                block.append("Legs considered:\n" + "\n".join(notes))
            graded = _graded(bets, eid)
            if graded:
                block.append("Our bets:\n" + "\n".join(graded))
            log.setdefault((g["league"], g[side], g.get(f"{side}_name") or g[side]), []).append("\n\n".join(block))
    for (league, abbr, name), entries in log.items():
        path = NOTES / league / f"{re.sub(r'[^A-Za-z0-9]', '', abbr) or 'team'}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        head = f"# {name} ({league})\n\n" + STANDING
        if path.exists():
            old = path.read_text()
            head = old.split(MARK, 1)[0] if MARK in old else old
        path.write_text(head.rstrip() + "\n\n" + MARK + " (auto-built from every researched game, newest first)\n\n"
                        + "\n\n".join(entries) + "\n")
    return len(log)
