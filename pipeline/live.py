"""Live bet tracking and phone alerts (ntfy).

    python3 -m pipeline.live          # check open bets once, send alerts for anything new

Each leg gets a live status from the score, the game clock and the box score:
  won / lost (already decided), on track / behind (by pace), or waiting.
Alerts go to the ntfy topic in NTFY_TOPIC (env) or .secrets/ntfy_topic:
  picks posted, a leg flips from on track to behind, a leg is lost, a bet is graded.
A small state file remembers what was already sent, so nothing is sent twice.
"""
import json
import re
import urllib.request

from . import config, grade, ledger, sources

STATE_FILE = config.DATA / "live_state.json"
# league -> (regulation periods, minutes per period); MLB counts innings, soccer one 90-minute clock
REGULATION = {"NFL": (4, 15), "NCAAF": (4, 15), "NBA": (4, 12), "NCAAB": (2, 20), "NHL": (3, 20),
              "MLB": (9, None), "SOCCER": (1, 90)}


def elapsed(game: dict) -> float:
    """Share of regulation played, 0 to 1."""
    if game.get("completed"):
        return 1.0
    if game.get("status") != "in":
        return 0.0
    periods, minutes = REGULATION.get(game["league"], (4, 15))
    p = max(1, int(game.get("period") or 1))
    if game["league"] == "MLB":
        half = 0.75 if re.search(r"bot|end", game.get("detail", ""), re.I) else 0.25
        return min(1.0, (p - 1 + half) / periods)
    if game["league"] == "SOCCER":
        m = re.match(r"(\d+)", game.get("clock") or "0")
        return min(1.0, int(m.group(1)) / 90 if m else 0.5)
    mm, _, ss = (game.get("clock") or "0:00").partition(":")
    try:
        left = int(mm) + int(ss or 0) / 60
    except ValueError:
        left = minutes / 2
    return min(1.0, ((p - 1) * minutes + (minutes - left)) / (periods * minutes))


def leg_status(leg: dict, game: dict, stats: dict | None) -> dict:
    """{'state': won|lost|on_track|behind|waiting, 'now': current value, 'pace': projected final}."""
    if game.get("status") == "pre":
        return {"state": "waiting"}
    if game.get("completed"):
        r = grade.leg_result(leg, game, stats)
        return {"state": {"win": "won", "loss": "lost", "push": "push"}.get(r, "waiting")}
    f = max(elapsed(game), 0.05)
    h, a = game["home_score"] or 0, game["away_score"] or 0
    if leg["market"] == "total":
        now = h + a
        over = leg["side"] == "over"
        if over and now > leg["line"]:
            return {"state": "won", "now": now}
        if not over and now > leg["line"]:
            return {"state": "lost", "now": now}
        pace = now / f
        good = pace > leg["line"] if over else pace < leg["line"]
        return {"state": "on_track" if good else "behind", "now": now, "pace": round(pace, 1)}
    if leg["market"] in ("ml", "spread"):
        mine, theirs = (h, a) if grade._is_home(leg.get("team"), game) else (a, h)
        margin = mine - theirs + (leg.get("line") or 0)
        return {"state": "on_track" if margin > 0 else "behind", "now": mine - theirs}
    if leg["market"] == "prop":
        val = (stats or {}).get(leg.get("player"), {}).get(leg.get("stat"))
        if val is None:
            return {"state": "waiting"}
        over = leg["side"] == "over"
        if val > leg["line"]:
            return {"state": "won" if over else "lost", "now": val}
        pace = val / f
        good = pace > leg["line"] if over else pace < leg["line"]
        return {"state": "on_track" if good else "behind", "now": val, "pace": round(pace, 1)}
    return {"state": "waiting"}


def notify_topic() -> str:
    return config._secret("NTFY_TOPIC")


def notify(title: str, message: str, tags: str = "", priority: str = "default"):
    topic = notify_topic()
    if not topic:
        print(f"(no NTFY_TOPIC) {title}: {message}")
        return
    req = urllib.request.Request(f"https://ntfy.sh/{topic}", data=message.encode(), method="POST",
                                 headers={"Title": title.encode("ascii", "ignore").decode(), "Tags": tags, "Priority": priority})
    try:
        urllib.request.urlopen(req, timeout=15).read()
    except Exception as e:
        print(f"ntfy failed: {e}")


def run():
    bets = ledger.load()
    try:
        state = json.loads(STATE_FILE.read_text())
    except FileNotFoundError:
        state = {"seen": [], "legs": {}, "graded": []}
    first_run = not state["seen"]

    # 1. New picks posted
    new = [b for b in bets if b["id"] not in state["seen"]]
    if new and not first_run:
        by_game = {}
        for b in new:
            by_game.setdefault(b["game"], []).append(b)
        for game, bs in by_game.items():
            lines = [f"{b['play'].title()}: {' + '.join(l['desc'] for l in b['legs'])} ({b['odds']:+d})" for b in bs]
            notify(f"New picks: {game}", "\n".join(lines), "money_with_wings")
    state["seen"] = sorted({b["id"] for b in bets})

    # 2. Graded bets
    for b in bets:
        if b["status"] in ("win", "loss", "push") and b["id"] not in state["graded"]:
            state["graded"].append(b["id"])
            if not first_run:
                notify(f"{b['play'].title()} {b['status'].upper()}: {b['game']}",
                       f"{' + '.join(l['desc'] for l in b['legs'])}. Net {b['net']:+.2f}. " + _record(bets),
                       "white_check_mark" if b["status"] == "win" else "x")

    # 3. Live legs on open bets
    games = {}
    for b in [b for b in bets if b["status"] == "pending"]:
        try:
            g = games.get(b["game"]) or grade.find_game(b)
        except sources.SourceError as e:
            print(f"live: {e}")
            continue
        if not g or g["status"] == "pre":
            continue
        games[b["game"]] = g
        stats = g.get("_stats")
        if stats is None and any(l["market"] == "prop" for l in b["legs"]):
            try:
                stats = g["_stats"] = grade.box_stats(g)
            except sources.SourceError:
                stats = {}
        for leg in b["legs"]:
            key = f"{b['id']}|{leg['desc']}"
            st = leg_status(leg, g, stats)
            before = state["legs"].get(key)
            state["legs"][key] = st["state"]
            if before == st["state"] or st["state"] in ("waiting", "on_track"):
                continue
            if st["state"] == "behind" and before == "on_track":
                notify(f"Behind: {leg['desc']}", f"{b['game']} {g.get('detail', '')}. Now {st.get('now')}, pace {st.get('pace', '-')}.",
                       "warning")
            elif st["state"] == "lost":
                notify(f"Leg lost: {leg['desc']}", f"{b['game']} {g.get('detail', '')}. {b['play'].title()} slip is done.", "x")
            elif st["state"] == "won" and before not in (None, "won"):
                notify(f"Leg hit: {leg['desc']}", f"{b['game']} {g.get('detail', '')}.", "tada")
    STATE_FILE.write_text(json.dumps(state, indent=1) + "\n")


def _record(bets):
    s = ledger.summary(bets)["all"]
    return f"Record {s['w']}-{s['l']}-{s['p']}, {s['net']:+.2f} ({s['roi']:+.1f}% ROI)."


if __name__ == "__main__":
    run()
