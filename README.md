# WinServerMonitor (Server Watcher)

A single-file Windows desktop tool that watches a list of local services, reports
their health, and automatically restarts any that are down.

## Requirements

- Python 3.7+ with `tkinter` (bundled with the standard CPython Windows installer)
- Windows only — uses `subprocess.CREATE_NEW_CONSOLE` and `os.startfile`
- No third-party dependencies

## Quick start

```powershell
cd C:\AI_Server\Coding\WinServerMonitor
python server_watcher.py
```

On first run the app uses the built-in defaults and writes
`server_watcher_settings.json` next to it. Keep the working directory as the
project folder so the settings and log files stay in the same place.

## What it does

For each configured service, on its own interval:

1. Probe the TCP port on `127.0.0.1` (0.3s timeout).
2. If a `url` is set, issue a `GET` and accept status `200-399` (2s timeout).
   An empty `url` means "port open is healthy".
3. If healthy, the row shows `RUNNING`.
4. If down, log the reason (`port closed` or `HTTP fail`), launch `cmd` in a new
   console window, increment the restart counter, and save the settings.

The GUI refreshes every second via `root.after`, and the restart counters are
persisted so they survive app restarts.

## Configuration

`server_watcher_settings.json` — an array of service objects:

```json
[
  {
    "name": "API Server",
    "port": 3000,
    "interval": 30,
    "url": "http://127.0.0.1:3000/health",
    "cmd": ["node", "server.js"],
    "restarts": 4
  }
]
```

| Field | Meaning |
|-------|---------|
| `name` | Label shown in the main window (also the UI key) |
| `port` | TCP port to probe on `127.0.0.1` |
| `interval` | Seconds between checks for this service (min 1) |
| `url` | Optional health endpoint; blank = port-only check |
| `cmd` | Command to run when the service is down — first token is the call path |
| `cwd` | Working directory used when restarting; blank = the folder the app runs from |
| `restarts` | Cumulative restart count, maintained by the app |

A corrupt or missing settings file falls back to the built-in default
(`python -m http.server 8080`, checked every 300s).

## UI

- **Main window** — one row per service: name, status, interval, restart count.
- **Check Now** — runs a check pass immediately, ignoring intervals.
- **Settings** — editable table (Name / Port / Interval / URL / Call path / Args / Working
  dir); a restart runs the command in that working directory, so a service launched with
  a relative script (e.g. `node server.js`) comes back in its own project folder. Save
  validates that Port and Interval are numbers, rebuilds the rows, and writes the
  config file. The call path is the first token of the command, so restarting a service
  uses the same interpreter that was running rather than whatever `python`/`node`
  resolves from `PATH`. Args is split quote-aware, so paths with spaces survive.
- **Scan** — lists running `python`/`node` processes that are listening on a local
  port in a Process / Call path / Working dir / Port / PID / Flag / Command table. The
  call path is the absolute path of the interpreter (`shutil.which`) and becomes the first
  token of the command when a row is added. Working dir is the process's live working
  directory, read from its PEB (`process_cwd()`); if that read fails it falls back to the
  folder implied by an absolute script argument. Click a column header to
  sort, click again to reverse. Each row is flagged `main` or `worker`
  (`is_subprocess()` matches build/test worker command lines such as
  `start-server.js`, `jest-worker`, `babel-loader`); worker rows are shown in red.
  Select one or more and Add Selected to append them to the config
  (interval 60s, no health URL). One row per PID — the lowest listening port wins.
- **Dashboard** — a frameless overlay window that reads `http://127.0.0.1:8080/metrics`
  (JSON) every 30s and shows Speed, GPU load, VRAM, GPU temp, Power, PCIe rx, CPU and
  Disk read as tiles, plus a `servers up N / down N` line (mirrors the main window status)
  and a state / context line. The window is always on top. The title bar is removed with
  Win32 style flags but it keeps a taskbar button, so the `-` button minimizes it to
  the taskbar; drag anywhere on the window to move it. The opacity slider (40–100%) is
  saved to `dashboard_settings.json` along with the URL and refresh interval. Fetches
  run on a background thread so the UI stays responsive.
- **Export Config** — writes the current services to `config_export.csv` and
  `config_export.json` (columns include the call path). The Scan dialog has the same for its results
  (`scan_export.csv` / `scan_export.json`). CSV is written with a UTF-8 BOM so
  Excel reads it correctly; fields containing commas are quoted. Each export
  overwrites the previous file.
- **Open Log** — opens `server_watcher.log` in the default editor.

## Logging

Every restart and settings save is written to both the in-window log pane and
`server_watcher.log`. The log is append-only and is not rotated.

## Known limitations

- A service that exits immediately is relaunched on every interval — there is no
  backoff, max-restart cap, or grace period for slow-to-bind ports, so duplicate
  instances can accumulate.
- Network checks run on the Tk event loop, so a hanging endpoint can freeze the UI
  for a few seconds.
- Service names are used as UI keys, so duplicate names collide and renaming a
  service resets its restart count.
- The Command field is re-parsed with plain whitespace splitting, so arguments
  containing spaces are broken on save. Use the JSON file for commands with spaced
  or quoted arguments.
- Settings and log paths are relative to the working directory, not the script folder.
