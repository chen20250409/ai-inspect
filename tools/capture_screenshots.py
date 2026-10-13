# -*- coding: utf-8 -*-
"""通过 SSH 隧道 + Chrome 无头模式，抓取运行中系统的真实浏览器截图。

用途：虚拟机的 80 端口不在宿主机网络内可达，先通过 SSH 建立本地端口转发，
再用本机 Chrome 无头模式访问 http://127.0.0.1:8080 截图。
"""
import os
import select
import socket
import subprocess
import sys
import threading
import time

import paramiko

SSH_HOST, SSH_PORT = "127.0.0.1", 2222
SSH_USER, SSH_PWD = "chen", "123456"
REMOTE_HOST, REMOTE_PORT = "127.0.0.1", 80       # 虚拟机上的 nginx
LOCAL_PORT = 8080

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "screenshots")

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PROFILE = os.path.join(os.environ.get("TEMP", "."), "chrome-shot-profile")

PAGES = [
    ("01_health.png",      "/health",                "验收1：/health 接口返回 ok"),
    ("02_console.png",     "/",                      "质检控制台（整条链路：Nginx->FastAPI->MySQL）"),
    ("03_records.png",     "/records",               "验收3：/records 查询到质检记录"),
    ("04_defect01.png",    "/static/defect01.jpg",   "验收4：浏览器打开缺陷原图 defect01.jpg（黑色）"),
    ("05_defect02.png",    "/static/defect02.jpg",   "缺陷样本 defect02.jpg（表面划痕）"),
]


def start_tunnel():
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(SSH_HOST, port=SSH_PORT, username=SSH_USER, password=SSH_PWD)
    transport = client.get_transport()

    def pump(conn, chan):
        try:
            while True:
                r, _, _ = select.select([conn, chan], [], [], 0.5)
                if conn in r:
                    data = conn.recv(8192)
                    if not data:
                        break
                    chan.sendall(data)
                if chan in r:
                    data = chan.recv(8192)
                    if not data:
                        break
                    conn.sendall(data)
        except Exception:
            pass
        finally:
            try:
                chan.close()
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass

    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", LOCAL_PORT))
    srv.listen(16)

    def accept_loop():
        while True:
            try:
                conn, addr = srv.accept()
            except OSError:
                return
            try:
                chan = transport.open_channel(
                    "direct-tcpip", (REMOTE_HOST, REMOTE_PORT), addr)
            except Exception:
                conn.close()
                continue
            threading.Thread(target=pump, args=(conn, chan), daemon=True).start()

    threading.Thread(target=accept_loop, daemon=True).start()
    return client, srv


def shoot(name, path, width=1440, height=900):
    out = os.path.join(OUT_DIR, name)
    url = "http://127.0.0.1:%d%s" % (LOCAL_PORT, path)
    cmd = [
        CHROME,
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        "--no-first-run",
        "--no-default-browser-check",
        "--user-data-dir=" + PROFILE,
        "--window-size=%d,%d" % (width, height),
        "--virtual-time-budget=8000",
        "--screenshot=" + out,
        url,
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=120)
    ok = os.path.exists(out) and os.path.getsize(out) > 0
    size = os.path.getsize(out) if ok else 0
    print("  %-18s %-24s %s (%d bytes)"
          % (name, path, "OK" if ok else "FAILED", size))
    if not ok:
        print("    stderr:", r.stderr.decode("utf-8", "replace")[:300])
    return ok


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(PROFILE, exist_ok=True)
    print("建立 SSH 隧道 127.0.0.1:%d -> %s:%d ..." % (LOCAL_PORT, REMOTE_HOST, REMOTE_PORT))
    client, srv = start_tunnel()
    time.sleep(1.5)
    print("输出目录：%s\n" % OUT_DIR)

    failed = 0
    for name, path, desc in PAGES:
        print("[%s]" % desc)
        if not shoot(name, path):
            failed += 1
        print()

    srv.close()
    client.close()
    print("完成：%d/%d 张截图成功" % (len(PAGES) - failed, len(PAGES)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
