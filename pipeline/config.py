"""Settings shared by every pipeline step. The app's sliders override the bankroll values per viewer."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CACHE = DATA / "cache"
BETS_FILE = DATA / "bets.json"
WEIGHTS_FILE = DATA / "weights.json"
LESSONS_FILE = DATA / "lessons.json"
SLATE_FILE = DATA / "slate.json"
APP_DIR = ROOT / "app"
BREAKDOWN_DIR = ROOT.parent / "betting"

TIMEZONE = "America/New_York"
REFRESH_TIME = "07:59"
LOCK_BEFORE_MIN = 60  # picks are final this many minutes before first pitch / kickoff / tip
GAMES_PER_DAY = 8  # most games with plays per day; the app's slider picks how many you bet
POTD_UNITS = 4  # Pick of the Day: one straight bet per league, sized like a straight
MIN_GAME_SCORE = 0.02  # sum of edge x win chance over a game's best 4 legs

UNIT = 3.0  # dollars
PLAYS = {  # play -> units (40/30/20/10 of a 10u, $30 game)
    "straight": 4,
    "safer": 3,
    "best": 2,
    "longshot": 1,
}
LONGSHOT_MIN = 1000  # American odds
TRAD_MAX_PER_GAME = 2  # a Traditional slip may take up to 2 legs from one game when they price well
MAX_LEG_REUSE = 2  # a leg may appear in at most 2 of a game's 4 plays
MIN_EDGE = 0.02  # drop legs with less than 2% expected value

SECRETS = ROOT / ".secrets"


def _secret(name: str) -> str:
    """Environment variable first, then the project's private .secrets/<name> file."""
    if os.environ.get(name.upper()):
        return os.environ[name.upper()]
    try:
        return (SECRETS / name.lower()).read_text().strip()
    except FileNotFoundError:
        return ""


ODDS_API_KEY = _secret("ODDS_API_KEY")
CFBD_API_KEY = os.environ.get("CFBD_API_KEY", "")
FOOTBALL_DATA_KEY = os.environ.get("FOOTBALL_DATA_KEY", "")

# league code -> (ESPN path, Odds API sport key, reference site)
LEAGUES = {
    "MLB": ("baseball/mlb", "baseball_mlb", "https://www.baseball-reference.com"),
    "NFL": ("football/nfl", "americanfootball_nfl", "https://www.pro-football-reference.com"),
    "NBA": ("basketball/nba", "basketball_nba", "https://www.basketball-reference.com"),
    "NHL": ("hockey/nhl", "icehockey_nhl", "https://www.hockey-reference.com"),
    "SOCCER": ("soccer/eng.1", "soccer_epl", "https://fbref.com"),
    "NCAAF": ("football/college-football", "americanfootball_ncaaf", "https://www.sports-reference.com/cfb"),
    "NCAAB": ("basketball/mens-college-basketball", "basketball_ncaab", "https://www.sports-reference.com/cbb"),
}
SOCCER_LEAGUES = ["eng.1", "esp.1", "ita.1", "ger.1", "fra.1", "usa.1", "uefa.champions"]

# ESPN college football conference ids (team.conferenceId on the scoreboard) -> the names the CFB tab groups by
CFB_CONFERENCES = {"1": "ACC", "4": "Big 12", "5": "Big Ten", "8": "SEC", "9": "Pac-12", "12": "Conference USA",
                   "15": "MAC", "17": "Mountain West", "18": "FBS Independents", "37": "Sun Belt", "151": "American"}
