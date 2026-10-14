"""Step 8: turn a game's scored legs into the four plays, and pick the day's top games.

Rules (user's, plus lessons from graded games):
- No leg in more than 2 of a game's 4 plays.
- Safer parlay = two strong, independent legs; no team side from a near coin-flip game.
- Low-total games anchor the straight bet on the Under or a pitcher/goalie prop.
- Longshot must pay +1000 or longer.
- Across the day, a team or player shows up in at most 2 games' plays.
"""
from collections import Counter
from itertools import combinations
from math import prod

from . import config
from .odds import parlay_american, to_decimal
from .project import simulate_parlay

COIN_FLIP = 0.06  # |fair - 0.5| below this is a coin flip


def _key(leg):
    return leg["desc"]


def _independent(legs):
    """Legs on different markets and not the same player/team: a loose stand-in for independence."""
    tags = [(l["market"], l.get("player") or l.get("team") or l.get("side")) for l in legs]
    return len(set(tags)) == len(tags) and len({l["desc"] for l in legs}) == len(legs)


def _coin_flip_side(leg):
    return leg["market"] in ("ml", "spread") and abs(leg["fair"] - 0.5) < COIN_FLIP


def _parlay(legs):
    p = prod(l["prob"] for l in legs)
    dec = prod(to_decimal(l["odds"]) for l in legs)
    return {"legs": legs, "odds": parlay_american([l["odds"] for l in legs]), "prob": round(p, 4),
            "ev": round(p * dec - 1, 4)}


def build_plays(scored: dict) -> dict | None:
    legs = scored["legs"][:12]
    if len(legs) < 3:
        return None
    used = Counter()

    def ok(combo):
        return all(used[_key(l)] < config.MAX_LEG_REUSE for l in combo)

    def take(combo):
        for l in combo:
            used[_key(l)] += 1

    # Straight: best edge among likely winners; in low-total games prefer the Under or a prop.
    pool = [l for l in legs if l["prob"] >= 0.5]
    if scored.get("low_total"):
        pool = [l for l in pool if l.get("side") == "under" or l["market"] == "prop"] or pool
    if not pool:
        return None
    straight = max(pool, key=lambda l: l["edge"])
    take([straight])
    plays = {"straight": {"legs": [straight], "odds": straight["odds"], "prob": straight["prob"], "ev": straight["edge"]}}

    # Safer: two independent strong legs, highest joint probability with positive EV.
    safer_pool = [l for l in legs if l["prob"] >= 0.55 and not _coin_flip_side(l)]
    cands = [_parlay(c) for c in combinations(safer_pool, 2) if _independent(c) and ok(c)]
    cands = [c for c in cands if c["ev"] > 0] or cands
    if cands:
        plays["safer"] = max(cands, key=lambda c: (c["prob"], c["ev"]))
        take(plays["safer"]["legs"])

    # Best: 3 legs, highest EV.
    cands = [_parlay(c) for c in combinations(legs, 3) if _independent(c) and ok(c)]
    if cands:
        plays["best"] = max(cands, key=lambda c: c["ev"])
        take(plays["best"]["legs"])

    # Longshot: 3-5 legs reaching +1000, highest hit chance.
    cands = [_parlay(c) for n in (3, 4, 5) for c in combinations(legs, n)
             if _independent(c) and ok(c)]
    cands = [c for c in cands if c["odds"] >= config.LONGSHOT_MIN]
    if cands:
        plays["longshot"] = max(cands, key=lambda c: (c["prob"], c["ev"]))
        take(plays["longshot"]["legs"])

    for name, play in plays.items():
        if len(play["legs"]) > 1:  # correlated same-game legs: simulate instead of multiplying
            p = simulate_parlay(play["legs"])
            play["prob"] = round(p, 4)
            play["ev"] = round(p * to_decimal(play["odds"]) - 1, 4)
        play["units"] = config.PLAYS[name]
        play["stake"] = play["units"] * config.UNIT
    return plays


def pick_top_games(scored_games: list[dict], n: int = config.GAMES_PER_DAY) -> list[dict]:
    """Highest-scoring games first, skipping any that would put a team in more than 2 games' plays.
    Up to n games, but only ones scoring at least MIN_GAME_SCORE, except that a league with only a
    couple of games that day (a short playoff slate) still gets its best playable game."""
    chosen, teams = [], Counter()
    per_league = Counter(g["game"]["league"] for g in scored_games)
    for g in sorted(scored_games, key=lambda g: g["score"], reverse=True):
        if len(chosen) == n:
            break
        if g["score"] < config.MIN_GAME_SCORE and not (
                per_league[g["game"]["league"]] <= 2 and not any(c["game"]["league"] == g["game"]["league"] for c in chosen)):
            continue
        plays = build_plays(g)
        if not plays:
            continue
        names = {g["game"]["home"], g["game"]["away"]}
        if any(teams[t] >= 2 for t in names):
            continue
        teams.update(names)
        chosen.append(g | {"plays": plays})
    return chosen


def build_traditional(scored_games: list[dict], sgp_straights: set | None = None) -> dict | None:
    """The same four plays, but every leg from a different game: the day's strongest legs across the
    whole slate. Legs from different games are independent, so parlay odds multiply cleanly.
    The straight skips any leg that is already a game's SGP straight, so the two slips don't double up."""
    pool = []
    for g in scored_games:
        meta = {"event_id": g["game"]["id"], "league": g["game"]["league"], "start": g["game"].get("start"),
                "matchup": f"{g['game']['away']} @ {g['game']['home']}"}
        # Only games with an analyst file feed the cross-game slip; unresearched market noise doesn't qualify.
        if g.get("analyst"):
            pool += [l | meta for l in g["legs"][:4]]
    pool = sorted(pool, key=lambda l: l["edge"] * l["prob"], reverse=True)[:18]
    if len({l["event_id"] for l in pool}) < 2:
        return None
    used = Counter()

    def ok(combo):
        return len({l["event_id"] for l in combo}) == len(combo) and all(used[_key(l)] < config.MAX_LEG_REUSE for l in combo)

    def take(combo):
        for l in combo:
            used[_key(l)] += 1

    plays = {}
    singles = [l for l in pool if l["prob"] >= 0.5 and _key(l) not in (sgp_straights or set())]
    if singles:
        s1 = max(singles, key=lambda l: l["edge"])
        plays["straight"] = {"legs": [s1], "odds": s1["odds"], "prob": s1["prob"], "ev": s1["edge"]}
        take([s1])
    safer_pool = [l for l in pool if l["prob"] >= 0.55 and not _coin_flip_side(l)]
    cands = [_parlay(c) for c in combinations(safer_pool, 2) if ok(c)]
    cands = [c for c in cands if c["ev"] > 0] or cands
    if cands:
        plays["safer"] = max(cands, key=lambda c: (c["prob"], c["ev"]))
        take(plays["safer"]["legs"])
    cands = [_parlay(c) for c in combinations(pool, 3) if ok(c)]
    if cands:
        plays["best"] = max(cands, key=lambda c: c["ev"])
        take(plays["best"]["legs"])
    cands = [_parlay(c) for n in (3, 4, 5) for c in combinations(pool[:14], n) if ok(c)]
    cands = [c for c in cands if c["odds"] >= config.LONGSHOT_MIN]
    if cands:
        plays["longshot"] = max(cands, key=lambda c: (c["prob"], c["ev"]))
    for name, play in plays.items():
        play["units"] = config.PLAYS[name]
        play["stake"] = play["units"] * config.UNIT
    return plays or None


def pick_of_day(scored_games: list[dict]) -> dict:
    """League -> the single strongest leg of the day in that league, from any game: a likely winner
    (model 55%+ when one exists) with the best edge x win chance."""
    best = {}
    for g in scored_games:
        lg = g["game"]["league"]
        for l in g["legs"]:
            leg = l | {"event_id": g["game"]["id"], "league": lg, "start": g["game"].get("start"),
                       "matchup": f"{g['game']['away']} @ {g['game']['home']}", "_game": g["game"]}
            score = (l["prob"] >= 0.55, l["edge"] * l["prob"]) if l["prob"] >= 0.5 else None
            if score and (lg not in best or score > best[lg][0]):
                best[lg] = (score, leg)
    return {lg: leg for lg, (_, leg) in best.items()}
