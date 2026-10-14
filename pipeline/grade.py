"""Grade pending bets once their games are final. Every leg is graded, so the learning loop knows
which leg sank a slip."""
from . import ledger, sources
from .odds import parlay_american

# Prop stat -> how to read it from a box score. ESPN labels per league; MLB uses the Stats API.
ESPN_STATS = {
    "rec_yds": ("receiving", "YDS"), "receptions": ("receiving", "REC"),
    "rush_yds": ("rushing", "YDS"), "pass_yds": ("passing", "YDS"), "pass_td": ("passing", "TD"),
    "points": (None, "PTS"), "rebounds": (None, "REB"), "assists": (None, "AST"), "threes": (None, "3PT"),
    "shots": (None, "S"), "saves": (None, "SV"), "goals": (None, "G"),
}
MLB_BATTING = {"hits": "hits", "total_bases": "totalBases", "hr": "homeRuns", "rbi": "rbi", "runs": "runs"}


def leg_result(leg: dict, game: dict, stats: dict | None) -> str:
    h, a = game["home_score"], game["away_score"]
    if leg["market"] == "total":
        t = h + a
        return "push" if t == leg["line"] else ("win" if (t > leg["line"]) == (leg["side"] == "over") else "loss")
    if leg["market"] in ("ml", "spread"):
        mine, theirs = (h, a) if _is_home(leg["team"], game) else (a, h)
        margin = mine - theirs + (leg.get("line") or 0)
        return "push" if margin == 0 else ("win" if margin > 0 else "loss")
    if leg["market"] == "prop":
        val = (stats or {}).get(leg["player"], {}).get(leg["stat"])
        if val is None:
            return "unknown"
        return "push" if val == leg["line"] else ("win" if (val > leg["line"]) == (leg["side"] == "over") else "loss")
    return "unknown"


def bet_result(bet: dict) -> str | None:
    rs = [l.get("result") for l in bet["legs"]]
    if "loss" in rs:
        return "loss"
    if any(r in (None, "unknown") for r in rs):
        return None  # wait for the box score
    live = [l for l in bet["legs"] if l["result"] == "win"]
    if not live:
        return "push"
    if len(live) < len(bet["legs"]):  # pushed legs drop out of a parlay
        bet["odds"] = parlay_american([l["odds"] for l in live])
        bet["to_win"] = round(bet["stake"] * (_dec(bet["odds"]) - 1), 2)
    return "win"


def box_stats(game: dict) -> dict:
    """player name -> {stat: value} for every stat we grade."""
    out = {}
    if game["league"] == "MLB" and game.get("mlb_pk"):
        box = sources.mlb_boxscore(game["mlb_pk"])
        for side in ("home", "away"):
            for p in box["teams"][side]["players"].values():
                b = p.get("stats", {}).get("batting", {})
                pit = p.get("stats", {}).get("pitching", {})
                row = {k: b.get(v) for k, v in MLB_BATTING.items() if b.get(v) is not None}
                if b:
                    row["h_r_rbi"] = b.get("hits", 0) + b.get("runs", 0) + b.get("rbi", 0)
                if pit:
                    row["strikeouts"] = pit.get("strikeOuts")
                    row["outs"] = pit.get("outs")
                out[p["person"]["fullName"]] = row
        return out
    summary = sources.espn_summary(game)
    for team in summary.get("boxscore", {}).get("players", []):
        for group in team.get("statistics", []):
            labels = group.get("labels", [])
            for ath in group.get("athletes", []):
                name = ath["athlete"]["displayName"]
                row = out.setdefault(name, {})
                for stat, (grp, label) in ESPN_STATS.items():
                    if (grp is None or group.get("name") == grp) and label in labels:
                        row[stat] = _num(ath["stats"][labels.index(label)])
                if group.get("name") in ("rushing", "receiving") and "TD" in labels:
                    row["anytime_td"] = row.get("anytime_td", 0) + (_num(ath["stats"][labels.index("TD")]) or 0)
    return out


def find_game(bet: dict) -> dict | None:
    abbrs = [x.strip().split(",")[0].split()[0] for x in bet["game"].split("@")]
    for g in sources.espn_scoreboard(bet["league"], bet["date"]):
        if bet.get("event_id") == g["id"] or {g["away"], g["home"]} == set(abbrs):
            if g["league"] == "MLB":  # MLB props grade from the Stats API box score
                for m in sources.mlb_schedule(bet["date"]):
                    t = {m["teams"][s]["team"].get("abbreviation") for s in ("home", "away")}
                    if t == {g["away"], g["home"]}:
                        g["mlb_pk"] = m["gamePk"]
            return g
    return None


def grade_all(bets: list[dict]) -> list[dict]:
    """Settle every pending bet whose game is final. Returns the bets that changed."""
    changed, cache = [], {}
    for bet in [b for b in bets if b["status"] == "pending"]:
        if bet.get("kind") == "traditional":
            if _grade_traditional(bet, cache):
                changed.append(bet)
            continue
        try:
            game = find_game(bet)
            if game and game["completed"] and game["id"] not in cache:
                cache[game["id"]] = box_stats(game) if any(l["market"] == "prop" for l in bet["legs"]) else {}
        except sources.SourceError as e:
            print(f"grade: can't reach the feed for {bet['game']}: {e}")
            continue
        if not game or not game["completed"]:
            continue
        for leg in bet["legs"]:
            if leg.get("result") in (None, "unknown"):
                leg["result"] = leg_result(leg, game, cache[game["id"]])
        status = bet_result(bet)
        if status:
            ledger.settle(bet, status)
            bet["final"] = f"{game['away']} {game['away_score']:g}, {game['home']} {game['home_score']:g}"
            changed.append(bet)
    return changed


def _grade_traditional(bet: dict, cache: dict) -> bool:
    """Multi-game slip: each leg grades against its own game. Settles once every leg is known or one loses."""
    finals = []
    for leg in bet["legs"]:
        if leg.get("result") not in (None, "unknown"):
            continue
        probe = {"league": leg["league"], "date": leg.get("date") or bet["date"], "event_id": leg.get("event_id"),
                 "game": leg.get("matchup", "")}
        try:
            game = find_game(probe)
            if not game or not game["completed"]:
                continue
            if game["id"] not in cache:
                cache[game["id"]] = box_stats(game) if leg["market"] == "prop" else {}
        except sources.SourceError as e:
            print(f"grade: can't reach the feed for {probe['game']}: {e}")
            continue
        leg["result"] = leg_result(leg, game, cache[game["id"]])
        finals.append(f"{game['away']} {game['away_score']:g}-{game['home_score']:g} {game['home']}")
    status = bet_result(bet)
    if status:
        ledger.settle(bet, status)
        bet["final"] = "; ".join(finals) or bet.get("final")
        return True
    return False


def _is_home(team, game):
    t = (team or "").lower()
    return t in (game["home"].lower(), game.get("home_name", "").lower())


def _dec(a):
    return 1 + a / 100 if a > 0 else 1 + 100 / abs(a)


def _num(x):
    try:
        return float(str(x).split("/")[0].split("-")[0])
    except ValueError:
        return None


STALE_NOTES = ("waiting on box score", "likely loss", "likely win", "pending")


def audit(bets: list[dict], refetch: bool = True, days: int = 3) -> list[str]:
    """Re-check every settled bet so the Record tab is exact. Runs after every grade, daily run and export.

    1. Recent bets with an ungraded leg ("unknown") re-read the final box score and fill it in.
    2. Stake and payout are recomputed from units and odds.
    3. The status is recomputed from the legs, and the net from the status.
    4. Notes that still say the result is pending are cleared.
    Returns one line per fix."""
    from datetime import datetime, timedelta
    from . import config
    from .odds import to_win
    fixes, cache = [], {}
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    for bet in bets:
        if bet["status"] not in ledger.SETTLED:
            continue
        if refetch and bet["date"] >= cutoff and any(l.get("result") in (None, "unknown") for l in bet["legs"]):
            for leg in bet["legs"]:
                if leg.get("result") not in (None, "unknown"):
                    continue
                probe = {"league": leg.get("league") or bet["league"], "date": leg.get("date") or bet["date"],
                         "event_id": leg.get("event_id") or bet.get("event_id"), "game": leg.get("matchup") or bet["game"]}
                try:
                    key = probe["event_id"] or f"{probe['league']}|{probe['date']}|{probe['game']}"
                    if key not in cache:
                        g = find_game(probe)
                        if g and g.get("completed"):
                            g["_stats"] = box_stats(g)
                        cache[key] = g if g and g.get("completed") else None
                    g = cache[key]
                    r = leg_result(leg, g, g["_stats"]) if g else "unknown"
                except (sources.SourceError, KeyError, TypeError) as e:
                    print(f"audit: can't re-check {bet['id']} {leg['desc']}: {e}")
                    continue
                if r != "unknown":
                    leg["result"] = r
                    fixes.append(f"{bet['id']}: {leg['desc']} graded {r}")
        stake = round(bet["units"] * config.UNIT, 2)
        if bet.get("stake") != stake:
            fixes.append(f"{bet['id']}: stake {bet.get('stake')} -> {stake}")
            bet["stake"] = stake
        status = bet_result(bet)  # also re-prices a parlay whose legs pushed
        if status is None:
            status = bet["status"]  # a leg is still unknown and none lost: keep the recorded result
        elif status != bet["status"]:
            fixes.append(f"{bet['id']}: status {bet['status']} -> {status}")
        if not any(l.get("result") == "push" for l in bet["legs"]):
            tw = to_win(bet["stake"], bet["odds"])
            if bet.get("to_win") != tw:
                fixes.append(f"{bet['id']}: to_win {bet.get('to_win')} -> {tw}")
                bet["to_win"] = tw
        old = bet.get("net")
        ledger.settle(bet, status)
        if old != bet["net"]:
            fixes.append(f"{bet['id']}: net {old} -> {bet['net']}")
        if any(s in (bet.get("note") or "").lower() for s in STALE_NOTES):
            fixes.append(f"{bet['id']}: cleared note '{bet['note']}'")
            bet["note"] = _result_note(bet)
    return fixes


def _result_note(bet: dict) -> str:
    won = sum(l.get("result") == "win" for l in bet["legs"])
    return f"{won} of {len(bet['legs'])} legs hit"
