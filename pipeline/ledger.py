"""The bet record: every pick, logged as placed. Totals, ROI, splits and the P&L curve."""
import json
from collections import defaultdict

from . import config
from .odds import to_win

SETTLED = ("win", "loss", "push")


def load(path=config.BETS_FILE) -> list[dict]:
    try:
        return json.loads(path.read_text())["bets"]
    except FileNotFoundError:
        return []


def save(bets: list[dict], path=config.BETS_FILE) -> None:
    path.write_text(json.dumps({"bets": bets}, indent=2) + "\n")


def add(bets: list[dict], bet: dict) -> dict:
    bet.setdefault("status", "pending")
    bet.setdefault("net", None)
    bet["stake"] = round(bet["units"] * config.UNIT, 2)
    bet["to_win"] = to_win(bet["stake"], bet["odds"])
    if any(b["id"] == bet["id"] for b in bets):
        raise ValueError(f"duplicate bet id {bet['id']}")
    bets.append(bet)
    return bet


def settle(bet: dict, status: str) -> dict:
    bet["status"] = status
    bet["net"] = {"win": bet["to_win"], "loss": -bet["stake"], "push": 0.0}[status]
    return bet


def _totals(bets):
    s = [b for b in bets if b["status"] in SETTLED]
    risked = sum(b["stake"] for b in s if b["status"] != "push")
    net = round(sum(b["net"] for b in s), 2)
    return {
        "w": sum(b["status"] == "win" for b in s),
        "l": sum(b["status"] == "loss" for b in s),
        "p": sum(b["status"] == "push" for b in s),
        "pending": sum(b["status"] == "pending" for b in bets),
        "risked": round(risked, 2),
        "net": net,
        "units": round(net / config.UNIT, 2),
        "roi": round(net / risked * 100, 1) if risked else 0.0,
    }


def summary(bets: list[dict]) -> dict:
    out = {"all": _totals(bets)}
    for key in ("league", "play", "market"):
        groups = defaultdict(list)
        for b in bets:
            groups[b.get(key) or _market(b)].append(b)
        out[f"by_{key}"] = {k: _totals(v) for k, v in sorted(groups.items())}
    kinds = defaultdict(list)
    for b in bets:
        kinds[b.get("kind") or "sgp"].append(b)
    out["by_kind"] = {k: _totals(v) for k, v in sorted(kinds.items())}
    run, curve = 0.0, []
    for b in sorted((b for b in bets if b["status"] in SETTLED), key=lambda b: (b["date"], b["id"])):
        run += b["net"]
        curve.append({"date": b["date"], "id": b["id"], "net": round(run, 2)})
    out["curve"] = curve
    out["calibration"] = calibration(bets)
    out["clv"] = clv(bets)
    return out


def calibration(bets: list[dict]) -> dict:
    """Do our win chances come true? Legs grouped by the probability we gave them, plus a Brier score
    (0 = perfect, 0.25 = coin-flip guessing)."""
    pts = [(l["prob"], l["result"] == "win") for b in bets for l in b["legs"]
           if l.get("prob") is not None and l.get("result") in ("win", "loss")]
    bins = []
    for lo in (0.3, 0.4, 0.5, 0.6, 0.7):
        grp = [(p, w) for p, w in pts if lo <= p < lo + 0.1 or (lo == 0.3 and p < 0.3) or (lo == 0.7 and p >= 0.8)]
        if grp:
            bins.append({"from": lo, "to": lo + 0.1, "n": len(grp), "said": round(sum(p for p, _ in grp) / len(grp), 3),
                         "hit": round(sum(w for _, w in grp) / len(grp), 3)})
    brier = round(sum((p - w) ** 2 for p, w in pts) / len(pts), 4) if pts else None
    return {"legs": len(pts), "brier": brier, "bins": bins}


def clv(bets: list[dict]) -> dict:
    """Closing line value: how much better our price was than the last line before the game.
    Positive on average = we're beating the market, the best early sign of a real edge."""
    from .odds import implied
    diffs = [implied(l["closing_odds"]) - implied(l["odds"]) for b in bets for l in b["legs"]
             if l.get("closing_odds") is not None and l.get("odds") is not None]
    if not diffs:
        return {"legs": 0, "avg_pct": None, "beat_close": None}
    return {"legs": len(diffs), "avg_pct": round(100 * sum(diffs) / len(diffs), 2),
            "beat_close": round(sum(d > 0 for d in diffs) / len(diffs), 3)}


def _market(b):
    kinds = {leg["market"] for leg in b["legs"]}
    return kinds.pop() if len(kinds) == 1 else "mixed"
