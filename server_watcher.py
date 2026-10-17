import socket, subprocess, time, json, os, urllib.request, urllib.error
import tkinter as tk
from tkinter import scrolledtext, messagebox

CONFIG_FILE = "server_watcher_settings.json"
LOG_FILE = "server_watcher.log"

DEFAULT_SERVICES = [
    {"name": "Web Server 1", "port": 8000, "url": "http://127.0.0.1:8000/", "cmd": ["python", "-m", "http.server", "8000"], "interval": 5},
    {"name": "Web Server 2", "port": 8080, "url": "", "cmd": ["python", "-m", "http.server", "8080"], "interval": 5},
    {"name": "API Server",   "port": 3000, "url": "http://127.0.0.1:3000/health", "cmd": ["node", "server.js"], "interval": 30},
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

def loop():
    check_all()
    root.after(1000, loop)

btn_frame = tk.Frame(root, bg="#1e1e1e")
btn_frame.pack(pady=(0, 6))
tk.Button(btn_frame, text="Check Now", command=check_all, bg="#333", fg="white").pack(side="left", padx=4)
tk.Button(btn_frame, text="Settings", command=open_settings, bg="#333", fg="white").pack(side="left", padx=4)
tk.Button(btn_frame, text="Open Log", command=lambda: os.startfile(LOG_FILE) if os.path.exists(LOG_FILE) else messagebox.showinfo("Log", "No log yet"), bg="#333", fg="white").pack(side="left", padx=4)

loop()
root.mainloop()
