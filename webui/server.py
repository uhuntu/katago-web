import hmac
import os
import re
import subprocess
import threading
import time

from flask import Flask, Response, jsonify, request, send_from_directory

def _env_path(name, default):
    return os.path.expanduser(os.environ.get(name, default))


AUTH_FILE = _env_path("KATAGO_AUTH_FILE", "~/katago/.auth")

AUTH_USER, AUTH_PASS = None, None
# Opt-in local-only mode: skip Basic Auth. Honored only when the server binds
# to loopback (enforced in __main__), so it can't be exposed by accident.
NO_AUTH = os.environ.get("KATAGO_NO_AUTH") == "1"


def load_auth():
    """Reads the auth file on first use rather than at import, so this module
    (and the unit tests that import it) load on machines that don't have
    ~/katago/.auth — e.g. a laptop clone of the repo. __main__ calls this
    before app.run() to keep the fail-fast-at-startup behavior."""
    global AUTH_USER, AUTH_PASS
    if AUTH_USER is None:
        with open(AUTH_FILE) as f:
            AUTH_USER, _, AUTH_PASS = f.read().strip().partition(":")
    return AUTH_USER, AUTH_PASS

# Each path can be overridden by an env var so the same server runs on the
# Linux box (defaults) and on a laptop (see README, "Running locally").
KATAGO_BIN = _env_path("KATAGO_BIN", "~/katago/src/cpp/build/katago")
MODEL = _env_path(
    "KATAGO_MODEL", "~/katago/models/kata1-b28c512nbt-s13255194368-d5935380940.bin.gz")
CONFIG = _env_path("KATAGO_CONFIG", "~/katago/gtp.cfg")
ENGINE_LOG = _env_path("KATAGO_ENGINE_LOG", "~/katago/gtp_logs/engine.log")
COLS = "ABCDEFGHJKLMNOPQRSTUVWXYZ"

DIFFICULTIES = {
    "beginner": {"maxVisits": 1, "chosenMoveTemperature": 1.0, "chosenMoveTemperatureEarly": 1.0},
    "easy": {"maxVisits": 10, "chosenMoveTemperature": 0.5, "chosenMoveTemperatureEarly": 0.6},
    "normal": {"maxVisits": 100, "chosenMoveTemperature": 0.15, "chosenMoveTemperatureEarly": 0.2},
    "strong": {"maxVisits": 500, "chosenMoveTemperature": 0.0, "chosenMoveTemperatureEarly": 0.0},
}
DEFAULT_DIFFICULTY = "normal"
ANALYSIS_VISITS = 300
ANALYSIS_INTERVAL_CS = 10

engine = None


class EngineRestarted(RuntimeError):
    """The KataGo process died mid-request and was restarted. The restart
    clears the board, so the interrupted command must not be replayed."""


def get_engine():
    """Lazily start the KataGo process on first use (also keeps the module
    importable by the unit tests, which must not spawn a subprocess)."""
    global engine
    if engine is None:
        engine = Engine()
    return engine


def parse_showboard(lines, size):
    """Parse KataGo `showboard` output lines into [{"x", "y", "color"}].
    x is the column index (left = 0, skipping the letter I, per GTP), y is the
    row index (top = 0). Pure function so it can be unit-tested without a GPU."""
    expected_header = " ".join(COLS[:size])
    header_idx = None
    for i, l in enumerate(lines):
        if l.strip() == expected_header:
            header_idx = i
            break
    stones = []
    if header_idx is None:
        return stones
    hline = lines[header_idx]
    col_positions = [hline.index(c) for c in COLS[:size]]
    for j in range(size):
        row_line = lines[header_idx + 1 + j]
        rm = re.match(r"^\s*(\d+)\s", row_line)
        if not rm:
            continue
        rownum = int(rm.group(1))
        y = size - rownum
        for x, pos in enumerate(col_positions):
            ch = row_line[pos] if pos < len(row_line) else "."
            if ch in ("X", "O"):
                stones.append({"x": x, "y": y, "color": "B" if ch == "X" else "W"})
    return stones


def parse_analyze_report(report):
    """Split one kata-analyze report line into its per-move segments and pull
    out the fields the UI needs. Pure function so it can be unit-tested
    without a GPU. Returns one dict per `info move` segment; "move" is always
    present, "winrate"/"scoreLead"/"order" only when that segment had them."""
    parsed = []
    for seg in re.split(r"(?=info move )", report):
        if not seg.startswith("info move "):
            continue
        mv = re.search(r"^info move (\S+)", seg)
        if not mv:
            continue
        entry = {"move": mv.group(1)}
        wr = re.search(r"winrate ([\d.]+)", seg)
        if wr:
            entry["winrate"] = float(wr.group(1))
        sl = re.search(r"scoreLead (-?[\d.]+)", seg)
        if sl:
            entry["scoreLead"] = float(sl.group(1))
        od = re.search(r"\border (\d+)", seg)
        if od:
            entry["order"] = int(od.group(1))
        parsed.append(entry)
    return parsed


def compute_point_loss(before_black_lead, opp_score_lead, human_color):
    """Point loss for the human's last move: the cached pre-move black-frame
    score lead versus the score lead from an analysis run at the opponent's
    point of view (`opp_score_lead`). Pure function so the color-frame
    arithmetic is unit-testable without an engine."""
    opponent_color = "w" if human_color == "b" else "b"
    after_black_score = opp_score_lead if opponent_color == "b" else -opp_score_lead
    if human_color == "b":
        loss = before_black_lead - after_black_score
    else:
        loss = after_black_score - before_black_lead
    return round(max(0.0, loss), 1)


class Engine:
    def __init__(self):
        self.lock = threading.RLock()
        self.size = 9
        self.proc = None
        os.makedirs(os.path.dirname(ENGINE_LOG), exist_ok=True)
        self.log = open(ENGINE_LOG, "a")
        self.difficulty = DEFAULT_DIFFICULTY
        self.human_color = "b"
        self.passes = 0  # consecutive passes; two end the game
        self.resigned = False
        self.last_black_eval = {"winrate": 0.5, "scoreLead": 0.0}
        self._start_process()
        self._reset(self.size)
        self.set_difficulty(self.difficulty)
        self.last_black_eval = self.black_eval()
        threading.Thread(target=self._watchdog, daemon=True).start()

    def _watchdog(self):
        """Respawn the KataGo process if it ever dies. Board state lives in the
        engine, so a restart means the current game is lost (logged loudly);
        this keeps the server from 500-ing on every request forever."""
        while True:
            time.sleep(5)
            try:
                self._ensure_process()
            except Exception as e:  # the watchdog itself must never die
                print(f"[engine watchdog] ensure_process failed: {e!r}", flush=True)

    def _alive(self):
        return self.proc is not None and self.proc.poll() is None

    def _ensure_process(self):
        """Re-entrant with self.lock (RLock): restarts KataGo if it died and
        restores boardsize/komi and the difficulty params. The whole restart
        sequence holds the lock, so a request arriving mid-restart waits for a
        consistent process instead of interleaving its own reads on the pipe."""
        if self._alive():
            return
        with self.lock:
            if self._alive():  # another thread may have restarted it meanwhile
                return
            if self.proc is not None:
                print(f"[engine] katago process ended (rc={self.proc.poll()}); "
                      "restarting — board contents reset", flush=True)
            self._start_process()
            self._reset(self.size)
            self.set_difficulty(self.difficulty)
            self.last_black_eval = self.eval_as_black_frame(self.human_color)

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
            stderr=self.log,
            text=True,
            bufsize=1,
        )

    def _raw_send(self, command):
        self.proc.stdin.write(command + "\n")
        self.proc.stdin.flush()
        return self._read_until_blank()

    def cmd(self, command):
        with self.lock:
            self._ensure_process()
            try:
                return self._raw_send(command)
            except (RuntimeError, OSError, ValueError):
                # Engine died mid-request (dead stdout / broken pipe). Restart
                # so later requests work, but don't replay the command: the
                # restart wiped the board, so replaying a play/genmove would
                # land on an empty position and desync the UI.
                self._ensure_process()
                raise EngineRestarted(
                    "engine restarted; the game was reset, please start a new game")

    def _reset(self, size):
        komi = {9: 5.5, 13: 6.5}.get(size, 7.5)
        self._raw_send(f"boardsize {size}")
        self._raw_send(f"komi {komi}")
        self._raw_send("clear_board")
        self.size = size
        self.passes = 0
        self.resigned = False

    def note_move(self, move_is_pass):
        """Track consecutive passes (a human pass or an AI pass counts alike)."""
        self.passes = self.passes + 1 if move_is_pass else 0

    @property
    def game_over(self):
        return self.passes >= 2 or self.resigned

    def final_result(self):
        if self.resigned:
            return "KataGo resigned. You win!"
        resp = self.cmd("final_score")
        return resp[0][1:].strip() if resp and resp[0].startswith("=") else ""

    def new_game(self, size, human_color="b"):
        self.human_color = human_color
        with self.lock:
            self._ensure_process()
            self._reset(size)
        opponent_color = "w" if human_color == "b" else "b"
        ai_move = None
        if human_color == "w":
            # Human plays White; Black (AI) must open.
            ai_move = self.genmove_color(opponent_color)
            self.note_move(bool(ai_move and ai_move.get("special")))
        self.last_black_eval = self.eval_as_black_frame(human_color)
        return ai_move

    def undo_turn(self):
        """Take back the human's last move and the AI's reply. Returns False if
        there is nothing to undo. When the human plays White and only the AI's
        opening move remains, that opening can't be taken back (the human
        would be left to move first as White), so it is replayed instead."""
        def failed(resp):
            return not resp or resp[0].startswith("?")

        with self.lock:
            if failed(self.cmd("undo")):
                return False
            if failed(self.cmd("undo")) and self.human_color == "w":
                self.genmove_color("b")
        self.passes = 0
        self.resigned = False
        return True

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
            self._ensure_process()
            lines = self._raw_send("showboard")
        return parse_showboard(lines, self.size)

    def genmove_color(self, color):
        resp = self.cmd(f"genmove {color}")
        text = resp[0][1:].strip() if resp and resp[0].startswith("=") else ""
        return self.gtp_to_coord(text) if text else None

    def analyze(self, color, duration=0.8, interval_cs=ANALYSIS_INTERVAL_CS):
        """Runs kata-analyze briefly and returns the top move's winrate/scoreLead
        from the given color's perspective, or None if nothing came back."""
        last_report = self._analyze_last_report(color, duration, interval_cs)
        if not last_report:
            return None
        for entry in parse_analyze_report(last_report):
            if "winrate" in entry and "scoreLead" in entry:
                return {"winrate": entry["winrate"], "scoreLead": entry["scoreLead"]}
        return None

    def _analyze_last_report(self, color, duration, interval_cs):
        difficulty_visits = DIFFICULTIES[self.difficulty]["maxVisits"]
        with self.lock:
            # Analysis quality (win rate / hints) must not be capped by the
            # difficulty setting, which only governs how well the AI *plays*.
            self._ensure_process()
            if difficulty_visits < ANALYSIS_VISITS:
                self.proc.stdin.write(f"kata-set-param maxVisits {ANALYSIS_VISITS}\n")
                self.proc.stdin.flush()
                self._read_until_blank()
            self.proc.stdin.write(f"kata-analyze {color} {interval_cs}\n")
            self.proc.stdin.flush()
            # The sleep must cover at least one report interval, otherwise the
            # terminating "name" can arrive before any info line does and the
            # caller would see no analysis at all (silent 50/50 default).
            time.sleep(max(duration, interval_cs / 100 + 0.3))
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

    def candidates(self, color, top_n=3, duration=0.8, interval_cs=ANALYSIS_INTERVAL_CS):
        """Top-N candidate moves (by order) with winrate/scoreLead, for the given color."""
        last_report = self._analyze_last_report(color, duration, interval_cs)
        if not last_report:
            return []
        parsed = []
        for entry in parse_analyze_report(last_report):
            if not ("winrate" in entry and "scoreLead" in entry and "order" in entry):
                continue
            if entry["move"].lower() in ("pass", "resign"):
                continue
            parsed.append({
                "coord": self.gtp_to_coord(entry["move"]),
                "winrate": entry["winrate"],
                "scoreLead": entry["scoreLead"],
                "order": entry["order"],
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

    def eval_as_black_frame(self, color_to_move):
        """Analyzes as whichever color actually has the move right now (required
        by kata-analyze), then converts the result into Black's frame of
        reference so all cached/internal state stays in one consistent frame
        regardless of which color the human is playing."""
        result = self.analyze(color_to_move)
        if result is None:
            return {"winrate": 0.5, "scoreLead": 0.0}
        if color_to_move == "b":
            return result
        return {"winrate": 1 - result["winrate"], "scoreLead": -result["scoreLead"]}

    def state(self):
        """Snapshot for restoring the UI after a page refresh. Always represents
        the human's turn — every move endpoint replies synchronously, so when no
        request is in flight it is the human's move."""
        return {
            "size": self.size,
            "color": self.human_color,
            "difficulty": self.difficulty,
            "stones": self.board(),
            "winrate": self.last_black_eval["winrate"],
            "score_lead": self.last_black_eval["scoreLead"],
            "game_over": self.game_over,
            "result": self.final_result() if self.game_over else None,
        }


app = Flask(__name__, static_folder="static")


@app.before_request
def require_auth():
    if NO_AUTH:
        return None
    auth = request.authorization
    auth_user, auth_pass = load_auth()
    valid = (
        auth is not None
        and hmac.compare_digest(auth.username or "", auth_user)
        and hmac.compare_digest(auth.password or "", auth_pass)
    )
    if not valid:
        return Response(
            "Authentication required", 401,
            {"WWW-Authenticate": 'Basic realm="KataGo"'},
        )


@app.before_request
def ensure_engine_started():
    get_engine()


@app.errorhandler(EngineRestarted)
def handle_engine_restarted(e):
    return jsonify({"ok": False, "error": str(e)}), 409


@app.route("/api/difficulty", methods=["GET", "POST"])
def api_difficulty():
    if request.method == "POST":
        data = request.get_json(force=True) or {}
        level = data.get("level", "")
        if not get_engine().set_difficulty(level):
            return jsonify({"ok": False, "error": "unknown level"}), 400
    return jsonify({"ok": True, "level": get_engine().difficulty,
                    "levels": list(DIFFICULTIES.keys())})


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/suggest", methods=["GET"])
def api_suggest():
    engine = get_engine()
    cands = engine.candidates(engine.human_color)
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
        top_eval = {"winrate": top["winrate"], "scoreLead": top["scoreLead"]}
        if engine.human_color == "b":
            engine.last_black_eval = top_eval
        else:
            engine.last_black_eval = {
                "winrate": 1 - top_eval["winrate"], "scoreLead": -top_eval["scoreLead"],
            }
    return jsonify({"ok": True, "moves": moves})


@app.route("/api/new", methods=["POST"])
def api_new():
    data = request.get_json(force=True) or {}
    size = int(data.get("size", 9))
    color = data.get("color", "b")
    if color not in ("b", "w"):
        color = "b"
    engine = get_engine()
    ai_move = engine.new_game(size, color)
    ev = engine.last_black_eval
    return jsonify({
        "ok": True, "size": size, "color": color, "stones": engine.board(),
        "ai_move": ai_move, "winrate": ev["winrate"], "score_lead": ev["scoreLead"],
    })


@app.route("/api/state", methods=["GET"])
def api_state():
    engine = get_engine()
    return jsonify({"ok": True, **engine.state()})


def _after_human_move():
    """Call after the human's move has been played (before the AI replies).
    Returns point loss for that move using the cached pre-move eval."""
    engine = get_engine()
    opponent_color = "w" if engine.human_color == "b" else "b"
    opp_view = engine.analyze(opponent_color)
    if opp_view is None:
        return None
    return compute_point_loss(
        engine.last_black_eval["scoreLead"], opp_view["scoreLead"], engine.human_color)


def _after_ai_move():
    """Call after the AI has replied; refreshes the cached black-frame eval."""
    engine = get_engine()
    engine.last_black_eval = engine.eval_as_black_frame(engine.human_color)
    return engine.last_black_eval


def _finish_turn():
    """Shared tail of /api/move and /api/pass: score the human's move, let the
    AI reply, refresh the cached eval. Returns the response payload fields."""
    engine = get_engine()
    point_loss = _after_human_move()
    opponent_color = "w" if engine.human_color == "b" else "b"
    ai_move = engine.genmove_color(opponent_color)
    special = (ai_move or {}).get("special")
    engine.note_move(special == "pass")
    engine.resigned = special == "resign"
    eval_now = _after_ai_move()
    return {
        "ai_move": ai_move, "stones": engine.board(),
        "point_loss": point_loss, "winrate": eval_now["winrate"],
        "score_lead": eval_now["scoreLead"],
        "game_over": engine.game_over,
        "result": engine.final_result() if engine.game_over else None,
    }


@app.route("/api/move", methods=["POST"])
def api_move():
    data = request.get_json(force=True)
    engine = get_engine()
    x, y = int(data["x"]), int(data["y"])
    gtp_coord = engine.coord_to_gtp(x, y)
    resp = engine.cmd(f"play {engine.human_color} {gtp_coord}")
    if resp and resp[0].startswith("?"):
        return jsonify({"ok": False, "error": resp[0]}), 400
    engine.note_move(False)
    return jsonify({"ok": True, **_finish_turn()})


@app.route("/api/pass", methods=["POST"])
def api_pass():
    engine = get_engine()
    engine.cmd(f"play {engine.human_color} pass")
    engine.note_move(True)
    if engine.game_over:
        # The AI had already passed: both sides passed, so the game ends here
        # and the AI doesn't get another move.
        ev = engine.last_black_eval
        return jsonify({
            "ok": True, "ai_move": None, "stones": engine.board(),
            "point_loss": None, "winrate": ev["winrate"],
            "score_lead": ev["scoreLead"], "game_over": True,
            "result": engine.final_result(),
        })
    return jsonify({"ok": True, **_finish_turn()})


@app.route("/api/undo", methods=["POST"])
def api_undo():
    engine = get_engine()
    if not engine.undo_turn():
        return jsonify({"ok": False, "error": "nothing to undo"}), 400
    eval_now = engine.eval_as_black_frame(engine.human_color)
    engine.last_black_eval = eval_now
    return jsonify({
        "ok": True, "stones": engine.board(),
        "winrate": eval_now["winrate"], "score_lead": eval_now["scoreLead"],
    })


@app.route("/api/score", methods=["GET"])
def api_score():
    engine = get_engine()
    resp = engine.cmd("final_score")
    text = resp[0][1:].strip() if resp and resp[0].startswith("=") else ""
    return jsonify({"ok": True, "result": text})


if __name__ == "__main__":
    host = os.environ.get("KATAGO_HOST", "0.0.0.0")
    if NO_AUTH and host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("KATAGO_NO_AUTH=1 requires KATAGO_HOST=127.0.0.1")
    if not NO_AUTH:
        load_auth()  # fail fast at startup if the auth file is missing
    get_engine()  # fail fast at startup if the binary/model are missing
    app.run(host=host,
            port=int(os.environ.get("KATAGO_PORT", "8765")), threaded=False)
