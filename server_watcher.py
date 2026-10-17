import socket, subprocess, time, json, os, csv, ctypes, queue, threading, shutil
import urllib.request, urllib.error
import tkinter as tk
from tkinter import scrolledtext, messagebox, ttk

CONFIG_FILE = "server_watcher_settings.json"
LOG_FILE = "server_watcher.log"
SCAN_EXPORT = "scan_export"
CONFIG_EXPORT = "config_export"
DASHBOARD_FILE = "dashboard_settings.json"

DEFAULT_DASHBOARD = {"url": "http://127.0.0.1:8080/metrics", "opacity": 0.85, "refresh": 30}
METRIC_KEYS = ("speed", "gpu_load", "vram", "temp", "power", "pcie", "cpu", "disk")
METRIC_TITLES = {"speed": "Speed", "gpu_load": "GPU load", "vram": "VRAM", "temp": "GPU temp",
                 "power": "Power", "pcie": "PCIe rx", "cpu": "CPU", "disk": "Disk read"}

GWL_STYLE, GWL_EXSTYLE = -16, -20
WS_CAPTION, WS_BORDER, WS_MINIMIZEBOX = 0x00C00000, 0x00800000, 0x00020000
WS_EX_APPWINDOW, WS_EX_TOOLWINDOW = 0x00040000, 0x00000080
SW_MINIMIZE, SW_RESTORE = 2, 9
SWP_NOMOVE, SWP_NOSIZE, SWP_NOZORDER, SWP_FRAMECHANGED = 0x0002, 0x0001, 0x0004, 0x0020
PROCESS_QUERY_LIMITED_INFORMATION, PROCESS_VM_READ = 0x1000, 0x0010

SCAN_EXES = ("python.exe", "pythonw.exe", "node.exe")
SCAN_BIND_HOSTS = {"0.0.0.0", "127.0.0.1", "::", "::1"}
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
                    s.setdefault("cwd", "")
                    s.setdefault("restarts", 0)
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
            "cmd": [exe_path] + cmd[1:],
            "restarts": 0,
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
    headers = ("name", "path", "cwd", "port", "interval", "url", "cmd", "restarts")
    rows = [(s.get("name", ""), (s.get("cmd") or [""])[0], s.get("cwd", ""), s.get("port", ""),
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
    def show(value, suffix=""):
        if value is None:
            return "-"
        if isinstance(value, float):
            value = round(value, 1)
        return f"{value}{suffix}"
    def mib(value):
        return "-" if value is None else round(value / 1024 / 1024)
    speed = live.get("tok_s") if live.get("tok_s") is not None else hw.get("tok_s")
    vals = {
        "speed": show(speed, " tok/s"),
        "gpu_load": show(hw.get("gpu_util"), "%"),
        "vram": f'{mib(hw.get("gpu_mem_used"))} / {mib(hw.get("gpu_mem_total"))} MiB',
        "temp": show(hw.get("gpu_temp"), " C"),
        "power": f'{show(hw.get("gpu_power"))} / {show(hw.get("gpu_power_limit"))} W',
        "pcie": show(hw.get("gpu_pcie_rx_mb"), " MB/s"),
        "cpu": show(hw.get("cpu"), "%"),
        "disk": show(hw.get("disk_read_mb"), " MB/s"),
    }
    ctx, max_ctx = live.get("prompt_total"), eng.get("max_context")
    vals["context"] = f"{round(ctx / max_ctx * 100)}%" if ctx and max_ctx else "-"
    vals["state"] = live.get("state") or "-"
    return vals

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
    win.geometry("400x280")
    win.configure(bg="#161616")
    win.attributes("-alpha", cfg["opacity"])
    win.attributes("-topmost", True)
    win.update()
    hwnd = make_frameless(win)
    top = tk.Frame(win, bg="#161616")
    top.pack(fill="x", padx=8, pady=(6, 2))
    tk.Label(top, text="Monitor", fg="#ddd", bg="#161616", font=("Segoe UI", 10, "bold")).pack(side="left")
    tk.Button(top, text="x", width=3, bg="#333", fg="white", command=win.destroy).pack(side="right")
    tk.Button(top, text="-", width=3, bg="#333", fg="white",
             command=lambda: ctypes.windll.user32.ShowWindow(hwnd, SW_MINIMIZE)).pack(side="right")
    def grab(event):
        win._drag = (event.x_root - win.winfo_x(), event.y_root - win.winfo_y())
    def drag(event):
        dx, dy = getattr(win, "_drag", (0, 0))
        win.geometry(f"+{event.x_root - dx}+{event.y_root - dy}")
    win.bind("<Button-1>", grab)
    win.bind("<B1-Motion>", drag)
    tiles = {}
    grid = tk.Frame(win, bg="#161616")
    grid.pack(fill="both", expand=True, padx=8, pady=2)
    for i, key in enumerate(METRIC_KEYS):
        cell = tk.Frame(grid, bg="#1e1e1e")
        cell.grid(row=i // 2, column=i % 2, sticky="nsew", padx=2, pady=2)
        tk.Label(cell, text=METRIC_TITLES[key], fg="#888", bg="#1e1e1e", font=("Segoe UI", 8)).pack(anchor="w", padx=6, pady=(4, 0))
        tiles[key] = tk.Label(cell, text="-", fg="#0f0", bg="#1e1e1e", font=("Segoe UI", 11, "bold"))
        tiles[key].pack(anchor="w", padx=6, pady=(0, 4))
    servers = tk.Label(win, text="-", fg="#ddd", bg="#161616", font=("Segoe UI", 9))
    servers.pack(anchor="w", padx=8, pady=(2, 0))
    state = tk.Label(win, text="-", fg="#ddd", bg="#161616", font=("Segoe UI", 9))
    state.pack(anchor="w", padx=8, pady=(0, 2))
    def on_opacity(_event):
        cfg["opacity"] = round(float(scale.get()) / 100, 2)
        win.attributes("-alpha", cfg["opacity"])
        save_dashboard(cfg)
    row = tk.Frame(win, bg="#161616")
    row.pack(fill="x", padx=8, pady=(4, 6))
    tk.Label(row, text="opacity", fg="#888", bg="#161616", font=("Segoe UI", 8)).pack(side="left")
    scale = ttk.Scale(row, from_=40, to=100, value=int(cfg["opacity"] * 100), orient="horizontal", command=on_opacity)
    scale.pack(side="left", fill="x", expand=True)
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
            state.config(text=f"fetch failed: {err[:70]}")
            logline(f'[{time.strftime("%H:%M:%S")}] dashboard fetch failed: {err}')
            return
        for key, text in vals.items():
            if key in tiles:
                tiles[key].config(text=text)
        state.config(text=f'{vals["state"]} | context {vals["context"]} | {time.strftime("%H:%M:%S")}')
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
root.geometry("520x420")
root.configure(bg="#1e1e1e")

services = load_settings()
status_labels = {}
last_check = {}

frame = tk.Frame(root, bg="#1e1e1e")
frame.pack(fill="both", expand=True, padx=10, pady=10)

def rebuild_rows():
    for w in frame.winfo_children():
        w.destroy()
    status_labels.clear()
    for svc in services:
        svc.setdefault("restarts", 0)
        row = tk.Frame(frame, bg="#1e1e1e")
        row.pack(fill="x", pady=3)
        tk.Label(row, text=svc["name"], width=14, anchor="w", fg="white", bg="#1e1e1e").pack(side="left")
        lab = tk.Label(row, text="checking...", width=10, fg="#888", bg="#1e1e1e")
        lab.pack(side="left")
        iv = tk.Label(row, text=f'{svc["interval"]}s', width=5, fg="#aaa", bg="#1e1e1e")
        iv.pack(side="left")
        rs = tk.Label(row, text=f'r:{svc["restarts"]}', width=6, fg="#fa0", bg="#1e1e1e")
        rs.pack(side="left")
        key = f'{svc["name"]} ({svc["port"]})'
        status_labels[key] = (lab, iv, rs, svc)
        last_check.setdefault(key, 0)

rebuild_rows()

log = scrolledtext.ScrolledText(root, height=10, bg="#111", fg="#0f0", insertbackground="white")
log.pack(fill="both", expand=True, padx=10, pady=(0, 10))

def logline(msg):
    log.insert("end", msg + "\n")
    log.see("end")
    file_log(msg)

def check_all():
    now = time.time()
    for name, (lab, iv, rs, svc) in list(status_labels.items()):
        if now - last_check.get(name, 0) < svc["interval"]:
            continue
        last_check[name] = now
        port_ok = port_open(svc["port"])
        healthy = port_ok and http_ok(svc.get("url", ""))
        if healthy:
            lab.config(text="RUNNING", fg="#3f3")
        else:
            lab.config(text="DOWN", fg="#f33")
            reason = "port closed" if not port_ok else "HTTP fail"
            logline(f'[{time.strftime("%H:%M:%S")}] {name} ({reason}) -> starting')
            try:
                start_service(svc)
                svc["restarts"] = svc.get("restarts", 0) + 1
                rs.config(text=f'r:{svc["restarts"]}')
                save_settings(services)
                logline(f'[{time.strftime("%H:%M:%S")}] started: {" ".join(svc["cmd"])} (total restarts: {svc["restarts"]})')
            except Exception as e:
                logline(f'[{time.strftime("%H:%M:%S")}] FAILED {name}: {e}')

def open_settings():
    win = tk.Toplevel(root)
    win.title("Settings")
    win.geometry("1120x420")
    win.configure(bg="#1e1e1e")
    cols = ("Name", "Port", "Interval", "URL (optional)", "Call path", "Args", "Working dir")
    widths = (12, 8, 8, 18, 34, 24, 30)
    tree = tk.Frame(win, bg="#1e1e1e")
    tree.pack(fill="both", expand=True, padx=10, pady=10)
    for i, c in enumerate(cols):
        tk.Label(tree, text=c, fg="#ddd", bg="#1e1e1e", width=widths[i], anchor="w").grid(row=0, column=i)
    entries = []
    for r, svc in enumerate(services, start=1):
        row_e = []
        cmd = svc.get("cmd", [])
        vals = [svc["name"], str(svc["port"]), str(svc["interval"]),
                svc.get("url", ""), cmd[0] if cmd else "", " ".join(cmd[1:]), svc.get("cwd", "")]
        for c, val in enumerate(vals):
            e = tk.Entry(tree, bg="#2a2a2a", fg="white", width=widths[c])
            e.grid(row=r, column=c, pady=2, padx=1)
            e.insert(0, val)
            row_e.append(e)
        entries.append(row_e)
    def on_save():
        new_services = []
        try:
            for row_e in entries:
                path = row_e[4].get().strip()
                args = split_command(row_e[5].get().strip())
                new_services.append({
                    "name": row_e[0].get().strip(),
                    "port": int(row_e[1].get().strip()),
                    "interval": max(1, int(row_e[2].get().strip())),
                    "url": row_e[3].get().strip(),
                    "cmd": ([path] + args) if path else args,
                    "cwd": row_e[6].get().strip(),
                    "restarts": next((s.get("restarts", 0) for s in services if s["name"] == row_e[0].get().strip()), 0),
                })
        except ValueError:
            messagebox.showerror("Error", "Port and Interval must be numbers")
            return
        services.clear()
        services.extend(new_services)
        save_settings(services)
        rebuild_rows()
        logline(f'[{time.strftime("%H:%M:%S")}] settings saved')
        win.destroy()
    tk.Button(win, text="Save", command=on_save, bg="#333", fg="white").pack(pady=5)

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
            services.append({k: cand[k] for k in ("name", "port", "interval", "url", "cmd", "cwd", "restarts")})
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
