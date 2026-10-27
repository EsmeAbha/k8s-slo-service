# k8s-slo-service

A small orders API run the way an SRE team runs production services. It has SLOs, multi-window burn-rate alerts with unit tests, a dashboard, a runbook, and zero-downtime rollouts on Kubernetes, all checked in CI.

## What's here

| Area | What it shows |
|---|---|
| `app/` | FastAPI service with **Prometheus metrics** (request count and latency histogram, labelled by route *template* to keep cardinality bounded), separate **liveness and readiness** probes, readiness that fails while warming up and during shutdown, and **fault injection** (`/chaos`, off by default) to test alerts |
| `chart/` | **Helm** chart: rolling updates with `maxUnavailable: 0`, `preStop` drain, non-root and read-only root filesystem with dropped capabilities, **HPA**, **PodDisruptionBudget**. Optional ServiceMonitor, PrometheusRule and Grafana dashboard for kube-prometheus-stack |
| `chart/files/slo-rules.yaml` | **SLO: 99.5% availability, p95 < 300 ms.** Recording rules, plus **multi-window burn-rate alerts** (fast burn 14.4× → page, slow burn 6× → ticket) following the Google SRE Workbook, and a "metrics absent" alert |
| `tests/slo-rules.test.yaml` | **promtool unit tests for the alerts:** a 10% error rate pages, 0.2% stays quiet, and no scrape target pages |
| `chart/files/dashboard.json` | Grafana: 30-day availability, **error budget remaining**, burn rate, latency percentiles |
| `docs/runbook.md` | What each alert means, what to check first, how to mitigate, plus a monthly **fire drill** |
| `docs/postmortem-template.md` | Blameless postmortem template |
| `loadtest/k6.js` | Load test whose pass/fail thresholds match the SLOs |
| CI | App tests → promtool rule tests → `helm lint` + **kubeconform** schema validation → **end-to-end on kind**: installs kube-prometheus-stack, deploys the chart, checks that Prometheus scrapes the service and loaded the rules, and checks that a rolling update drops **zero** requests |

The SLO rules and the dashboard each live in **one file**, used by both docker-compose and Helm, so local and cluster monitoring can't drift apart.

## Run locally (no Kubernetes)

```bash
docker compose -f compose/docker-compose.yml up --build
# app :8000 · Prometheus :9090 (Alerts tab) · Grafana :3000 → dashboard "slo-service: SLOs"
k6 run loadtest/k6.js                                         # traffic
curl -X POST localhost:8000/chaos -H 'content-type: application/json' -d '{"error_rate":0.2}'
# watch the error budget drain on the dashboard and SLOErrorBudgetFastBurn go pending → firing
```

## Run on Kubernetes (kind)

```bash
kind create cluster --name slo
docker build -t slo-service:0.1.0 . && kind load docker-image slo-service:0.1.0 --name slo
helm install kube-prometheus-stack prometheus-community/kube-prometheus-stack -n monitoring --create-namespace
helm install demo ./chart --set monitoring.serviceMonitor.enabled=true \
  --set monitoring.prometheusRule.enabled=true --set monitoring.grafanaDashboard.enabled=true
```

## Tests

```bash
pip install -r requirements.txt -r requirements-dev.txt && pytest -q
promtool test rules tests/slo-rules.test.yaml
```

## Design decisions

- **Alert on symptoms users feel (error budget burn), not causes (CPU).** CPU drives autoscaling; people get paged on SLO burn.
- **Two windows per alert.** The long window proves the problem is real. The short window makes the alert clear quickly once it's fixed.
- **Readiness ≠ liveness.** A pod that's warming up or draining is alive but shouldn't get traffic. Liveness only restarts a process that is truly stuck.
- **No CPU limit, only a request.** This avoids throttling-induced latency. The memory limit stays to contain leaks.
- **Route templates as labels.** `/api/orders/{order_id}` is one time series, not one per order, so Prometheus memory stays flat.
