"""Entry point. Same steps, same order, every day.

    python3 -m pipeline.run daily     # 7:59 AM ET: slate, lines, analysis, top games, four plays each
    python3 -m pipeline.run recheck   # every 30 min: re-run games still waiting on lineups or news
    python3 -m pipeline.run grade     # after games end: grade, update P&L, learn
    python3 -m pipeline.run close     # 10-30 min before games: save closing lines (for CLV)
    python3 -m pipeline.run export    # write the app's data file
    python3 -m pipeline.run status    # print the record
"""
import json
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from . import analyze, builder, config, grade, learn, ledger, sources


def today() -> str:
    return datetime.now(ZoneInfo(config.TIMEZONE)).strftime("%Y-%m-%d")


def log(msg):
    print(f"[{datetime.now(ZoneInfo(config.TIMEZONE)):%H:%M}] {msg}", flush=True)


def scan(date: str, only_ids: set | None = None) -> tuple[list[dict], list[str]]:
    weights = analyze.load_weights()
    scored, problems = [], []
    for league in config.LEAGUES:
        try:
            games = [g for g in sources.espn_scoreboard(league, date) if g["status"] == "pre"]
        except sources.SourceError as e:
            problems.append(f"{league} schedule: {e}")
            continue
        if only_ids is not None:
            games = [g for g in games if g["id"] in only_ids]
        if not games:
            continue
        try:
            injuries = sources.espn_injuries(league)
        except sources.SourceError as e:
            injuries, _ = [], problems.append(f"{league} injuries: {e}")
        try:
            lines = sources.odds(league)
        except sources.SourceError as e:
            lines, _ = None, problems.append(f"{league} odds: {e}")
        for g in games:
            scored.append(analyze.score_game(date, g, lines, injuries, weights))
        log(f"{league}: scored {len(games)} games")
    return scored, problems


def record_plays(date: str, picks: list[dict], bets: list[dict]) -> list[dict]:
    added = []
    for p in picks:
        g = p["game"]
        for play, data in p["plays"].items():
            bid = f"{date}-{g['away']}{g['home']}-{play}"
            existing = next((b for b in bets if b["id"] == bid), None)
            if existing and existing["status"] != "pending":
                continue
            if existing:  # a re-check changed the picks before the game started
                bets.remove(existing)
            bet = {
                "id": bid, "date": date, "league": g["league"], "event_id": g["id"],
                "game": f"{g['away']} @ {g['home']}", "start": g["start"], "play": play,
                "venue": g.get("venue"), "city": g.get("city"),
                "home_logo": g.get("home_logo"), "away_logo": g.get("away_logo"),
                "odds": data["odds"], "units": data["units"], "prob": data["prob"], "ev": data["ev"],
                "provisional": p["provisional"],
                "legs": [{k: l.get(k) for k in ("desc", "market", "side", "team", "player", "stat", "line",
                                                 "odds", "book", "fair", "prob", "edge", "signals", "note")}
                         for l in data["legs"]],
            }
            ledger.add(bets, bet)
            added.append(bet)
    return added


def daily(date: str | None = None, only_ids: set | None = None):
    date = date or today()
    bets = ledger.load()
    changed = grade.grade_all(bets)
    if changed:
        learn.run(bets, changed)
    scored, problems = scan(date, only_ids)
    picks = builder.pick_top_games(scored, config.GAMES_PER_DAY)
    added = record_plays(date, picks, bets)
    ledger.save(bets)
    slate = {"date": date, "generated": datetime.now(ZoneInfo(config.TIMEZONE)).isoformat(timespec="minutes"),
             "games_scanned": len(scored), "problems": problems,
             "games": [{"id": p["game"]["id"], "league": p["game"]["league"], "game": f"{p['game']['away']} @ {p['game']['home']}",
                        "start": p["game"]["start"], "venue": p["game"]["venue"], "provisional": p["provisional"],
                        "summary": p.get("analyst")} for p in picks]}
    config.SLATE_FILE.write_text(json.dumps(slate, indent=2) + "\n")
    export()
    log(f"{len(picks)} games picked, {len(added)} plays logged, {len(problems)} feed problems")
    for msg in problems:
        log(f"  feed problem: {msg}")
    return slate


def recheck(date: str | None = None):
    date = date or today()
    slate = json.loads(config.SLATE_FILE.read_text())
    waiting = {g["id"] for g in slate["games"] if g["provisional"]}
    if not waiting:
        log("nothing provisional")
        return
    daily(date, waiting)


def grade_cmd():
    bets = ledger.load()
    changed = grade.grade_all(bets)
    if changed:
        learn.run(bets, changed)
    ledger.save(bets)
    export()
    log(f"graded {len(changed)} bets")
    status()


def close():
    """Save the closing line for every game-line leg on bets whose games start within 45 minutes."""
    from datetime import timezone
    bets = ledger.load()
    now = datetime.now(timezone.utc)
    soon = [b for b in bets if b["status"] == "pending" and b.get("start")
            and 0 <= (datetime.fromisoformat(b["start"].replace("Z", "+00:00")) - now).total_seconds() <= 2700]
    saved = 0
    for league in {b["league"] for b in soon}:
        try:
            events = sources.odds(league)
        except sources.SourceError as e:
            log(f"close: {e}")
            continue
        for b in [b for b in soon if b["league"] == league]:
            g = grade.find_game(b)
            if not g:
                continue
            lines = {l["desc"]: l for l in analyze.best_lines(events, g["home_name"], g["away_name"])}
            for leg in b["legs"]:
                match = next((l for l in lines.values() if l["market"] == leg["market"] and l.get("line") == leg.get("line")
                              and (l.get("side") == leg.get("side") or (l.get("team") or "").endswith(leg.get("team") or "~"))), None)
                if match:
                    leg["closing_odds"] = match["consensus"]
                    saved += 1
    ledger.save(bets)
    log(f"close: saved {saved} closing lines")


def export():
    bets = ledger.load()
    data = {"generated": datetime.now(ZoneInfo(config.TIMEZONE)).isoformat(timespec="minutes"),
            "settings": {"unit": config.UNIT, "plays": config.PLAYS, "games_per_day": config.GAMES_PER_DAY,
                         "timezone": config.TIMEZONE, "refresh": config.REFRESH_TIME},
            "bets": bets, "summary": ledger.summary(bets), "lessons": learn.load_lessons(),
            "slate": json.loads(config.SLATE_FILE.read_text()) if config.SLATE_FILE.exists() else None}
    data["logos"] = logos(bets)
    blob = json.dumps(data, ensure_ascii=False)
    (config.APP_DIR / "data.json").write_text(blob)
    index = config.APP_DIR / "index.html"
    if index.exists():
        html = index.read_text()
        start, end = '<script id="seed" type="application/json">', "</script><!--/seed-->"
        a, b = html.index(start) + len(start), html.index(end)
        index.write_text(html[:a] + blob.replace("</", "<\\/") + html[b:])
    return data


def logos(bets: list[dict]) -> dict:
    """league:abbr -> data: URI for teams in open or recent games, cached in data/logos.json."""
    path = config.DATA / "logos.json"
    try:
        cache = json.loads(path.read_text())
    except FileNotFoundError:
        cache = {}
    recent = sorted({b["date"] for b in bets})[-4:]
    for b in bets:
        if b["status"] != "pending" and b["date"] not in recent:
            continue
        away, home = [x.strip().split(",")[0] for x in b["game"].split(",")[0].split("@")]
        for abbr, url in ((away, b.get("away_logo")), (home, b.get("home_logo"))):
            key = f"{b['league']}:{abbr}"
            if key not in cache:
                uri = sources.logo_data_uri(b["league"], abbr, url)
                if uri:
                    cache[key] = uri
    path.write_text(json.dumps(cache))
    return cache


def status():
    s = ledger.summary(ledger.load())["all"]
    print(f"Record {s['w']}-{s['l']}-{s['p']}, {s['pending']} pending | net ${s['net']:+.2f} "
          f"({s['units']:+.2f}u) | ROI {s['roi']:+.1f}% on ${s['risked']:.2f} risked")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    {"daily": daily, "recheck": recheck, "grade": grade_cmd, "close": close, "export": export, "status": status}[cmd]()
