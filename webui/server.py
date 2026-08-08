import os
import re
import subprocess
import threading
import time

from flask import Flask, jsonify, request, send_from_directory

KATAGO_BIN = os.path.expanduser("~/katago/src/cpp/build/katago")
MODEL = os.path.expanduser(
    "~/katago/models/kata1-b28c512nbt-s13255194368-d5935380940.bin.gz"
)
CONFIG = os.path.expanduser("~/katago/gtp.cfg")
COLS = "ABCDEFGHJKLMNOPQRSTUVWXYZ"

DIFFICULTIES = {
    "beginner": {"maxVisits": 1, "chosenMoveTemperature": 1.0, "chosenMoveTemperatureEarly": 1.0},
    "easy": {"maxVisits": 10, "chosenMoveTemperature": 0.5, "chosenMoveTemperatureEarly": 0.6},
    "normal": {"maxVisits": 100, "chosenMoveTemperature": 0.15, "chosenMoveTemperatureEarly": 0.2},
    "strong": {"maxVisits": 500, "chosenMoveTemperature": 0.0, "chosenMoveTemperatureEarly": 0.0},
}
DEFAULT_DIFFICULTY = "normal"
ANALYSIS_VISITS = 300


class Engine:
    def __init__(self):
        self.lock = threading.Lock()
        self.size = 9
        self.proc = None
        self.difficulty = DEFAULT_DIFFICULTY
        self.last_black_eval = {"winrate": 0.5, "scoreLead": 0.0}
        self._start_process()
        self._reset(self.size)
        self.set_difficulty(self.difficulty)
        self.last_black_eval = self.black_eval()

    def set_difficulty(self, level):
        params = DIFFICULTIES.get(level)
        if not params:
            return False
        with self.lock:
            for key, value in params.items():
                self.proc.stdin.write(f"kata-set-param {key} {value}\n")
                self.proc.stdin.flush()
                self._read_until_blank()
        self.difficulty = level
        return True

    def _start_process(self):
        self.proc = subprocess.Popen(
            [KATAGO_BIN, "gtp", "-model", MODEL, "-config", CONFIG],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )

    def _raw_send(self, command):
        self.proc.stdin.write(command + "\n")
        self.proc.stdin.flush()
        return self._read_until_blank()

    def cmd(self, command):
        with self.lock:
            return self._raw_send(command)

    def _reset(self, size):
        komi = {9: 5.5, 13: 6.5}.get(size, 7.5)
        self._raw_send(f"boardsize {size}")
        self._raw_send(f"komi {komi}")
        self._raw_send("clear_board")
        self.size = size

    def new_game(self, size):
        with self.lock:
            self._reset(size)
        self.last_black_eval = self.black_eval()

    def coord_to_gtp(self, x, y):
        col = COLS[x]
        row = self.size - y
        return f"{col}{row}"

    def gtp_to_coord(self, s):
        s = s.strip()
        low = s.lower()
        if low in ("pass", "resign"):
            return {"special": low}
        col = s[0].upper()
        row = int(s[1:])
        x = COLS.index(col)
        y = self.size - row
        return {"x": x, "y": y}

    def board(self):
        with self.lock:
            lines = self._raw_send("showboard")
        expected_header = " ".join(COLS[: self.size])
        header_idx = None
        for i, l in enumerate(lines):
            if l.strip() == expected_header:
                header_idx = i
                break
        stones = []
        if header_idx is not None:
            hline = lines[header_idx]
            col_positions = [hline.index(c) for c in COLS[: self.size]]
            for j in range(self.size):
                row_line = lines[header_idx + 1 + j]
                rm = re.match(r"^\s*(\d+)\s", row_line)
                if not rm:
                    continue
                rownum = int(rm.group(1))
                y = self.size - rownum
                for x, pos in enumerate(col_positions):
                    ch = row_line[pos] if pos < len(row_line) else "."
                    if ch in ("X", "O"):
                        stones.append({"x": x, "y": y, "color": "B" if ch == "X" else "W"})
        return stones

    def genmove_white(self):
        resp = self.cmd("genmove w")
        text = resp[0][1:].strip() if resp and resp[0].startswith("=") else ""
        return self.gtp_to_coord(text) if text else None

    def analyze(self, color, duration=0.8, interval_cs=15):
        """Runs kata-analyze briefly and returns the top move's winrate/scoreLead
        from the given color's perspective, or None if nothing came back."""
        last_report = self._analyze_last_report(color, duration, interval_cs)
        if not last_report:
            return None
        wr = re.search(r"winrate ([\d.]+)", last_report)
        sl = re.search(r"scoreLead (-?[\d.]+)", last_report)
        if not wr or not sl:
            return None
        return {"winrate": float(wr.group(1)), "scoreLead": float(sl.group(1))}

    def _analyze_last_report(self, color, duration, interval_cs):
        difficulty_visits = DIFFICULTIES[self.difficulty]["maxVisits"]
        with self.lock:
            # Analysis quality (win rate / hints) must not be capped by the
            # difficulty setting, which only governs how well the AI *plays*.
            if difficulty_visits < ANALYSIS_VISITS:
                self.proc.stdin.write(f"kata-set-param maxVisits {ANALYSIS_VISITS}\n")
                self.proc.stdin.flush()
                self._read_until_blank()
            self.proc.stdin.write(f"kata-analyze {color} {interval_cs}\n")
            self.proc.stdin.flush()
            time.sleep(duration)
            self.proc.stdin.write("name\n")
            self.proc.stdin.flush()
            analyze_lines = self._read_until_blank()
            self._read_until_blank()  # drain the interrupt command's own response
            if difficulty_visits < ANALYSIS_VISITS:
                self.proc.stdin.write(f"kata-set-param maxVisits {difficulty_visits}\n")
                self.proc.stdin.flush()
                self._read_until_blank()
        last_report = None
        for l in analyze_lines:
            if l.startswith("info move") or " info move " in l:
                last_report = l
        return last_report

    def candidates(self, color, top_n=3, duration=0.8, interval_cs=15):
        """Top-N candidate moves (by order) with winrate/scoreLead, for the given color."""
        last_report = self._analyze_last_report(color, duration, interval_cs)
        if not last_report:
            return []
        segments = re.split(r"(?=info move )", last_report)
        parsed = []
        for seg in segments:
            if not seg.startswith("info move "):
                continue
            mv = re.search(r"^info move (\S+)", seg)
            wr = re.search(r"winrate ([\d.]+)", seg)
            sl = re.search(r"scoreLead (-?[\d.]+)", seg)
            od = re.search(r"\border (\d+)", seg)
            if not (mv and wr and sl and od):
                continue
            move_str = mv.group(1)
            if move_str.lower() in ("pass", "resign"):
                continue
            parsed.append({
                "coord": self.gtp_to_coord(move_str),
                "winrate": float(wr.group(1)),
                "scoreLead": float(sl.group(1)),
                "order": int(od.group(1)),
            })
        parsed.sort(key=lambda c: c["order"])
        return parsed[:top_n]

    def _read_until_blank(self):
        lines = []
        while True:
            line = self.proc.stdout.readline()
            if line == "":
                raise RuntimeError("katago process ended unexpectedly")
            line = line.rstrip("\n")
            if line == "":
                if lines:
                    break
                continue
            lines.append(line)
        return lines

    def black_eval(self):
        """Black-perspective winrate/scoreLead, tolerant of a failed analyze pass."""
        result = self.analyze("b")
        if result is None:
            return {"winrate": 0.5, "scoreLead": 0.0}
        return result


engine = Engine()
app = Flask(__name__, static_folder="static")


@app.route("/api/difficulty", methods=["GET", "POST"])
def api_difficulty():
    if request.method == "POST":
        data = request.get_json(force=True) or {}
        level = data.get("level", "")
        if not engine.set_difficulty(level):
            return jsonify({"ok": False, "error": "unknown level"}), 400
    return jsonify({"ok": True, "level": engine.difficulty, "levels": list(DIFFICULTIES.keys())})


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/suggest", methods=["GET"])
def api_suggest():
    cands = engine.candidates("b")
    moves = [
        {"x": c["coord"]["x"], "y": c["coord"]["y"], "winrate": c["winrate"],
         "score_lead": c["scoreLead"], "rank": c["order"] + 1}
        for c in cands
        if "x" in c["coord"]
    ]
    # Use this exact search as the point-loss baseline for whatever gets
    # played next, so following the #1 hint can't show a false "mistake"
    # just because a separate, independently-noisy search ran afterwards.
    if cands:
        top = cands[0]
        engine.last_black_eval = {"winrate": top["winrate"], "scoreLead": top["scoreLead"]}
    return jsonify({"ok": True, "moves": moves})


@app.route("/api/new", methods=["POST"])
def api_new():
    data = request.get_json(force=True) or {}
    size = int(data.get("size", 9))
    engine.new_game(size)
    ev = engine.last_black_eval
    return jsonify({
        "ok": True, "size": size, "stones": engine.board(),
        "winrate": ev["winrate"], "score_lead": ev["scoreLead"],
    })


def _after_black_move():
    """Call after a black move has been played (before white replies).
    Returns point loss for that move using the cached pre-move eval."""
    before = engine.last_black_eval
    white_view = engine.analyze("w")
    if white_view is None:
        return None
    after_black_score = -white_view["scoreLead"]
    return round(max(0.0, before["scoreLead"] - after_black_score), 1)


def _after_white_move():
    """Call after white has replied; refreshes the cached black-perspective eval."""
    engine.last_black_eval = engine.black_eval()
    return engine.last_black_eval


@app.route("/api/move", methods=["POST"])
def api_move():
    data = request.get_json(force=True)
    x, y = int(data["x"]), int(data["y"])
    gtp_coord = engine.coord_to_gtp(x, y)
    resp = engine.cmd(f"play b {gtp_coord}")
    if resp and resp[0].startswith("?"):
        return jsonify({"ok": False, "error": resp[0]}), 400
    point_loss = _after_black_move()
    ai_move = engine.genmove_white()
    eval_now = _after_white_move()
    return jsonify({
        "ok": True, "ai_move": ai_move, "stones": engine.board(),
        "point_loss": point_loss, "winrate": eval_now["winrate"],
        "score_lead": eval_now["scoreLead"],
    })


@app.route("/api/pass", methods=["POST"])
def api_pass():
    engine.cmd("play b pass")
    point_loss = _after_black_move()
    ai_move = engine.genmove_white()
    eval_now = _after_white_move()
    return jsonify({
        "ok": True, "ai_move": ai_move, "stones": engine.board(),
        "point_loss": point_loss, "winrate": eval_now["winrate"],
        "score_lead": eval_now["scoreLead"],
    })


@app.route("/api/undo", methods=["POST"])
def api_undo():
    engine.cmd("undo")
    engine.cmd("undo")
    eval_now = engine.black_eval()
    engine.last_black_eval = eval_now
    return jsonify({
        "ok": True, "stones": engine.board(),
        "winrate": eval_now["winrate"], "score_lead": eval_now["scoreLead"],
    })


@app.route("/api/score", methods=["GET"])
def api_score():
    resp = engine.cmd("final_score")
    text = resp[0][1:].strip() if resp and resp[0].startswith("=") else ""
    return jsonify({"ok": True, "result": text})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8765, threaded=False)
