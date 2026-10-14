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


# ---------- retraining on every graded leg (data/candidates) ----------

MIN_TRAIN = 300      # graded legs before any retrain is trusted
MIN_FACTOR_ROWS = 30  # a factor needs this many rows with a real signal to be refit


def _features(row, factors):
    from .analyze import logit
    base = logit(row["fair"])
    sig = row.get("signals") or {}
    return base, [(logit(sig[f]) - base) / 4 if sig.get(f) is not None else 0.0 for f in factors]


def _loss(rows, factors, w):
    import math
    total = 0.0
    for base, x, y in rows:
        z = base + sum(wi * xi for wi, xi in zip(w, x))
        p = min(max(1 / (1 + math.exp(-z)), 1e-4), 1 - 1e-4)
        total -= y * math.log(p) + (1 - y) * math.log(1 - p)
    return total / max(len(rows), 1)


def fit_weights(rows: list[dict], current: dict, epochs: int = 300, lr: float = 0.5, l2: float = 0.02) -> dict:
    """Fit factor weights by minimizing log loss on graded legs, oldest 70% as training and newest 30%
    held out. The new weights are kept only if they beat both the current weights and the market's own
    price on the held-out games, so the model never trusts a change that just memorized the past."""
    import math
    rows = sorted((r for r in rows if r.get("fair") is not None and r.get("result") in ("win", "loss")), key=lambda r: r["start"])
    out = {"n": len(rows), "accepted": False}
    if len(rows) < MIN_TRAIN:
        out["reason"] = f"waiting for {MIN_TRAIN} graded legs"
        return out
    counts = {}
    for r in rows:
        for f, p in (r.get("signals") or {}).items():
            if p is not None and abs(p - r["fair"]) >= 0.01:
                counts[f] = counts.get(f, 0) + 1
    factors = sorted(f for f, n in counts.items() if n >= MIN_FACTOR_ROWS)
    if not factors:
        out["reason"] = "no factor has enough signal rows yet"
        return out
    data = [(*_features(r, factors), 1 if r["result"] == "win" else 0) for r in rows]
    cut = int(len(data) * 0.7)
    train, test = data[:cut], data[cut:]
    w0 = [current.get(f, 0.5) for f in factors]
    w = list(w0)
    for _ in range(epochs):  # plain gradient descent with a pull back toward the current weights
        g = [0.0] * len(w)
        for base, x, y in train:
            z = base + sum(wi * xi for wi, xi in zip(w, x))
            err = 1 / (1 + math.exp(-z)) - y
            for i, xi in enumerate(x):
                g[i] += err * xi
        w = [min(MAX_W, max(MIN_W, wi - lr * (gi / len(train) + l2 * (wi - w0i)))) for wi, gi, w0i in zip(w, g, w0)]
    new, old, market = _loss(test, factors, w), _loss(test, factors, w0), _loss(test, factors, [0.0] * len(w))
    out |= {"factors": factors, "held_out": len(test), "logloss_new": round(new, 4), "logloss_current": round(old, 4),
            "logloss_market": round(market, 4), "weights": {f: round(x, 3) for f, x in zip(factors, w)}}
    if new < old and new < market:
        out["accepted"] = True
    else:
        out["reason"] = "new weights did not beat the current weights and the market on held-out games"
    return out


def retrain() -> dict:
    """Refit on every graded leg; save the weights only when they win out of sample."""
    from . import candidates
    from .analyze import load_weights
    current = load_weights()
    result = fit_weights(candidates.graded(), current)
    if result.get("accepted"):
        config.WEIGHTS_FILE.write_text(json.dumps(current | result["weights"], indent=2) + "\n")
    lessons = load_lessons()
    lessons["retrain"] = result | {"market_check": candidates.report()}
    config.LESSONS_FILE.write_text(json.dumps(lessons, indent=2) + "\n")
    return result
