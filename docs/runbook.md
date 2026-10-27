# slo-service runbook

**SLOs:**
- **Availability:** 99.5% of `/api` requests succeed over 30 days. The error budget is 0.5%, about 3.6 hours of full outage a month.
- **Latency:** p95 under 300 ms.

**Dashboard:** Grafana → "slo-service: SLOs". **Alert rules:** `chart/files/slo-rules.yaml`.

Useful variables:
```bash
NS=default; APP=slo-service   # adjust to your release
```

---

## SLOErrorBudgetFastBurn
**Severity:** page. **Meaning:** errors are using the 30-day budget about 14× too fast. At this rate the budget is gone in about 2 days. Both the 1h and 5m windows agree, so this is not a blip.

**Check first (5 minutes):**
1. Did something just change? `kubectl rollout history deploy -n $NS -l app.kubernetes.io/name=$APP` and recent CI deploys.
2. Which status codes and routes? Use the dashboard panel "Requests per second by status", or PromQL:
   `sum by (status, route) (rate(slo_service_http_requests_total{status=~"5.."}[5m]))`
3. Are pods healthy? `kubectl get pods -n $NS -l app.kubernetes.io/name=$APP` and look for restarts or `0/1 READY`.
4. Logs: `kubectl logs -n $NS -l app.kubernetes.io/name=$APP --tail=200 --prefix`

**Mitigate (restore service before finding the root cause):**
- A recent deploy is the cause → `kubectl rollout undo deploy/<name> -n $NS`.
- Fault injection was left on in a test cluster → `curl -X POST .../chaos -d '{"error_rate":0}'`.
- Pods are overloaded → check whether the HPA is at max (`kubectl get hpa -n $NS`) and raise `maxReplicas`.

**Afterwards:** write a postmortem (`docs/postmortem-template.md`) if more than 20% of the monthly budget was used.

## SLOErrorBudgetSlowBurn
**Severity:** ticket (working hours). **Meaning:** a steady error rate about 6× the budget over 6h. Usually a partial failure: one route, one dependency, or a bad pod.

**Check:** run the errors-by-route query above over `[6h]`. Then check per-pod error rates (`by (pod)` once scraped through the ServiceMonitor) to find a single bad replica. **Fix:** delete the bad pod, or fix the failing route. Track it in the ticket.

## SLOLatencyP95High
**Severity:** ticket. **Meaning:** p95 has been above 300 ms for 10 minutes.

**Check:** CPU against requests (`kubectl top pods -n $NS`) and HPA status. Look at the dashboard "Latency percentiles": does p50 also rise (everything is slow) or only p95/p99 (a slow tail)?

**Fix:** scale out if saturated. If only the tail is slow, look for one slow dependency or pod.

## SLOServiceMetricsAbsent
**Severity:** page. **Meaning:** Prometheus scrapes no instance at all. Either the service is down or monitoring is broken. Both need a human.

**Check:**
1. `kubectl get pods,endpoints -n $NS -l app.kubernetes.io/name=$APP`. No ready endpoints means the service is down. Treat it as a fast burn.
2. Pods are fine → check the Prometheus targets page (Status → Targets) for the ServiceMonitor. Is the `release` label right? Did a NetworkPolicy change?

---

## Fire drill (run monthly in a test cluster)
```bash
helm upgrade $APP ./chart --reuse-values --set env.CHAOS_ENABLED=true
kubectl port-forward svc/<release>-slo-service 8000:80 &
k6 run loadtest/k6.js &                                    # steady traffic
curl -X POST localhost:8000/chaos -H 'content-type: application/json' -d '{"error_rate":0.2}'
# expect SLOErrorBudgetFastBurn to fire within ~7 minutes; then follow this runbook
curl -X POST localhost:8000/chaos -H 'content-type: application/json' -d '{"error_rate":0}'
```
