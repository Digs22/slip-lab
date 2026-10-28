"""Entry point. Same steps, same order, every day.

    python3 -m pipeline.run daily     # 7:59 AM ET: slate, lines, analysis, top games, four plays each
    python3 -m pipeline.run recheck   # every 30 min: re-run games still waiting on lineups or news
    python3 -m pipeline.run grade     # after games end: grade, update P&L, learn
    python3 -m pipeline.run close     # 10-30 min before games: save closing lines (for CLV)
    python3 -m pipeline.run export    # write the app's data file
    python3 -m pipeline.run status    # print the record
    python3 -m pipeline.run teams     # refresh the Settings tab's team list (and who's eliminated)
"""
import json
from collections import Counter
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import analyze, builder, candidates, config, grade, learn, ledger, sources, sync, team_notes, teams


def today() -> str:
    return datetime.now(ZoneInfo(config.TIMEZONE)).strftime("%Y-%m-%d")


def et_date(iso: str) -> str:
    """The Eastern calendar day a game starts on (ESPN gives UTC)."""
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ZoneInfo(config.TIMEZONE)).strftime("%Y-%m-%d")


def locked(game_start: str) -> bool:
    """Picks post the day before and can change with the odds or new research until one hour
    before the game starts (config.LOCK_BEFORE_MIN). After that they stay as posted."""
    start = datetime.fromisoformat(game_start.replace("Z", "+00:00"))
    lead = getattr(config, "LOCK_OVERRIDE", {}).get(game_start, config.LOCK_BEFORE_MIN)
    return datetime.now(ZoneInfo(config.TIMEZONE)) >= start - timedelta(minutes=lead)


def log(msg):
    print(f"[{datetime.now(ZoneInfo(config.TIMEZONE)):%H:%M}] {msg}", flush=True)


FROZEN = set()  # dates whose odds feed failed this run: posted picks are not rebuilt from worse lines


def scan(date: str, only_ids: set | None = None) -> tuple[list[dict], list[str]]:
    weights = analyze.load_weights()
    scored, problems = [], []
    filters = teams.load_filters()
    for league in config.LEAGUES:
        if league in filters["off_leagues"]:
            continue  # toggled off in Settings: no picks, no API calls
        try:
            games = [g for g in sources.espn_scoreboard(league, date) if g["status"] == "pre" and teams.allowed(g, filters)]
        except sources.SourceError as e:
            problems.append(f"{league} schedule: {e}")
            continue
        try:
            team_notes.record(games, league)
        except Exception as e:  # notes must never block picks
            problems.append(f"{league} team notes: {e}")
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
            if config.ODDS_API_KEY:  # an outage or a spent quota: fall back to public lines (user, 2026-10-17),
                FROZEN.add(date)      # ESPN's consensus plus lines the research priced from public odds pages
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

    def rebuilt_later(b):  # an unlocked, unplaced Traditional bet is rebuilt after the game slips, so it can't block one
        return bet.get("kind") == "sgp" and b.get("kind") == "traditional" and not b.get("placed") \
            and not (b.get("start") and locked(b["start"]))
    return any(b["id"] != bet["id"] and b["status"] == "pending" and b.get("kind") != "potd" and not rebuilt_later(b)
               and _legset(b) == key for b in bets)


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
                "home_conf": g.get("home_conf"), "away_conf": g.get("away_conf"),
                "odds": data["odds"], "units": data["units"], "prob": data["prob"], "ev": data["ev"],
                "provisional": p["provisional"], "score": p.get("score"), "rank": rank, "lean": bool(data.get("lean")),
                "summary": p.get("analyst"), "weather": g.get("weather"),
                "legs": [{k: l.get(k) for k in ("desc", "market", "side", "team", "player", "stat", "line",
                                                 "odds", "book", "fair", "prob", "edge", "signals", "note", "status", "alt", "main_line")}
                         for l in data["legs"]],
            }
            if _duplicate(bets, bet):
                continue
            ledger.add(bets, bet)
        if g["league"] == "NBA":
            _usage_sgp(gdate, g, p, rank, bets)
            added.append(bet)
    return added


def _usage_sgp(gdate: str, g: dict, p: dict, rank: int, bets: list[dict]) -> None:
    """The user's NBA usage SGP (NBA.md, 2026-10-20) takes the game's Best parlay slot: 24%+ usage players
    against bottom-10 position defenses, 93% alt lines. "highlight" marks one whose true chance beats the price."""
    path = config.DATA / "analyst" / f"{gdate}-{g['id']}.json"
    try:
        a = json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return
    sgp = a.get("sgp") or {}
    pool = {l["desc"]: l for l in a.get("extra_legs", [])}
    legs = [pool[d] for d in sgp.get("legs", []) if d in pool and pool[d].get("odds")]
    if len(legs) < 2:
        return
    bid = f"{gdate}-{g['away']}{g['home']}-best"
    existing = next((b for b in bets if b["id"] == bid), None)
    if existing and (existing["status"] != "pending" or existing.get("placed") or locked(g["start"])):
        return
    dec = 1.0
    for l in legs:
        dec *= 1 + (l["odds"] / 100 if l["odds"] > 0 else 100 / -l["odds"])
    odds = sgp.get("odds") or round((dec - 1) * 100)
    prob = sgp.get("true_prob")
    if existing:
        bets.remove(existing)
    ledger.add(bets, {
        "id": bid, "kind": "sgp", "date": gdate, "league": g["league"], "event_id": g["id"],
        "game": f"{g['away']} @ {g['home']}", "start": g["start"], "play": "best",
        "venue": g.get("venue"), "city": g.get("city"), "home_logo": g.get("home_logo"), "away_logo": g.get("away_logo"),
        "odds": odds, "units": config.PLAYS["best"], "prob": prob,
        "ev": round(prob * (1 + (odds / 100 if odds > 0 else 100 / -odds)) - 1, 4) if prob else None,
        "provisional": p["provisional"], "score": p.get("score"), "rank": rank, "lean": False,
        "highlight": bool(sgp.get("highlight", sgp.get("edge"))), "usage_sgp": True, "summary": p.get("analyst"),
        "legs": [{k: l.get(k) for k in ("desc", "market", "side", "team", "player", "stat", "line", "odds", "book",
                                         "fair", "prob", "edge", "signals", "note", "status", "alt", "main_line")} for l in legs],
    })


def record_traditional(date: str, plays: dict | None, bets: list[dict], prune: bool = True) -> list[dict]:
    """The day's multi-game slip: same four plays, one leg per game, logged like any other bet."""
    added = []
    if prune:
        _prune(bets, f"{date}-TRAD-", {f"{date}-TRAD-{play}" for play in (plays or {})})
    for play, data in (plays or {}).items():
        bid = f"{date}-TRAD-{play}"
        first = min((l["start"] for l in data["legs"] if l.get("start")), default=None)
        existing = next((b for b in bets if b["id"] == bid), None)
        # Locked means the posted slip's own first game, not the rebuilt one's (2026-10-24: a slip whose games had
        # started was replaced by one built from later games).
        if existing and (existing["status"] != "pending" or existing.get("placed") or (first and locked(first))
                         or (existing.get("start") and locked(existing["start"]))):
            continue
        if existing:
            bets.remove(existing)
        bet = {"id": bid, "kind": "traditional", "date": date, "league": "MULTI",
               "game": f"Multi-game · {len({l.get('event_id') for l in data['legs']})} game{'s' if len({l.get('event_id') for l in data['legs']}) > 1 else ''}", "start": first, "play": play,
               "odds": data["odds"], "units": data["units"], "prob": data["prob"], "ev": data["ev"], "lean": bool(data.get("lean")),
               "legs": [{k: l.get(k) for k in ("desc", "market", "side", "team", "player", "stat", "line", "odds", "book", "fair",
                                                "prob", "edge", "signals", "note", "status", "alt", "main_line",
                                                "event_id", "league", "matchup", "start")} | {"date": et_date(l["start"]) if l.get("start") else date}
                        for l in data["legs"]]}
        if _duplicate(bets, bet):
            continue
        ledger.add(bets, bet)
        added.append(bet)
    return added


def _started(game_start: str) -> bool:
    return datetime.now(ZoneInfo(config.TIMEZONE)) >= datetime.fromisoformat(game_start.replace("Z", "+00:00"))


def record_daily_longshot(date: str, bets: list[dict]) -> list[dict]:
    """The Longshot section. User, 2026-10-24: "make it a reasonable slip comprised of the best picks", not every
    game (a 13-game parlay had paid 4,589-to-1 and lost). One parlay of the day's best straights: edge picks only
    (no Leans), one per game, highest win chance first, from games that haven't started (user, 2026-10-24: "add noon
    games if they're better value", so a locked but not yet started game still counts), adding legs until it pays
    config.LONGSHOT_MIN, between LONGSHOT_MIN_LEGS and LONGSHOT_MAX_LEGS legs. No such slip, no Longshot.
    An unplaced Longshot rebuilds each run from whatever is still unlocked."""
    bid = f"{date}-LONG-longshot"
    existing = next((b for b in bets if b["id"] == bid), None)
    every_game = existing and len(existing["legs"]) > config.LONGSHOT_MAX_LEGS  # the old every-game slip is replaced
    if existing and (existing["status"] != "pending" or existing.get("placed")
                     or (not every_game and any(_started(l["start"]) for l in existing["legs"] if l.get("start")))):
        return []
    open_straights = [b for b in bets if b.get("kind") == "sgp" and b.get("date") == date and b["play"] == "straight"
                      and b["status"] == "pending" and b.get("start") and not _started(b["start"])]
    straights = sorted((b for b in open_straights if not b.get("lean") and not b["legs"][0].get("lean")),
                       key=lambda b: -(b["legs"][0].get("prob") or b.get("prob") or 0))
    lean = False
    if len({b.get("event_id") for b in straights}) < config.LONGSHOT_MIN_LEGS:
        # User, 2026-10-28: a day without enough edge picks still gets a Longshot, topped up with the best Leans
        # (most likely winners first) and marked a Lean.
        straights += sorted((b for b in open_straights if b not in straights),
                            key=lambda b: -(b["legs"][0].get("prob") or b.get("prob") or 0))
        lean = True
    if existing:
        bets.remove(existing)
    legs, dec, prob, games = [], 1.0, 1.0, set()
    # The day-wide 2-bet cap (user, 2026-10-15) applies here too; Picks of the Day don't count toward it.
    uses = Counter(l["desc"] for x in bets if x.get("date") == date and x["id"] != bid and "-POTD-" not in x["id"]
                   for l in x.get("legs", []))
    for b in straights:
        if b.get("event_id") in games or uses[b["legs"][0]["desc"]] >= config.MAX_LEG_REUSE:
            continue
        l = b["legs"][0]
        games.add(b.get("event_id"))
        legs.append({k: l.get(k) for k in ("desc", "market", "side", "team", "player", "stat", "line", "odds", "book", "fair",
                                            "prob", "edge", "signals", "note", "status", "alt", "main_line")}
                    | {"event_id": b.get("event_id"), "league": b["league"], "matchup": b["game"], "start": b["start"], "date": date})
        dec *= 1 + (l["odds"] / 100 if l["odds"] > 0 else 100 / -l["odds"])
        prob *= l.get("prob") or b.get("prob") or 0.5
        if len(legs) >= config.LONGSHOT_MIN_LEGS and (dec - 1) * 100 >= config.LONGSHOT_MIN or len(legs) == config.LONGSHOT_MAX_LEGS:
            break
    if len(legs) < config.LONGSHOT_MIN_LEGS or (dec - 1) * 100 < config.LONGSHOT_MIN:
        return []
    bet = {"id": bid, "kind": "longshot", "date": date, "league": "MULTI",
           "game": f"Longshot · {len(legs)} best picks", "start": min(l["start"] for l in legs), "play": "longshot",
           "odds": round((dec - 1) * 100), "units": config.PLAYS["longshot"], "prob": round(prob, 4), "ev": round(prob * dec - 1, 4),
           "lean": lean, "legs": legs}
    ledger.add(bets, bet)
    return [bet]


def record_potd(date: str, picks: dict, bets: list[dict], prune: bool = True) -> list[dict]:
    """Pick of the Day: the best single bet in each league, logged as a straight bet."""
    added = []
    if prune:
        _prune(bets, f"{date}-POTD-", {f"{date}-POTD-{lg}" for lg in picks})
    for league, leg in picks.items():
        g = leg.pop("_game")
        bid = f"{date}-POTD-{league}"
        existing = next((b for b in bets if b["id"] == bid), None)
        if existing and (existing["status"] != "pending" or existing.get("placed") or (leg.get("start") and locked(leg["start"]))
                         or (existing.get("start") and locked(existing["start"]))):
            continue
        if existing:
            bets.remove(existing)
        units = config.POTD_UNITS
        bet = {"id": bid, "kind": "potd", "date": date, "league": league, "event_id": leg["event_id"],
               "game": leg["matchup"], "start": leg.get("start"), "play": "straight", "venue": g.get("venue"), "city": g.get("city"),
               "home_logo": g.get("home_logo"), "away_logo": g.get("away_logo"),
               "odds": leg["odds"], "units": units, "prob": leg["prob"], "ev": leg["edge"], "lean": bool(leg.get("lean")),
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
    _sync(bets)
    changed = grade.grade_all(bets)
    if changed:
        learn.run(bets, changed)
    _audit(bets)
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
        # Picks already posted from full multi-book lines are kept: rebuilding them on ESPN-only lines
        # replaced good picks on 2026-10-17. A date with no picks yet is built from the public lines.
        if d in FROZEN and not only_ids and any(b.get("date") == d and b["status"] == "pending" and b.get("kind") == "sgp" for b in bets):
            scored += sc; problems += [f"{d}: {m}" for m in pr]
            continue
        pk = builder.pick_top_games(sc, config.GAMES_PER_DAY)
        if not only_ids:  # a game that dropped out of the picks (lost its edge or its research) loses its open plays
            keep = {b["id"] for p in pk for b in bets if b.get("event_id") == p["game"]["id"]}
            for b in [b for b in bets if b.get("kind") == "sgp" and b.get("date") == d and b["id"] not in keep]:
                _prune(bets, b["id"], set())
        added += record_plays(d, pk, bets)
        sgp_straights = {p["plays"]["straight"]["legs"][0]["desc"] for p in pk if "straight" in p["plays"]}
        # Spread the risk (user, 2026-10-15): a leg already in the day's game slips counts toward its 2-bet cap.
        # Locked or placed Traditional bets stay on the slip, so their legs count too.
        sgp_use = Counter(l["desc"] for b in bets if b.get("date") == d and b["status"] == "pending"
                          and "-POTD-" not in b["id"]
                          and ("-TRAD-" not in b["id"] or b.get("placed") or (b.get("start") and locked(b["start"])))
                          for l in b["legs"])
        trad = builder.build_traditional(sc, sgp_straights, sgp_use)
        added += record_traditional(d, trad, bets, prune=not only_ids)
        added += record_potd(d, builder.pick_of_day(sc), bets, prune=not only_ids)
        added += record_daily_longshot(d, bets)
        scored += sc; problems += [f"{d}: {m}" for m in pr]; picks += pk
    try:
        schedule()
    except Exception as e:  # the schedule is a preview; never block picks on it
        problems.append(f"schedule: {e}")
    try:
        t = teams.load_teams()
        if not t or t.get("generated", "")[:10] != datetime.now(ZoneInfo("UTC")).strftime("%Y-%m-%d"):
            teams.build()  # once a day: the Settings team list and who's been eliminated
    except Exception as e:
        problems.append(f"teams: {e}")
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
                            "away_id": g.get("away_id"), "home_id": g.get("home_id"),
                            "away_name": g.get("away_name"), "home_name": g.get("home_name"),
                            "away_logo": g.get("away_logo"), "home_logo": g.get("home_logo"),
                            "venue": g.get("venue"), "city": g.get("city"), "detail": g.get("detail"),
                            "away_score": g.get("away_score"), "home_score": g.get("home_score"),
                            "home_conf": g.get("home_conf"), "away_conf": g.get("away_conf"),
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


def _audit(bets: list[dict], refetch: bool = True) -> list[str]:
    """Re-verify every settled bet against its legs and odds so the Record tab is exact."""
    try:
        fixes = grade.audit(bets, refetch=refetch)
    except Exception as e:  # the audit must never block grading or picks
        log(f"audit skipped: {e}")
        return []
    for f in fixes:
        log(f"audit fix: {f}")
    return fixes


def _sync(bets: list[dict]) -> None:
    """Settings, pick toggles and placed taps from the website (see sync.py)."""
    try:
        n = sync.pull(bets)
        if any(n.values()):
            log(f"device sync: {n}")
    except Exception as e:  # never block picks on it
        log(f"device sync skipped: {e}")


def grade_cmd():
    bets = ledger.load()
    _sync(bets)
    changed = grade.grade_all(bets)
    if changed:
        learn.run(bets, changed)
    _audit(bets)
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
    for league in {b["league"] for b in soon} & set(config.LEAGUES):  # Traditional slips (MULTI) have no single feed
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
    if _audit(bets, refetch=False):
        ledger.save(bets)
    data = {"generated": datetime.now(ZoneInfo(config.TIMEZONE)).isoformat(timespec="minutes"),
            "settings": {"unit": config.UNIT, "plays": config.PLAYS, "games_per_day": config.GAMES_PER_DAY,
                         "timezone": config.TIMEZONE, "refresh": config.REFRESH_TIME},
            "bets": bets, "summary": ledger.summary(bets), "lessons": learn.load_lessons(),
            "slate": json.loads(config.SLATE_FILE.read_text()) if config.SLATE_FILE.exists() else None,
            "schedule": json.loads((config.DATA / "schedule.json").read_text()) if (config.DATA / "schedule.json").exists() else None,
            "teams": teams.load_teams(), "filters": teams.load_filters(remote=False),
            "settings": sync.load_settings(), "placed_events": sync.load_placed_events()}
    data["logos"] = logos(bets)
    try:
        log(f"team notes: {team_notes.build()} teams")
    except Exception as e:  # notes must never block the export
        log(f"team notes failed: {e}")
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
        if (b["status"] != "pending" and b["date"] not in recent) or b.get("kind") in ("traditional", "longshot"):
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
     "candidates": lambda: print(candidates.report()), "retrain": lambda: print(learn.retrain()),
     "teams": lambda: (teams.build(), export())}[cmd]()
