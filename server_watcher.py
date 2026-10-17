import socket, subprocess, time, json, os, urllib.request, urllib.error
import tkinter as tk
from tkinter import scrolledtext, messagebox

CONFIG_FILE = "server_watcher_settings.json"
LOG_FILE = "server_watcher.log"

SCAN_EXES = ("python.exe", "pythonw.exe", "node.exe")
# Bind addresses that a probe from 127.0.0.1 can reach.
SCAN_BIND_HOSTS = {"0.0.0.0", "127.0.0.1", "::", "::1"}

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

def start_service(cmd):
    subprocess.Popen(cmd, creationflags=subprocess.CREATE_NEW_CONSOLE)

def list_listening_ports():
    """Map PID -> listening TCP ports that are reachable from 127.0.0.1."""
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
    """Return [{'pid', 'name', 'cmdline'}] for running python/node processes."""
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
    """Split a command line into argv, keeping double-quoted parts together."""
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

def scan_candidates():
    """Find running python/node services that listen on a local port."""
    ports_by_pid = list_listening_ports()
    candidates = []
    for proc in list_python_node_processes():
        cmd = split_command(proc["cmdline"])
        if not cmd:
            continue
        exe = os.path.splitext(proc["name"])[0]
        hint = proc["cmdline"] if len(proc["cmdline"]) <= 60 else proc["cmdline"][:57] + "..."
        for port in ports_by_pid.get(proc["pid"], []):
            candidates.append({
                "label": f'{exe} :{port}   (pid {proc["pid"]})   {hint}',
                "name": f"{exe} :{port}",
                "port": port,
                "interval": 5,
                "url": "",
                "cmd": cmd,
                "restarts": 0,
            })
    return candidates

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
        status_labels[svc["name"]] = (lab, iv, rs, svc)
        last_check.setdefault(svc["name"], 0)

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
                start_service(svc["cmd"])
                svc["restarts"] = svc.get("restarts", 0) + 1
                rs.config(text=f'r:{svc["restarts"]}')
                save_settings(services)
                logline(f'[{time.strftime("%H:%M:%S")}] started: {" ".join(svc["cmd"])} (total restarts: {svc["restarts"]})')
            except Exception as e:
                logline(f'[{time.strftime("%H:%M:%S")}] FAILED {name}: {e}')

def open_settings():
    win = tk.Toplevel(root)
    win.title("Settings")
    win.geometry("780x400")
    win.configure(bg="#1e1e1e")
    cols = ("Name", "Port", "Interval", "URL (optional)", "Command")
    tree = tk.Frame(win, bg="#1e1e1e")
    tree.pack(fill="both", expand=True, padx=10, pady=10)
    for i, c in enumerate(cols):
        tk.Label(tree, text=c, fg="#ddd", bg="#1e1e1e",
                 width=14 if i < 4 else 28, anchor="w").grid(row=0, column=i)
    entries = []
    for r, svc in enumerate(services, start=1):
        row_e = []
        vals = [svc["name"], str(svc["port"]), str(svc["interval"]),
                svc.get("url", ""), " ".join(svc["cmd"])]
        for c, val in enumerate(vals):
            e = tk.Entry(tree, bg="#2a2a2a", fg="white", width=14 if c < 4 else 28)
            e.grid(row=r, column=c, pady=2, padx=1)
            e.insert(0, val)
            row_e.append(e)
        entries.append(row_e)

    def on_save():
        new_services = []
        try:
            for row_e in entries:
                new_services.append({
                    "name": row_e[0].get().strip(),
                    "port": int(row_e[1].get().strip()),
                    "interval": max(1, int(row_e[2].get().strip())),
                    "url": row_e[3].get().strip(),
                    "cmd": row_e[4].get().strip().split(),
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
    win.geometry("820x400")
    win.configure(bg="#1e1e1e")
    tk.Label(win, text="Running python/node services with a listening port "
                       "(Ctrl/Shift click to select several):",
             fg="#ddd", bg="#1e1e1e").pack(anchor="w", padx=10, pady=(10, 4))
    box = tk.Listbox(win, selectmode="extended", bg="#111", fg="#0f0",
                     selectbackground="#0a5", activestyle="none")
    box.pack(fill="both", expand=True, padx=10)
    for cand in candidates:
        box.insert("end", cand["label"])
    if not candidates:
        tk.Label(win, text="Nothing found - no python/node process is listening on a local port.",
                 fg="#f88", bg="#1e1e1e").pack(pady=6)

    def on_add():
        picked = box.curselection()
        if not picked:
            messagebox.showinfo("Scan", "Select at least one service to add")
            return
        added, skipped = [], []
        for i in picked:
            cand = candidates[i]
            if any(s.get("port") == cand["port"] and s.get("name") == cand["name"] for s in services):
                skipped.append(cand["name"])
                continue
            services.append({k: cand[k] for k in ("name", "port", "interval", "url", "cmd", "restarts")})
            added.append(cand["name"])
        if added:
            save_settings(services)
            rebuild_rows()
            logline(f'[{time.strftime("%H:%M:%S")}] scan added {len(added)} service(s): {", ".join(added)}')
        if skipped:
            logline(f'[{time.strftime("%H:%M:%S")}] scan skipped already configured: {", ".join(skipped)}')
        win.destroy()

    tk.Button(win, text="Add Selected", command=on_add, bg="#333", fg="white").pack(pady=6)

def loop():
    check_all()
    root.after(1000, loop)

btn_frame = tk.Frame(root, bg="#1e1e1e")
btn_frame.pack(pady=(0, 6))
tk.Button(btn_frame, text="Check Now", command=check_all, bg="#333", fg="white").pack(side="left", padx=4)
tk.Button(btn_frame, text="Settings", command=open_settings, bg="#333", fg="white").pack(side="left", padx=4)
tk.Button(btn_frame, text="Scan", command=open_scanner, bg="#333", fg="white").pack(side="left", padx=4)
tk.Button(btn_frame, text="Open Log", command=lambda: os.startfile(LOG_FILE) if os.path.exists(LOG_FILE) else messagebox.showinfo("Log", "No log yet"), bg="#333", fg="white").pack(side="left", padx=4)

loop()
root.mainloop()
