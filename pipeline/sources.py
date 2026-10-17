"""Direct reads from free JSON feeds. No scraping except a few cached Sports-Reference pages per game.

Keyless: ESPN site API, MLB Stats API, NHL API, Open-Meteo.
Free key: The Odds API (ODDS_API_KEY), College Football Data (CFBD_API_KEY), football-data.org (FOOTBALL_DATA_KEY).
"""
import hashlib
import json
import time
import urllib.parse
import urllib.request

from . import config

ESPN = "https://site.api.espn.com/apis/site/v2/sports"
ESPN_CORE = "https://sports.core.api.espn.com/v2/sports"
MLB = "https://statsapi.mlb.com/api/v1"
NHL = "https://api-web.nhle.com/v1"
ODDS = "https://api.the-odds-api.com/v4"
METEO = "https://api.open-meteo.com/v1/forecast"
UA = {"User-Agent": "betting-app-pipeline/1.0 (personal research)"}

_last_ref_hit = 0.0


class SourceError(RuntimeError):
    pass


def get_json(url: str, params: dict | None = None, ttl: int = 300, headers: dict | None = None):
    """GET a JSON URL with a small on-disk cache so re-runs don't hammer the feeds."""
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    config.CACHE.mkdir(parents=True, exist_ok=True)
    key = config.CACHE / (hashlib.sha1(url.encode()).hexdigest() + ".json")
    if ttl and key.exists() and time.time() - key.stat().st_mtime < ttl:
        return json.loads(key.read_text())
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode())
    except Exception as e:  # network policy, 4xx/5xx, bad JSON
        raise SourceError(f"{url.split('?')[0]}: {e}") from e
    key.write_text(json.dumps(data))
    return data


def get_page(url: str, ttl: int = 6 * 3600) -> str:
    """Sports-Reference pages (Baseball/Basketball/Pro-Football/Hockey-Reference, CFB/CBB).
    Their limit is 20 requests a minute, so this waits 4 seconds between uncached hits."""
    global _last_ref_hit
    config.CACHE.mkdir(parents=True, exist_ok=True)
    key = config.CACHE / (hashlib.sha1(url.encode()).hexdigest() + ".html")
    if key.exists() and time.time() - key.stat().st_mtime < ttl:
        return key.read_text()
    wait = 4 - (time.time() - _last_ref_hit)
    if wait > 0:
        time.sleep(wait)
    _last_ref_hit = time.time()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
            html = r.read().decode("utf-8", "replace")
    except Exception as e:
        raise SourceError(f"{url}: {e}") from e
    key.write_text(html)
    return html


# ---------- ESPN (every league) ----------

def espn_scoreboard(league: str, date: str) -> list[dict]:
    """Normalized games for a league on YYYY-MM-DD."""
    path = config.LEAGUES[league][0]
    paths = [f"soccer/{s}" for s in config.SOCCER_LEAGUES] if league == "SOCCER" else [path]
    games = []
    for p in paths:
        params = {"dates": date.replace("-", "")}
        if league in ("NCAAF", "NCAAB"):
            params["groups"] = "80" if league == "NCAAF" else "50"  # FBS / all D-I
        data = get_json(f"{ESPN}/{p}/scoreboard", params, ttl=120)
        for ev in data.get("events", []):
            comp = ev["competitions"][0]
            teams = {c["homeAway"]: c for c in comp["competitors"]}
            venue = comp.get("venue", {})
            games.append({
                "id": ev["id"], "league": league, "espn_path": p,
                "name": ev.get("shortName"), "start": ev["date"],
                "status": ev["status"]["type"]["state"],  # pre | in | post
                "completed": ev["status"]["type"].get("completed", False),
                "postponed": ev["status"]["type"].get("name", "") in ("STATUS_POSTPONED", "STATUS_CANCELED", "STATUS_SUSPENDED"),
                "period": ev["status"].get("period", 0), "clock": ev["status"].get("displayClock", ""),
                "detail": ev["status"]["type"].get("shortDetail", ""),
                "home": teams["home"]["team"]["abbreviation"], "away": teams["away"]["team"]["abbreviation"],
                "home_id": str(teams["home"]["team"].get("id") or ""), "away_id": str(teams["away"]["team"].get("id") or ""),
                "home_name": teams["home"]["team"].get("displayName", ""), "away_name": teams["away"]["team"].get("displayName", ""),
                "home_logo": teams["home"]["team"].get("logo"), "away_logo": teams["away"]["team"].get("logo"),
                "home_score": _num(teams["home"].get("score")), "away_score": _num(teams["away"].get("score")),
                "home_h1": _h1(teams["home"]), "away_h1": _h1(teams["away"]),
                "venue": venue.get("fullName"), "indoor": venue.get("indoor"),
                "city": ", ".join(x for x in (venue.get("address", {}).get("city"), venue.get("address", {}).get("state")) if x),
                "odds": comp.get("odds", [{}])[0] if comp.get("odds") else {},
                "home_conf": _conf(league, teams["home"]["team"]), "away_conf": _conf(league, teams["away"]["team"]),
            })
    return games


def _conf(league: str, team: dict) -> str | None:
    """College football conference name for the CFB tab's grouping; None elsewhere."""
    if league != "NCAAF":
        return None
    cid = str(team.get("conferenceId") or "")
    return config.CFB_CONFERENCES.get(cid) or ("FCS / other" if cid else None)


def espn_summary(game: dict) -> dict:
    """Box score, injuries, leaders, last-five, head-to-head and ESPN's own matchup predictor."""
    return get_json(f"{ESPN}/{game['espn_path']}/summary", {"event": game["id"]}, ttl=120)


def espn_injuries(league: str) -> list[dict]:
    data = get_json(f"{ESPN}/{config.LEAGUES[league][0]}/injuries", ttl=900)
    out = []
    for team in data.get("injuries", []):
        for i in team.get("injuries", []):
            out.append({"team": team.get("displayName"), "player": i["athlete"]["displayName"],
                        "status": i.get("status"), "detail": i.get("shortComment")})
    return out


def espn_gamelog(league: str, athlete_id: str) -> dict:
    sport_path = config.LEAGUES[league][0]
    return get_json(f"https://site.web.api.espn.com/apis/common/v3/sports/{sport_path}/athletes/{athlete_id}/gamelog", ttl=3600)


# ---------- MLB Stats API ----------

def mlb_schedule(date: str) -> list[dict]:
    data = get_json(f"{MLB}/schedule", {"sportId": 1, "date": date,
                                        "hydrate": "probablePitcher,lineups,weather,venue,team"}, ttl=300)
    return [g for d in data.get("dates", []) for g in d.get("games", [])]


def mlb_boxscore(game_pk: int) -> dict:
    return get_json(f"{MLB}/game/{game_pk}/boxscore", ttl=120)


def mlb_gamelog(person_id: int, season: int, group: str = "hitting") -> list[dict]:
    data = get_json(f"{MLB}/people/{person_id}/stats", {"stats": "gameLog", "season": season, "group": group}, ttl=3600)
    return [s for st in data.get("stats", []) for s in st.get("splits", [])]


def mlb_vs_pitcher(batter_id: int, pitcher_id: int) -> dict:
    data = get_json(f"{MLB}/people/{batter_id}/stats",
                    {"stats": "vsPlayer", "opposingPlayerId": pitcher_id, "group": "hitting"}, ttl=86400)
    splits = [s for st in data.get("stats", []) for s in st.get("splits", [])]
    return splits[-1]["stat"] if splits else {}


# ---------- NHL API ----------

def nhl_schedule(date: str) -> list[dict]:
    data = get_json(f"{NHL}/schedule/{date}", ttl=300)
    return [g for w in data.get("gameWeek", []) if w["date"] == date for g in w.get("games", [])]


def nhl_boxscore(game_id: int) -> dict:
    return get_json(f"{NHL}/gamecenter/{game_id}/boxscore", ttl=120)


# ---------- The Odds API (free key, 500 requests/month) ----------

def odds(league: str, markets: str = "h2h,spreads,totals") -> list[dict]:
    if not config.ODDS_API_KEY:
        raise SourceError("ODDS_API_KEY is not set; lines come from ESPN's single consensus line instead")
    sport = config.LEAGUES[league][1]
    return get_json(f"{ODDS}/sports/{sport}/odds", {
        "apiKey": config.ODDS_API_KEY, "regions": "us", "markets": markets, "oddsFormat": "american"}, ttl=900)


def player_props(league: str, event_id: str, markets: str) -> dict:
    """Props cost one request per market per event, so only call this for the top games."""
    if not config.ODDS_API_KEY:
        raise SourceError("ODDS_API_KEY is not set")
    sport = config.LEAGUES[league][1]
    return get_json(f"{ODDS}/sports/{sport}/events/{event_id}/odds", {
        "apiKey": config.ODDS_API_KEY, "regions": "us", "markets": markets, "oddsFormat": "american"}, ttl=900)


# ---------- Weather ----------

def weather(lat: float, lon: float, iso_start: str) -> dict:
    """Hourly wind, temperature and rain at the stadium for the game's start hour."""
    data = get_json(METEO, {"latitude": lat, "longitude": lon, "timezone": "UTC",
                            "hourly": "temperature_2m,wind_speed_10m,wind_direction_10m,precipitation_probability",
                            "temperature_unit": "fahrenheit", "wind_speed_unit": "mph"}, ttl=1800)
    hour = iso_start[:13] + ":00"
    times = data["hourly"]["time"]
    i = times.index(hour) if hour in times else 0
    return {k: data["hourly"][k][i] for k in data["hourly"] if k != "time"}


def _h1(team: dict):
    """First-half points (periods 1-2) from ESPN's linescores, once both quarters are in."""
    ls = team.get("linescores") or []
    if len(ls) < 2:
        return None
    return sum(_num(p.get("value", p.get("displayValue"))) or 0 for p in ls[:2])


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def logo_data_uri(league: str, abbr: str, url: str | None = None) -> str | None:
    """A small team logo as a data: URI, so the private preview can show it (its sandbox blocks outside images)."""
    import base64
    pro = {"MLB": "mlb", "NFL": "nfl", "NBA": "nba", "NHL": "nhl"}
    if league in pro:
        url = f"https://a.espncdn.com/i/teamlogos/{pro[league]}/500/{abbr.lower()}.png"
    if not url:
        return None
    small = "https://a.espncdn.com/combiner/i?" + urllib.parse.urlencode({"img": urllib.parse.urlparse(url).path, "h": 96, "w": 96})
    try:
        with urllib.request.urlopen(urllib.request.Request(small, headers=UA), timeout=15) as r:
            return "data:image/png;base64," + base64.b64encode(r.read()).decode()
    except Exception:
        return None
