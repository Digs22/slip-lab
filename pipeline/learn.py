"""The self-improvement loop, run after grading.

1. Tag why each losing leg lost: bad read, variance, price, or late news.
2. Nudge factor weights: factors whose signal pointed the right way gain weight, wrong ones lose it.
   Small, capped steps so one game can't swing the model.
3. Flag bet types that keep losing, and keep the written rules from post-game reviews.
"""
import json

from . import config, ledger
from .analyze import load_weights

STEP = 0.05
MIN_W, MAX_W = 0.0, 2.0
MIN_SAMPLE = 8


def tag_cause(leg: dict) -> str:
    if leg.get("cause"):
        return leg["cause"]
    close, ours = leg.get("closing_odds"), leg.get("odds")
    if close is not None and ours is not None:
        from .odds import implied
        if implied(close) > implied(ours) + 0.01:
            return "variance"  # we beat the closing line, so the read was sound
        if implied(close) < implied(ours) - 0.03:
            return "price"  # market moved hard against us
    if leg.get("late_news"):
        return "late news"
    return "bad read"


def update_weights(graded_bets: list[dict]) -> dict:
    w = load_weights()
    seen = set()
    for bet in graded_bets:
        for leg in bet["legs"]:
            if leg.get("result") not in ("win", "loss") or leg["desc"] in seen:
                continue
            seen.add(leg["desc"])
            won = leg["result"] == "win"
            fair = leg.get("fair", 0.5)
            for factor, p in (leg.get("signals") or {}).items():
                if p is None or abs(p - fair) < 0.01:
                    continue
                right = (p > fair) == won
                w[factor] = round(min(MAX_W, max(MIN_W, w.get(factor, 0.5) + (STEP if right else -STEP))), 3)
    config.WEIGHTS_FILE.write_text(json.dumps(w, indent=2) + "\n")
    return w


def bet_type_flags(bets: list[dict]) -> list[dict]:
    s = ledger.summary(bets)
    flags = []
    for split in ("by_play", "by_market", "by_league"):
        for name, t in s[split].items():
            n = t["w"] + t["l"]
            if n >= MIN_SAMPLE and t["roi"] < -15:
                flags.append({"split": split[3:], "name": name, "roi": t["roi"], "n": n,
                              "action": "Cut stake in half until ROI recovers"})
            elif n >= MIN_SAMPLE and t["roi"] > 15:
                flags.append({"split": split[3:], "name": name, "roi": t["roi"], "n": n,
                              "action": "Working: keep full stake"})
    return flags


def run(bets: list[dict], newly_graded: list[dict]) -> dict:
    for bet in newly_graded:
        for leg in bet["legs"]:
            if leg.get("result") == "loss":
                leg["cause"] = tag_cause(leg)
        if bet["status"] == "loss" and not bet.get("cause"):
            losers = [l for l in bet["legs"] if l.get("result") == "loss"]
            bet["cause"] = ", ".join(sorted({l["cause"] for l in losers})) or "bad read"
    weights = update_weights(newly_graded)
    lessons = load_lessons()
    lessons["flags"] = bet_type_flags(bets)
    causes = {}
    for b in bets:
        for l in b["legs"]:
            if l.get("result") == "loss":
                c = l.get("cause", "untagged").split(":")[0]
                causes[c] = causes.get(c, 0) + 1
    lessons["loss_causes"] = causes
    lessons["weights"] = weights
    config.LESSONS_FILE.write_text(json.dumps(lessons, indent=2) + "\n")
    return lessons


def load_lessons() -> dict:
    try:
        return json.loads(config.LESSONS_FILE.read_text())
    except FileNotFoundError:
        return {"rules": [], "flags": [], "loss_causes": {}}
