"""Unit tests for the pure helpers in webui/server.py.

No GPU, KataGo binary, or auth file needed: coordinate conversion,
`showboard` parsing, kata-analyze report parsing and point-loss math are
plain functions, and the engine/auth file are both set up lazily so
importing the module under test spawns nothing and reads nothing.

Run from the repo root:
    webui-venv/bin/python -m unittest discover -s tests -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "webui"))

import server  # noqa: E402


def make_engine_stub(size):
    """An Engine-like object with only `size` set — enough for the pure
    coordinate helpers, without starting KataGo."""
    e = server.Engine.__new__(server.Engine)
    e.size = size
    return e


SHOWBOARD_9 = """\
   A B C D E F G H J
 9 . . . . . . . . .
 8 . . . . . . . . .
 7 . . . O . . . . .
 6 . . . X . . . . .
 5 . . . . . . . . .
 4 . . . . . . . . .
 3 . . . X . . . . .
 2 . . . . . . . . .
 1 . . . . . . . . .
"""

SHOWBOARD_19 = """\
   A B C D E F G H J K L M N O P Q R S T
19 . . . . . . . . . . . . . . . . . . O
18 . . . . . . . . . . . . . . . . . . .
17 . . . . . . . . . . . . . . . X . . .
16 . . . . . . . . . . . . . . . . . . .
15 . . . . . . . . . . . . . . . . . . .
14 . . . . . . . . . . . . . . . . . . .
13 . . . . . . . . . . . . . . . . . . .
12 . . . . . . . . . . . . . . . . . . .
11 . . . . . . . . . . . . . . . . . . .
10 . . . . . . . . . . . . . . . . . . .
 9 . . . . . . . . . . . . . . . . . . .
 8 . . . . . . . . . . . . . . . . . . .
 7 . . . . . . . . . . . . . . . . . . .
 6 . . . . . . . . . . . . . . . . . . .
 5 . . . . . . . . . . . . . . . . . . .
 4 . . . . . . . . . X . . . . . . . . .
 3 . . . . . . . . . . . . . . . . . . .
 2 . . . . . . . . . . . . . . . . . . .
 1 . . . . . . . . . . . . . . . . . . .
"""


class TestCoordinateConversion(unittest.TestCase):
    def test_roundtrip_all_sizes(self):
        for size in (9, 13, 19):
            e = make_engine_stub(size)
            for x in range(size):
                for y in range(size):
                    gtp = e.coord_to_gtp(x, y)
                    back = e.gtp_to_coord(gtp)
                    self.assertEqual(
                        back, {"x": x, "y": y},
                        f"size={size} x={x} y={y} gtp={gtp} -> {back}")

    def test_skips_letter_i(self):
        # GTP column letter 8 is J, never I.
        e = make_engine_stub(9)
        self.assertEqual(e.coord_to_gtp(8, 0), "J9")
        self.assertEqual(e.gtp_to_coord("J9"), {"x": 8, "y": 0})

    def test_origin_and_corners(self):
        e = make_engine_stub(19)
        self.assertEqual(e.coord_to_gtp(0, 0), "A19")   # top-left
        self.assertEqual(e.coord_to_gtp(18, 18), "T1")  # bottom-right
        self.assertEqual(e.coord_to_gtp(0, 18), "A1")   # bottom-left
        self.assertEqual(e.coord_to_gtp(18, 0), "T19")  # top-right

    def test_special_moves(self):
        e = make_engine_stub(9)
        self.assertEqual(e.gtp_to_coord("pass"), {"special": "pass"})
        self.assertEqual(e.gtp_to_coord("PASS"), {"special": "pass"})
        self.assertEqual(e.gtp_to_coord("resign"), {"special": "resign"})
        self.assertEqual(e.gtp_to_coord(" pass\n"), {"special": "pass"})

    def test_tolerates_surrounding_text(self):
        e = make_engine_stub(9)
        self.assertEqual(e.gtp_to_coord("D4"), {"x": 3, "y": 5})


class TestParseShowboard(unittest.TestCase):
    def test_empty_9(self):
        empty = ["   " + " ".join(server.COLS[:9])] + [
            f"{r:2d} " + " ".join(["."] * 9) for r in range(9, 0, -1)
        ]
        self.assertEqual(server.parse_showboard(empty, 9), [])

    def test_stones_9(self):
        stones = server.parse_showboard(SHOWBOARD_9.split("\n"), 9)
        self.assertEqual(stones, [
            {"x": 3, "y": 2, "color": "W"},  # O D7
            {"x": 3, "y": 3, "color": "B"},  # X D6
            {"x": 3, "y": 6, "color": "B"},  # X D3
        ])

    def test_stones_19_two_digit_rows_and_last_column(self):
        stones = server.parse_showboard(SHOWBOARD_19.split("\n"), 19)
        self.assertEqual(stones, [
            {"x": 18, "y": 0, "color": "W"},  # O T19 — last column, letter T not I
            {"x": 15, "y": 2, "color": "B"},  # X Q17
            {"x": 9, "y": 15, "color": "B"},  # X K4
        ])

    def test_garbage_returns_empty(self):
        self.assertEqual(server.parse_showboard(["hello", "world"], 9), [])
        self.assertEqual(server.parse_showboard([], 9), [])


class TestParseAnalyzeReport(unittest.TestCase):
    # Shaped like a real kata-analyze report: several info-move segments per
    # line, each ending in its pv before the next `info move`.
    TWO_MOVES = (
        "info move D4 visits 45 utility 0.02 winrate 0.514 scoreLead 0.6 "
        "prior 0.31 order 0 pv D4 Q16 "
        "info move Q16 visits 30 utility -0.01 winrate 0.486 scoreLead -0.6 "
        "prior 0.22 order 1 pv Q16 D4"
    )

    def test_two_moves_all_fields(self):
        entries = server.parse_analyze_report(self.TWO_MOVES)
        self.assertEqual(entries, [
            {"move": "D4", "winrate": 0.514, "scoreLead": 0.6, "order": 0},
            {"move": "Q16", "winrate": 0.486, "scoreLead": -0.6, "order": 1},
        ])

    def test_negative_score_lead(self):
        entries = server.parse_analyze_report(
            "info move Q16 winrate 0.4 scoreLead -12.5 order 0")
        self.assertEqual(entries[0]["scoreLead"], -12.5)

    def test_pass_is_just_a_move_to_the_parser(self):
        # pass/resign filtering is candidates()'s job, not the parser's.
        entries = server.parse_analyze_report(
            "info move pass winrate 0.1 scoreLead -3.0 order 0")
        self.assertEqual(entries, [{"move": "pass", "winrate": 0.1,
                                    "scoreLead": -3.0, "order": 0}])

    def test_segment_missing_optional_fields_still_listed(self):
        entries = server.parse_analyze_report("info move E5 visits 3")
        self.assertEqual(entries, [{"move": "E5"}])

    def test_text_before_first_segment_ignored(self):
        entries = server.parse_analyze_report("= info move D4 order 0")
        self.assertEqual(entries, [{"move": "D4", "order": 0}])

    def test_garbage_returns_empty(self):
        self.assertEqual(server.parse_analyze_report(""), [])
        self.assertEqual(server.parse_analyze_report("hello world"), [])
        self.assertEqual(server.parse_analyze_report("pv D4 Q16 only"), [])


class TestCandidatesAndAnalyze(unittest.TestCase):
    # All moves must exist on a 9x9 board (the stub engine's size): E5 and D4.
    REPORT = (
        "info move E5 winrate 0.4 scoreLead -2.0 order 1 pv E5 D4 "
        "info move pass winrate 0.1 scoreLead -9.0 order 2 "
        "info move D4 winrate 0.6 scoreLead 1.5 order 0 pv D4 E5"
    )

    def make_analyzing_stub(self, report):
        e = make_engine_stub(9)
        e._analyze_last_report = lambda *a, **k: report
        return e

    def test_candidates_sort_filter_convert(self):
        # Input line is not in order sequence: D4 (order 0) must come first,
        # the pass candidate must be dropped, coords converted to x/y grid.
        cands = self.make_analyzing_stub(self.REPORT).candidates("b")
        self.assertEqual([(c["coord"], c["order"]) for c in cands], [
            ({"x": 3, "y": 5}, 0),   # D4
            ({"x": 4, "y": 4}, 1),   # E5
        ])

    def test_candidates_top_n(self):
        cands = self.make_analyzing_stub(self.REPORT).candidates("b", top_n=1)
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0]["coord"], {"x": 3, "y": 5})

    def test_analyze_takes_first_reported_move(self):
        # analyze() reads the report's lead move as-is (real reports lead with
        # the engine's best move); it does not re-rank by `order`.
        result = self.make_analyzing_stub(self.REPORT).analyze("b")
        self.assertEqual(result, {"winrate": 0.4, "scoreLead": -2.0})

    def test_analyze_none_when_no_report(self):
        self.assertIsNone(self.make_analyzing_stub("").analyze("b"))

    def test_eval_as_black_frame_converts_white_view(self):
        e = make_engine_stub(9)
        e.analyze = lambda color: {"winrate": 0.3, "scoreLead": -2.0}
        self.assertEqual(e.eval_as_black_frame("b"),
                         {"winrate": 0.3, "scoreLead": -2.0})
        self.assertEqual(e.eval_as_black_frame("w"),
                         {"winrate": 0.7, "scoreLead": 2.0})


class TestComputePointLoss(unittest.TestCase):
    def test_human_black(self):
        # Before: Black leads by 5. After the move, an analysis from White's
        # point of view (scoreLead +3.0 = White leads by 3, i.e. Black -3 in
        # the black frame): the move cost 8 points.
        self.assertEqual(server.compute_point_loss(5.0, 3.0, "b"), 8.0)

    def test_human_white(self):
        # Before: Black leads by 2. After, Black leads by 4: White lost 2.
        self.assertEqual(server.compute_point_loss(2.0, 4.0, "w"), 2.0)

    def test_negative_loss_clamped_to_zero(self):
        # Before: Black leads by 1. After, Black leads by 2 — from White's
        # point of view that's scoreLead -2.0. A "gain" (noise or the
        # opponent slipping) must not show as negative loss.
        self.assertEqual(server.compute_point_loss(1.0, -2.0, "b"), 0.0)

    def test_rounded_to_one_decimal(self):
        self.assertEqual(server.compute_point_loss(1.23, 2.0, "w"), 0.8)


class TestLazyEngine(unittest.TestCase):
    def test_import_does_not_spawn_engine(self):
        # The module-level engine must stay None until the first request, so
        # this test file can import server.py without KataGo running.
        self.assertIsNone(server.engine)

    def test_import_does_not_read_auth(self):
        # The auth file is likewise read on first use, not at import, so this
        # suite runs on machines without ~/katago/.auth.
        self.assertIsNone(server.AUTH_USER)


class FakeGtpEngine(server.Engine):
    """Engine whose GTP pipe is a scripted list of replies."""

    def __init__(self, human_color, replies):
        self.size = 9
        self.lock = server.threading.RLock()
        self.human_color = human_color
        self.replies = list(replies)
        self.sent = []

    def _ensure_process(self):
        pass

    def _raw_send(self, command):
        self.sent.append(command)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class UndoTurnTests(unittest.TestCase):
    def test_black_undoes_both_plies(self):
        e = FakeGtpEngine("b", [["= "], ["= "]])
        self.assertTrue(e.undo_turn())
        self.assertEqual(e.sent, ["undo", "undo"])

    def test_nothing_to_undo(self):
        e = FakeGtpEngine("b", [["? cannot undo"]])
        self.assertFalse(e.undo_turn())

    def test_white_replays_opening_when_only_opening_left(self):
        e = FakeGtpEngine("w", [["= "], ["? cannot undo"], ["= E5"]])
        self.assertTrue(e.undo_turn())
        self.assertEqual(e.sent, ["undo", "undo", "genmove b"])


class EngineRestartTests(unittest.TestCase):
    def test_cmd_does_not_replay_after_restart(self):
        e = FakeGtpEngine("b", [RuntimeError("dead"), ["= "]])
        with self.assertRaises(server.EngineRestarted):
            e.cmd("play b E5")
        self.assertEqual(e.sent, ["play b E5"])


class GameOverTests(unittest.TestCase):
    def test_two_consecutive_passes_end_game(self):
        e = FakeGtpEngine("b", [])
        e.passes, e.resigned = 0, False
        e.note_move(True)
        self.assertFalse(e.game_over)
        e.note_move(True)
        self.assertTrue(e.game_over)

    def test_stone_resets_pass_count(self):
        e = FakeGtpEngine("b", [])
        e.passes, e.resigned = 0, False
        e.note_move(True)
        e.note_move(False)
        e.note_move(True)
        self.assertFalse(e.game_over)

    def test_resign_ends_game(self):
        e = FakeGtpEngine("b", [])
        e.passes, e.resigned = 0, True
        self.assertTrue(e.game_over)
        self.assertIn("resigned", e.final_result())


if __name__ == "__main__":
    unittest.main()
