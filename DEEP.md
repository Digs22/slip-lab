# Deep second pass: one college football game, Sat 2026-10-17

A first research pass already exists. The user says it was surface level and wants a DEEP analysis of every aspect. Your job is to go far deeper, challenge the first pass, and rewrite the analyst file.

Inputs (paths in your task):
- The market file <id>.json: game_lines and team_and_half_lines, each with desc, odds, book, fair (no-vig) and books.
- The first-pass analyst file <id>.analyst.json, with its summary, legs, signals, notes and research.

Do AT LEAST 15 distinct web searches (WebSearch; WebFetch may fail with DNS errors, so rely on search results if it does). Cover each of these in depth, with numbers:
1. Quarterbacks and key personnel. For every QB who may play (starters AND backups), give the full career résumé: prior seasons and schools, comp%, YPA, TD/INT, QBR or PFF grades. Do not judge a player on one game. Do the same for the top skill players and any injured starters (who replaces them and how good they are).
2. Latest injury and availability news from today and yesterday: beat writers, official SEC/Big Ten/ACC/Big 12 availability reports, and depth-chart changes.
3. Advanced team stats: SP+ (offense and defense), success rate, EPA/play, explosiveness, havoc, red-zone TD%, turnover margin, pace (plays per game), and third-down rates. Use opponent-adjusted numbers where possible, and note strength of schedule.
4. Scheme matchups: offensive system vs defensive front and coverage, OL vs DL (sacks allowed and pressure rates), run game vs run defense, and explosive pass vs secondary.
5. Coaching: head coach and coordinator tendencies, ATS records as favorite or underdog and home or away, record after a bye, fourth-down aggressiveness, and how each coach manages a lead or a blowout.
6. Situational factors: rest and bye, travel, lookahead or letdown spots, rivalry, revenge, homecoming, night game, and crowd.
7. History: head-to-head results, recent meetings and ATS trends.
8. Weather at kickoff (forecast for the venue).
9. Analysts and models: collect at least 5 named expert or model picks (side AND total) and the consensus, plus line movement since open and bet/money splits if available.

Then rewrite the analyst file at the output path, using the same schema as the first pass:
{"summary": "...", "lineups_confirmed": false, "legs": [{"desc","signals","note"}], "extra_legs": [], "research": "<15-30 lines of findings under the 9 headings, with source names>"}

Rules:
- NO player props (banned in the user's state). "extra_legs" must be []. Legs must use the EXACT desc text from game_lines or team_and_half_lines in the market file. Stick to mainstream lines (spread, total, moneyline, team totals, 1H lines); no obscure props.
- Give 3-5 legs, and include the single leg you believe is MOST LIKELY TO WIN. The app always posts one pick per game, so ranking matters even if no edge exists.
- Calibrate: signals are per-factor win probabilities, mostly between 0.45 and 0.62. Go above 0.62 only with strong, specific evidence, and leave out factors with no information. The model caps any leg at market fair ±5 points.
- You may reverse the first pass if the deeper evidence says so; say so in the summary.
- Validate the JSON with python3 -c "import json;json.load(open(PATH))".
- Write nothing else anywhere, install nothing, and never place bets.

Finally, reply in 4 lines: what changed from the first pass, the leg most likely to win, and the leg with the strongest edge.

Write-up style (user, 2026-10-17): the app shows "summary" and each leg's "note" under a Why? button, so keep them short and plain. The summary is 1-2 sentences (max ~30 words) of key facts. Each note is ONE sentence (max ~20 words) on why the bet could win. Put all detail, numbers and sources in "research", not in the summary or notes.

When the market file has no multi-book lines (the odds API quota ran out), price legs yourself from public odds pages (VegasInsider, Covers, Action Network, ESPN, Oddsshark, sportsbook sites). Put each one in "extra_legs" with "market" (spread, total, ml, team_total, h1_spread, h1_total), "desc" in the app's format ("Team +3.5", "Over 50.5", "Team ML", "Team team total Under 24.5", "1H Team +1.5", "1H Over 23.5"), "team", "side" (over/under, or null for sides), "line", "odds" (best price found), "book", "fair" (no-vig probability from averaging both sides), "books" (count of sources), plus "signals" and "note". Still no player props in college.

- Role check for every rushing or receiving prop: look for recent fumbles, benchings, snap-share trends and depth-chart changes (10/18 Skattebo was active but got no carries). Injury status alone is not enough.
Team notes (user, 2026-10-18: keep research so teams aren't researched from scratch every time): before researching, read data/team_notes/<LEAGUE>/<ABBR>.md for both teams. It holds every earlier researched game for that team (summary, research, legs, our graded bets) and a "Standing notes" section. Reuse what still holds and spend the searches on what's new: injuries, lines, recent form, weather. After researching, add durable findings (coaching tendencies, home field, key players and depth, injury history, betting trends) as short bullets under "Standing notes" in each team's file, editing only that section and dating each bullet. The Game log section below it is rebuilt automatically on every export.
