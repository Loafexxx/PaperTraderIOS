"""Phone companion server: lets the iPhone app use Claude (and your trading skills) through this PC.

Plain HTTP + a bearer token, meant for your home Wi-Fi or a Tailscale network. Claude runs as background
jobs the phone polls, because scans take minutes. Only one Claude job runs at a time.

Endpoints (all JSON; every route except /api/ping needs `Authorization: Bearer <token>`):
  GET  /api/ping                 -> {ok, version, auth}
  GET  /api/status               -> {claude, busy}
  GET  /api/usage                -> [{key, label, pct, resets}]
  GET  /api/posture              -> breadth / uptrend / sector skills summary
  POST /api/scan                 -> {job}    body: account + budget fields (see _scan_prompt)
  POST /api/estimate             -> {job}    body: {symbol, name, price, entry, stop, t1, t2}
  GET  /api/job/<id>             -> {status: running|done|error|cancelled, elapsed, result?, error?}
  POST /api/job/<id>/cancel      -> {ok}
"""
from __future__ import annotations

import datetime as dt
import hmac
import json
import secrets
import socket
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import claude_bridge, scanner
from .verify import verify_claude_picks

VERSION = "1.0"


def new_token() -> str:
    return secrets.token_urlsafe(18)


def local_addresses() -> list[str]:
    """LAN IPv4 addresses (plus Tailscale's, if installed) the phone can use to reach this PC."""
    out = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        out.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        r = subprocess.run(["ip", "-4", "-o", "addr", "show", "scope", "global"], capture_output=True, text=True,
                           timeout=3)
        for line in r.stdout.splitlines():
            ip = line.split()[3].split("/")[0]
            if ip not in out:
                out.append(ip)
    except Exception:  # noqa: BLE001
        pass
    return out


class _Jobs:
    def __init__(self):
        self.lock = threading.Lock()
        self.jobs: dict[str, dict] = {}

    def busy(self) -> bool:
        with self.lock:
            return any(j["status"] == "running" for j in self.jobs.values())

    def start(self, kind: str, fn) -> str:
        jid = uuid.uuid4().hex[:12]
        handle = claude_bridge.RunHandle()
        job = {"id": jid, "kind": kind, "status": "running", "started": time.time(), "handle": handle}
        with self.lock:
            # keep the last 20 jobs
            for old in sorted(self.jobs, key=lambda k: self.jobs[k]["started"])[:-19]:
                self.jobs.pop(old, None)
            self.jobs[jid] = job

        def run():
            try:
                job["result"] = fn(handle)
                job["status"] = "cancelled" if handle.cancelled else "done"
            except claude_bridge.Cancelled:
                job["status"] = "cancelled"
            except Exception as e:  # noqa: BLE001
                job["status"] = "cancelled" if handle.cancelled else "error"
                job["error"] = f"{type(e).__name__}: {e}"
            job["finished"] = time.time()
        threading.Thread(target=run, daemon=True).start()
        return jid

    def get(self, jid):
        with self.lock:
            return self.jobs.get(jid)


def _num(d: dict, k: str, default: float) -> float:
    try:
        return float(d.get(k, default))
    except (TypeError, ValueError):
        return default


def _scan_prompt(b: dict) -> str:
    return claude_bridge.SCAN_PROMPT.format(
        equity=_num(b, "equity", 10000), cash=_num(b, "cash", 10000), holdings=str(b.get("holdings") or "nothing")[:400],
        budget=_num(b, "budget", 1000), risk_pct=_num(b, "risk_pct", 1), max_pos=_num(b, "max_pos", 20),
        max_risk=_num(b, "max_risk", 5), fx=_num(b, "fx", 0.65), n=int(_num(b, "n", 8)),
        exclude=", ".join(str(x) for x in (b.get("exclude") or [])[:30]) or "none",
        today=dt.date.today().isoformat(), data_howto=claude_bridge._DATA_HOWTO)


class CompanionServer:
    def __init__(self, port: int, token: str, claude_path: str = ""):
        self.port, self.token, self.claude_path = int(port), token, claude_path
        self.jobs = _Jobs()
        self._posture = (0.0, None)
        self.httpd: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    # ----- lifecycle
    def start(self):
        srv = self

        class H(BaseHTTPRequestHandler):
            server_version = "PaperTraderCompanion/" + VERSION

            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                body = json.dumps(obj, default=float).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _authed(self):
                got = self.headers.get("Authorization", "")
                ok = hmac.compare_digest(got.encode(), f"Bearer {srv.token}".encode())
                if not ok:
                    self._send(401, {"error": "bad or missing token"})
                return ok

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                if n > 100_000:
                    raise ValueError("body too large")
                return json.loads(self.rfile.read(n) or b"{}") if n else {}

            def do_GET(self):  # noqa: N802
                try:
                    srv._get(self)
                except Exception as e:  # noqa: BLE001
                    self._send(500, {"error": f"{type(e).__name__}: {e}"})

            def do_POST(self):  # noqa: N802
                try:
                    srv._post(self)
                except Exception as e:  # noqa: BLE001
                    self._send(500, {"error": f"{type(e).__name__}: {e}"})

        self.httpd = ThreadingHTTPServer(("0.0.0.0", self.port), H)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return self

    def stop(self):
        if self.httpd:
            for j in list(self.jobs.jobs.values()):
                if j["status"] == "running":
                    j["handle"].cancel()
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None

    # ----- routes
    def _job_view(self, j):
        v = {"id": j["id"], "kind": j["kind"], "status": j["status"],
             "elapsed": round((j.get("finished") or time.time()) - j["started"], 1)}
        if j["status"] == "done":
            v["result"] = j.get("result")
        if j.get("error"):
            v["error"] = j["error"]
        return v

    def _get(self, h):
        path = h.path.split("?")[0].rstrip("/")
        if path == "/api/ping":
            return h._send(200, {"ok": True, "version": VERSION, "auth": "bearer"})
        if not h._authed():
            return
        if path == "/api/status":
            return h._send(200, {"claude": bool(claude_bridge.find_claude(self.claude_path)), "busy": self.jobs.busy()})
        if path == "/api/usage":
            return h._send(200, claude_bridge.usage(self.claude_path))
        if path == "/api/posture":
            ts, val = self._posture
            if not val or time.time() - ts > 1800:
                val = scanner.market_posture()
                self._posture = (time.time(), val)
            return h._send(200, val)
        if path.startswith("/api/job/"):
            j = self.jobs.get(path.split("/")[3])
            return h._send(200, self._job_view(j)) if j else h._send(404, {"error": "no such job"})
        h._send(404, {"error": "not found"})

    def _post(self, h):
        path = h.path.split("?")[0].rstrip("/")
        if not h._authed():
            return
        if path.startswith("/api/job/") and path.endswith("/cancel"):
            j = self.jobs.get(path.split("/")[3])
            if not j:
                return h._send(404, {"error": "no such job"})
            j["handle"].cancel()
            return h._send(200, {"ok": True})
        if path in ("/api/scan", "/api/estimate"):
            if not claude_bridge.find_claude(self.claude_path):
                return h._send(503, {"error": "Claude Code CLI not found on the PC"})
            if self.jobs.busy():
                return h._send(409, {"error": "Claude is already busy with another job"})
            b = h._body()
            if path == "/api/scan":
                prompt = _scan_prompt(b)
                mps = int(_num(b, "max_per_sector", 2))

                def fn(handle):
                    j = claude_bridge.ask(prompt, self.claude_path, 1500, 80, handle=handle)
                    picks, dropped = verify_claude_picks(j.get("picks", []), mps, lambda: handle.cancelled)
                    return {"claude": j, "picks": picks, "dropped": dropped}
                return h._send(200, {"job": self.jobs.start("scan", fn)})
            sym = str(b.get("symbol", "")).upper()[:10]
            if not sym.replace(".", "").replace("-", "").isalnum():
                return h._send(400, {"error": "bad symbol"})
            price = _num(b, "price", 0)
            prompt = claude_bridge.ESTIMATE_PROMPT.format(
                symbol=sym, name=str(b.get("name", ""))[:80], price=price, entry=_num(b, "entry", price),
                stop=_num(b, "stop", price * 0.95), t1=_num(b, "t1", price * 1.05), t2=_num(b, "t2", price * 1.1),
                today=dt.date.today().isoformat(), data_howto=claude_bridge._DATA_HOWTO)
            return h._send(200, {"job": self.jobs.start(
                "estimate", lambda handle: claude_bridge.ask(prompt, self.claude_path, 900, 40, handle=handle))})
        h._send(404, {"error": "not found"})


def run_headless():
    """`papertrader --server`: run only the companion server (uses the token saved in the ledger settings)."""
    from .engine import Ledger
    L = Ledger()
    s = L.s
    if not s.get("server_token"):
        s["server_token"] = new_token()
        L.save()
    srv = CompanionServer(int(s.get("server_port", 8787)), s["server_token"], s.get("claude_path", "")).start()
    print(f"Paper Trader companion server on port {srv.port}")
    for ip in local_addresses():
        print(f"  http://{ip}:{srv.port}")
    print(f"  token: {s['server_token']}")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        srv.stop()
