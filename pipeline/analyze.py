"""Steps 2-7: price every market, gather factor signals, and score each leg's edge.

A signal is a probability (0-1) that the leg wins, from one factor. The model starts from the
no-vig market price and moves toward each signal in log-odds space, by that factor's weight.
Weights live in data/weights.json and the learning loop adjusts them after every graded game.

Quant signals come from the free feeds. Qualitative ones (coaching, scheme, news, postseason
history, matchup history from Sports-Reference) come from the analyst file Claude writes for each
game at data/analyst/<date>-<game id>.json, following the betting-analysis skill.
"""
import json
import math
from statistics import mean

from . import config, sources
from .odds import edge, implied, no_vig, to_american

FACTORS = ["projection", "recent_form", "season", "matchup", "home_away", "rest_travel", "weather",
           "injuries", "coaching", "postseason", "news", "line_move"]
DEFAULT_WEIGHTS = {f: 0.5 for f in FACTORS} | {"projection": 1.0, "recent_form": 0.8, "injuries": 0.7, "postseason": 0.3}


def load_weights() -> dict:
    try:
        return DEFAULT_WEIGHTS | json.loads(config.WEIGHTS_FILE.read_text())
    except FileNotFoundError:
        return dict(DEFAULT_WEIGHTS)


def logit(p):
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def model_prob(fair: float, signals: dict, weights: dict) -> float:
    """Blend the fair market price with factor signals. Each signal's pull is capped by its weight / 4,
    so no one factor can move the price more than a fraction of the gap."""
    base = logit(fair)
    shift = sum(weights.get(f, 0) / 4 * (logit(p) - base) for f, p in signals.items() if p is not None)
    p = 1 / (1 + math.exp(-(base + shift)))
    # Signals mostly come from one write-up, so they aren't independent, and summing them let the model drift
    # 15-18 points off the market (Snell u14.5 outs: market 40%, model 58%, lost). The market beats the model
    # so far, so never move more than config.MAX_MODEL_SHIFT away from it.
    return min(max(p, fair - config.MAX_MODEL_SHIFT), fair + config.MAX_MODEL_SHIFT)


def shrunk_rate(hits: int, n: int, prior: float = 0.5, k: int = 5) -> float:
    """Hit rate pulled toward the prior so a 3-for-3 sample doesn't read as 100%."""
    return (hits + prior * k) / (n + k)


# ---------- markets ----------

def best_lines(odds_events: list[dict], home: str, away: str) -> list[dict]:
    """Best price per side across every book, plus the no-vig consensus probability."""
    ev = next((e for e in odds_events if _match(e, home, away)), None)
    if not ev:
        return []
    book = {}
    for bm in ev.get("bookmakers", []):
        for m in bm["markets"]:
            for o in m["outcomes"]:
                k = (m["key"], o["name"], o.get("point"))
                book.setdefault(k, []).append((o["price"], bm["title"]))
    legs = []
    for (mkt, name, point), prices in book.items():
        other = [v for (m2, n2, p2), v in book.items() if m2 == mkt and n2 != name
                 and (p2 == point or (mkt == "spreads" and p2 == -(point or 0)))]
        if not other:
            continue
        # Average in probability space: the mean of -105 and +105 in American odds is 0, not even money.
        a = to_american(1 / mean(implied(p) for p, _ in prices))
        b = to_american(1 / mean(implied(p) for p, _ in other[0]))
        fair = no_vig(a, b)[0]
        if mkt == "h2h":  # soccer moneylines are 3-way: de-vig against every other result, draw included
            rest = [v for (m2, n2, _), v in book.items() if m2 == "h2h" and n2 != name]
            if len(rest) > 1:
                pa = mean(implied(x) for x, _ in prices)
                fair = pa / (pa + sum(mean(implied(x) for x, _ in v) for v in rest))
        price, title = max(prices, key=lambda x: x[0])
        legs.append({
            "market": {"h2h": "ml", "spreads": "spread", "totals": "total"}[mkt],
            "side": name.lower() if mkt == "totals" else None,
            "team": None if mkt == "totals" else name, "line": point,
            "odds": price, "book": title, "consensus": round(a), "fair": round(fair, 4),
            "books": len(prices),
            "desc": f"{name} {point:+g}" if mkt == "spreads" else (f"{name} {point:g}" if mkt == "totals" else f"{name} ML"),
        })
    return legs


def extra_game_lines(game: dict, odds_events: list[dict]) -> list[dict]:
    """Team totals and first-half lines for one game: best price per side, no-vig fair from the book average."""
    ev = next((e for e in odds_events if _match(e, game["home_name"], game["away_name"])), None)
    if not ev:
        return []
    try:
        data = sources.player_props(game["league"], ev["id"], config.EXTRA_GAME_MARKETS)
    except sources.SourceError:
        return []
    book = {}
    for bm in data.get("bookmakers", []):
        for m in bm["markets"]:
            for o in m["outcomes"]:
                book.setdefault((m["key"], o.get("description") or "", o["name"], o.get("point")), []).append((o["price"], bm["title"]))
    legs = []
    for (mkt, team, name, point), prices in book.items():
        if mkt in ("team_totals", "totals_h1"):
            other = book.get((mkt, team, "Under" if name == "Over" else "Over", point))
        else:  # h2h_h1, spreads_h1: the other team, opposite point
            other = next((v for (m2, t2, n2, p2), v in book.items() if m2 == mkt and n2 != name
                          and (mkt == "h2h_h1" or p2 == -(point or 0))), None)
        if not other:
            continue
        a = to_american(1 / mean(implied(p) for p, _ in prices))
        b = to_american(1 / mean(implied(p) for p, _ in other))
        price, title = max(prices, key=lambda x: x[0])
        if mkt == "team_totals":
            leg = {"market": "team_total", "side": name.lower(), "team": team, "desc": f"{team} team total {name} {point:g}"}
        elif mkt == "totals_h1":
            leg = {"market": "h1_total", "side": name.lower(), "team": None, "desc": f"1H {name} {point:g}"}
        elif mkt == "spreads_h1":
            leg = {"market": "h1_spread", "side": None, "team": name, "desc": f"1H {name} {point:+g}"}
        else:
            leg = {"market": "h1_ml", "side": None, "team": name, "desc": f"1H {name} ML"}
        legs.append(leg | {"line": point, "odds": price, "book": title, "consensus": round(a),
                           "fair": round(no_vig(a, b)[0], 4), "books": len(prices)})
    return legs


def espn_consensus(game: dict) -> list[dict]:
    """Fallback when there is no Odds API key: ESPN's one posted line, no best-price check."""
    o = game.get("odds") or {}
    legs = []
    if o.get("overUnder"):
        ou = o["overUnder"]
        for side, key in (("over", "overOdds"), ("under", "underOdds")):
            price = o.get(key) or -110
            legs.append({"market": "total", "side": side, "line": ou, "odds": price, "book": "ESPN consensus",
                         "fair": round(implied(price) / (implied(o.get("overOdds") or -110) + implied(o.get("underOdds") or -110)), 4),
                         "desc": f"{side.title()} {ou:g}", "books": 1})
    return legs


# ---------- signals ----------

def total_signals(game: dict, leg: dict, summary: dict) -> dict:
    """Recent scoring of both teams vs the posted total, and weather for outdoor games."""
    sig = {}
    try:
        recent = []
        for side in summary.get("lastFiveGames", []):
            for e in side.get("events", []):
                s = e.get("score", "")
                if "-" in s:
                    recent.append(sum(float(x) for x in s.split("-")))
        if recent:
            over_hits = sum(t > leg["line"] for t in recent)
            p_over = shrunk_rate(over_hits, len(recent))
            sig["recent_form"] = p_over if leg["side"] == "over" else 1 - p_over
    except (TypeError, ValueError):
        pass
    from .venues import coords
    where = (game.get("lat"), game.get("lon")) if game.get("lat") else coords(game["league"], game["home"])
    if game.get("indoor") is False and where:
        try:
            w = sources.weather(where[0], where[1], game["start"])
        except sources.SourceError:
            return sig
        wind, rain = w.get("wind_speed_10m") or 0, w.get("precipitation_probability") or 0
        game["weather"] = f"{round(w.get('temperature_2m') or 0)}°F, wind {round(wind)} mph, {round(rain)}% rain"
        if game["league"] in ("MLB", "NFL", "NCAAF") and (wind >= 15 or rain >= 60):
            sig["weather"] = 0.56 if leg["side"] == "under" else 0.44
    return sig


def injury_signals(game: dict, leg: dict, injuries: list[dict]) -> dict:
    out = [i for i in injuries if (i.get("status") or "").lower() in ("out", "doubtful")]
    if leg.get("player") and any(i["player"] == leg["player"] for i in injuries):
        return {"injuries": 0.35 if leg.get("side") == "over" else 0.6}
    return {"injuries": 0.5} if not out else {}


def injury_status(leg: dict, injuries: list[dict]) -> str | None:
    """The player's line on the league injury report, for the app's "why" panel."""
    i = next((i for i in injuries if leg.get("player") and i["player"] == leg["player"]), None)
    return f"{i['status']}: {i['detail']}" if i and i.get("detail") else (i or {}).get("status")


NFL_COLS = {"pass_yds": "passing_yards", "rec_yds": "receiving_yards", "rush_yds": "rushing_yards",
            "receptions": "receptions", "pass_td": "passing_tds"}


def projection_signal(game: dict, leg: dict) -> dict:
    """Project the stat itself and price the line from that distribution.
    Inputs, in order of preference: the analyst file's projection {mu, sd}, its game log, pitcher K
    inputs, then automatic feeds (nflverse game logs for NFL, Savant K% for MLB strikeouts)."""
    from . import project, stats_feeds
    pj = leg.get("projection") or {}
    try:
        if "mu" in pj:
            return {"projection": project.prop_prob(leg["stat"], leg["side"], leg["line"], pj["mu"], pj.get("sd"))}
        if pj.get("log"):
            return {"projection": project.from_game_log(leg["stat"], leg["side"], leg["line"], pj["log"], pj.get("season_avg"))}
        if {"pitcher_k", "opp_k", "batters_faced"} <= pj.keys():
            mu = project.project_pitcher_ks(pj["pitcher_k"], pj["opp_k"], pj["batters_faced"])
            return {"projection": project.prop_prob("strikeouts", leg["side"], leg["line"], mu)}
        season = int(game["start"][:4])
        if game["league"] == "NFL" and leg.get("stat") in NFL_COLS:
            log = [float(r[NFL_COLS[leg["stat"]]] or 0) for r in stats_feeds.nfl_player_log(season, leg["player"])]
            p = project.from_game_log(leg["stat"], leg["side"], leg["line"], log)
            return {"projection": p} if p is not None else {}
        if game["league"] == "MLB" and leg.get("stat") == "strikeouts" and pj.get("opp_k"):
            sv = stats_feeds.savant(season, "pitcher").get(leg["player"])
            if sv and isinstance(sv.get("k_percent"), float):
                mu = project.project_pitcher_ks(sv["k_percent"] / 100, pj["opp_k"], pj.get("batters_faced", 22))
                return {"projection": project.prop_prob("strikeouts", leg["side"], leg["line"], mu)}
    except (sources.SourceError, KeyError, ValueError, ZeroDivisionError):
        pass
    return {}


def analyst_file(date: str, game: dict) -> dict:
    p = config.DATA / "analyst" / f"{date}-{game['id']}.json"
    try:
        return json.loads(p.read_text())
    except FileNotFoundError:
        return {}


def score_game(date: str, game: dict, odds_events: list[dict] | None, injuries: list[dict], weights: dict) -> dict:
    legs = best_lines(odds_events or [], game["home_name"], game["away_name"]) if odds_events else []
    if not legs:  # no Odds API market for this game: fall back to ESPN's consensus line
        legs = espn_consensus(game)
    try:
        summary = sources.espn_summary(game)
    except sources.SourceError:
        summary = {}
    analyst = analyst_file(date, game)
    if analyst and odds_events and game["league"] in config.NO_PLAYER_PROPS:
        legs += extra_game_lines(game, odds_events)
    legs += analyst.get("extra_legs", [])  # props priced by hand from public odds pages
    tp = analyst.get("totals_projection") or {}
    if game["league"] == "NBA" and tp.get("total") and tp.get("line"):  # lesson 2026-10-21/22: the totals model is 6-0 when 4+ off the line
        gap = tp["total"] - tp["line"]
        side = "over" if gap > 0 else "under"
        if abs(gap) >= 4:
            legs = [l | {"prefer_straight": True} if l.get("market") == "total" and l.get("side") == side else l for l in legs]
    if game["league"] in config.NO_PLAYER_PROPS:
        legs = [l for l in legs if l["market"] != "prop"]  # not bettable in the user's state
    overrides = {a["desc"]: a for a in analyst.get("legs", [])}
    scored, evaluated = [], []
    for leg in legs:
        sig = {}
        if leg["market"] == "total":
            sig |= total_signals(game, leg, summary)
        sig |= injury_signals(game, leg, injuries)
        if leg["market"] == "prop":
            sig |= projection_signal(game, leg)
        s = overrides.get(leg["desc"], {}).get("signals", {})
        if isinstance(s, dict): sig |= s
        if isinstance(leg.get("signals"), dict): sig |= leg["signals"]
        p = model_prob(leg["fair"], sig, weights)
        ov = overrides.get(leg["desc"], {})
        leg = leg | {"game": game["id"], "signals": sig, "prob": round(p, 4), "edge": round(edge(p, leg["odds"]), 4),
                     "note": ov.get("note", leg.get("note")),
                     "status": ov.get("status", leg.get("status")) or injury_status(leg, injuries)}
        evaluated.append(leg)
        # In a researched game only the legs the research argued for can be picked: the automatic signals
        # once put "Over 43.5" on a game whose write-up backed the Under (2026-10-17, WIS @ UCLA).
        if leg["edge"] >= config.MIN_EDGE and (leg["note"] or not overrides):
            scored.append(leg)
    scored.sort(key=lambda l: l["edge"], reverse=True)
    return {"game": game, "legs": scored, "provisional": not analyst.get("lineups_confirmed", False),
            "low_total": any(l["market"] == "total" and l["line"] and l["line"] <= _low(game["league"]) for l in legs),
            "score": round(sum(l["edge"] * l["prob"] for l in scored[:4]), 4), "analyst": analyst.get("summary"),
            "evaluated": evaluated}  # every leg priced, picked or not: training data for the learning loop


def _low(league):
    return {"MLB": 7.5, "NFL": 41.5, "NHL": 5.5, "NBA": 215, "SOCCER": 2.5, "NCAAF": 44.5, "NCAAB": 135}[league]


def _match(ev, home, away):
    """Odds API uses full names ("Seattle Seahawks"), the same as ESPN's displayName."""
    return ev.get("home_team", "").lower() == home.lower() and ev.get("away_team", "").lower() == away.lower()
