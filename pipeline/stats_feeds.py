"""Deeper free stat feeds for player props. All cached; all optional (the model works without them).

- Baseball Savant (baseballsavant.mlb.com): pitcher and batter K%, BB%, xwOBA, hard-hit and barrel rates.
- nflverse (github.com/nflverse/nflverse-data release files): weekly NFL player stats (targets, carries,
  snap-driven volume, red-zone usage via play-by-play).
- MoneyPuck (moneypuck.com): NHL skater and goalie season data, expected goals, shots, saves.
"""
import csv
import io
import urllib.request

from . import config, sources
from .sources import SourceError, UA

SAVANT = "https://baseballsavant.mlb.com/leaderboard/custom"
NFLVERSE = "https://github.com/nflverse/nflverse-data/releases/download"
MONEYPUCK = "https://moneypuck.com/moneypuck/playerData/seasonSummary"


def _csv(url: str, ttl: int = 6 * 3600) -> list[dict]:
    key = config.CACHE / ("csv-" + __import__("hashlib").sha1(url.encode()).hexdigest() + ".csv")
    config.CACHE.mkdir(parents=True, exist_ok=True)
    import time
    if key.exists() and time.time() - key.stat().st_mtime < ttl:
        text = key.read_text()
    else:
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                text = r.read().decode("utf-8-sig", "replace")
        except Exception as e:
            raise SourceError(f"{url.split('?')[0]}: {e}") from e
        key.write_text(text)
    return list(csv.DictReader(io.StringIO(text)))


def savant(season: int, kind: str = "pitcher") -> dict[str, dict]:
    """player name ("First Last") -> {k_percent, bb_percent, xwoba, hard_hit_percent, barrel_batted_rate, pa}."""
    rows = _csv(f"{SAVANT}?year={season}&type={kind}&filter=&min=1&csv=true&selections="
                "pa,k_percent,bb_percent,woba,xwoba,hard_hit_percent,barrel_batted_rate")
    out = {}
    for r in rows:
        name = r.get("last_name, first_name") or r.get("player_name") or ""
        if "," in name:
            last, first = [x.strip() for x in name.split(",", 1)]
            name = f"{first} {last}"
        out[name] = {k: _f(v) for k, v in r.items() if k not in ("last_name, first_name", "player_name")}
    return out


def nflverse_weekly(season: int) -> list[dict]:
    """Weekly player stat rows. nflverse renamed this release in 2025, so both names are tried."""
    for url in (f"{NFLVERSE}/stats_player/stats_player_week_{season}.csv",
                f"{NFLVERSE}/player_stats/player_stats_{season}.csv"):
        try:
            return _csv(url)
        except SourceError:
            continue
    raise SourceError(f"nflverse weekly stats for {season} not reachable")


def nfl_player_log(season: int, name: str) -> list[dict]:
    rows = [r for r in nflverse_weekly(season) if (r.get("player_display_name") or r.get("player_name")) == name]
    return sorted(rows, key=lambda r: int(r.get("week") or 0))


def moneypuck(season: int, kind: str = "skaters") -> dict[str, dict]:
    """season = the year the season starts (2026 for 2026-27). kind: skaters | goalies | teams."""
    rows = _csv(f"{MONEYPUCK}/{season}/regular/{kind}.csv")
    return {r.get("name"): r for r in rows if r.get("situation", "all") == "all"}


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return v
