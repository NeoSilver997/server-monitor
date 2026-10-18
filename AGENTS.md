# Agent guideline — updating the watcher config

`server_watcher_settings.json` is the service list the watcher polls and restarts. It is a
plain JSON array of service objects (no wrapper object, no comments, no trailing commas).

## Service object

| Key | Type | Meaning |
| --- | --- | --- |
| `name` | string | Row label. Required; blank names are rejected by the Settings UI. |
| `host` | string | Host to connect to, default `127.0.0.1`. |
| `port` | number | TCP port to check. Must be an integer. |
| `interval` | number | Seconds between checks, minimum 1. |
| `url` | string | Optional health check, e.g. `http://127.0.0.1:8080/metrics`. Empty = port check only. |
| `cmd` | array | Command to run when the port is down. First element is the executable, the rest are arguments. |
| `cwd` | string | Absolute working directory for `cmd`. Empty = the watcher's own directory. |
| `auto_restart` | boolean | `false` = monitor only, never start or restart the service. |
| `restarts` | number | Restart counter for the row. Leave the existing value unless asked to reset it. |

## Rules when editing

1. Keep the file a JSON array. Preserve existing entries and their order; add new services at
   the end unless the user asks otherwise.
2. `cmd` entries that are relative (`server.js`, `ComfyUI\main.py`, `src/main.ts`) only work if
   `cwd` is the absolute project root. If a service reads relative files, set `cwd`.
3. Use absolute paths for the executable. Paths must be JSON-escaped (`C:\\AI_Server\\...`).
4. `port` and `interval` are numbers, not strings. `auto_restart` is a real boolean.
5. Do not change a port, interval, `cmd` or `cwd` that the user set unless the task asks for it.
6. The config is read once at startup (`load_settings()`), so an edit only takes effect after
   the watcher is restarted. Editing through the Settings UI writes the same file immediately.

## Before writing

- Validate: `python -m json.tool server_watcher_settings.json`
- Confirm the target directory exists for every `cwd`, and the executable exists for every
  `cmd[0]`.
- Do not commit this file — it is runtime state, along with `dashboard_settings.json`,
  `daily_extremes.json`, `server_watcher.log`, `scan_export.csv` and `scan_export.json`.

## Dashboard config

`dashboard_settings.json` is an object: `url` (the metrics endpoint), `opacity` (0.4–1.0),
`refresh` (seconds), and `fields` — an ordered list of field keys that is the tile order. Only
keys that exist in `FIELD_TITLES` in `server_watcher.py` are valid; the order in the list is the
order of the tiles, so keep it rather than re-sorting it.
