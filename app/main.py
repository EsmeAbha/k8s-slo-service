"""A small orders API instrumented for SLO-based operations.

- /healthz  liveness: the process is up
- /readyz   readiness: warm-up finished and not draining (Kubernetes stops routing traffic otherwise)
- /metrics  Prometheus metrics: request count and latency histogram per route and status
- /api/orders/{id}  the "business" endpoint the SLOs are measured on
- /chaos    fault injection (error rate, extra latency), only when CHAOS_ENABLED=true,
            used to prove the alerts fire before a real incident does
"""
import asyncio
import os
import random
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from pydantic import BaseModel, Field

VERSION = os.environ.get("APP_VERSION", "dev")
WARMUP_SECONDS = float(os.environ.get("WARMUP_SECONDS", "2"))
BASE_LATENCY_MS = float(os.environ.get("BASE_LATENCY_MS", "20"))
CHAOS_ENABLED = os.environ.get("CHAOS_ENABLED", "false").lower() == "true"
NOT_MEASURED = {"/metrics", "/healthz", "/readyz", "/chaos"}

REQUESTS = Counter("slo_service_http_requests_total", "HTTP requests", ["method", "route", "status"])
LATENCY = Histogram("slo_service_http_request_duration_seconds", "HTTP request latency", ["route"],
                    buckets=(0.01, 0.025, 0.05, 0.1, 0.2, 0.3, 0.5, 1, 2.5, 5))
BUILD_INFO = Gauge("slo_service_build_info", "Always 1; labels carry the version", ["version"])
BUILD_INFO.labels(version=VERSION).set(1)


class State:
    started_at = time.monotonic()
    draining = False
    error_rate = 0.0
    extra_latency_ms = 0.0


@asynccontextmanager
async def lifespan(app):
    State.started_at = time.monotonic()
    yield
    # on SIGTERM: fail readiness first so the endpoint is removed before the process exits
    State.draining = True


app = FastAPI(title="slo-service", version=VERSION, lifespan=lifespan)


@app.middleware("http")
async def record_metrics(request: Request, call_next):
    start = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        route = request.scope.get("route")
        path = route.path if route else "unmatched"  # template, not raw path: keeps label cardinality bounded
        if path not in NOT_MEASURED:
            REQUESTS.labels(request.method, path, str(status)).inc()
            LATENCY.labels(path).observe(time.perf_counter() - start)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/readyz")
def readyz(response: Response):
    warming = time.monotonic() - State.started_at < WARMUP_SECONDS
    if warming or State.draining:
        response.status_code = 503
        return {"ready": False, "reason": "warming up" if warming else "draining"}
    return {"ready": True}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/api/orders/{order_id}")
async def get_order(order_id: int):
    await asyncio.sleep((BASE_LATENCY_MS * random.uniform(0.5, 1.5) + State.extra_latency_ms) / 1000)
    if random.random() < State.error_rate:
        raise HTTPException(status_code=503, detail="injected failure")
    return {"id": order_id, "status": "shipped", "version": VERSION}


class Chaos(BaseModel):
    error_rate: float = Field(0.0, ge=0.0, le=1.0)
    extra_latency_ms: float = Field(0.0, ge=0.0, le=10_000)


@app.post("/chaos")
def chaos(settings: Chaos):
    if not CHAOS_ENABLED:
        raise HTTPException(status_code=404)
    State.error_rate = settings.error_rate
    State.extra_latency_ms = settings.extra_latency_ms
    return settings
