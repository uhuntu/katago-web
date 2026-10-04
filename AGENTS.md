# KataGo Web Board — Project Context

**Before doing anything else in this directory, read the full file
`notes/2026-08-09-201515-i-want-to-install-katago.txt` in this repo.** It is
the verbatim transcript of the session that built this entire project —
every decision, every bug hit and fixed, every "why" behind the choices
below. This AGENTS.md is only a summary; the transcript is the real context.
Reading it is the closest equivalent to resuming that prior conversation,
since Desktop app and CLI sessions don't share history and there's no
product feature to actually import one into the other.

(That file is gitignored — it contains a secret, see Credentials below.
Don't commit it or paste its contents into anything that gets pushed.)

> `CLAUDE.md` is a symlink to this file — edit AGENTS.md only.
>
> Unit tests (no GPU or KataGo binary needed):
> `cd ~/katago && webui-venv/bin/python -m unittest discover -s tests -v`

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

- LAN: `http://10.11.108.35:8765`. The box's primary NIC (eno1) moved to the
  10.11.108.0/24 network; the old 10.11.17.x address survives only on a USB
  ethernet adapter (`enx00e03c3604ad`, now 10.11.17.25) — don't use it.
- SSH hop when you can reach 241 but have no direct route to the box (e.g.
  AnyConnect split-tunnel lists 10.11.108.241 but not .35): `ssh -L
  8765:10.11.108.35:8765 10.11.108.241`, then open `http://localhost:8765`.
  Nested `ssh 10.11.108.241 'ssh hunt@10.11.108.35 ...'` works for shell.
- Tailscale (works from any network, no VPN app needed on the client since
  Funnel makes it a real public HTTPS URL): `https://hunt-optiplex-7071.tail187bfc.ts.net/`
  Toggle off with `tailscale funnel --https=443 off`.
- SSH reverse tunnel via 10.11.108.241 (reachable from any device on that
  LAN/VPN, plain HTTP — no TLS): `http://10.11.108.241:8765/`. Independent of
  Tailscale, so it's a fallback if Funnel/its DNS misbehaves. Backed by the
  `reverse-241.service` systemd user unit (`ssh -NT 241-tunnel`,
  `Restart=always`); the forwards live in the box's `~/.ssh/config` `Host
  241-tunnel` block (split off the interactive host on 2026-10-01 to stop
  ControlPersist flapping). That block must carry `RemoteForward 0.0.0.0:8765
  localhost:8765` — the split accidentally dropped it, and the tunnel ran 3
  days with no 8765 listener while still showing "active" (re-added
  2026-10-04; `ss -tln | grep 8765` on 241 is the real health check, not the
  unit status). The explicit `0.0.0.0:` bind is required — 241's sshd has
  `GatewayPorts clientspecified`; the block's other forwards
  (7897/3389/3390/2222) intentionally stay loopback.
  `loginctl enable-linger hunt` is enabled (2026-10-04) so the user unit
  starts at boot without a login session. From clients reaching 241 over the
  corporate VPN, only port 22 passes (8765/3390 time out even though the
  listener is up) — tunnel over SSH instead: `ssh -L
  8765:10.11.108.35:8765 10.11.108.241`.
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
- **Engine watchdog.** If the KataGo process dies, a daemon thread
  (`_watchdog` → `_ensure_process`) restarts it within ~5s and restores
  boardsize/komi + the difficulty params; `cmd()` also retries once if a
  request catches the death mid-flight. A restart resets the board — game
  state is not persisted. Engine stderr is logged to
  `~/katago/gtp_logs/engine.log` (used to go to `/dev/null`).
- **All GTP pipe IO must happen under `Engine.lock`.** KataGo answers from a
  single stdout pipe, so two threads reading concurrently interleave
  responses (proven by the watchdog-vs-request race above). `_ensure_process`
  therefore wraps its whole restart sequence in the lock, and `board()` calls
  it under the lock too, so read paths recover from a dead engine as well.
  The lock is an `RLock` because these helpers re-enter each other.
- **The server takes ~10–15s before it listens on 8765.** `__main__` calls
  `get_engine()` before `app.run()` (model load + GPU init), so an immediate
  `curl` after launch gets connection-refused, not a response. Wait for
  "Running on ... :8765" in the log before smoke-testing.
