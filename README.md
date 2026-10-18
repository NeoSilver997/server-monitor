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

1. Probe the TCP port on `host` (0.3s timeout for a local host, 1.5s for a remote one).
2. If a `url` is set, issue a `GET` and accept status `200-399` (2s timeout).
   An empty `url` means "port open is healthy".
3. If healthy, the row shows `RUNNING`.
4. If down, log the reason (`port closed` or `HTTP fail`), launch `cmd` in a new
   console window, increment the restart counter, and save the settings. Remote hosts are
   reported as down but never started — the app can only launch processes on this machine.

The GUI refreshes every second via `root.after`, and the restart counters are
persisted so they survive app restarts.

## Configuration

`server_watcher_settings.json` — an array of service objects:

```json
[
  {
    "name": "API Server",
    "host": "127.0.0.1",
    "port": 3000,
    "interval": 30,
    "url": "http://127.0.0.1:3000/health",
    "cmd": ["node", "server.js"],
    "cwd": "C:\\projects\\api",
    "restarts": 4,
    "auto_restart": true
  }
]
```

| Field | Meaning |
|-------|---------|
| `name` | Label shown in the main window (also the UI key) |
| `host` | Host to probe — `127.0.0.1` for local, any address/name for a remote site |
| `port` | TCP port to probe on `host` |
| `interval` | Seconds between checks for this service (min 1) |
| `url` | Optional health endpoint; blank = port-only check |
| `cmd` | Command to run when the service is down — first token is the call path |
| `cwd` | Working directory used when restarting; blank = the folder the app runs from |
| `restarts` | Cumulative restart count, maintained by the app |
| `auto_restart` | `false` = report DOWN but never start the service |

A corrupt or missing settings file falls back to the built-in default
(`python -m http.server 8080`, checked every 300s).

## UI

- **Main window** — header row plus one row per service (Status / Name / Host / Port /
  Interval / Restarts / Auto) with a `start` button that launches that service immediately, logged as
  a manual start. A summary line above the rows shows `servers up N / down N`; rows
  alternate shading, status is green for RUNNING and red for DOWN, and a remote `host` is
  shown in blue.
- **Check Now** — runs a check pass immediately, ignoring intervals.
- **Settings** — editable table (Name / Host / Port / Interval / URL / Call path / Args /
  Working dir / Auto restart); a restart runs the command in that working directory, so a service launched with
  a relative script (e.g. `node server.js`) comes back in its own project folder. Save
  validates that Port and Interval are numbers, rebuilds the rows, and writes the
  config file. The call path is the first token of the command, so restarting a service
  uses the same interpreter that was running rather than whatever `python`/`node`
  resolves from `PATH`. Args is split quote-aware, so paths with spaces survive.
  **Add Row** appends a blank service row (host `127.0.0.1`, interval 60, auto restart on)
  that is written to the config on the next Save. A row left completely blank is skipped, and
  Save names the row when Name, Port or Interval is missing or non-numeric. The window sizes
  itself to the picker so the Save / Add Row buttons are always visible.
  Below the table is a **Dashboard opacity** number (40–100%) and a **Dashboard fields**
  picker — tick any of the 62 fields exposed by `/metrics` (grouped as hardware / engine /
  live / totals / conversation_cache / hardware_static) to choose which tiles the dashboard
  shows. Both apply straight away: the value is written to `dashboard_settings.json` and any
  open dashboard is recoloured and rebuilt without pressing Save.
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
  (JSON) every 30s and shows one tile per field selected in the Settings picker. The default
  selection is Speed, GPU load, VRAM, GPU temp, Power, PCIe rx, CPU, Disk read, State and
  Context used. VRAM and RAM show usage as a percentage of the total. Below the tiles: a
  `servers up N / down N` line (mirrors the main window status) and an `updated HH:MM:SS`
  line that shows the fetch error if the endpoint is unreachable. Long values (model name,
  GPU name) are truncated to fit the tile. The window is always on top and grows with the
  number of selected tiles. The title bar is removed with Win32 style flags but it keeps a
  taskbar button, so the `-` button minimizes it to the taskbar. Drag anywhere, including
  across the tiles, to move it — the window is force-repainted after each move so the tiles
  redraw instead of leaving stale pixels. Opacity is a number in Settings; the window keeps
  the refresh spinbox (5–600s, applied on the next tick). Both, with the URL and the field
  selection, are saved to `dashboard_settings.json`. Double-click a tile to drop it from the
  dashboard — the selection is saved (the Settings picker shows it unticked) and the window
  shrinks. Fetches
  run on a background thread so the UI stays responsive.

  Fields available in the picker:

  | Group | Fields |
  |-------|--------|
  | hardware | Speed, GPU load, VRAM, GPU temp, Power, PCIe rx, PCIe tx, PCIe gen, CPU, RAM, Disk read, Disk write |
  | engine | Model, Engine ver, Max context, Context used, KV type, KV resident, Expert slots, Expert cache, Arena, Pool workers, Images, Spec, MTP max, Lookup |
  | live | State, Queued, Phase, Prompt tokens, Prompt read, Prompt total, Generated, Max tokens, Elapsed, tok/s, tok/s mean, Prefill tok/s |
  | totals | Requests, Total prompt, Total reused, Total output, Hit rate, Prompt ms, Decode ms, Drafts, Requests kept |
  | conversation_cache | CC enabled, CC slots, CC parked, CC bytes, CC evictions, CC parks, CC restores, CC requests, CC reused, CC hit rate |
  | hardware_static | GPU, CPU model, Cores, Threads, GPU count |
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
