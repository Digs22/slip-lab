"""Unit tests for the money math, grading, play builder and learning loop. Inputs are made-up test cases."""
import unittest

from pipeline import builder, grade, ledger, learn, odds
from pipeline.analyze import model_prob


class Odds(unittest.TestCase):
    def test_conversions(self):
        self.assertAlmostEqual(odds.to_decimal(-115), 1.8696, 3)
        self.assertEqual(odds.to_american(2.5), 150)
        self.assertEqual(odds.to_win(12, -115), 10.43)
        self.assertEqual(odds.to_win(3, 1127), 33.81)

    def test_parlay_matches_record_sheet(self):
        # SEA @ DEN longshot: +215, +100, -110 -> about +1103
        self.assertAlmostEqual(odds.parlay_american([215, 100, -110]), 1103, delta=2)

    def test_no_vig(self):
        a, b = odds.no_vig(-110, -110)
        self.assertAlmostEqual(a, 0.5)


class Ledger(unittest.TestCase):
    def test_seed_totals(self):
        s = ledger.summary(ledger.load())["all"]
        self.assertEqual((s["w"], s["l"], s["pending"]), (1, 2, 5))
        self.assertEqual(s["net"], -1.57)
        self.assertEqual(s["roi"], -6.5)


GAME = {"home": "DEN", "away": "SEA", "home_name": "Denver Broncos", "home_score": 17.0, "away_score": 20.0}


class Grade(unittest.TestCase):
    def test_total_and_spread(self):
        self.assertEqual(grade.leg_result({"market": "total", "side": "under", "line": 40.5}, GAME, {}), "win")
        self.assertEqual(grade.leg_result({"market": "spread", "team": "SEA", "line": -8.5}, GAME, {}), "loss")
        self.assertEqual(grade.leg_result({"market": "spread", "team": "SEA", "line": -3}, GAME, {}), "push")
        self.assertEqual(grade.leg_result({"market": "ml", "team": "Denver Broncos"}, GAME, {}), "loss")

    def test_prop_and_parlay_push(self):
        stats = {"Bo Nix": {"pass_yds": 188}}
        self.assertEqual(grade.leg_result({"market": "prop", "player": "Bo Nix", "stat": "pass_yds",
                                           "side": "under", "line": 209.5}, GAME, stats), "win")
        bet = {"stake": 6.0, "odds": 300, "legs": [{"odds": 100, "result": "win"}, {"odds": 100, "result": "push"}]}
        self.assertEqual(grade.bet_result(bet), "win")
        self.assertEqual(bet["odds"], 100)


def leg(desc, market, prob, o, fair=0.5, **kw):
    return {"desc": desc, "market": market, "prob": prob, "odds": o, "fair": fair,
            "edge": odds.edge(prob, o), **kw}


class Builder(unittest.TestCase):
    def test_four_plays_and_reuse_cap(self):
        legs = [leg("Under 40.5", "total", 0.6, -108, side="under", line=40.5),
                leg("JSN o75.5", "prop", 0.6, -115, player="JSN"),
                leg("Nix u209.5", "prop", 0.6, -115, player="Nix"),
                leg("JSN TD", "prop", 0.5, 110, player="JSN2"),
                leg("Wilson TD", "prop", 0.5, -105, player="Wilson"),
                leg("SEA -8.5", "spread", 0.3, 230, fair=0.27, team="SEA")]
        plays = builder.build_plays({"legs": legs, "low_total": True})
        self.assertEqual(set(plays), {"straight", "safer", "best", "longshot"})
        self.assertGreaterEqual(plays["longshot"]["odds"], 1000)
        counts = {}
        for p in plays.values():
            for l in p["legs"]:
                counts[l["desc"]] = counts.get(l["desc"], 0) + 1
        self.assertLessEqual(max(counts.values()), 2)
        self.assertIn(plays["straight"]["legs"][0]["market"], ("total", "prop"))

    def test_safer_skips_coin_flip_side(self):
        legs = [leg("CLE +1.5", "spread", 0.7, -200, fair=0.52, team="CLE"),
                leg("Under 7", "total", 0.62, -115, side="under", line=7),
                leg("Ramirez hit", "prop", 0.66, -160, player="Ramirez"),
                leg("Diaz u1.5", "prop", 0.62, -145, player="Diaz")]
        plays = builder.build_plays({"legs": legs, "low_total": True})
        self.assertNotIn("CLE +1.5", [l["desc"] for l in plays["safer"]["legs"]])


class Learn(unittest.TestCase):
    def test_model_moves_toward_signal_but_capped(self):
        p = model_prob(0.5, {"recent_form": 0.9}, {"recent_form": 0.8})
        self.assertTrue(0.5 < p < 0.75)

    def test_cause(self):
        self.assertEqual(learn.tag_cause({"odds": -110, "closing_odds": -130}), "variance")
        self.assertEqual(learn.tag_cause({"odds": -110}), "bad read")


if __name__ == "__main__":
    unittest.main()
