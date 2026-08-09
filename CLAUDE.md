# KataGo Web Board — Project Context

This project was built interactively over a long session. Full transcript
(if deeper history is ever needed) is at:
`notes/2026-08-09-201515-i-want-to-install-katago.txt`
(gitignored — contains a secret, see Credentials below; don't commit it or
paste its contents into anything that gets pushed.)

## What this is

- KataGo (v1.17.2) built from source at `src/`, OpenCL backend, targeting an
  NVIDIA RTX 2080. Binary: `src/cpp/build/katago`.
- Model: `models/kata1-b28c512nbt-s13255194368-d5935380940.bin.gz` (b28c512nbt).
- `webui/`: a Flask app (`server.py` + `static/index.html`) serving a
  touch-friendly Go board for phone play, with win-rate/point-loss feedback,
  move hints, and a live-adjustable difficulty selector (Beginner→Strong via
  `kata-set-param maxVisits`, decoupled from analysis quality — see "Known
  gotchas" below).
- KaTrain (desktop GUI, richer teaching features) installed separately in
  `katrain-venv/` — config at `~/.katrain/config.json`.
- Git repo pushed to `git@github.com:uhuntu/katago-web.git` (private). Only
  the actual project code is tracked — `src/`, `models/`, both venvs, logs,
  and `.auth` are gitignored (see `.gitignore` for the full list and why).

## Running things

```bash
# Web board (binds 0.0.0.0:8765, protected by HTTP Basic Auth — see Credentials)
cd ~/katago && webui-venv/bin/python webui/server.py

# KaTrain desktop app
~/katago/katrain-venv/bin/katrain     # or launch "KaTrain" from the GNOME app grid

# Raw KataGo CLI wrapper (uses gtp.cfg + the model above by default)
~/.local/bin/katago gtp   # or: bin/katago gtp
```

Check if the web server's already running before starting another (it binds
a fixed port): `pgrep -af server.py`. Kill with `pkill -f "webui/server.py"`.

## Remote access

- LAN: `http://10.11.17.36:8765`
- Tailscale (works from any network, no VPN app needed on the client since
  Funnel makes it a real public HTTPS URL): `https://hunt-optiplex-7071.tail187bfc.ts.net/`
  Toggle off with `tailscale funnel --https=443 off`.

## Credentials

The web board requires HTTP Basic Auth (since Funnel exposes it publicly).
Username/password live in `~/katago/.auth` (gitignored, `chmod 600`), format
`username:password`. `webui/server.py` reads it at startup.

## Known gotchas (don't re-discover these the hard way)

- **Analysis vs. difficulty are decoupled on purpose.** `kata-set-param
  maxVisits` controls how well the AI *plays* (difficulty). Win-rate/hint
  analysis (`kata-analyze`) used to share that same cap, which made feedback
  meaningless at low difficulty (e.g. maxVisits=1 for Beginner returns no
  usable analysis at all). Fixed in `Engine._analyze_last_report`: it
  temporarily bumps `maxVisits` to `ANALYSIS_VISITS` (300) around every
  analysis call, then restores the difficulty's real value. Don't remove
  this coupling fix.
- **Hint baseline sync.** `/api/suggest` also updates `engine.last_black_eval`
  to match its own search result, so following the #1 hinted move can't show
  a false "mistake" purely from two independent short searches disagreeing.
- **9x9 komi is 5.5, not 7.5** (`Engine._reset`) — 7.5 heavily favors White on
  a 9x9 board and skewed the win-rate display unrealistically at game start.
- **KaTrain's window `WM_CLASS` is just `"python3.12"`** (Kivy quirk) — the
  desktop launcher (`~/.local/share/applications/katrain.desktop`) needs
  `StartupWMClass=python3.12` or GNOME shows a generic icon for the running
  window (the app-grid icon before launch is unaffected).
- Old background server processes can survive a `pkill` if the match pattern
  doesn't hit their exact argv (e.g. one instance was launched via `cd
  webui && python server.py`, not `python webui/server.py` — different
  strings). If port 8765 says "already in use" after a supposedly clean
  kill, check `pgrep -af server.py` for a stray PID and kill it directly.
