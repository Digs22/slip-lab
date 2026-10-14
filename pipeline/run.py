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
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import analyze, builder, candidates, config, grade, learn, ledger, sources


def today() -> str:
    return datetime.now(ZoneInfo(config.TIMEZONE)).strftime("%Y-%m-%d")


def et_date(iso: str) -> str:
    """The Eastern calendar day a game starts on (ESPN gives UTC)."""
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ZoneInfo(config.TIMEZONE)).strftime("%Y-%m-%d")


def locked(game_start: str) -> bool:
    """Picks post the day before and can change with the odds or new research until one hour
    before the game starts (config.LOCK_BEFORE_MIN). After that they stay as posted."""
    start = datetime.fromisoformat(game_start.replace("Z", "+00:00"))
    return datetime.now(ZoneInfo(config.TIMEZONE)) >= start - timedelta(minutes=config.LOCK_BEFORE_MIN)


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


def _legset(b: dict) -> tuple:
    return tuple(sorted((l.get("event_id") or b.get("event_id") or "", l["desc"]) for l in b["legs"]))


def _duplicate(bets: list[dict], bet: dict) -> bool:
    """User rule: never post two SGP or Traditional bets with exactly the same legs. A leg may repeat
    inside different bets (a straight and a parlay). Pick of the Day is exempt: it is the day's highest
    hit-rate pick even when an SGP or Traditional straight already has it."""
    if bet.get("kind") == "potd":
        return False
    key = _legset(bet)
    return any(b["id"] != bet["id"] and b["status"] == "pending" and b.get("kind") != "potd" and _legset(b) == key for b in bets)


def _prune(bets: list[dict], prefix: str, keep: set) -> None:
    """Drop pending, still-unlocked bets under an id prefix that the latest run no longer builds,
    so a play that lost its edge on a rerun doesn't linger next to the new ones. A bet the user placed stays as placed."""
    for b in [b for b in bets if b["id"].startswith(prefix) and b["id"] not in keep]:
        if b["status"] == "pending" and not b.get("placed") and not (b.get("start") and locked(b["start"])):
            bets.remove(b)


def record_plays(date: str, picks: list[dict], bets: list[dict]) -> list[dict]:
    added = []
    for rank, p in enumerate(picks, 1):
        g = p["game"]
        gdate = et_date(g["start"]) if g.get("start") else date
        _prune(bets, f"{gdate}-{g['away']}{g['home']}-", {f"{gdate}-{g['away']}{g['home']}-{play}" for play in p["plays"]})
        for play, data in p["plays"].items():
            bid = f"{gdate}-{g['away']}{g['home']}-{play}"
            existing = next((b for b in bets if b["id"] == bid), None)
            if existing and (existing["status"] != "pending" or existing.get("placed") or locked(g["start"])):
                continue
            if existing:  # odds or research changed before game-day lock
                bets.remove(existing)
            bet = {
                "id": bid, "kind": "sgp", "date": gdate, "league": g["league"], "event_id": g["id"],
                "game": f"{g['away']} @ {g['home']}", "start": g["start"], "play": play,
                "venue": g.get("venue"), "city": g.get("city"),
                "home_logo": g.get("home_logo"), "away_logo": g.get("away_logo"),
                "odds": data["odds"], "units": data["units"], "prob": data["prob"], "ev": data["ev"],
                "provisional": p["provisional"], "score": p.get("score"), "rank": rank,
                "summary": p.get("analyst"), "weather": g.get("weather"),
                "legs": [{k: l.get(k) for k in ("desc", "market", "side", "team", "player", "stat", "line",
                                                 "odds", "book", "fair", "prob", "edge", "signals", "note", "status", "alt", "main_line")}
                         for l in data["legs"]],
            }
            if _duplicate(bets, bet):
                continue
            ledger.add(bets, bet)
            added.append(bet)
    return added


def record_traditional(date: str, plays: dict | None, bets: list[dict], prune: bool = True) -> list[dict]:
    """The day's multi-game slip: same four plays, one leg per game, logged like any other bet."""
    added = []
    if prune:
        _prune(bets, f"{date}-TRAD-", {f"{date}-TRAD-{play}" for play in (plays or {})})
    for play, data in (plays or {}).items():
        bid = f"{date}-TRAD-{play}"
        first = min((l["start"] for l in data["legs"] if l.get("start")), default=None)
        existing = next((b for b in bets if b["id"] == bid), None)
        if existing and (existing["status"] != "pending" or existing.get("placed") or (first and locked(first))):
            continue
        if existing:
            bets.remove(existing)
        bet = {"id": bid, "kind": "traditional", "date": date, "league": "MULTI",
               "game": f"Multi-game · {len({l.get('event_id') for l in data['legs']})} game{'s' if len({l.get('event_id') for l in data['legs']}) > 1 else ''}", "start": first, "play": play,
               "odds": data["odds"], "units": data["units"], "prob": data["prob"], "ev": data["ev"],
               "legs": [{k: l.get(k) for k in ("desc", "market", "side", "team", "player", "stat", "line", "odds", "book", "fair",
                                                "prob", "edge", "signals", "note", "status", "alt", "main_line",
                                                "event_id", "league", "matchup", "start")} | {"date": et_date(l["start"]) if l.get("start") else date}
                        for l in data["legs"]]}
        if _duplicate(bets, bet):
            continue
        ledger.add(bets, bet)
        added.append(bet)
    return added


def record_potd(date: str, picks: dict, bets: list[dict], prune: bool = True) -> list[dict]:
    """Pick of the Day: the best single bet in each league, logged as a straight bet."""
    added = []
    if prune:
        _prune(bets, f"{date}-POTD-", {f"{date}-POTD-{lg}" for lg in picks})
    for league, leg in picks.items():
        g = leg.pop("_game")
        bid = f"{date}-POTD-{league}"
        existing = next((b for b in bets if b["id"] == bid), None)
        if existing and (existing["status"] != "pending" or existing.get("placed") or (leg.get("start") and locked(leg["start"]))):
            continue
        if existing:
            bets.remove(existing)
        units = config.POTD_UNITS
        bet = {"id": bid, "kind": "potd", "date": date, "league": league, "event_id": leg["event_id"],
               "game": leg["matchup"], "start": leg.get("start"), "play": "straight", "venue": g.get("venue"), "city": g.get("city"),
               "home_logo": g.get("home_logo"), "away_logo": g.get("away_logo"),
               "odds": leg["odds"], "units": units, "prob": leg["prob"], "ev": leg["edge"],
               "legs": [{k: leg.get(k) for k in ("desc", "market", "side", "team", "player", "stat", "line", "odds", "book", "fair",
                                                  "prob", "edge", "signals", "note", "status", "alt", "main_line")}]}
        if _duplicate(bets, bet):
            continue
        ledger.add(bets, bet)
        added.append(bet)
    return added


def daily(date: str | None = None, only_ids: set | None = None):
    date = date or today()
    bets = ledger.load()
    changed = grade.grade_all(bets)
    if changed:
        learn.run(bets, changed)
    _grade_candidates()
    # Picks cover today and tomorrow: a game gets its plays the day before and they update until game-day lock.
    from datetime import timedelta
    tomorrow = (datetime.fromisoformat(date) + timedelta(days=1)).strftime("%Y-%m-%d")
    scored, problems, picks, added = [], [], [], []
    for d in (date, tomorrow):
        sc, pr = scan(d, only_ids)
        try:
            log(f"{d}: logged {candidates.log(sc)} priced legs for training")
        except Exception as e:  # training data must never block picks
            pr.append(f"candidates: {e}")
        pk = builder.pick_top_games(sc, config.GAMES_PER_DAY)
        added += record_plays(d, pk, bets)
        sgp_straights = {p["plays"]["straight"]["legs"][0]["desc"] for p in pk if "straight" in p["plays"]}
        trad = builder.build_traditional(sc, sgp_straights)
        added += record_traditional(d, trad, bets, prune=not only_ids)
        added += record_potd(d, builder.pick_of_day(sc), bets, prune=not only_ids)
        scored += sc; problems += [f"{d}: {m}" for m in pr]; picks += pk
    try:
        schedule()
    except Exception as e:  # the schedule is a preview; never block picks on it
        problems.append(f"schedule: {e}")
    ledger.save(bets)
    slate = {"date": date, "generated": datetime.now(ZoneInfo(config.TIMEZONE)).isoformat(timespec="minutes"),
             "games_scanned": len(scored), "problems": problems,
             "upcoming": upcoming(scored, picks),
             "games": [{"id": p["game"]["id"], "league": p["game"]["league"], "game": f"{p['game']['away']} @ {p['game']['home']}",
                        "start": p["game"]["start"], "venue": p["game"]["venue"], "provisional": p["provisional"],
                        "score": p.get("score"), "home_logo": p["game"].get("home_logo"), "away_logo": p["game"].get("away_logo"),
                        "summary": p.get("analyst")} for p in picks]}
    config.SLATE_FILE.write_text(json.dumps(slate, indent=2) + "\n")
    export()
    log(f"{len(picks)} games picked, {len(added)} plays logged, {len(problems)} feed problems")
    for msg in problems:
        log(f"  feed problem: {msg}")
    return slate


def _preview_odds(o: dict) -> dict:
    """ESPN's consensus line in either of its two shapes: spread text, total, moneylines."""
    if not o:
        return {}
    ml = o.get("moneyline") or {}
    def pick(side):
        v = (o.get(f"{side}TeamOdds") or {}).get("moneyLine")
        if v is None:
            v = ((ml.get(side) or {}).get("close") or (ml.get(side) or {}).get("open") or {}).get("odds")
        try:
            return int(str(v).replace("+", "")) if v not in (None, "", "EVEN", "OFF") else (100 if v == "EVEN" else None)
        except ValueError:
            return None
    return {k: v for k, v in {"spread": o.get("details"), "total": o.get("overUnder"),
                              "home_ml": pick("home"), "away_ml": pick("away"), "book": (o.get("provider") or {}).get("name")}.items()
            if v not in (None, "")}


def schedule(days: int = 7):
    """Every game in every league for the next 7 Eastern days, with ESPN's consensus odds, for the Games tab."""
    from datetime import timedelta
    start = datetime.fromisoformat(today())
    out, problems = [], []
    for i in range(days):
        d = (start + timedelta(days=i)).strftime("%Y-%m-%d")
        for league in config.LEAGUES:
            try:
                games = sources.espn_scoreboard(league, d)
            except sources.SourceError as e:
                problems.append(f"{league} {d}: {e}")
                continue
            for g in games:
                if not g.get("start") or et_date(g["start"]) != d:
                    continue
                out.append({"id": g["id"], "league": league, "date": d, "start": g["start"], "status": g["status"],
                            "game": f"{g['away']} @ {g['home']}", "away": g["away"], "home": g["home"],
                            "away_name": g.get("away_name"), "home_name": g.get("home_name"),
                            "away_logo": g.get("away_logo"), "home_logo": g.get("home_logo"),
                            "venue": g.get("venue"), "city": g.get("city"), "detail": g.get("detail"),
                            "away_score": g.get("away_score"), "home_score": g.get("home_score"),
                            "odds": _preview_odds(g.get("odds") or {})})
    seen, uniq = set(), []
    for g in sorted(out, key=lambda g: g["start"]):
        if g["id"] not in seen:
            seen.add(g["id"]); uniq.append(g)
    (config.DATA / "schedule.json").write_text(json.dumps({"generated": datetime.now(ZoneInfo(config.TIMEZONE)).isoformat(timespec="minutes"),
                                                           "games": uniq, "problems": problems}, indent=1) + "\n")
    log(f"schedule: {len(uniq)} games over {days} days, {len(problems)} feed problems")
    return uniq


def upcoming(scored: list[dict], picks: list[dict], n: int = 12) -> list[dict]:
    """Games on the slate that didn't get plays, best model score first, so the app can show what else is on."""
    taken = {p["game"]["id"] for p in picks}
    rest = sorted((s for s in scored if s["game"]["id"] not in taken), key=lambda s: s["score"], reverse=True)[:n]
    return [{"id": s["game"]["id"], "league": s["game"]["league"], "game": f"{s['game']['away']} @ {s['game']['home']}",
             "start": s["game"]["start"], "venue": s["game"].get("venue"), "city": s["game"].get("city"),
             "score": s["score"], "edges": len(s["legs"]),
             "home_logo": s["game"].get("home_logo"), "away_logo": s["game"].get("away_logo")} for s in rest]


def recheck(date: str | None = None):
    date = date or today()
    slate = json.loads(config.SLATE_FILE.read_text())
    waiting = {g["id"] for g in slate["games"] if g["provisional"]}
    if not waiting:
        log("nothing provisional")
        return
    daily(date, waiting)


def _grade_candidates():
    try:
        n = candidates.grade_all()
        if n:
            log(f"graded {n} logged legs; model vs market: {candidates.report()}")
            log(f"retrain: {learn.retrain()}")
    except Exception as e:
        log(f"candidates grading skipped: {e}")


def grade_cmd():
    bets = ledger.load()
    changed = grade.grade_all(bets)
    if changed:
        learn.run(bets, changed)
    _grade_candidates()
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
            "slate": json.loads(config.SLATE_FILE.read_text()) if config.SLATE_FILE.exists() else None,
            "schedule": json.loads((config.DATA / "schedule.json").read_text()) if (config.DATA / "schedule.json").exists() else None}
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
        if (b["status"] != "pending" and b["date"] not in recent) or b.get("kind") == "traditional":
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
    {"daily": daily, "schedule": schedule, "recheck": recheck, "grade": grade_cmd, "close": close, "export": export, "status": status,
     "candidates": lambda: print(candidates.report()), "retrain": lambda: print(learn.retrain())}[cmd]()
