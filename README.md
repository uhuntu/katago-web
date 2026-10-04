# KataGo Web Board

A self-hosted KataGo setup with a phone-friendly web board: play against KataGo,
see win-rate/point-loss feedback after every move, get move hints, and adjust
AI strength — all from a browser, no app install required.

Built for a local machine with an NVIDIA GPU (tested on RTX 2080, OpenCL backend).

## Layout

```
.
├── bin/katago          # CLI wrapper: `katago gtp`, `katago benchmark`, etc.
├── gtp.cfg              # Tuned GTP engine config (used by webui + bin/katago)
├── analysis.cfg         # Analysis engine config (used by KaTrain)
├── requirements.txt      # Python deps for the webui Flask app
├── webui/
│   ├── server.py         # Flask app wrapping a KataGo GTP subprocess
│   └── static/index.html # Touch-friendly board UI (canvas-based)
├── tests/test_server.py # Unit tests for parsing/coord helpers (no GPU needed)
├── src/                  # KataGo source clone (gitignored — see Setup)
├── models/                # Neural net weights (gitignored — see Setup)
├── webui-venv/            # Python venv for webui/ (gitignored)
└── katrain-venv/           # Python venv for KaTrain desktop app (gitignored)
```

## Setup from scratch

### 1. Build KataGo (OpenCL backend)

Requires: `cmake`, `g++`, `ocl-icd-opencl-dev`, `zlib1g-dev`, a working NVIDIA/AMD
OpenCL driver.

```bash
git clone --branch v1.17.2 --depth 1 https://github.com/lightvector/KataGo.git src
cd src/cpp
cmake -B build -DUSE_BACKEND=OPENCL -DCMAKE_BUILD_TYPE=Release
cmake --build build -j$(nproc)
```

### 2. Get a neural net

```bash
mkdir -p models
curl -L -o models/kata1-b28c512nbt-s13255194368-d5935380940.bin.gz \
  https://media.katagotraining.org/uploaded/networks/models/kata1/kata1-b28c512nbt-s13255194368-d5935380940.bin.gz
```

SHA256: `c5bca453d7b08ea8df6546439325d4dd681e77d975e1da3f7593d771147b73bc`

Swap in any other net from [katagotraining.org](https://katagotraining.org/) —
just update `MODEL` in `webui/server.py` and `bin/katago` to match.

### 3. Tune OpenCL + set up the web app venv

```bash
./src/cpp/build/katago benchmark -model models/*.bin.gz -config gtp.cfg   # auto-tunes and caches OpenCL kernels

python3 -m venv webui-venv
webui-venv/bin/pip install -r requirements.txt
```

### 4. Run

```bash
webui-venv/bin/python webui/server.py
```

Serves on `0.0.0.0:8765`. Open `http://<this-machine's-LAN-or-Tailscale-IP>:8765`
from your phone's browser.

## Running locally (Windows, CPU)

No GPU needed: use the Eigen (CPU) release and a small net. On a Celeron
N5105 the b18c384nbt net managed ~2 visits/s on 19x19 (too slow); the old
`g170e-b10c128` net does ~70 visits/s on 9x9, which is fine for play + hints.

```bash
gh release download v1.18.1 -R lightvector/KataGo -p 'katago-v1.18.1-eigen-windows-x64.zip'
# unzip into katago-win/; net: https://katagoarchive.org/g170/neuralnets/g170e-b10c128-s1141046784-d204142634.bin.gz
# (send a browser User-Agent, the hosts 403 the default Python/curl-less ones)
sed 's/^numSearchThreads *=.*/numSearchThreads = 4/' katago-win/default_gtp.cfg > katago-win/gtp-local.cfg
echo 'me:some-password' > katago-win/.auth
```

```powershell
$env:KATAGO_BIN="katago-win\katago.exe"; $env:KATAGO_MODEL="models\g170e-b10c128.bin.gz"
$env:KATAGO_CONFIG="katago-win\gtp-local.cfg"; $env:KATAGO_AUTH_FILE="katago-win\.auth"
$env:KATAGO_ENGINE_LOG="katago-win\engine.log"; $env:KATAGO_HOST="127.0.0.1"
python webui\server.py     # then open http://127.0.0.1:8765
```

Optional: `KATAGO_PORT` (default 8765); `KATAGO_NO_AUTH=1` skips the login (only allowed with `KATAGO_HOST=127.0.0.1`, which is handy for the VS Code preview pane — don't put credentials in the URL, browsers then refuse `fetch()`). Stick to 9x9 on CPU.

## Features

- **Play**: tap to place stones as Black; KataGo (White) replies automatically.
- **Win-rate bar**: live Black/White win% and score lead, via `kata-analyze`.
- **Point loss**: after each of your moves, shows how many points it cost
  relative to KataGo's best option (green = good, yellow = minor, red = mistake).
- **Hint**: shows KataGo's top 3 candidate moves as numbered markers.
- **Difficulty**: Beginner/Easy/Normal/Strong, adjustable live via
  `kata-set-param maxVisits` — no restart needed. Analysis quality (win-rate,
  point-loss, hints) is always computed at a fixed 300 visits regardless of
  difficulty, so feedback stays meaningful even when the AI is playing weak.

## Tests

Pure helper functions (coordinate conversion, `showboard` parsing) are covered
by unit tests that need neither the GPU nor the KataGo binary:

```bash
webui-venv/bin/python -m unittest discover -s tests -v
```

## Known limitations

- Analysis runs on a short ~1.3s search per query to stay responsive on a
  phone — it's a good general read, not as precise as a deep desktop review.
- Flask's built-in dev server, protected by HTTP Basic Auth (credentials in
  `~/katago/.auth`, gitignored). Fine behind Tailscale Funnel (TLS) or a home
  LAN; don't port-forward it to the public internet — the plain-HTTP fallback
  tunnel (see AGENTS.md) sends Basic Auth credentials in the clear, and Basic
  Auth alone isn't hardened.
- One shared board, one game at a time, no accounts. Game state lives in the
  KataGo process, so a refresh keeps the game (server-side state), but an engine
  crash restarts KataGo (watchdog) and the game in progress.

## Desktop companion: KaTrain

For deeper teaching features (move-by-move review, mistake highlighting,
teaching games), install [KaTrain](https://github.com/sanderland/katrain) in
its own venv and point it at `analysis.cfg` + the same model:

```bash
python3.12 -m venv katrain-venv
katrain-venv/bin/pip install katrain
```

Then set `~/.katrain/config.json`'s `engine` section to use
`src/cpp/build/katago`, the model in `models/`, and `analysis.cfg`.
