import time

import pytest
from fastapi.testclient import TestClient

import app.main as main


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "WARMUP_SECONDS", 0)
    monkeypatch.setattr(main, "BASE_LATENCY_MS", 1)
    monkeypatch.setattr(main, "CHAOS_ENABLED", True)
    main.State.draining = False
    main.State.error_rate = 0.0
    main.State.extra_latency_ms = 0.0
    with TestClient(main.app) as c:
        yield c, main


def metric(c, name, **labels):
    for line in c.get("/metrics").text.splitlines():
        if line.startswith(name + "{") and all(f'{k}="{v}"' in line for k, v in labels.items()):
            return float(line.rsplit(" ", 1)[1])
    return 0.0


def test_order_endpoint_and_metrics_use_route_template(client):
    c, _ = client
    before = metric(c, "slo_service_http_requests_total", route="/api/orders/{order_id}", status="200")
    assert c.get("/api/orders/42").json()["id"] == 42
    after = metric(c, "slo_service_http_requests_total", route="/api/orders/{order_id}", status="200")
    assert after == before + 1  # one series per route template, not per order id


def test_probes_and_metrics_endpoint_are_not_counted(client):
    c, _ = client
    c.get("/healthz"), c.get("/readyz")
    assert metric(c, "slo_service_http_requests_total", route="/healthz") == 0


def test_readiness_fails_while_warming_and_draining(client):
    c, _ = client
    assert c.get("/readyz").status_code == 200
    main.State.draining = True
    assert c.get("/readyz").json() == {"ready": False, "reason": "draining"}
    main.State.draining = False
    main.State.started_at = time.monotonic()
    main.WARMUP_SECONDS = 60  # restored by monkeypatch
    assert c.get("/readyz").status_code == 503


def test_chaos_injects_errors_that_metrics_capture(client):
    c, _ = client
    assert c.post("/chaos", json={"error_rate": 1.0}).status_code == 200
    before = metric(c, "slo_service_http_requests_total", status="503")
    assert c.get("/api/orders/1").status_code == 503
    assert metric(c, "slo_service_http_requests_total", status="503") == before + 1


def test_chaos_validates_input_and_is_off_by_default(client, monkeypatch):
    c, _ = client
    assert c.post("/chaos", json={"error_rate": 2}).status_code == 422
    monkeypatch.setattr(main, "CHAOS_ENABLED", False)
    assert c.post("/chaos", json={"error_rate": 0.5}).status_code == 404


def test_build_info_always_present(client):
    c, _ = client
    assert 'slo_service_build_info{version="dev"} 1.0' in c.get("/metrics").text
