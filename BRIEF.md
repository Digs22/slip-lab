# Brief: deep pre-game research for one college football game (Sat 2026-10-17)

You are researching one game for a sports-picks app. The user wants EVERY aspect analyzed. Do real research with WebSearch/WebFetch (ESPN, sports-reference.com/cfb, team beat writers, official injury reports, Action Network/Covers/CBS/ESPN expert picks, weather). Cover:
1. Home-field advantage and atmosphere (crowd, altitude, night game, rivalry, homecoming).
2. Historical context: head-to-head history, recent meetings, ATS trends for both teams and coaches.
3. Coaching schemes and tendencies: offensive/defensive systems, tempo, run/pass split, aggressiveness, how the coaches have fared as favorite/underdog.
4. Player matchups: QB vs pass defense, OL vs DL, top WR vs secondary, run game vs run defense.
5. Health and rosters: the latest injury report and practice news (QBs especially), depth, transfers, suspensions.
6. Stats: season stats per game, efficiency (yards/play, success rate, SP+/FPI if found), turnovers, red zone, pace.
7. Weather and environment at kickoff.
8. What analysts are saying: collect several named expert/model picks (side and total) and the consensus.
9. Line movement and public betting splits if you can find them.

The market lines and player props (best price per side across books, with no-vig fair probability) are in the input file named in your task. Only propose legs that exist in that file, using the EXACT `desc` text for game lines. For a player prop, use a desc like "J. Smith o132.5 receiving yds", give "market":"prop", "player", "team" (abbreviation), "stat" (one of pass_yds, rush_yds, rec_yds), "side" ("over"/"under"), "line", "odds" (best price from the file), "book", "fair" (no-vig probability for that side, from averaging both sides across books), "status" (injury/practice status), "projection" {"mu","sd"} from per-game stats.

Calibration rules (important, the app lost money by inflating):
- For each leg give "signals": factor -> your probability (0-1) that THIS leg wins, judged from that factor alone. Use factors like home, matchup, season, recent_form, injuries, coaching, history, weather, experts. Most signals should land between 0.45 and 0.62. Go above 0.62 only with strong, specific evidence. Factors with no information should be left out, not set to 0.5.
- The model caps any leg at the market fair +/- 5 points, so only legs where the evidence clearly disagrees with the market will become picks. It is fine, and expected, to conclude that there is no edge.
- Lessons: weight season-long efficiency over 1-2 game samples; don't anchor on anytime-TD or other coin-flip legs; a backup QB is strong evidence; avoid legs that only hit if a single unlikely event happens.

Output: write a JSON file to the output path named in your task:
{"summary": "<one or two plain sentences with the key facts>", "lineups_confirmed": false, "legs": [<game-line legs: {"desc","signals","note"}>], "extra_legs": [<prop legs with all fields above plus "signals","note">], "research": "<8-15 lines of your findings under the 9 headings, with source names>"}
Each "note" is 1-2 plain sentences on why. Include 2-5 legs total, covering the most promising sides of the game (both a side/total and props where data is good). Then reply with a 3-line summary of what you found and which leg you think is strongest. Do not write anywhere else, do not install anything, and never place bets.

Write-up style (user, 2026-10-17): the summary is 1-2 short plain sentences (max ~30 words). Each note is ONE sentence (max ~20 words). Detail goes in "research".

When the market file has no multi-book lines (the odds API quota ran out), price legs yourself from public odds pages (VegasInsider, Covers, Action Network, ESPN, Oddsshark, sportsbook sites). Put each one in "extra_legs" with "market" (spread, total, ml, team_total, h1_spread, h1_total), "desc" in the app's format ("Team +3.5", "Over 50.5", "Team ML", "Team team total Under 24.5", "1H Team +1.5", "1H Over 23.5"), "team", "side" (over/under, or null for sides), "line", "odds" (best price found), "book", "fair" (no-vig probability from averaging both sides), "books" (count of sources), plus "signals" and "note". Still no player props in college.

Team notes (user, 2026-10-18: keep research so teams aren't researched from scratch every time): before researching, read data/team_notes/<LEAGUE>/<ABBR>.md for both teams. It holds every earlier researched game for that team (summary, research, legs, our graded bets) and a "Standing notes" section. Reuse what still holds and spend the searches on what's new: injuries, lines, recent form, weather. After researching, add durable findings (coaching tendencies, home field, key players and depth, injury history, betting trends) as short bullets under "Standing notes" in each team's file, editing only that section and dating each bullet. The Game log section below it is rebuilt automatically on every export.
