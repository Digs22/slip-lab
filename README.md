# Sigma Locks

Daily picks app: four plays per game (Straight, Safer parlay, Best parlay, Longshot at +1000 or longer) on up to 8 ranked games a day (only games scoring at least `MIN_GAME_SCORE`, plus the best game in a league with just one or two games that day; the app's Games per day slider, default 3, sets how many you bet) across MLB, NFL, NBA, NHL, soccer and college. Every pick is logged as if placed, graded when the game ends, and fed back into the model.

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

## Live tracking and phone alerts

- In the app (website), each leg on a game in progress shows Hit, Lost, On track or Behind, with the current number and its pace to the final. It reads ESPN's live scoreboard and box score from the phone every minute.
- `python3 -m pipeline.live` sends ntfy alerts only for games and slips the user watches. The bell on a game, Traditional slip or Pick of the Day posts `{"key", "on"}` to the private topic `<topic>-watch` (the user pastes their topic once in Settings; it stays on the device). Keys: `game:<espn id>`, `trad:<date>`, `bet:<id>`. For a watched pick it alerts on market line moves between refreshes (spread or total by half a point, moneyline by 3%), refresh changes (pick, odds, injury status, dropped, added), the lock an hour before the game, legs behind, lost or hit, and the result. In the private preview, bells save to the artifact database collection `watch` (the sandbox blocks ntfy), and each refresh copies them to `<topic>-watch`. The topic name is in `.secrets/ntfy_topic` (and the repo secret `NTFY_TOPIC`). GitHub Actions runs it every 10 minutes from 9 AM to 3 AM Eastern (`.github/workflows/live.yml`).

## Device sync

With the same alerts topic saved on each device, every settings change (unit size, splits, theme, pick toggles, placed bets with their locked stake, bells) posts to the private ntfy channel `<topic>-prefs` (bells to `<topic>-watch`). Other devices pull every 30 seconds while open and on reopen; the newest change wins. ntfy keeps messages about 12 hours, so `daily` and `grade` also run `sync.pull()`, which saves the newest settings to data/settings.json, toggles to data/filters.json and placed taps into the ledger, and export puts them in data.json for devices that were off longer. The private preview can't reach ntfy and uses the artifact database (`prefs` docs `settings` and `filters`, collection `placed`) instead.

## Statistical model

- `pipeline/project.py` projects each prop's stat (Poisson for counts, normal for yardage) and prices the line from that distribution, as the `projection` signal. Inputs come from the analyst file (`projection: {mu, sd}`, `{log: [...]}`, or `{pitcher_k, opp_k, batters_faced}`) or automatically from nflverse (NFL) and Baseball Savant (MLB strikeouts).
- Parlays are re-priced with a 20,000-run simulation that accounts for same-game correlation (an Under with a QB passing under, teammates' overs, a side with its players' overs).
- `python3 -m pipeline.run close` saves the closing line 10 to 45 minutes before each game. The Lessons tab shows closing line value and a calibration chart with a Brier score.
- `pipeline/stats_feeds.py`: Baseball Savant, nflverse, MoneyPuck.

## Games tab and pick timing

The Games tab has a 7-day strip. `python3 -m pipeline.run schedule` (also run by `daily`) writes data/schedule.json: every game in every league for the next 7 Eastern days with ESPN's consensus spread, total and moneylines, shown as a preview. `daily` now picks today's and tomorrow's games, so picks post the day before and keep updating with the odds and news until one hour before the game starts (`LOCK_BEFORE_MIN` in config.py), when they're final. A Traditional slip locks an hour before its first game. Traditional slips span at least 2 games and take up to 2 legs from one game (`TRAD_MAX_PER_GAME`) when the pair prices better after the correlation simulation. A slip leaves the Games tab once every bet in it is settled; its results stay on the Record tab.

## Run-time syncs (every routine run, before `daily`)

The preview can't write to files, so each scheduled run copies what the user did in the app into data/:

1. Placed bets: for each doc in the artifact db collection `placed` (doc id = bet id), set `"placed": true` on that bet in data/bets.json. If the doc has `units` and `unit`, store them as `placed_units` and `placed_unit`. Placed bets never change after that.
2. Pick toggles: if the artifact db collection `prefs`, doc `filters`, has an `updated` newer than data/filters.json, save it with `teams.save_filters()`.
2b. Settings: if the artifact db doc `prefs/settings` has an `updated` newer than data/settings.json, write it there as-is (`{"key": "settings", "updated", "prefs", "theme"}`).
3. Bell alerts, after export: for each doc in `watch` with on=true whose game hasn't finished, POST {"key","label","on":true} to the ntfy `<topic>-watch` topic. The topic is in .secrets and is never printed. Never send a "new picks" alert.

## Commands

```
cd /mnt/project-files/betting-app
python3 -m pipeline.run daily     # 7:59 AM ET
python3 -m pipeline.run recheck   # games waiting on lineups or news
python3 -m pipeline.run grade     # after games end
python3 -m pipeline.run auto      # GitHub Actions: sync, closing lines, grading
python3 -m pipeline.run export    # rebuild app/data.json and the app's built-in data
python3 -m pipeline.run status
python3 -m unittest tests.test_core
```

## Data feeds

Keyless: ESPN site API (all leagues, scores, box scores, injuries), MLB Stats API (probables, lineups, splits, batter vs pitcher), NHL API, Open-Meteo (weather). Free key: The Odds API (stored), College Football Data (`CFBD_API_KEY`), football-data.org (`FOOTBALL_DATA_KEY`). Sports-Reference pages are pulled at most once every 4 seconds and cached for 6 hours.

On 2026-10-14 the project's new default cloud environment reached ESPN, MLB Stats, NHL, The Odds API and Sports-Reference. Open-Meteo was added later the same day: outdoor NFL and MLB games get wind, rain and temperature from it using `pipeline/venues.py` stadium coordinates. Without data/analyst files the model finds no edges, so the daily run researches candidate games and writes those files before re-running `daily`.

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

An alternate line (a spread or total moved off the main number for a different price) sets `"alt": true` and `"main_line"` (for example `"-3"`); the app tags it "alt line · main -3" in faded italics. Every leg should carry a `note` (one or two plain sentences on why it's recommended) and, for player props, a `status` (injury or practice status, for example "Ankle, limited at practice" or "Healthy"). The app shows both under "Why this pick". Without a note it falls back to the strongest signals. `status` is filled from ESPN's injury report when the analyst file leaves it out.

Signals are probabilities that the leg wins, from one factor: recent_form, season, matchup, home_away, rest_travel, weather, injuries, coaching, postseason, news, line_move.

Pitcher props (lesson from MIL @ LAD G3, 2026-10-14): base the projection on season strikeout rate, pitches per inning and walk rate, not on the last 2 or 3 starts. Keep `recent_form` close to the `season` signal unless something real changed (an injury, a pitch-count limit, a role change). A short outing caused by a manager's quick hook is weak evidence. Snell's 12- and 11-out starts didn't predict tonight's 18 outs and 9 strikeouts.

## Hosting for real

The preview is a private claude.ai link, so it can't install as a phone app or poll live scores (the page's sandbox blocks outside requests). To get that:

1. Create a free GitHub repo and connect it to this project.
2. Push this folder. Turn on GitHub Pages for `app/`.
3. `.github/workflows/slip-lab.yml` runs `python3 -m pipeline.run auto` every 15 minutes from noon to 3 AM ET: device sync, closing lines and grading, committing only when the ledger changed and then redeploying the website. Claude keeps the 7:59 AM research and picks, pre-lock checks and post-game reviews; `daily` never runs on a schedule there, or picks would be logged twice. Repo secrets: `NTFY_TOPIC` and `ODDS_API_KEY` (closing lines are skipped without it). Claude's runs pull `main` before they start, since Actions commits grades there.

## Roadmap (not built)

1. **Live in-game alerts.** Watch live scores and odds for open bets. Push a notification when cashing out, hedging at a new price or adding a live bet is worth it, with the math and the reason. Needs hosting (above) for push notifications, and frequent odds polling (likely a paid odds tier).
2. **Approve or deny bet placement.** One Approve and one Deny button per slip. US sportsbooks have no public betting API and automating their sites breaks their terms, so the options are a book or exchange with an official API in the user's state, or a link that opens the slip pre-filled in the book's app. Nothing connects to a sportsbook account until the user picks a path and approves it.
3. Bet slip photo upload so the record uses the user's actual odds.
4. Closing-line value tracked by book, and a weekly performance summary.
