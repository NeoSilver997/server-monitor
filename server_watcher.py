import socket, subprocess, time, json, os, csv, ctypes, queue, threading, shutil
import urllib.request, urllib.error
import tkinter as tk
from tkinter import scrolledtext, messagebox, ttk

CONFIG_FILE = "server_watcher_settings.json"
LOG_FILE = "server_watcher.log"
SCAN_EXPORT = "scan_export"
CONFIG_EXPORT = "config_export"
DASHBOARD_FILE = "dashboard_settings.json"

FIELD_GROUPS = {
    "hardware": ("speed", "gpu_load", "vram", "temp", "power", "pcie_rx", "pcie_tx", "pcie_gen",
                 "cpu", "ram", "disk_read", "disk_write"),
    "engine": ("model", "version", "max_context", "context", "kv", "kv_resident", "expert_slots",
               "expert_cache", "arena", "pool_workers", "images", "spec", "mtp_max", "lookup"),
    "live": ("state", "queued", "phase", "prompt_tokens", "prompt_read", "prompt_total", "generated",
             "max_tokens", "elapsed", "tok_s", "tok_s_mean", "prefill_mean"),
    "totals": ("requests", "t_prompt", "t_reused", "t_output", "t_hit", "t_prompt_ms", "t_decode_ms",
               "drafts", "requests_kept"),
    "conversation_cache": ("cc_enabled", "cc_slots", "cc_parked", "cc_bytes", "cc_evictions", "cc_parks",
                           "cc_restores", "cc_requests", "cc_reused", "cc_hit"),
    "hardware_static": ("gpu_name", "cpu_name", "cores", "threads", "gpu_count"),
}

FIELD_TITLES = {
    "speed": "Speed", "gpu_load": "GPU load", "vram": "VRAM", "temp": "GPU temp", "power": "Power",
    "pcie_rx": "PCIe rx", "pcie_tx": "PCIe tx", "pcie_gen": "PCIe gen", "cpu": "CPU", "ram": "RAM",
    "disk_read": "Disk read", "disk_write": "Disk write",
    "model": "Model", "version": "Engine ver", "max_context": "Max context", "context": "Context used",
    "kv": "KV type", "kv_resident": "KV resident", "expert_slots": "Expert slots", "expert_cache": "Expert cache",
    "arena": "Arena", "pool_workers": "Pool workers", "images": "Images", "spec": "Spec",
    "mtp_max": "MTP max", "lookup": "Lookup",
    "state": "State", "queued": "Queued", "phase": "Phase", "prompt_tokens": "Prompt tokens",
    "prompt_read": "Prompt read", "prompt_total": "Prompt total", "generated": "Generated",
    "max_tokens": "Max tokens", "elapsed": "Elapsed", "tok_s": "tok/s", "tok_s_mean": "tok/s mean",
    "prefill_mean": "Prefill tok/s",
    "requests": "Requests", "t_prompt": "Total prompt", "t_reused": "Total reused", "t_output": "Total output",
    "t_hit": "Hit rate", "t_prompt_ms": "Prompt ms", "t_decode_ms": "Decode ms", "drafts": "Drafts",
    "requests_kept": "Requests kept",
    "cc_enabled": "CC enabled", "cc_slots": "CC slots", "cc_parked": "CC parked", "cc_bytes": "CC bytes",
    "cc_evictions": "CC evictions", "cc_parks": "CC parks", "cc_restores": "CC restores",
    "cc_requests": "CC requests", "cc_reused": "CC reused", "cc_hit": "CC hit rate",
    "gpu_name": "GPU", "cpu_name": "CPU model", "cores": "Cores", "threads": "Threads", "gpu_count": "GPU count",
}

DEFAULT_FIELDS = ("speed", "gpu_load", "vram", "temp", "power", "pcie_rx", "cpu", "disk_read", "state", "context")
DEFAULT_DASHBOARD = {"url": "http://127.0.0.1:8080/metrics", "opacity": 0.85, "refresh": 30,
                     "fields": list(DEFAULT_FIELDS)}

GWL_STYLE, GWL_EXSTYLE = -16, -20
WS_CAPTION, WS_BORDER, WS_MINIMIZEBOX = 0x00C00000, 0x00800000, 0x00020000
WS_EX_APPWINDOW, WS_EX_TOOLWINDOW = 0x00040000, 0x00000080
SW_MINIMIZE, SW_RESTORE = 2, 9
SWP_NOMOVE, SWP_NOSIZE, SWP_NOZORDER, SWP_FRAMECHANGED = 0x0002, 0x0001, 0x0004, 0x0020
RDW_INVALIDATE, RDW_UPDATENOW, RDW_ALLCHILDREN = 0x0001, 0x0100, 0x0080
PROCESS_QUERY_LIMITED_INFORMATION, PROCESS_VM_READ = 0x1000, 0x0010

SCAN_EXES = ("python.exe", "pythonw.exe", "node.exe")
SCAN_BIND_HOSTS = {"0.0.0.0", "127.0.0.1", "::", "::1"}
LOCAL_HOSTS = {"", "127.0.0.1", "localhost", "::1"}
SCRIPT_EXTS = (".js", ".mjs", ".cjs", ".ts", ".py", ".bat", ".cmd")

SUBPROCESS_PATTERNS = (
    "webpack-loaders.js", "postcss.js", "start-server.js",
    "next-router-worker", "jest-worker", "tsx-loader",
    "babel-loader", "css-loader", "sass-loader",
    "file-loader", "url-loader", "mini-css-extract-plugin",
    "fork-ts-checker", "eslint-webpack-plugin",
)

DEFAULT_SERVICES = [
    {"name": "Web Server 2", "port": 8080, "url": "", "cmd": ["python", "-m", "http.server", "8080"], "interval": 300},
]

def load_settings():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list) and data:
                for s in data:
                    s.setdefault("url", "")
                    s.setdefault("host", "127.0.0.1")
                    s.setdefault("cwd", "")
                    s.setdefault("restarts", 0)
                    s.setdefault("auto_restart", True)
                return data
        except Exception:
            pass
    return [dict(s) for s in DEFAULT_SERVICES]

def save_settings(services):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(services, f, indent=2, ensure_ascii=False)

def file_log(msg):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass

def port_open(port, host="127.0.0.1", timeout=0.3):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return True
    except Exception:
        return False
    finally:
        s.close()

def http_ok(url, timeout=2.0):
    if not url:
        return True
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 400
    except Exception:
        return False

def start_service(svc):
    subprocess.Popen(svc["cmd"], cwd=svc.get("cwd") or None, creationflags=subprocess.CREATE_NEW_CONSOLE)

def list_listening_ports():
    ports = {}
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=20,
                             creationflags=subprocess.CREATE_NO_WINDOW).stdout
    except Exception:
        return ports
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP" or parts[3].upper() != "LISTENING":
            continue
        host, _, port_text = parts[1].rpartition(":")
        if host.strip("[]") not in SCAN_BIND_HOSTS:
            continue
        try:
            ports.setdefault(int(parts[-1]), set()).add(int(port_text))
        except ValueError:
            continue
    return {pid: sorted(found) for pid, found in ports.items()}

def list_python_node_processes():
    names = ",".join(f"'{n}'" for n in SCAN_EXES)
    script = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
              "Get-CimInstance Win32_Process | "
              f"Where-Object {{ $_.Name -in @({names}) }} | "
              "Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Compress")
    try:
        done = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception as e:
        raise RuntimeError(f"could not list processes: {e}")
    text = (done.stdout or "").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except ValueError:
        raise RuntimeError("process list was not readable")
    if isinstance(data, dict):
        data = [data]
    procs = []
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            procs.append({"pid": int(item["ProcessId"]),
                          "name": str(item.get("Name") or ""),
                          "cmdline": str(item.get("CommandLine") or "").strip()})
        except (KeyError, TypeError, ValueError):
            continue
    return procs

def split_command(cmdline):
    args, cur, quoted = [], "", False
    for ch in cmdline:
        if ch == '"':
            quoted = not quoted
        elif ch.isspace() and not quoted:
            if cur:
                args.append(cur)
                cur = ""
        else:
            cur += ch
    if cur:
        args.append(cur)
    return args

kernel32 = ctypes.windll.kernel32
ntdll = ctypes.windll.ntdll
kernel32.OpenProcess.restype = ctypes.c_void_p
kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_bool, ctypes.c_ulong]
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
kernel32.ReadProcessMemory.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                       ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
ntdll.NtQueryInformationProcess.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p,
                                            ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)]

class PROCESS_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [("ExitStatus", ctypes.c_void_p),
                ("PebBaseAddress", ctypes.c_void_p),
                ("AffinityMask", ctypes.c_void_p),
                ("BasePriority", ctypes.c_long),
                ("UniqueProcessId", ctypes.c_void_p),
                ("InheritedFromUniqueProcessId", ctypes.c_void_p)]

def read_memory(handle, address, size):
    buf = ctypes.create_string_buffer(size)
    read = ctypes.c_size_t(0)
    if not kernel32.ReadProcessMemory(handle, ctypes.c_void_p(address), buf, size, ctypes.byref(read)):
        return None
    return buf.raw

def process_cwd(pid):
    """Live working directory of a running process, read from its PEB (same-user processes only)."""
    if ctypes.sizeof(ctypes.c_void_p) != 8:
        return ""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ, False, pid)
    if not handle:
        return ""
    try:
        info = PROCESS_BASIC_INFORMATION()
        returned = ctypes.c_ulong()
        if ntdll.NtQueryInformationProcess(handle, 0, ctypes.byref(info), ctypes.sizeof(info),
                                           ctypes.byref(returned)):
            return ""
        params = read_memory(handle, info.PebBaseAddress + 0x20, 8)
        if not params:
            return ""
        base = int.from_bytes(params, "little")
        name = read_memory(handle, base + 0x38, 16)
        if not name:
            return ""
        length = int.from_bytes(name[:2], "little")
        text = read_memory(handle, int.from_bytes(name[8:], "little"), length)
        return text.decode("utf-16-le").rstrip("\\") if text else ""
    finally:
        kernel32.CloseHandle(handle)

def resolve_path(token):
    if os.path.isabs(token):
        return token
    return shutil.which(token) or token

def find_script_path(cmd):
    """Return the absolute path of the main script being executed, or empty string."""
    for token in cmd[1:]:
        if not token:
            continue
        lower = token.lower()
        if any(lower.endswith(ext) for ext in SCRIPT_EXTS):
            if os.path.isabs(token):
                return token
            # relative script — try to resolve later with cwd
            return token
    return ""

def guess_cwd(cmd):
    for token in cmd[1:]:
        if os.path.isabs(token) and os.path.splitext(token)[1].lower() in SCRIPT_EXTS:
            if "node_modules" not in token.lower():
                return os.path.dirname(token)
    return ""

def is_subprocess(cmdline):
    if not cmdline:
        return True
    lower = cmdline.lower()
    for pat in SUBPROCESS_PATTERNS:
        if pat in lower:
            return True
    return False

def scan_candidates():
    ports_by_pid = list_listening_ports()
    candidates = []
    seen_pids = set()

    for proc in list_python_node_processes():
        cmd = split_command(proc["cmdline"])
        if not cmd:
            continue
        ports = ports_by_pid.get(proc["pid"], [])
        if not ports:
            continue
        main_port = min(ports)
        if proc["pid"] in seen_pids:
            continue
        seen_pids.add(proc["pid"])
        exe = os.path.splitext(proc["name"])[0]
        exe_path = resolve_path(cmd[0])
        script_path = find_script_path(cmd)
        cwd = process_cwd(proc["pid"]) or guess_cwd(cmd)
        # Prefer script path as "call path" if we found one
        call_path = script_path if script_path and os.path.isabs(script_path) else exe_path
        candidates.append({
            "process": exe,
            "path": call_path,
            "cwd": cwd,
            "pid": proc["pid"],
            "cmdline": proc["cmdline"],
            "flag": "worker" if is_subprocess(proc["cmdline"]) else "main",
            "name": f"{exe} :{main_port}",
            "port": main_port,
            "interval": 60,
            "url": "",
            "host": "127.0.0.1",
            "cmd": [exe_path] + cmd[1:],
            "restarts": 0,
            "auto_restart": True,
        })
    return sorted(candidates, key=lambda c: (c["flag"], c["process"], c["port"]))

def write_export(base, headers, csv_rows, json_rows):
    csv_path, json_path = f"{base}.csv", f"{base}.json"
    try:
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            writer.writerows(csv_rows)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(json_rows, f, indent=2, ensure_ascii=False)
    except OSError as e:
        messagebox.showerror("Export failed", str(e))
        return None
    return csv_path, json_path

def export_scan(candidates):
    headers = ("process", "path", "cwd", "port", "pid", "flag", "command")
    rows = [(c["process"], c["path"], c["cwd"], c["port"], c["pid"], c["flag"], c["cmdline"]) for c in candidates]
    paths = write_export(SCAN_EXPORT, headers, rows, candidates)
    if paths:
        logline(f'[{time.strftime("%H:%M:%S")}] exported {len(rows)} scanned service(s) to {paths[0]} / {paths[1]}')

def export_config():
    headers = ("name", "host", "path", "cwd", "port", "interval", "url", "cmd", "restarts")
    rows = [(s.get("name", ""), s.get("host") or "127.0.0.1", (s.get("cmd") or [""])[0], s.get("cwd", ""), s.get("port", ""),
            s.get("interval", ""), s.get("url", ""), " ".join(s.get("cmd", [])),
            s.get("restarts", 0)) for s in services]
    paths = write_export(CONFIG_EXPORT, headers, rows, services)
    if paths:
        logline(f'[{time.strftime("%H:%M:%S")}] exported {len(rows)} configured service(s) to {paths[0]} / {paths[1]}')

def load_dashboard():
    if os.path.exists(DASHBOARD_FILE):
        try:
            with open(DASHBOARD_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                cfg = dict(DEFAULT_DASHBOARD)
                cfg.update({k: v for k, v in data.items() if k in DEFAULT_DASHBOARD})
                return cfg
        except Exception:
            pass
    return dict(DEFAULT_DASHBOARD)

def save_dashboard(cfg):
    try:
        with open(DASHBOARD_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except OSError:
        pass

def fetch_metrics(url, timeout=3.0):
    req = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))

def metric_values(data):
    hw = data.get("hardware") or {}
    live = data.get("live") or {}
    eng = data.get("engine") or {}
    tot = data.get("totals") or {}
    cc = data.get("conversation_cache") or {}
    stat = data.get("hardware_static") or {}

    def show(value, suffix="", digits=1):
        if value is None or value == "":
            return "-"
        if isinstance(value, bool):
            return "yes" if value else "no"
        if isinstance(value, float):
            value = round(value, digits)
        return f"{value}{suffix}"

    def mib(value, digits=1):
        if value is None:
            return "-"
        v = value / 1024 / 1024
        return round(v, digits) if v < 100 else round(v)

    def pct(part, whole):
        return f"{round(part / whole * 100)}%" if whole and part is not None else "-"

    speed = live.get("tok_s") if live.get("tok_s") is not None else hw.get("tok_s")
    return {
        "speed": show(speed, " tok/s"),
        "gpu_load": show(hw.get("gpu_util"), "%"),
        "vram": pct(hw.get("gpu_mem_used"), hw.get("gpu_mem_total")),
        "temp": show(hw.get("gpu_temp"), " C"),
        "power": f'{show(hw.get("gpu_power"))} / {show(hw.get("gpu_power_limit"))} W',
        "pcie_rx": show(hw.get("gpu_pcie_rx_mb"), " MB/s"),
        "pcie_tx": show(hw.get("gpu_pcie_tx_mb"), " MB/s"),
        "pcie_gen": f'gen {show(hw.get("gpu_pcie_gen"))} max {show(hw.get("gpu_pcie_gen_max"))} x{show(hw.get("gpu_pcie_width"))}',
        "cpu": show(hw.get("cpu"), "%"),
        "ram": pct(hw.get("ram_used"), hw.get("ram_total")),
        "disk_read": show(hw.get("disk_read_mb"), " MB/s"),
        "disk_write": show(hw.get("disk_write_mb"), " MB/s"),

        "model": show(eng.get("model")),
        "version": show(eng.get("version") or eng.get("engine")),
        "max_context": show(eng.get("max_context")),
        "context": pct(live.get("prompt_total"), eng.get("max_context")),
        "kv": show(eng.get("kv")),
        "kv_resident": show(eng.get("kv_resident")),
        "expert_slots": show(eng.get("expert_slots")),
        "expert_cache": f'{show(eng.get("expert_cache_mib"))} MiB',
        "arena": f'{show(eng.get("arena_mib"))} MiB',
        "pool_workers": show(eng.get("pool_workers")),
        "images": show(eng.get("images")),
        "spec": show(eng.get("spec")),
        "mtp_max": show(eng.get("mtp_max")),
        "lookup": show(eng.get("lookup")),

        "state": show(live.get("state")),
        "queued": show(live.get("queued")),
        "phase": show(live.get("phase")),
        "prompt_tokens": show(live.get("prompt_tokens")),
        "prompt_read": show(live.get("prompt_read")),
        "prompt_total": show(live.get("prompt_total")),
        "generated": show(live.get("generated")),
        "max_tokens": show(live.get("max_tokens")),
        "elapsed": show(live.get("elapsed_s"), " s"),
        "tok_s": show(live.get("tok_s"), " tok/s"),
        "tok_s_mean": show(live.get("tok_s_mean"), " tok/s"),
        "prefill_mean": show(live.get("prefill_tok_s_mean"), " tok/s"),

        "requests": show(tot.get("requests")),
        "t_prompt": show(tot.get("prompt_tokens")),
        "t_reused": show(tot.get("reused")),
        "t_output": show(tot.get("output_tokens")),
        "t_hit": pct(tot.get("reused"), tot.get("prompt_tokens")),
        "t_prompt_ms": show(tot.get("prompt_ms"), " ms"),
        "t_decode_ms": show(tot.get("decode_ms"), " ms"),
        "drafts": f'{show(tot.get("drafts_offered"))} / {show(tot.get("drafts_accepted"))}',
        "requests_kept": show(data.get("requests_kept")),

        "cc_enabled": show(cc.get("enabled")),
        "cc_slots": show(cc.get("slots")),
        "cc_parked": show(cc.get("parked")),
        "cc_bytes": f'{mib(cc.get("bytes"))} MiB',
        "cc_evictions": show(cc.get("evictions")),
        "cc_parks": show(cc.get("parks")),
        "cc_restores": show(cc.get("restores")),
        "cc_requests": show(cc.get("requests")),
        "cc_reused": show(cc.get("reused_tokens")),
        "cc_hit": pct(cc.get("reused_tokens"), cc.get("prompt_tokens")),

        "gpu_name": show(stat.get("gpu_name")),
        "cpu_name": show(stat.get("cpu_name")),
        "cores": show(stat.get("cores")),
        "threads": show(stat.get("threads")),
        "gpu_count": show(stat.get("gpu_count")),
    }

def clip(text, length=32):
    return text if len(text) <= length else text[:length - 1] + "…"

dashboards = []

def refresh_dashboards():
    for win, apply in list(dashboards):
        try:
            if win.winfo_exists():
                apply()
        except Exception:
            pass

def close_dashboard(win):
    dashboards[:] = [entry for entry in dashboards if entry[0] is not win]

def server_counts():
    up = sum(1 for lab, _iv, _rs, _svc in status_labels.values() if lab.cget("text") == "RUNNING")
    return up, len(status_labels) - up

def make_frameless(win):
    user32 = ctypes.windll.user32
    hwnd = user32.GetParent(win.winfo_id())
    style = user32.GetWindowLongW(hwnd, GWL_STYLE)
    user32.SetWindowLongW(hwnd, GWL_STYLE, (style & ~WS_CAPTION & ~WS_BORDER) | WS_MINIMIZEBOX)
    exstyle = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, (exstyle | WS_EX_APPWINDOW) & ~WS_EX_TOOLWINDOW)
    user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED)
    return hwnd

def open_dashboard():
    cfg = load_dashboard()
    win = tk.Toplevel(root)
    win.title("Monitor dashboard")
    win.geometry("400x310")
    win.configure(bg="#161616")
    win.attributes("-alpha", cfg["opacity"])
    win.attributes("-topmost", True)
    win.update()
    hwnd = make_frameless(win)
    top = tk.Frame(win, bg="#161616")
    top.pack(fill="x", padx=8, pady=(6, 2))
    tk.Label(top, text="Monitor", fg="#ddd", bg="#161616", font=("Segoe UI", 10, "bold")).pack(side="left")
    tk.Button(top, text="x", width=3, bg="#333", fg="white",
             command=lambda: (close_dashboard(win), win.destroy())).pack(side="right")
    tk.Button(top, text="-", width=3, bg="#333", fg="white",
             command=lambda: ctypes.windll.user32.ShowWindow(hwnd, SW_MINIMIZE)).pack(side="right")
    def redraw():
        ctypes.windll.user32.RedrawWindow(hwnd, None, None,
                                         RDW_INVALIDATE | RDW_UPDATENOW | RDW_ALLCHILDREN)

    def grab(event):
        win._drag = (event.x_root - win.winfo_x(), event.y_root - win.winfo_y())

    def drag(event):
        dx, dy = getattr(win, "_drag", (0, 0))
        win.geometry(f"+{event.x_root - dx}+{event.y_root - dy}")
        win.update_idletasks()
        redraw()

    def bind_drag(w):
        if isinstance(w, (tk.Button, tk.Spinbox)):
            return
        w.bind("<Button-1>", grab)
        w.bind("<B1-Motion>", drag)
        w.bind("<ButtonRelease-1>", lambda _e: redraw())
        for c in w.winfo_children():
            bind_drag(c)
    tiles = {}
    grid = tk.Frame(win, bg="#161616")
    grid.pack(fill="both", expand=True, padx=8, pady=2)

    def disable(key):
        cfg["fields"] = [k for k in cfg["fields"] if k != key]
        save_dashboard(cfg)
        logline(f'[{time.strftime("%H:%M:%S")}] dashboard field {FIELD_TITLES[key]} off')
        build_tiles()

    def build_tiles():
        for w in grid.winfo_children():
            w.destroy()
        tiles.clear()
        keys = [k for k in cfg["fields"] if k in FIELD_TITLES]
        for i, key in enumerate(keys):
            cell = tk.Frame(grid, bg="#1e1e1e")
            cell.grid(row=i // 2, column=i % 2, sticky="nsew", padx=2, pady=2)
            title = tk.Label(cell, text=FIELD_TITLES[key], fg="#888", bg="#1e1e1e",
                             font=("Segoe UI", 8))
            title.pack(anchor="w", padx=6, pady=(4, 0))
            lab = tk.Label(cell, text="-", fg="#0f0", bg="#1e1e1e", font=("Segoe UI", 11, "bold"))
            lab.pack(anchor="w", padx=6, pady=(0, 4))
            tiles[key] = lab
            for w in (cell, title, lab):
                w.bind("<Double-1>", lambda _e, key=key: disable(key))
        rows = max(1, (len(keys) + 1) // 2)
        win.geometry(f"400x{rows * 46 + 102}")
        bind_drag(grid)
        redraw()

    def apply_settings():
        cfg.update(load_dashboard())
        win.attributes("-alpha", cfg["opacity"])
        build_tiles()

    apply_settings()
    dashboards.append((win, apply_settings))
    win.protocol("WM_DELETE_WINDOW", lambda: (close_dashboard(win), win.destroy()))
    servers = tk.Label(win, text="-", fg="#ddd", bg="#161616", font=("Segoe UI", 9))
    servers.pack(anchor="w", padx=8, pady=(2, 0))
    stamp = tk.Label(win, text="-", fg="#ddd", bg="#161616", font=("Segoe UI", 9))
    stamp.pack(anchor="w", padx=8, pady=(0, 2))
    def on_refresh(_event=None):
        try:
            cfg["refresh"] = max(1, int(float(refresh_var.get())))
        except ValueError:
            return
        save_dashboard(cfg)
    row2 = tk.Frame(win, bg="#161616")
    row2.pack(fill="x", padx=8, pady=(0, 6))
    tk.Label(row2, text="refresh", fg="#888", bg="#161616", font=("Segoe UI", 8)).pack(side="left")
    refresh_var = tk.StringVar(value=str(cfg["refresh"]))
    spin = tk.Spinbox(row2, from_=5, to=600, textvariable=refresh_var, width=6, bg="#1e1e1e",
                      fg="#ddd", buttonbackground="#333", insertbackground="white", command=on_refresh)
    spin.pack(side="left")
    spin.bind("<KeyRelease>", on_refresh)
    tk.Label(row2, text="s", fg="#888", bg="#161616", font=("Segoe UI", 8)).pack(side="left")
    bind_drag(win)
    results = queue.Queue()
    def worker():
        try:
            results.put((None, metric_values(fetch_metrics(cfg["url"]))))
        except Exception as e:
            results.put((str(e), None))
    def render(err, vals):
        up, down = server_counts()
        servers.config(text=f"servers  up {up} / down {down}")
        if err:
            stamp.config(text=f"fetch failed: {err[:70]}")
            logline(f'[{time.strftime("%H:%M:%S")}] dashboard fetch failed: {err}')
            return
        for key, text in vals.items():
            if key in tiles:
                tiles[key].config(text=clip(text))
        stamp.config(text=f'updated {time.strftime("%H:%M:%S")}')
    def drain():
        try:
            while True:
                render(*results.get_nowait())
        except queue.Empty:
            pass
        win.after(1000, drain)
    def tick():
        threading.Thread(target=worker, daemon=True).start()
        win.after(int(cfg["refresh"] * 1000), tick)
    tick()
    drain()
    return win

root = tk.Tk()
root.title("Server Watcher")
root.geometry("560x460")
root.configure(bg="#1e1e1e")

services = load_settings()
status_labels = {}
last_check = {}

summary = tk.Label(root, text="", fg="#ddd", bg="#1e1e1e", font=("Segoe UI", 9, "bold"))
summary.pack(anchor="w", padx=10, pady=(8, 0))

frame = tk.Frame(root, bg="#1e1e1e")
frame.pack(fill="both", expand=True, padx=10, pady=10)

ROW_WIDTHS = (10, 14, 12, 6, 5, 6, 6)
ROW_HEADERS = ("Status", "Name", "Host", "Port", "Interval", "Restarts", "Auto")

def rebuild_rows():
    for w in frame.winfo_children():
        w.destroy()
    status_labels.clear()
    head = tk.Frame(frame, bg="#1e1e1e")
    head.pack(fill="x", pady=(0, 4))
    for title, width in zip(ROW_HEADERS, ROW_WIDTHS):
        tk.Label(head, text=title, width=width, anchor="w", fg="#888", bg="#1e1e1e",
                 font=("Segoe UI", 8, "bold")).pack(side="left")
    for r, svc in enumerate(services, start=1):
        svc.setdefault("restarts", 0)
        bg = "#232323" if r % 2 else "#1e1e1e"
        row = tk.Frame(frame, bg=bg)
        row.pack(fill="x", pady=2)
        lab = tk.Label(row, text="checking...", width=ROW_WIDTHS[0], fg="#888", bg=bg)
        lab.pack(side="left")
        tk.Label(row, text=svc["name"], width=ROW_WIDTHS[1], anchor="w", fg="white", bg=bg).pack(side="left")
        host = svc.get("host") or "127.0.0.1"
        tk.Label(row, text=host, width=ROW_WIDTHS[2], anchor="w",
                 fg="#888" if host in LOCAL_HOSTS else "#3cf", bg=bg).pack(side="left")
        tk.Label(row, text=str(svc["port"]), width=ROW_WIDTHS[3], fg="#ddd", bg=bg).pack(side="left")
        iv = tk.Label(row, text=f'{svc["interval"]}s', width=ROW_WIDTHS[4], fg="#aaa", bg=bg)
        iv.pack(side="left")
        rs = tk.Label(row, text=f'r:{svc["restarts"]}', width=ROW_WIDTHS[5], fg="#fa0", bg=bg)
        rs.pack(side="left")
        auto = svc.get("auto_restart", True)
        tk.Label(row, text="auto" if auto else "manual", width=ROW_WIDTHS[6],
                 fg="#3f3" if auto else "#888", bg=bg).pack(side="left")
        tk.Button(row, text="start", width=6, bg="#333", fg="white",
                  command=lambda s=svc: start_now(s)).pack(side="left")
        key = f'{svc["name"]} {host}:{svc["port"]}'
        status_labels[key] = (lab, iv, rs, svc)
        last_check.setdefault(key, 0)

rebuild_rows()

log = scrolledtext.ScrolledText(root, height=10, bg="#111", fg="#0f0", insertbackground="white")
log.pack(fill="both", expand=True, padx=10, pady=(0, 10))

def logline(msg):
    log.insert("end", msg + "\n")
    log.see("end")
    file_log(msg)

def start_now(svc):
    host = svc.get("host") or "127.0.0.1"
    if host not in LOCAL_HOSTS:
        logline(f'[{time.strftime("%H:%M:%S")}] {svc["name"]} {host}:{svc["port"]} is remote, cannot start locally')
        return
    try:
        start_service(svc)
        logline(f'[{time.strftime("%H:%M:%S")}] {svc["name"]} ({svc["port"]}) manual start: {" ".join(svc["cmd"])}')
    except Exception as e:
        logline(f'[{time.strftime("%H:%M:%S")}] FAILED {svc["name"]}: {e}')

def check_all():
    now = time.time()
    for name, (lab, iv, rs, svc) in list(status_labels.items()):
        if now - last_check.get(name, 0) < svc["interval"]:
            continue
        last_check[name] = now
        host = svc.get("host") or "127.0.0.1"
        local = host in LOCAL_HOSTS
        port_ok = port_open(svc["port"], host, timeout=0.3 if local else 1.5)
        healthy = port_ok and http_ok(svc.get("url", ""))
        if healthy:
            lab.config(text="RUNNING", fg="#3f3")
        else:
            lab.config(text="DOWN", fg="#f33")
            reason = "port closed" if not port_ok else "HTTP fail"
            if not local:
                logline(f'[{time.strftime("%H:%M:%S")}] {name} ({reason}) -> remote, not startable')
                continue
            if not svc.get("auto_restart", True):
                logline(f'[{time.strftime("%H:%M:%S")}] {name} ({reason}) -> auto-restart off')
                continue
            logline(f'[{time.strftime("%H:%M:%S")}] {name} ({reason}) -> starting')
            try:
                start_service(svc)
                svc["restarts"] = svc.get("restarts", 0) + 1
                rs.config(text=f'r:{svc["restarts"]}')
                save_settings(services)
                logline(f'[{time.strftime("%H:%M:%S")}] started: {" ".join(svc["cmd"])} (total restarts: {svc["restarts"]})')
            except Exception as e:
                logline(f'[{time.strftime("%H:%M:%S")}] FAILED {name}: {e}')
    up, down = server_counts()
    summary.config(text=f"servers  up {up} / down {down}")

def open_settings():
    win = tk.Toplevel(root)
    win.title("Settings")
    win.geometry("1180x700")
    win.configure(bg="#1e1e1e")
    cols = ("Name", "Host", "Port", "Interval", "URL (optional)", "Call path", "Args", "Working dir", "Auto restart")
    widths = (12, 14, 8, 8, 16, 30, 20, 26, 10)
    tree = tk.Frame(win, bg="#1e1e1e")
    tree.pack(fill="both", expand=True, padx=10, pady=10)
    for i, c in enumerate(cols):
        tk.Label(tree, text=c, fg="#ddd", bg="#1e1e1e", width=widths[i], anchor="w").grid(row=0, column=i)
    entries = []

    def make_row(svc):
        r = len(entries) + 1
        row_e = []
        cmd = svc.get("cmd", [])
        vals = [svc.get("name", ""), svc.get("host") or "127.0.0.1", str(svc.get("port", "")),
                str(svc.get("interval", 60)), svc.get("url", ""), cmd[0] if cmd else "",
                " ".join(cmd[1:]), svc.get("cwd", "")]
        for c, val in enumerate(vals):
            e = tk.Entry(tree, bg="#2a2a2a", fg="white", width=widths[c])
            e.grid(row=r, column=c, pady=2, padx=1)
            e.insert(0, val)
            row_e.append(e)
        var = tk.BooleanVar(value=bool(svc.get("auto_restart", True)))
        tk.Checkbutton(tree, variable=var, bg="#1e1e1e", fg="white", selectcolor="#2a2a2a").grid(row=r, column=8, pady=2, padx=1)
        row_e.append(var)
        entries.append(row_e)
        return row_e

    for svc in services:
        make_row(svc)

    def add_row():
        row = make_row({"name": "", "host": "127.0.0.1", "port": "", "interval": 60,
                        "url": "", "cmd": [], "cwd": "", "auto_restart": True})
        row[0].focus_set()

    dash_cfg = load_dashboard()

    def apply_fields():
        dash_cfg["fields"] = [key for keys in FIELD_GROUPS.values() for key in keys if field_vars[key].get()]
        save_dashboard(dash_cfg)
        refresh_dashboards()

    def apply_field(key):
        apply_fields()
        logline(f'[{time.strftime("%H:%M:%S")}] dashboard field {FIELD_TITLES[key]} {"on" if field_vars[key].get() else "off"}')

    def apply_opacity(_event=None):
        try:
            value = min(100, max(40, int(float(opacity_var.get()))))
        except ValueError:
            return
        dash_cfg["opacity"] = value / 100
        opacity_var.set(str(value))
        save_dashboard(dash_cfg)
        refresh_dashboards()
        logline(f'[{time.strftime("%H:%M:%S")}] dashboard opacity {value}%')

    drow = tk.Frame(win, bg="#1e1e1e")
    drow.pack(fill="x", padx=10, pady=(4, 2))
    tk.Label(drow, text="Dashboard opacity", fg="#ddd", bg="#1e1e1e", font=("Segoe UI", 9, "bold")).pack(side="left")
    opacity_var = tk.StringVar(value=str(int(round(dash_cfg["opacity"] * 100))))
    spin = tk.Spinbox(drow, from_=40, to=100, increment=5, textvariable=opacity_var, width=6,
                      bg="#2a2a2a", fg="white", buttonbackground="#333", insertbackground="white",
                      font=("Segoe UI", 9), command=apply_opacity)
    spin.pack(side="left", padx=(8, 0))
    spin.bind("<KeyRelease>", apply_opacity)
    tk.Label(drow, text="%", fg="#888", bg="#1e1e1e", font=("Segoe UI", 8)).pack(side="left")

    tk.Label(win, text="Dashboard fields (tick to show as tiles)", fg="#ddd", bg="#1e1e1e",
             font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=10)
    panel = tk.Frame(win, bg="#1e1e1e")
    panel.pack(fill="x", padx=10, pady=(4, 6))
    field_vars = {}

    for group, keys in FIELD_GROUPS.items():
        tk.Label(panel, text=group, fg="#888", bg="#1e1e1e", font=("Segoe UI", 8, "bold")).pack(anchor="w", pady=(4, 0))
        for i in range(0, len(keys), 6):
            line = tk.Frame(panel, bg="#1e1e1e")
            line.pack(fill="x", pady=1)
            for key in keys[i:i + 6]:
                var = tk.BooleanVar(value=key in dash_cfg["fields"])
                field_vars[key] = var
                tk.Checkbutton(line, text=FIELD_TITLES[key], variable=var, command=lambda key=key: apply_field(key),
                               bg="#1e1e1e", fg="white", selectcolor="#2a2a2a", font=("Segoe UI", 8)).pack(side="left", padx=6)

    def on_save():
        new_services = []
        for i, row_e in enumerate(entries, start=1):
            name = row_e[0].get().strip()
            if not name:
                if not (row_e[2].get().strip() or row_e[5].get().strip() or row_e[6].get().strip()):
                    continue
                messagebox.showerror("Error", f"Row {i}: Name is required")
                return
            try:
                port = int(row_e[2].get().strip())
                interval = max(1, int(row_e[3].get().strip()))
            except ValueError:
                messagebox.showerror("Error", f"{name}: Port and Interval must be numbers")
                return
            path = row_e[5].get().strip()
            args = split_command(row_e[6].get().strip())
            new_services.append({
                "name": name,
                "host": row_e[1].get().strip() or "127.0.0.1",
                "port": port,
                "interval": interval,
                "url": row_e[4].get().strip(),
                "cmd": ([path] + args) if path else args,
                "cwd": row_e[7].get().strip(),
                "auto_restart": bool(row_e[8].get()),
                "restarts": next((s.get("restarts", 0) for s in services if s["name"] == name), 0),
            })
        services.clear()
        services.extend(new_services)
        save_settings(services)
        apply_fields()
        rebuild_rows()
        logline(f'[{time.strftime("%H:%M:%S")}] settings saved')
        win.destroy()
    btns = tk.Frame(win, bg="#1e1e1e")
    btns.pack(pady=5)
    tk.Button(btns, text="Save", command=on_save, bg="#333", fg="white").pack(side="left", padx=4)
    tk.Button(btns, text="Add Row", command=add_row, bg="#333", fg="white").pack(side="left", padx=4)
    win.geometry(f"1180x{max(700, win.winfo_reqheight())}")

def open_scanner():
    try:
        candidates = scan_candidates()
    except RuntimeError as e:
        messagebox.showerror("Scan failed", str(e))
        return
    win = tk.Toplevel(root)
    win.title("Scan running services")
    win.geometry("1150x420")
    win.configure(bg="#1e1e1e")
    tk.Label(win, text="Running python/node services (Call path prefers the script, not just node/python.exe):",
             fg="#ddd", bg="#1e1e1e").pack(anchor="w", padx=10, pady=(10, 4))
    style = ttk.Style(win)
    style.theme_use("clam")
    style.configure("Scan.Treeview", background="#111", fieldbackground="#111", foreground="#0f0", rowheight=22, borderwidth=0)
    style.configure("Scan.Treeview.Heading", background="#333", foreground="#ddd")
    style.map("Scan.Treeview", background=[("selected", "#0a5")], foreground=[("selected", "#fff")])
    body = tk.Frame(win, bg="#1e1e1e")
    body.pack(fill="both", expand=True, padx=10)
    cols = (("process", "Process", 70), ("path", "Call path", 280), ("cwd", "Working dir", 170),
            ("port", "Port", 60), ("pid", "PID", 70), ("flag", "Flag", 60), ("command", "Command", 300))
    keys = [c[0] for c in cols]
    tree = ttk.Treeview(body, columns=keys, show="headings", selectmode="extended", style="Scan.Treeview")
    tree.tag_configure("worker", foreground="#f88")
    def row_values(cand):
        return tuple(cand["cmdline"] if k == "command" else cand[k] for k in keys)
    def sort_value(cand, key):
        val = row_values(cand)[keys.index(key)]
        return int(val) if key in ("port", "pid") else str(val).lower()
    def render(order):
        tree.delete(*tree.get_children())
        for i in order:
            cand = candidates[i]
            tree.insert("", "end", iid=str(i), values=row_values(cand), tags=("worker",) if cand["flag"] == "worker" else ())
    descending = {}
    def sort_by(key):
        order = sorted(range(len(candidates)), key=lambda i: sort_value(candidates[i], key), reverse=descending.get(key, False))
        descending[key] = not descending.get(key, False)
        render(order)
    for key, title, width in cols:
        tree.heading(key, text=title, command=lambda k=key: sort_by(k))
        tree.column(key, width=width, anchor="w", stretch=(key == "command"))
    render(range(len(candidates)))
    bar = tk.Scrollbar(body, command=tree.yview, bg="#333")
    tree.configure(yscrollcommand=bar.set)
    bar.pack(side="right", fill="y")
    tree.pack(side="left", fill="both", expand=True)
    if not candidates:
        tk.Label(win, text="Nothing found.", fg="#f88", bg="#1e1e1e").pack(anchor="w", padx=10, pady=6)
    def on_add():
        picked = tree.selection()
        if not picked:
            messagebox.showinfo("Scan", "Select at least one service to add")
            return
        added, skipped = [], []
        for iid in picked:
            cand = candidates[int(iid)]
            if any(s.get("port") == cand["port"] and s.get("name") == cand["name"] for s in services):
                skipped.append(cand["name"])
                continue
            services.append({k: cand[k] for k in ("name", "host", "port", "interval", "url", "cmd", "cwd", "restarts", "auto_restart")})
            added.append(cand["name"])
        if added:
            save_settings(services)
            rebuild_rows()
            logline(f'[{time.strftime("%H:%M:%S")}] scan added {len(added)} service(s): {", ".join(added)}')
        if skipped:
            logline(f'[{time.strftime("%H:%M:%S")}] scan skipped already configured: {", ".join(skipped)}')
        win.destroy()
    btns = tk.Frame(win, bg="#1e1e1e")
    btns.pack(pady=6)
    tk.Button(btns, text="Add Selected", command=on_add, bg="#333", fg="white").pack(side="left", padx=4)
    tk.Button(btns, text="Export CSV + JSON", command=lambda: export_scan(candidates), bg="#333", fg="white").pack(side="left", padx=4)

def loop():
    check_all()
    root.after(1000, loop)

btn_frame = tk.Frame(root, bg="#1e1e1e")
btn_frame.pack(pady=(0, 6))
tk.Button(btn_frame, text="Check Now", command=check_all, bg="#333", fg="white").pack(side="left", padx=4)
tk.Button(btn_frame, text="Settings", command=open_settings, bg="#333", fg="white").pack(side="left", padx=4)
tk.Button(btn_frame, text="Scan", command=open_scanner, bg="#333", fg="white").pack(side="left", padx=4)
tk.Button(btn_frame, text="Dashboard", command=open_dashboard, bg="#333", fg="white").pack(side="left", padx=4)
tk.Button(btn_frame, text="Export Config", command=export_config, bg="#333", fg="white").pack(side="left", padx=4)
tk.Button(btn_frame, text="Open Log", command=lambda: os.startfile(LOG_FILE) if os.path.exists(LOG_FILE) else messagebox.showinfo("Log", "No log yet"), bg="#333", fg="white").pack(side="left", padx=4)

loop()
root.mainloop()
