"""Stat projections for player props, and parlay simulation.

A prop is priced by projecting the stat itself (a mean and a spread), then asking how often that
distribution lands over the line, instead of only nudging the betting line. Count stats (strikeouts,
hits, shots, touchdowns) use a Poisson model; yardage and points use a normal model.
"""
import math
import random
from statistics import mean, pstdev

LEAGUE_K_PCT = 0.225  # MLB batter strikeout rate, used as the log5 baseline
COUNT_STATS = {"strikeouts", "hits", "hr", "rbi", "runs", "total_bases", "h_r_rbi", "shots", "goals", "saves",
               "anytime_td", "pass_td", "receptions", "threes", "assists", "rebounds", "outs"}


def blended_mean(recent: list[float], season_avg: float | None = None, w_recent: float = 0.55) -> float:
    """Recent form weighted against the season average (recent form counts more, per our lessons)."""
    r = mean(recent) if recent else None
    if r is None:
        return season_avg or 0.0
    return r if season_avg is None else w_recent * r + (1 - w_recent) * season_avg


def log5_k_rate(pitcher_k: float, opp_k: float, league_k: float = LEAGUE_K_PCT) -> float:
    """Expected K rate when this pitcher faces this lineup (log5)."""
    num = pitcher_k * opp_k / league_k
    den = num + (1 - pitcher_k) * (1 - opp_k) / (1 - league_k)
    return num / den


def project_pitcher_ks(pitcher_k: float, opp_k: float, batters_faced: float) -> float:
    return log5_k_rate(pitcher_k, opp_k) * batters_faced


def poisson_over(mu: float, line: float) -> float:
    """P(X > line) for a Poisson count with mean mu (lines are usually x.5)."""
    k = math.floor(line)
    cdf, term = 0.0, math.exp(-mu)
    for i in range(k + 1):
        cdf += term
        term *= mu / (i + 1)
    return max(0.0, min(1.0, 1 - cdf))


def normal_over(mu: float, sd: float, line: float) -> float:
    sd = max(sd, 1e-6)
    return 0.5 * math.erfc((line - mu) / (sd * math.sqrt(2)))


def prop_prob(stat: str, side: str, line: float, mu: float, sd: float | None = None) -> float:
    """Chance the prop wins. sd defaults to 35% of the mean for yardage/points."""
    p_over = poisson_over(mu, line) if stat in COUNT_STATS else normal_over(mu, sd or 0.35 * max(mu, 1), line)
    return p_over if side == "over" else 1 - p_over


def from_game_log(stat: str, side: str, line: float, log: list[float], season_avg: float | None = None) -> float | None:
    """Projection straight from a player's game log: blended mean, spread from the log."""
    if not log:
        return None
    mu = blended_mean(log[-5:], season_avg if season_avg is not None else mean(log))
    sd = pstdev(log) if len(log) >= 4 else None
    return prop_prob(stat, side, line, mu, sd)


# ---------- parlay simulation ----------

def correlation(a: dict, b: dict) -> float:
    """Rough same-game correlation between two legs' outcomes. 0 for different games or unknown pairs."""
    if a.get("game") and b.get("game") and a["game"] != b["game"]:
        return 0.0
    if a.get("event_id") and b.get("event_id") and a["event_id"] != b["event_id"]:
        return 0.0
    kinds = {a["market"], b["market"]}
    sides = (a.get("side"), b.get("side"))
    if kinds == {"total", "prop"}:
        tot, prop = (a, b) if a["market"] == "total" else (b, a)
        yardage = prop.get("stat") in ("pass_yds", "rec_yds", "rush_yds", "points", "anytime_td", "hits", "runs", "rbi", "h_r_rbi", "total_bases", "goals", "shots")
        if not yardage:
            return -0.15 if prop.get("stat") in ("strikeouts", "saves") and tot.get("side") == "over" else 0.0
        same_dir = (tot.get("side") == "over") == (prop.get("side") == "over")
        return 0.35 if same_dir else -0.35
    if kinds == {"prop"} and a.get("team") and a.get("team") == b.get("team"):
        return 0.2 if sides[0] == sides[1] else -0.2  # teammates rise and fall with the offense
    if kinds <= {"ml", "spread"} and a.get("team") == b.get("team"):
        return 0.8
    if kinds <= {"total", "team_total", "h1_total"} and len(kinds) == 2 or kinds == {"team_total"}:
        same = sides[0] == sides[1]
        if kinds == {"team_total"}:
            return 0.25 if same else -0.25
        return 0.55 if same else -0.55
    if kinds <= {"ml", "spread", "h1_ml", "h1_spread"} and a.get("team") and b.get("team"):
        return 0.55 if a.get("team") == b.get("team") else -0.55
    if "team_total" in kinds and kinds & {"ml", "spread", "h1_ml", "h1_spread"}:
        tt, side = (a, b) if a["market"] == "team_total" else (b, a)
        same_team = tt.get("team") == side.get("team")
        return (0.35 if tt.get("side") == "over" else -0.35) * (1 if same_team else -1)
    if kinds == {"ml", "prop"} or kinds == {"spread", "prop"}:
        side, prop = (a, b) if a["market"] != "prop" else (b, a)
        if prop.get("team") and prop.get("team") == side.get("team"):
            return 0.25 if prop.get("side") == "over" else -0.25
    return 0.0


def simulate_parlay(legs: list[dict], n: int = 20000, seed: int = 7) -> float:
    """Joint hit chance with correlated legs (Gaussian copula). Each leg needs 'prob'."""
    k = len(legs)
    if k == 1:
        return legs[0]["prob"]
    corr = [[1.0 if i == j else correlation(legs[i], legs[j]) for j in range(k)] for i in range(k)]
    L = _cholesky(corr)
    thresholds = [_norm_ppf(l["prob"]) for l in legs]
    rng = random.Random(seed)
    hits = 0
    for _ in range(n):
        z = [rng.gauss(0, 1) for _ in range(k)]
        x = [sum(L[i][j] * z[j] for j in range(i + 1)) for i in range(k)]
        hits += all(x[i] < thresholds[i] for i in range(k))
    return hits / n


def _cholesky(m):
    k = len(m)
    L = [[0.0] * k for _ in range(k)]
    for i in range(k):
        for j in range(i + 1):
            s = sum(L[i][t] * L[j][t] for t in range(j))
            if i == j:
                L[i][j] = math.sqrt(max(m[i][i] - s, 1e-9))
            else:
                L[i][j] = (m[i][j] - s) / L[j][j]
    return L


def _norm_ppf(p: float) -> float:
    """Inverse standard normal CDF (Acklam's approximation)."""
    p = min(max(p, 1e-9), 1 - 1e-9)
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02, 1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02, 6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00, -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00]
    if p < 0.02425:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > 1 - 0.02425:
        return -_norm_ppf(1 - p)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)
