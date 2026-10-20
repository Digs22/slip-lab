"""Live bet tracking and phone alerts (ntfy).

    python3 -m pipeline.live          # check open bets once, send alerts for anything new

Each leg gets a live status from the score, the game clock and the box score:
  won / lost (already decided), on track / behind (by pace), or waiting.
Alerts go to the ntfy topic in NTFY_TOPIC (env) or .secrets/ntfy_topic, and only for what the user
watches: tapping the bell on a game or slip in the app posts {"key", "on"} to the private topic
"<topic>-watch", which this reads. Keys: "game:<espn id>" (a game's SGP plays), "trad:<date>" (that
day's Traditional slip), "bet:<id>" (one bet, used for Pick of the Day). For a watched pick:
  the market line moves (checked every run, between pick refreshes), a refresh changes the pick, its
  odds or a player's injury status, a pick is dropped or added, the picks lock an hour before the game,
  a leg falls behind, is lost or hits, and the bet is graded.
A small state file remembers what was already sent, so nothing is sent twice.
NTFY_TOPIC may list several topics, separated by commas or new lines (the owner's first, then friends'):
each topic gets its own bells, alerts and state file (data/live_state.json for the first one,
data/live_state-<hash>.json for the rest).
"""
import hashlib
import json
import re
import urllib.request
from datetime import datetime, timezone

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


_TOPIC = ""  # the topic this pass is sending to, set by run()


def topics() -> list[str]:
    return [t for t in re.split(r"[,\s]+", config._secret("NTFY_TOPIC")) if t]


def notify_topic() -> str:
    return _TOPIC or next(iter(topics()), "")


def state_file(topic: str, first: bool):
    return STATE_FILE if first else config.DATA / f"live_state-{hashlib.sha256(topic.encode()).hexdigest()[:10]}.json"


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


def watch_keys(b: dict) -> list[str]:
    keys = [f"bet:{b['id']}"]
    if b.get("kind") == "traditional":
        keys.append(f"trad:{b['date']}")
    elif b.get("kind") != "potd" and b.get("event_id"):
        keys.append(f"game:{b['event_id']}")
    return keys


def read_watches(state: dict) -> dict:
    """Apply the bell taps posted to <topic>-watch (ntfy keeps them about 12 hours; the state file keeps the rest)."""
    topic = notify_topic()
    if not topic:
        return state.get("watch", {})
    watch = dict(state.get("watch", {}))
    try:
        with urllib.request.urlopen(urllib.request.Request(f"https://ntfy.sh/{topic}-watch/json?poll=1&since=all",
                                                           headers=sources.UA), timeout=15) as r:
            msgs = [json.loads(x) for x in r.read().decode().splitlines() if x.strip()]
    except Exception as e:
        print(f"watch list: {e}")
        return watch
    for m in sorted((m for m in msgs if m.get("event") == "message"), key=lambda m: m.get("time", 0)):
        try:
            w = json.loads(m.get("message") or "{}")
        except ValueError:
            continue
        if isinstance(w, dict) and isinstance(w.get("key"), str):
            if w.get("on"):
                watch[w["key"]] = w.get("label") or w["key"]
            else:
                watch.pop(w["key"], None)
    return watch


def _legs_desc(b):
    return " + ".join(l["desc"] for l in b["legs"])


def _snap(b):
    return {"odds": b["odds"], "legs": [l["desc"] for l in b["legs"]], "play": b.get("play"), "game": b["game"],
            "status": {l["desc"]: l.get("status") for l in b["legs"] if l.get("status")}}


def _line_nums(o: dict) -> dict:
    """Spread as a number for the favorite (e.g. 'LAD -1.5' -> -1.5), total, moneyline win chance."""
    out = {}
    m = re.search(r"([+-]?\d+(?:\.\d+)?)\s*$", o.get("spread") or "")
    if m:
        out["spread"] = float(m.group(1))
        out["fav"] = (o.get("spread") or "").split()[0]
    if o.get("total") is not None:
        out["total"] = float(o["total"])
    if o.get("home_ml") is not None:
        out["home_ml"] = o["home_ml"]
    return out


def _implied(odds):
    return 100 / (odds + 100) if odds > 0 else -odds / (-odds + 100)


def _games_of(b):
    """(lookup bet, legs) per game a bet touches; a Traditional slip spans several games."""
    if b.get("kind") not in ("traditional", "longshot"):
        return [(b, b["legs"])]
    by = {}
    for l in b["legs"]:
        by.setdefault(l.get("event_id"), []).append(l)
    return [({"league": ls[0].get("league"), "date": ls[0].get("date") or b["date"], "game": ls[0].get("matchup", ""),
              "event_id": eid}, ls) for eid, ls in by.items()]


def run():
    global _TOPIC
    all_topics = topics() or [""]
    for i, t in enumerate(all_topics):
        _TOPIC = t
        try:
            run_one(state_file(t, i == 0))
        finally:
            _TOPIC = ""


def run_one(path):
    from .run import locked, _preview_odds
    bets = ledger.load()
    try:
        state = json.loads(path.read_text())
    except FileNotFoundError:
        state = {}
    for k, v in {"seen": [], "legs": {}, "graded": [], "snap": {}, "lines": {}, "locked": [], "watch": {}}.items():
        state.setdefault(k, v)
    first_run = not state["seen"]
    state["watch"] = watch = read_watches(state)
    watched = lambda b: any(k in watch for k in watch_keys(b))
    send = (lambda *a, **k: None) if first_run else notify
    ids = {b["id"] for b in bets}

    # 1. Refresh changes to watched picks: new, dropped, re-picked, odds, injury status
    for b in bets:
        if b["status"] != "pending" or not watched(b):
            state["snap"].pop(b["id"], None)
            continue
        old, new = state["snap"].get(b["id"]), _snap(b)
        state["snap"][b["id"]] = new
        name = f"{b['play'].title()} · {b['game']}"
        if old is None:
            if b["id"] not in state["seen"]:
                send(f"New play: {name}", f"{_legs_desc(b)} ({b['odds']:+d})", "new")
            continue
        if old["legs"] != new["legs"]:
            send(f"Pick changed: {name}", f"Was {' + '.join(old['legs'])} ({old['odds']:+d}). Now {_legs_desc(b)} ({b['odds']:+d}).", "arrows_counterclockwise")
        elif abs(_implied(old["odds"]) - _implied(b["odds"])) >= 0.02:
            send(f"Odds moved: {name}", f"{_legs_desc(b)}: {old['odds']:+d} to {b['odds']:+d}.", "chart_with_upwards_trend")
        for desc, st in new["status"].items():
            if old["status"].get(desc) not in (None, st):
                send(f"Injury news: {desc}", f"{b['game']}: {old['status'][desc]} to {st}.", "ambulance", "high")
    for bid in [i for i in state["snap"] if i not in ids]:
        old = state["snap"].pop(bid)
        send(f"Pick dropped: {old.get('play', '').title()} · {old['game']}",
             f"{' + '.join(old['legs'])} no longer has an edge after the latest refresh.", "wastebasket")
    state["seen"] = sorted(ids)

    # 2. Picks lock an hour before the game: one "final" alert per watched game or slip
    final = {}
    for b in bets:
        if b["status"] == "pending" and watched(b) and b.get("start") and b["id"] not in state["locked"] and locked(b["start"]) \
                and datetime.now(timezone.utc) < datetime.fromisoformat(b["start"].replace("Z", "+00:00")):
            state["locked"].append(b["id"])
            final.setdefault(b["game"], []).append(b)
    for game, bs in final.items():
        send(f"Picks final: {game}", "\n".join(f"{b['play'].title()}: {_legs_desc(b)} ({b['odds']:+d})" for b in bs), "lock")

    # 3. Graded bets
    for b in bets:
        if b["status"] in ("win", "loss", "push") and b["id"] not in state["graded"]:
            state["graded"].append(b["id"])
            if watched(b):
                send(f"{b['play'].title()} {b['status'].upper()}: {b['game']}",
                     f"{_legs_desc(b)}. Net {b['net']:+.2f}. " + _record(bets),
                     "white_check_mark" if b["status"] == "win" else "x")

    # 4. Market lines between refreshes, and live legs, for watched open bets the user placed
    # (user, 2026-10-20: no live lookups for games without a placed bet)
    games = {}
    for b in [b for b in bets if b["status"] == "pending" and watched(b) and b.get("placed")]:
        for probe, legs in _games_of(b):
            try:
                key = probe.get("event_id") or probe["game"]
                g = games[key] if key in games else grade.find_game(probe)
                games[key] = g
            except (sources.SourceError, KeyError, IndexError) as e:
                print(f"live: {e}")
                continue
            if not g:
                continue
            if g["status"] == "pre":
                now = _line_nums(_preview_odds(g.get("odds") or {}))
                lk = g["id"]
                before = state["lines"].get(lk)
                state["lines"][lk] = now
                if before and now:
                    moves = []
                    if "spread" in now and "spread" in before and (abs(now["spread"] - before["spread"]) >= 0.5 or now.get("fav") != before.get("fav")):
                        moves.append(f"spread {before.get('fav', '')} {before['spread']:+g} to {now.get('fav', '')} {now['spread']:+g}")
                    if "total" in now and "total" in before and abs(now["total"] - before["total"]) >= 0.5:
                        moves.append(f"total {before['total']:g} to {now['total']:g}")
                    if "home_ml" in now and "home_ml" in before and abs(_implied(now["home_ml"]) - _implied(before["home_ml"])) >= 0.03:
                        moves.append(f"{g['home']} moneyline {before['home_ml']:+d} to {now['home_ml']:+d}")
                    if moves:
                        send(f"Line moved: {g['away']} @ {g['home']}",
                             f"{'; '.join(moves).capitalize()}. Your picks: {'; '.join(l['desc'] for l in legs)}.", "chart_with_upwards_trend")
                continue
            stats = g.get("_stats")
            if stats is None and any(l["market"] == "prop" for l in legs):
                try:
                    stats = g["_stats"] = grade.box_stats(g)
                except sources.SourceError:
                    stats = {}
            for leg in legs:
                key = f"{b['id']}|{leg['desc']}"
                st = leg_status(leg, g, stats)
                before = state["legs"].get(key)
                state["legs"][key] = st["state"]
                if before == st["state"] or st["state"] in ("waiting", "on_track"):
                    continue
                where = f"{g['away']} @ {g['home']} {g.get('detail', '')}"
                if st["state"] == "behind" and before == "on_track":
                    send(f"Behind: {leg['desc']}", f"{where}. Now {st.get('now')}, pace {st.get('pace', '-')}.", "warning")
                elif st["state"] == "lost":
                    send(f"Leg lost: {leg['desc']}", f"{where}. {b['play'].title()} slip is done.", "x")
                elif st["state"] == "won" and before not in (None, "won"):
                    send(f"Leg hit: {leg['desc']}", f"{where}.", "tada")
    path.write_text(json.dumps(state, indent=1) + "\n")


def _record(bets):
    s = ledger.summary(bets)["all"]
    return f"Record {s['w']}-{s['l']}-{s['p']}, {s['net']:+.2f} ({s['roi']:+.1f}% ROI)."


if __name__ == "__main__":
    run()
