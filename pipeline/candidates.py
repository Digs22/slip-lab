"""Every leg the model prices, picked or not, logged and graded: the training set for the learning loop.

Picks alone are a few dozen bets a week, far too few to learn from. Every scanned game yields many
priced legs (sides, totals, props) with the market's fair chance, the model's chance and each factor's
signal. Grading all of them gives thousands of outcomes, so we can measure whether the model beats
the market (lower log loss than the fair price) and which factors actually help.

    data/candidates/<game date ET>.json   {"<espn id>|<desc>": {...leg, "result": win|loss|push}}

A leg's entry is refreshed on every run until the game's picks lock (1 hour before start), then frozen.
"""
import json
import math
from datetime import datetime, timedelta, timezone

from . import config, grade, sources

DIR = config.DATA / "candidates"
FIELDS = ("desc", "market", "side", "team", "player", "stat", "line", "odds", "book", "fair", "prob", "edge", "signals")


def _load(path):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {}


def log(scored_games: list[dict]) -> int:
    from .run import et_date, locked
    DIR.mkdir(parents=True, exist_ok=True)
    files, n = {}, 0
    for g in scored_games:
        game = g["game"]
        if not game.get("start") or locked(game["start"]):
            continue
        day = et_date(game["start"])
        book = files.setdefault(day, _load(DIR / f"{day}.json"))
        for leg in g.get("evaluated", []):
            key = f"{game['id']}|{leg['desc']}"
            old = book.get(key, {})
            book[key] = {k: leg.get(k) for k in FIELDS} | {
                "event_id": game["id"], "league": game["league"], "game": f"{game['away']} @ {game['home']}",
                "start": game["start"], "researched": bool(g.get("analyst")), "picked": leg["edge"] >= config.MIN_EDGE,
                "first_odds": old.get("first_odds", leg.get("odds")), "first_fair": old.get("first_fair", leg.get("fair")),
                "logged": datetime.now(timezone.utc).isoformat(timespec="minutes")}
            n += 1
    for day, book in files.items():
        (DIR / f"{day}.json").write_text(json.dumps(book, separators=(",", ":")))
    return n


def grade_all() -> int:
    """Grade every logged leg whose game has finished. Returns how many were graded."""
    if not DIR.exists():
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(hours=3)
    done = 0
    for path in sorted(DIR.glob("*.json")):
        book = _load(path)
        open_ = [v for v in book.values() if "result" not in v
                 and datetime.fromisoformat(v["start"].replace("Z", "+00:00")) < cutoff]
        if not open_:
            continue
        games = {}
        for v in open_:
            eid = v["event_id"]
            if eid not in games:
                try:
                    g = grade.find_game({"league": v["league"], "date": path.stem, "game": v["game"], "event_id": eid})
                    if g and g.get("completed"):
                        props = any(x["market"] == "prop" for x in open_ if x["event_id"] == eid)
                        g["_stats"] = grade.box_stats(g) if props else {}
                    games[eid] = g if g and g.get("completed") else None
                except sources.SourceError as e:
                    print(f"candidates: {e}")
                    games[eid] = None
            g = games[eid]
            if not g:
                continue
            try:
                r = grade.leg_result(v, g, g.get("_stats"))
            except (KeyError, TypeError):
                r = "unknown"
            if r != "unknown":
                v["result"] = r
                done += 1
            elif datetime.fromisoformat(v["start"].replace("Z", "+00:00")) < cutoff - timedelta(days=2):
                v["result"] = "void"  # no box score line for this player after two days: drop it from training
        path.write_text(json.dumps(book, separators=(",", ":")))
    return done


def graded() -> list[dict]:
    rows = []
    for path in sorted(DIR.glob("*.json")) if DIR.exists() else []:
        rows += [v for v in _load(path).values() if v.get("result") in ("win", "loss")]
    return rows


def _logloss(p, y):
    p = min(max(p, 1e-4), 1 - 1e-4)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def report() -> dict:
    """Does the model beat the market? Lower log loss than the fair price means our adjustments add information."""
    rows = [r for r in graded() if r.get("prob") is not None and r.get("fair") is not None]
    if not rows:
        return {"n": 0}
    y = [1 if r["result"] == "win" else 0 for r in rows]
    model = sum(_logloss(r["prob"], t) for r, t in zip(rows, y)) / len(rows)
    market = sum(_logloss(r["fair"], t) for r, t in zip(rows, y)) / len(rows)
    picked = [(r, t) for r, t in zip(rows, y) if r.get("picked")]
    out = {"n": len(rows), "model_logloss": round(model, 4), "market_logloss": round(market, 4),
           "beats_market": model < market, "picked_n": len(picked),
           "picked_hit": round(sum(t for _, t in picked) / len(picked), 3) if picked else None,
           "picked_expected": round(sum(r["prob"] for r, _ in picked) / len(picked), 3) if picked else None}
    return out
