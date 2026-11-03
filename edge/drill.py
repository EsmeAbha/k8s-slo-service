"""Breaks backends behind Envoy under steady traffic and measures what clients actually saw.

    bash edge/run.sh                  # starts everything and runs this

Exit code 1 if any check fails. Standard library only.
"""
from __future__ import annotations

import argparse
import http.client
import json
import ssl
import subprocess
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
COMPOSE = ["docker", "compose", "-f", str(HERE / "docker-compose.yml")]
ADMIN = "http://127.0.0.1:9901"
CHAOS = {"app1": 18001, "app2": 18002, "app3": 18003}
MAX_CLIENT_ERROR_RATE = 0.01


@dataclass
class Sample:
    status: int              # 0 means no HTTP response at all
    instance: str
    seconds: float


def summarize(samples: list[Sample]) -> dict:
    """Error rate counts everything except a 200, including a connection that got no response."""
    if not samples:
        return {"requests": 0, "errors": 0, "error_rate": 1.0, "p95_ms": 0.0, "max_ms": 0.0, "by_instance": {}, "by_status": {}}
    errors = sum(s.status != 200 for s in samples)
    latencies = sorted(s.seconds for s in samples)
    by_instance: dict[str, int] = {}
    by_status: dict[int, int] = {}
    for s in samples:
        by_status[s.status] = by_status.get(s.status, 0) + 1
        if s.instance:
            by_instance[s.instance] = by_instance.get(s.instance, 0) + 1
    return {"requests": len(samples), "errors": errors, "error_rate": errors / len(samples),
            "p95_ms": latencies[int(0.95 * (len(latencies) - 1))] * 1000, "max_ms": latencies[-1] * 1000,
            "by_instance": by_instance, "by_status": by_status}


CERT = HERE / "certs" / "edge.crt"


def connect(ctx: ssl.SSLContext) -> http.client.HTTPSConnection:
    return http.client.HTTPSConnection("localhost", 8443, context=ctx, timeout=5)


def fetch(conn: http.client.HTTPSConnection, method: str = "GET", path: str = "/api/orders/1") -> tuple[int, bytes]:
    conn.putrequest(method, path)
    conn.endheaders()
    r = conn.getresponse()
    return r.status, r.read()


def traffic(ctx: ssl.SSLContext, seconds: float, workers: int = 5, per_second: float = 10.0,
            during=None) -> list[Sample]:
    """Paced to stay under the rate limit: workers x per_second must be below 100 requests a second."""
    samples: list[Sample] = []
    lock = threading.Lock()
    end = time.monotonic() + seconds

    def worker() -> None:
        conn = connect(ctx)
        while time.monotonic() < end:
            start = time.monotonic()
            try:
                status, body = fetch(conn)
                instance = json.loads(body).get("version", "") if status == 200 else ""
            except (OSError, http.client.HTTPException, ValueError):
                status, instance = 0, ""
                conn.close()
                conn = connect(ctx)
            taken = time.monotonic() - start
            with lock:
                samples.append(Sample(status, instance, taken))
            time.sleep(max(0.0, 1 / per_second - taken))
        conn.close()

    threads = [threading.Thread(target=worker) for _ in range(workers)]
    for t in threads:
        t.start()
    if during:
        during()
    for t in threads:
        t.join()
    return samples


def stat(name: str) -> int:
    with urllib.request.urlopen(f"{ADMIN}/stats?filter=^{name}$", timeout=5) as r:
        for line in r.read().decode().splitlines():
            key, _, value = line.partition(": ")
            if key == name:
                return int(value)
    raise KeyError(name)


def chaos(instance: str, error_rate: float = 0.0, extra_latency_ms: float = 0.0) -> None:
    body = json.dumps({"error_rate": error_rate, "extra_latency_ms": extra_latency_ms}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{CHAOS[instance]}/chaos", body,
                                 {"content-type": "application/json"})
    urllib.request.urlopen(req, timeout=5).close()


def wait_until(cond, timeout: float, every: float = 0.5) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if cond():
                return True
        except (OSError, KeyError, http.client.HTTPException):
            pass
        time.sleep(every)
    return False


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, bool, str]] = []

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.rows.append((name, ok, detail))
        print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}", flush=True)

    @property
    def ok(self) -> bool:
        return all(ok for _, ok, _ in self.rows)

    def markdown(self) -> str:
        lines = ["| Check | Result | Measured |", "|---|---|---|"]
        lines += [f"| {n} | {'PASS' if ok else '**FAIL**'} | {d} |" for n, ok, d in self.rows]
        return "\n".join(lines) + "\n"


def describe(s: dict) -> str:
    return f"{s['errors']} of {s['requests']} failed, p95 {s['p95_ms']:.0f} ms"


def steady_state(ctx: ssl.SSLContext, r: Report) -> None:
    s = summarize(traffic(ctx, 6))
    r.check("Healthy: no client errors", s["errors"] == 0, describe(s))
    share = {k: round(v / s["requests"], 2) for k, v in s["by_instance"].items()}
    r.check("Healthy: all three instances serve", len(share) == 3 and min(share.values()) >= 0.1, str(share))


def tls_and_exposure(ctx: ssl.SSLContext, r: Report) -> None:
    conn = connect(ctx)
    status, _ = fetch(conn)
    r.check("TLS: certificate verifies, modern version", status == 200 and conn.sock.version() in ("TLSv1.2", "TLSv1.3"),
            conn.sock.version())
    conn.close()
    alpn = ssl.create_default_context(cafile=str(CERT))
    alpn.set_alpn_protocols(["h2"])
    conn = connect(alpn)
    conn.connect()
    r.check("TLS: HTTP/2 offered", conn.sock.selected_alpn_protocol() == "h2", str(conn.sock.selected_alpn_protocol()))
    conn.close()

    for method, path in (("GET", "/metrics"), ("GET", "/healthz"), ("GET", "/readyz"), ("POST", "/chaos"),
                         ("POST", "/api/orders/../../chaos"), ("POST", "/api/orders/%2e%2e/%2e%2e/chaos"),
                         ("GET", "//metrics")):
        conn = connect(ctx)
        status, _ = fetch(conn, method, path)
        conn.close()
        r.check(f"Not exposed: {method} {path}", status == 404, str(status))


def failing_instance(ctx: ssl.SSLContext, r: Report) -> None:
    """Answers health checks but fails every real request: only passive detection can catch this."""
    ejected = stat("cluster.orders.outlier_detection.ejections_enforced_total")
    retried = stat("cluster.orders.upstream_rq_retry")
    s = summarize(traffic(ctx, 15, during=lambda: chaos("app3", error_rate=1.0)))
    chaos("app3")
    r.check("One instance returns 503 for everything: clients unaffected",
            s["error_rate"] <= MAX_CLIENT_ERROR_RATE, describe(s))
    r.check("It is ejected", stat("cluster.orders.outlier_detection.ejections_enforced_total") > ejected)
    r.check("Retries covered the gap", stat("cluster.orders.upstream_rq_retry") > retried,
            f"{stat('cluster.orders.upstream_rq_retry') - retried} retries")
    r.check("It returns after the ejection ends",
            wait_until(lambda: stat("cluster.orders.outlier_detection.ejections_active") == 0, 45))


def slow_instance(ctx: ssl.SSLContext, r: Report) -> None:
    s = summarize(traffic(ctx, 12, during=lambda: chaos("app2", extra_latency_ms=5000)))
    chaos("app2")
    r.check("One instance hangs for 5 s: clients still answered", s["error_rate"] <= MAX_CLIENT_ERROR_RATE, describe(s))
    r.check("Nobody waits past the 3 s route timeout", s["max_ms"] < 3500, f"slowest {s['max_ms']:.0f} ms")
    wait_until(lambda: stat("cluster.orders.outlier_detection.ejections_active") == 0, 45)


def killed_instance(ctx: ssl.SSLContext, r: Report) -> None:
    def kill() -> None:
        time.sleep(3)
        subprocess.run([*COMPOSE, "kill", "app2"], check=True, capture_output=True)

    s = summarize(traffic(ctx, 12, during=kill))
    r.check("One instance killed without warning: clients unaffected", s["error_rate"] <= MAX_CLIENT_ERROR_RATE, describe(s))
    r.check("Health checks remove it", wait_until(lambda: stat("cluster.orders.membership_healthy") == 2, 15))
    after = summarize(traffic(ctx, 5))
    r.check("No traffic reaches it afterwards", "app2" not in after["by_instance"] and after["errors"] == 0, describe(after))
    subprocess.run([*COMPOSE, "start", "app2"], check=True, capture_output=True)
    r.check("It rejoins when it comes back", wait_until(lambda: stat("cluster.orders.membership_healthy") == 3, 30))


def rate_limit(ctx: ssl.SSLContext, r: Report) -> None:
    s = summarize(traffic(ctx, 4, workers=20, per_second=1000))
    other = {k: v for k, v in s["by_status"].items() if k not in (200, 429)}
    r.check("Burst over the limit: the excess gets 429, nothing else fails",
            s["by_status"].get(429, 0) > 0 and not other, str(s["by_status"]))
    time.sleep(3)
    after = summarize(traffic(ctx, 3))
    r.check("Limit recovers once the burst ends", after["errors"] == 0, describe(after))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default="edge-report.md")
    args = ap.parse_args(argv)
    ctx = ssl.create_default_context(cafile=str(CERT))
    r = Report()

    def ready() -> bool:
        return stat("cluster.orders.membership_healthy") == 3

    if not wait_until(ready, 90):
        r.check("All three instances become healthy", False)
    else:
        for scenario in (steady_state, tls_and_exposure, failing_instance, slow_instance, killed_instance, rate_limit):
            scenario(ctx, r)
    Path(args.report).write_text(r.markdown(), encoding="utf-8")
    print("Edge drill: PASS" if r.ok else "Edge drill: FAIL")
    return 0 if r.ok else 1


if __name__ == "__main__":
    sys.exit(main())
