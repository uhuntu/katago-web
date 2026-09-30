"""Unit tests for the pure helpers in webui/server.py.

No GPU or KataGo binary needed: coordinate conversion and `showboard` parsing
are plain functions, and the engine is created lazily so importing the module
under test does not spawn a subprocess.

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


class TestLazyEngine(unittest.TestCase):
    def test_import_does_not_spawn_engine(self):
        # The module-level engine must stay None until the first request, so
        # this test file can import server.py without KataGo running.
        self.assertIsNone(server.engine)


if __name__ == "__main__":
    unittest.main()
