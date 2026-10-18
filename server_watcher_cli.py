#!/usr/bin/env python3
"""CLI-only server watcher: no GUI, configured by server_watcher_settings.json."""

import argparse
import csv
import ctypes
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(HERE, "server_watcher_settings.json")
LOG_FILE = os.path.join(HERE, "server_watcher.log")
DASHBOARD_FILE = os.path.join(HERE, "dashboard_settings.json")
SCAN_EXPORT = os.path.join(HERE, "scan_export")

LOCAL_HOSTS = {"", "127.0.0.1", "localhost", "::1"}
SCAN_EXES = ("python.exe", "pythonw.exe", "node.exe")
SCAN_BIND_HOSTS = {"0.0.0.0", "127.0.0.1", "::", "::1"}
SCRIPT_EXTS = (".js", ".mjs", ".cjs", ".ts", ".py", ".bat", ".cmd")
SUBPROCESS_PATTERNS = ("webpack-loaders.js", "postcss.js", "start-server.js", "next-router-worker",
                       "jest-worker", "tsx-loader", "babel-loader", "css-loader", "sass-loader",
                       "file-loader", "url-loader", "mini-css-extract-plugin", "fork-ts-checker",
                       "eslint-webpack-plugin")

DEFAULT_SERVICES = [
    {"name": "Web Server 2", "host": "127.0.0.1", "port": 8080, "interval": 300, "url": "",
     "cmd": ["python", "-m", "http.server", "8080"], "cwd": "", "auto_restart": True, "restarts": 0},
]

DEFAULT_DASHBOARD = {"url": "http://127.0.0.1:8080/metrics", "refresh": 30,
                     "fields": ["speed", "gpu_load", "vram", "temp", "power", "pcie_rx", "cpu",
                                "disk_read", "state", "context"]}

PROCESS_QUERY_LIMITED_INFORMATION, PROCESS_VM_READ = 0x1000, 0x0010


def clock():
    return time.strftime("%H:%M:%S")


def log(msg):
    print(f"[{clock()}] {msg}")
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{clock()}] {msg}\n")
    except OSError:
        pass


def load_settings():
    if not os.path.exists(CONFIG_FILE):
        return [dict(s) for s in DEFAULT_SERVICES]
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return [dict(s) for s in DEFAULT_SERVICES]
    if not isinstance(data, list) or not data:
        return [dict(s) for s in DEFAULT_SERVICES]
    for s in data:
        s.setdefault("name", "")
        s.setdefault("host", "127.0.0.1")
        s.setdefault("url", "")
        s.setdefault("cwd", "")
        s.setdefault("cmd", [])
        s.setdefault("interval", 60)
        s.setdefault("restarts", 0)
        s.setdefault("auto_restart", True)
    return data


def save_settings(services):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(services, f, indent=2, ensure_ascii=False)


def load_dashboard():
    if os.path.exists(DASHBOARD_FILE):
        try:
            with open(DASHBOARD_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                cfg = dict(DEFAULT_DASHBOARD)
                cfg.update({k: v for k, v in data.items() if k in DEFAULT_DASHBOARD})
                return cfg
        except (OSError, ValueError):
            pass
    return dict(DEFAULT_DASHBOARD)


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


def start_service(svc, hidden=False):
    flags = subprocess.CREATE_NO_WINDOW if hidden else subprocess.CREATE_NEW_CONSOLE
    subprocess.Popen(svc["cmd"], cwd=svc.get("cwd") or None, creationflags=flags)


def check(svc):
    host = svc.get("host") or "127.0.0.1"
    local = host in LOCAL_HOSTS
    ok = port_open(svc["port"], host, timeout=0.3 if local else 1.5)
    if ok and http_ok(svc.get("url", "")):
        return "RUNNING", "", local
    return "DOWN", ("port closed" if not ok else "HTTP fail"), local


def check_all(services, last, restart=True):
    now = time.time()
    rows, up, down = [], 0, 0
    for svc in services:
        if now - last.get(id(svc), 0) < svc["interval"]:
            continue
        last[id(svc)] = now
        state, reason, local = check(svc)
        if state == "RUNNING":
            up += 1
        else:
            down += 1
            if not restart:
                pass
            elif not local:
                log(f'{svc["name"]} ({reason}) -> remote, not startable')
            elif not svc.get("auto_restart", True):
                log(f'{svc["name"]} ({reason}) -> auto-restart off')
            else:
                log(f'{svc["name"]} ({reason}) -> starting')
                try:
                    start_service(svc)
                    svc["restarts"] = svc.get("restarts", 0) + 1
                    save_settings(services)
                    log(f'started: {" ".join(svc["cmd"])} (total restarts: {svc["restarts"]})')
                except Exception as e:
                    log(f'FAILED {svc["name"]}: {e}')
        rows.append((state, svc["name"], svc.get("host") or "127.0.0.1", svc["port"],
                     svc["interval"], svc.get("restarts", 0),
                     "yes" if svc.get("auto_restart", True) else "no"))
    print_table(("STATUS", "NAME", "HOST", "PORT", "INTERVAL", "RESTARTS", "AUTO"), rows)
    print(f"servers up {up} / down {down}")
    return down


def start_now(svc):
    host = svc.get("host") or "127.0.0.1"
    if host not in LOCAL_HOSTS:
        log(f'{svc["name"]} {host}:{svc["port"]} is remote, cannot start locally')
        return
    try:
        start_service(svc)
        log(f'{svc["name"]} ({svc["port"]}) manual start: {" ".join(svc["cmd"])}')
    except Exception as e:
        log(f'FAILED {svc["name"]}: {e}')


def print_table(headers, rows):
    widths = [max(len(str(h)), max((len(str(r[i])) for r in rows), default=0))
              for i, h in enumerate(headers)]
    print("  ".join(str(h).ljust(w) for h, w in zip(headers, widths)))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print("  ".join(str(v).ljust(w) for v, w in zip(row, widths)))


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


def listening_ports():
    ports = {}
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=20,
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


def processes():
    names = ",".join(f"'{n}'" for n in SCAN_EXES)
    script = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-CimInstance Win32_Process | "
              f"Where-Object {{ $_.Name -in @({names}) }} | "
              "Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Compress")
    done = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    text = (done.stdout or "").strip()
    if not text:
        return []
    data = json.loads(text)
    if isinstance(data, dict):
        data = [data]
    return [{"pid": int(i["ProcessId"]), "name": str(i.get("Name") or ""),
             "cmdline": str(i.get("CommandLine") or "").strip()}
            for i in data if isinstance(i, dict) and i.get("ProcessId")]


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
    _fields_ = [("ExitStatus", ctypes.c_void_p), ("PebBaseAddress", ctypes.c_void_p),
                ("AffinityMask", ctypes.c_void_p), ("BasePriority", ctypes.c_long),
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


def guess_cwd(cmd):
    for token in cmd[1:]:
        if os.path.isabs(token) and os.path.splitext(token)[1].lower() in SCRIPT_EXTS:
            if "node_modules" not in token.lower():
                return os.path.dirname(token)
    return ""


def is_worker(cmdline):
    lower = (cmdline or "").lower()
    return not cmdline or any(pat in lower for pat in SUBPROCESS_PATTERNS)


def scan():
    ports_by_pid = listening_ports()
    found = []
    for proc in processes():
        cmd = split_command(proc["cmdline"])
        ports = ports_by_pid.get(proc["pid"], [])
        if not cmd or not ports:
            continue
        exe_path = shutil.which(cmd[0]) or cmd[0]
        script = next((t for t in cmd[1:] if os.path.splitext(t)[1].lower() in SCRIPT_EXTS), "")
        found.append({
            "process": os.path.splitext(proc["name"])[0],
            "path": script if os.path.isabs(script) else exe_path,
            "cwd": process_cwd(proc["pid"]) or guess_cwd(cmd),
            "port": min(ports),
            "pid": proc["pid"],
            "flag": "worker" if is_worker(proc["cmdline"]) else "main",
            "cmdline": proc["cmdline"],
            "cmd": [exe_path] + cmd[1:],
        })
    return sorted(found, key=lambda c: (c["flag"], c["process"], c["port"]))


def export(base, headers, rows):
    try:
        with open(base + ".csv", "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            writer.writerows(rows)
        with open(base + ".json", "w", encoding="utf-8") as f:
            json.dump([dict(zip(headers, r)) for r in rows], f, indent=2, ensure_ascii=False)
    except OSError as e:
        print(f"export failed: {e}")
        return None
    return base + ".csv", base + ".json"


def fetch_metrics(url, timeout=3.0):
    req = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def main():
    sys.stdout.reconfigure(line_buffering=True)
    parser = argparse.ArgumentParser(description="Server watcher CLI — JSON config, no GUI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_check = sub.add_parser("check", help="one check pass, restart anything down")
    p_check.add_argument("--no-restart", action="store_true", help="report only")
    p_run = sub.add_parser("run", help="watch loop, honouring each service interval")
    p_run.add_argument("--no-restart", action="store_true", help="report only")
    sub.add_parser("list", help="print the configured services")
    p_start = sub.add_parser("start", help="start one service by name")
    p_start.add_argument("name")
    p_scan = sub.add_parser("scan", help="list listening python/node processes")
    p_scan.add_argument("--add", action="store_true", help="append the main rows to the config")
    p_metrics = sub.add_parser("metrics", help="print the dashboard fields from the metrics endpoint")
    p_metrics.add_argument("--watch", type=int, metavar="SECONDS", help="repeat every N seconds")

    args = parser.parse_args()
    services = load_settings()

    if args.cmd == "list":
        print_table(("NAME", "HOST", "PORT", "INTERVAL", "URL", "CMD", "CWD", "AUTO", "RESTARTS"),
                    [(s["name"], s["host"], s["port"], s["interval"], s["url"],
                      " ".join(s["cmd"]), s["cwd"],
                      "yes" if s["auto_restart"] else "no", s["restarts"]) for s in services])
        print(f"\nconfig: {CONFIG_FILE}")
        return 0

    if args.cmd == "start":
        svc = next((s for s in services if s["name"] == args.name), None)
        if not svc:
            print(f"no service named {args.name!r}")
            return 1
        start_now(svc)
        return 0

    if args.cmd == "scan":
        found = scan()
        print_table(("PROCESS", "PATH", "CWD", "PORT", "PID", "FLAG", "COMMAND"),
                    [(c["process"], c["path"], c["cwd"], c["port"], c["pid"], c["flag"], c["cmdline"])
                     for c in found])
        print(f"\n{len(found)} listening process(es)")
        if args.add:
            added = [c for c in found if c["flag"] == "main"]
            for c in added:
                services.append({"name": f'{c["process"]} :{c["port"]}', "host": "127.0.0.1",
                                 "port": c["port"], "interval": 60, "url": "", "cmd": c["cmd"],
                                 "cwd": c["cwd"], "auto_restart": True, "restarts": 0})
            save_settings(services)
            log(f"added {len(added)} scanned service(s) to {CONFIG_FILE}")
        export(SCAN_EXPORT, ("process", "path", "cwd", "port", "pid", "flag", "command"),
              [(c["process"], c["path"], c["cwd"], c["port"], c["pid"], c["flag"], c["cmdline"])
               for c in found])
        return 0

    if args.cmd == "metrics":
        cfg = load_dashboard()
        while True:
            try:
                data = fetch_metrics(cfg["url"])
            except Exception as e:
                print(f"fetch failed: {e}")
                return 1
            for group, keys in data.items():
                if isinstance(keys, dict):
                    picked = [(k, v) for k, v in keys.items()
                              if k in cfg["fields"] and not isinstance(v, (list, dict))]
                    if picked:
                        print(f"[{group}]")
                        for k, v in picked:
                            print(f"  {k}: {v if v not in (None, '') else '-'}")
                elif group in cfg["fields"] and not isinstance(keys, (list, dict)):
                    print(f"{group}: {keys if keys not in (None, '') else '-'}")
            if not args.watch:
                return 0
            time.sleep(args.watch)

    last = {}
    down = check_all(services, last, restart=not args.no_restart)
    if args.cmd == "check":
        return 1 if down else 0

    print(f"watching {len(services)} service(s), Ctrl+C to stop")
    try:
        while True:
            time.sleep(min(s["interval"] for s in services))
            check_all(services, last, restart=not args.no_restart)
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
