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
    run, curve = 0.0, []
    for b in sorted((b for b in bets if b["status"] in SETTLED), key=lambda b: (b["date"], b["id"])):
        run += b["net"]
        curve.append({"date": b["date"], "id": b["id"], "net": round(run, 2)})
    out["curve"] = curve
    return out


def _market(b):
    kinds = {leg["market"] for leg in b["legs"]}
    return kinds.pop() if len(kinds) == 1 else "mixed"
