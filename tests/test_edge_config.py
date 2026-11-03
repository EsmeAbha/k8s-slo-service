"""Edge properties a drill can't see, plus the drill's own arithmetic. No Docker needed."""
from pathlib import Path

import yaml

from edge.drill import Sample, summarize

ROOT = Path(__file__).resolve().parent.parent
CONFIG = yaml.safe_load((ROOT / "edge" / "envoy.yaml").read_text(encoding="utf-8"))
COMPOSE = yaml.safe_load((ROOT / "edge" / "docker-compose.yml").read_text(encoding="utf-8"))
LISTENER = CONFIG["static_resources"]["listeners"][0]["filter_chains"][0]
HCM = LISTENER["filters"][0]["typed_config"]
ROUTES = HCM["route_config"]["virtual_hosts"][0]["routes"]
CLUSTER = CONFIG["static_resources"]["clusters"][0]


def test_only_get_is_retried():
    retried = [r for r in ROUTES if "retry_policy" in r.get("route", {})]
    assert len(retried) == 1
    assert retried[0]["match"]["headers"] == [{"name": ":method", "string_match": {"exact": "GET"}}]


def test_retry_attempts_fit_inside_the_route_timeout():
    route = next(r["route"] for r in ROUTES if "retry_policy" in r.get("route", {}))
    seconds = lambda v: float(v.rstrip("s"))
    attempts = route["retry_policy"]["num_retries"] + 1
    assert seconds(route["retry_policy"]["per_try_timeout"]) * attempts <= seconds(route["timeout"])


def test_retries_are_budgeted():
    assert CLUSTER["circuit_breakers"]["thresholds"][0]["retry_budget"]["budget_percent"]["value"] <= 20


def test_default_route_exposes_nothing():
    assert ROUTES[-1] == {"match": {"prefix": "/"}, "direct_response": {"status": 404}}
    assert all(r["match"]["prefix"].startswith("/api/") for r in ROUTES[:-1])


def test_path_is_normalised_before_routing():
    assert HCM["normalize_path"] and HCM["merge_slashes"]


def test_tls_floor_is_1_2():
    tls = LISTENER["transport_socket"]["typed_config"]["common_tls_context"]
    assert tls["tls_params"]["tls_minimum_protocol_version"] == "TLSv1_2"


def test_ejection_can_never_remove_most_of_the_pool():
    assert CLUSTER["outlier_detection"]["max_ejection_percent"] <= 50


def test_admin_and_instances_are_published_on_loopback_only():
    services = COMPOSE["services"]
    published = [p for name in ("app1", "app2", "app3") for p in services[name]["ports"]]
    published += [p for p in services["envoy"]["ports"] if p.endswith(":9901")]
    assert len(published) == 4 and all(p.startswith("127.0.0.1:") for p in published)


def test_envoy_image_is_pinned():
    assert COMPOSE["services"]["envoy"]["image"].rsplit("-v", 1)[1].count(".") == 2


def test_summary_counts_a_dropped_connection_as_an_error():
    s = summarize([Sample(200, "app1", 0.01), Sample(0, "", 1.0), Sample(503, "", 0.2), Sample(200, "app2", 0.02)])
    assert s["errors"] == 2 and s["error_rate"] == 0.5
    assert s["by_instance"] == {"app1": 1, "app2": 1} and s["by_status"] == {200: 2, 0: 1, 503: 1}
    assert s["max_ms"] == 1000.0


def test_summary_of_no_traffic_is_a_failure_not_a_pass():
    assert summarize([])["error_rate"] == 1.0
