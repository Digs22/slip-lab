# NBA method (user's standing rules, 2026-10-20, "for tonight's games and moving forward")

Use this for every NBA game, together with BRIEF.md (team notes, calibration, write-up style, public-price rules).

## 1. NBA same-game parlay (the "usage SGP")
1. Find every player in the game with a **usage rate of 24% or higher** (current season; early in the season, blend with last season and say so). Sources: Basketball-Reference advanced stats, NBA.com, Cleaning the Glass.
2. For each one, check the opponent's **fantasy points allowed to that player's position**. Keep the player only if the opponent ranks **bottom 10** (that is, it allows among the most fantasy points to that position). Sources: FantasyPros or Hashtag Basketball DvP, NBA.com.
3. For each kept player, look at **points, rebounds and assists** and find **alternate lines** whose hit rate is **93% or higher**, this season or last season (from game logs). Use the highest line in each category that still clears 93%.
4. Combine all three categories into one leg set when possible (points + rebounds + assists alts for one player, or across the qualifying players).
5. **Edge test:** the sportsbook's implied probability for the parlay must be lower than the true probability that all of the events happen. Estimate the true probability from the hit rates (multiply them, and shrink toward the market for correlation and small samples). Positive edge = true probability > implied.
6. If no combination has a positive edge today, still post the best possible version, but only a parlay that passes the edge test gets the **highlight** (`"highlight": true` on the play, shown as "+EV" in the app).


**Real lines only (user, 2026-10-22: "I can't even move the alt line that low, just give me good player props instead"):** every prop leg must be a line a sportsbook actually lists, at a price found on a public page: the main line or an alt rung the book shows. Never estimate a price for an alt line no book has published, and never use deep alts like 9+ points at -1400. When the 93% rung isn't offered, use the main line, and build the usage SGP from 2-3 real main-line props (ideally priced around +150 to +400). The 93% hit rate stays a research input, not the line to bet.

## 2. NBA totals
For each team: projected points = average of (team offensive efficiency x possessions per game / 100) and (opponent defensive efficiency x possessions per game / 100). Use the average possessions for both teams. Projected total = the sum of the two teams' projections. Compare it with the posted total, and give the projection in the research.

## 3. Always also cover
- Every player and team stat from last season and this season as the year goes on.
- Injury reports and news about the team or players.
- Usage rates.
- Team chemistry and new additions.
- Coaching schemes and tendencies: pace, rotation length, minutes limits, rest habits.

## Output
Write the usual analyst file. Put the usage SGP legs in "extra_legs" as props:
- desc: "J. Tatum o19.5 points", "J. Tatum o5.5 rebounds", "J. Tatum o3.5 assists"
- stat: points, rebounds or assists; "alt": true and "main_line" when the line is an alternate
- each leg's "hit_rate" and the sample used
- "sgp": {"legs": [descs], "odds": quoted SGP price if found, "true_prob": ..., "implied_prob": ..., "edge": true or false}

The totals projection goes in "research" and in the total leg's note.
