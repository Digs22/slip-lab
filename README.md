# Slip Lab

Daily picks app: four plays per game (Straight, Safer parlay, Best parlay, Longshot at +1000 or longer) on the top 3 games a day across MLB, NFL, NBA, NHL, soccer and college. Every pick is logged as if placed, graded when the game ends, and fed back into the model.

- Private preview: https://claude.ai/artifact/KAA7s28w1RzxqF4ThgPqx1
- Plan: https://claude.ai/code/artifact/7ac6608b-19be-41c8-8069-32876b3605fb

## Layout

| Path | What it is |
|---|---|
| `pipeline/` | Python, standard library only. `run.py` is the entry point |
| `data/bets.json` | Every bet, legs, odds, units, result, net, loss cause |
| `data/lessons.json` | Rules from post-game reviews, loss causes, stake flags, factor weights |
| `data/weights.json` | Factor weights the learning loop adjusts (created after the first grade) |
| `data/analyst/<date>-<espn game id>.json` | Claude's research per game: qualitative signals, hand-priced props |
| `data/slate.json` | The last run's picked games and any feed problems |
| `app/` | The installable web app (index.html, manifest, service worker, icon, data.json) |
| `.secrets/odds_api_key` | The Odds API key. Never publish or copy it anywhere else |
| `tests/test_core.py` | Odds math, grading, play builder, learning loop |

## Your placed bets

Tapping "I placed this" (or "I placed all") in the app saves a doc in the artifact's database, collection `placed`, doc id = bet id. The Record tab's "My bets" view uses those for your personal record, units and ROI. Claude can read them with ArtifactData `list` on collection `placed`. In a self-hosted copy without that database, marks are kept in the browser.

## Commands

```
cd /mnt/project-files/betting-app
python3 -m pipeline.run daily     # 7:59 AM ET
python3 -m pipeline.run recheck   # games waiting on lineups or news
python3 -m pipeline.run grade     # after games end
python3 -m pipeline.run export    # rebuild app/data.json and the app's built-in data
python3 -m pipeline.run status
python3 -m unittest tests.test_core
```

## Data feeds

Keyless: ESPN site API (all leagues, scores, box scores, injuries), MLB Stats API (probables, lineups, splits, batter vs pitcher), NHL API, Open-Meteo (weather). Free key: The Odds API (stored), College Football Data (`CFBD_API_KEY`), football-data.org (`FOOTBALL_DATA_KEY`). Sports-Reference pages are pulled at most once every 4 seconds and cached for 6 hours.

On 2026-10-14 the project's new default cloud environment reached ESPN, MLB Stats, NHL, The Odds API and Sports-Reference. Open-Meteo was still blocked, so weather comes from web search. Without data/analyst files the model finds no edges, so the daily run researches candidate games and writes those files before re-running `daily`.

## Analyst file format

```json
{
  "summary": "One line on the game",
  "lineups_confirmed": true,
  "legs": [{"desc": "Under 40.5", "signals": {"coaching": 0.56, "news": 0.5}, "note": "why"}],
  "extra_legs": [{"desc": "Bo Nix u209.5 pass yds", "market": "prop", "player": "Bo Nix", "stat": "pass_yds",
                  "side": "under", "line": 209.5, "odds": -115, "fair": 0.52, "book": "consensus",
                  "signals": {"recent_form": 0.62, "matchup": 0.58}}]
}
```

Signals are probabilities that the leg wins, from one factor: recent_form, season, matchup, home_away, rest_travel, weather, injuries, coaching, postseason, news, line_move.

## Hosting for real

The preview is a private claude.ai link, so it can't install as a phone app or poll live scores (the page's sandbox blocks outside requests). To get that:

1. Create a free GitHub repo and connect it to this project.
2. Push this folder. Turn on GitHub Pages for `app/`.
3. The workflow is already written at `.github/workflows/slip-lab.yml` (`.gitignore` keeps `.secrets/` out of the repo). It runs `daily` at 7:59 AM ET, `recheck` every 30 minutes and `grade` every 30 minutes in the evening, then commits `data/` and `app/data.json`. Store the key as a repo secret named `ODDS_API_KEY`.

## Roadmap (not built)

1. **Live in-game alerts.** Watch live scores and odds for open bets. Push a notification when cashing out, hedging at a new price or adding a live bet is worth it, with the math and the reason. Needs hosting (above) for push notifications, and frequent odds polling (likely a paid odds tier).
2. **Approve or deny bet placement.** One Approve and one Deny button per slip. US sportsbooks have no public betting API and automating their sites breaks their terms, so the options are a book or exchange with an official API in the user's state, or a link that opens the slip pre-filled in the book's app. Nothing connects to a sportsbook account until the user picks a path and approves it.
3. Bet slip photo upload so the record uses the user's actual odds.
4. Closing-line value tracked by book, and a weekly performance summary.
