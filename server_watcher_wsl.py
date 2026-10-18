#!/usr/bin/env python3
"""WSL/Linux server watcher: no GUI, configured by server_watcher_settings.json.

Run inside WSL, e.g.
  wsl -d OpenClaw-U3 -- python3 /mnt/c/AI_Server/Coding/WinServerMonitor/server_watcher_wsl.py check
"""

import argparse
import csv
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(HERE, "server_watcher_settings.json")
LOG_FILE = os.path.join(HERE, "server_watcher.log")
DASHBOARD_FILE = os.path.join(HERE, "dashboard_settings.json")
SCAN_EXPORT = os.path.join(HERE, "scan_export")

LOCAL_HOSTS = {"", "127.0.0.1", "localhost", "::1"}
SCAN_BIND_HOSTS = {"0.0.0.0", "127.0.0.1", "*", "::", "::1"}
SCRIPT_EXTS = (".js", ".mjs", ".cjs", ".ts", ".py", ".sh", ".bat", ".cmd")
SUBPROCESS_PATTERNS = ("webpack-loaders.js", "postcss.js", "start-server.js", "next-router-worker",
                       "jest-worker", "tsx-loader", "babel-loader", "css-loader", "sass-loader",
                       "file-loader", "url-loader", "mini-css-extract-plugin", "fork-ts-checker",
                       "eslint-webpack-plugin")

DEFAULT_SERVICES = [
    {"name": "Web Server 2", "host": "127.0.0.1", "port": 8080, "interval": 300, "url": "",
     "cmd": ["python3", "-m", "http.server", "8080"], "cwd": "", "auto_restart": True, "restarts": 0},
]

DEFAULT_DASHBOARD = {"url": "http://127.0.0.1:8080/metrics", "refresh": 30,
                     "fields": ["speed", "gpu_load", "vram", "temp", "power", "pcie_rx", "cpu",
                                "disk_read", "state", "context"]}


def clock():
    return time.strftime("%H:%M:%S")


def log(msg):
    print(f"[{clock()}] {msg}")
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{clock()}] {msg}\n")
    except OSError:
        pass


def wsl_path(path):
    """Map a Windows path (C:\\...) to its WSL mount (/mnt/c/...); leave Linux paths as-is."""
    match = re.match(r"^([A-Za-z]):[\\/](.*)$", path)
    if not match:
        return path
    return f"/mnt/{match.group(1).lower()}/{match.group(2).replace(chr(92), '/')}"


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
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    s = socket.socket(family, socket.SOCK_STREAM)
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
    cmd = [shutil.which(t) or t for t in svc["cmd"]]
    cwd = wsl_path(svc.get("cwd") or "") or None
    # start_new_session keeps the service alive when the watcher exits
    subprocess.Popen(cmd, cwd=cwd, start_new_session=True)


def gateway_ip():
    """WSL2 default-route address, i.e. the Windows host when networking is NAT."""
    try:
        out = subprocess.run(["ip", "route"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return ""
    match = re.search(r"default via (\S+)", out)
    return match.group(1) if match else ""


def target_host(host, gateway):
    return gateway if gateway and host in LOCAL_HOSTS else host


def check(svc, gateway=""):
    host = svc.get("host") or "127.0.0.1"
    local = host in LOCAL_HOSTS
    ok = port_open(svc["port"], target_host(host, gateway), timeout=0.3 if local else 1.5)
    if ok and http_ok(svc.get("url", "").replace("127.0.0.1", gateway or "127.0.0.1", 1)
                     .replace("localhost", gateway or "localhost", 1)):
        return "RUNNING", "", local
    return "DOWN", ("port closed" if not ok else "HTTP fail"), local


def check_all(services, last, restart=True, gateway=""):
    now = time.time()
    rows, up, down = [], 0, 0
    for svc in services:
        if now - last.get(id(svc), 0) < svc["interval"]:
            continue
        last[id(svc)] = now
        state, reason, local = check(svc, gateway)
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
        rows.append((state, svc["name"], target_host(svc.get("host") or "127.0.0.1", gateway), svc["port"],
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


def listening_ports():
    try:
        out = subprocess.run(["ss", "-tlnp"], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return {}
    ports = {}
    for line in out.splitlines()[1:]:
        parts = line.split(maxsplit=5)
        if len(parts) < 6 or parts[0] != "LISTEN":
            continue
        host, _, port_text = parts[3].rpartition(":")
        if host.strip("[]") not in SCAN_BIND_HOSTS:
            continue
        match = re.search(r"pid=(\d+)", parts[5])
        if not match:
            continue
        try:
            ports.setdefault(int(match.group(1)), set()).add(int(port_text))
        except ValueError:
            continue
    return {pid: sorted(found) for pid, found in ports.items()}


def processes():
    procs = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        pid = int(entry)
        try:
            with open(f"/proc/{pid}/comm", encoding="utf-8", errors="replace") as f:
                name = f.read().strip()
            if name != "node" and not name.startswith("python"):
                continue
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmdline = f.read().replace(b"\0", b" ").decode("utf-8", "replace").strip()
            procs.append({"pid": pid, "name": name, "cmdline": cmdline,
                          "cwd": os.path.realpath(f"/proc/{pid}/cwd")})
        except OSError:
            continue
    return procs


def guess_cwd(cmd):
    for token in cmd[1:]:
        if token.startswith("/") and os.path.splitext(token)[1].lower() in SCRIPT_EXTS:
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
        cmd = proc["cmdline"].split()
        ports = ports_by_pid.get(proc["pid"], [])
        if not cmd or not ports:
            continue
        exe_path = shutil.which(cmd[0]) or cmd[0]
        script = next((t for t in cmd[1:] if os.path.splitext(t)[1].lower() in SCRIPT_EXTS), "")
        found.append({
            "process": proc["name"],
            "path": script if script.startswith("/") else exe_path,
            "cwd": proc["cwd"] or guess_cwd(cmd),
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
    parser = argparse.ArgumentParser(description="Server watcher for WSL — JSON config, no GUI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_check = sub.add_parser("check", help="one check pass, restart anything down")
    p_check.add_argument("--no-restart", action="store_true", help="report only")
    p_run = sub.add_parser("run", help="watch loop, honouring each service interval")
    p_run.add_argument("--no-restart", action="store_true", help="report only")
    sub.add_parser("list", help="print the configured services")
    p_start = sub.add_parser("start", help="start one service by name")
    p_start.add_argument("name")
    p_scan = sub.add_parser("scan", help="list listening python/node processes in WSL")
    p_scan.add_argument("--add", action="store_true", help="append the main rows to the config")
    p_metrics = sub.add_parser("metrics", help="print the dashboard fields from the metrics endpoint")
    p_metrics.add_argument("--watch", type=int, metavar="SECONDS", help="repeat every N seconds")
    for p in (p_check, p_run, p_metrics):
        p.add_argument("--gateway", action="store_true",
                       help="reach localhost services through the WSL default route (the Windows host)")

    args = parser.parse_args()
    gateway = gateway_ip() if getattr(args, "gateway", False) else ""
    if gateway:
        print(f"gateway: {gateway or 'no default route, using localhost'}")
    services = load_settings()

    if args.cmd == "list":
        print_table(("NAME", "HOST", "PORT", "INTERVAL", "URL", "CMD", "CWD", "AUTO", "RESTARTS"),
                    [(s["name"], s["host"], s["port"], s["interval"], s["url"],
                      " ".join(s["cmd"]), wsl_path(s["cwd"]),
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
        url = cfg["url"]
        if gateway:
            parsed = urllib.parse.urlparse(url)
            if parsed.hostname in LOCAL_HOSTS:
                url = urllib.parse.urlunparse(parsed._replace(netloc=f"{gateway}:{parsed.port}"))
        while True:
            try:
                data = fetch_metrics(url)
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
    down = check_all(services, last, restart=not args.no_restart, gateway=gateway)
    if args.cmd == "check":
        return 1 if down else 0

    print(f"watching {len(services)} service(s), Ctrl+C to stop")
    try:
        while True:
            time.sleep(min(s["interval"] for s in services))
            check_all(services, last, restart=not args.no_restart, gateway=gateway)
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
