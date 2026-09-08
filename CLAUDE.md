# KataGo Web Board — Project Context

**Before doing anything else in this directory, read the full file
`notes/2026-08-09-201515-i-want-to-install-katago.txt` in this repo.** It is
the verbatim transcript of the session that built this entire project —
every decision, every bug hit and fixed, every "why" behind the choices
below. This CLAUDE.md is only a summary; the transcript is the real context.
Reading it is the closest equivalent to resuming that prior conversation,
since Desktop app and CLI sessions don't share history and there's no
product feature to actually import one into the other.

(That file is gitignored — it contains a secret, see Credentials below.
Don't commit it or paste its contents into anything that gets pushed.)

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
- SSH reverse tunnel via 10.11.108.241 (reachable from any device on that
  VPN, plain HTTP — no TLS): `http://10.11.108.241:8765/`. Independent of
  Tailscale, so it's a fallback if Funnel/its DNS misbehaves. Backed by the
  `reverse-241.service` systemd user unit (`ssh -NT 10.11.108.241`,
  `Restart=always`), which reads the `RemoteForward 0.0.0.0:8765
  localhost:8765` line in `~/.ssh/config`'s `Host 10.11.108.241` block. The
  explicit `0.0.0.0:` bind is required — that host's sshd has `GatewayPorts
  clientspecified`, so an unqualified `RemoteForward 8765 ...` would silently
  bind loopback-only and not be reachable from other devices. The existing
  `7897`/`3390` forwards on that same host intentionally stay loopback (no
  bind address given), so don't add one there either.
  Same trick would work via 10.200.7.149, but that host's sshd doesn't have
  `GatewayPorts` set (defaults to `no`) — needs `GatewayPorts clientspecified`
  added to `/etc/ssh/sshd_config` there (needs root) before a forward on it
  would be externally reachable.
  To open a second, non-conflicting SSH session to either host without
  colliding with the systemd-managed tunnel's own forwards, use the `241n`/
  `149n` aliases in `~/.ssh/config` (`ClearAllForwardings yes`).

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
