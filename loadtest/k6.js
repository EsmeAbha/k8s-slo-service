// Steady traffic for the dashboard and alerts:  k6 run loadtest/k6.js  (BASE_URL defaults to localhost:8000)
import http from "k6/http";
import { check, sleep } from "k6";

export const options = {
  vus: 20,
  duration: "10m",
  thresholds: {
    http_req_failed: ["rate<0.005"],      // the 99.5% availability SLO
    http_req_duration: ["p(95)<300"],     // the 300ms p95 latency objective
  },
};

const BASE = __ENV.BASE_URL || "http://localhost:8000";

export default function () {
  const res = http.get(`${BASE}/api/orders/${Math.floor(Math.random() * 10000)}`);
  check(res, { "status 200": (r) => r.status === 200 });
  sleep(0.1);
}
