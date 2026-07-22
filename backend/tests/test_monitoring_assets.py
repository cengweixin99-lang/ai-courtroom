from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).parents[2]
MONITORING = ROOT / "monitoring"


def test_prometheus_assets_reference_only_existing_low_cardinality_metrics() -> None:
    """监控契约防止规则漂移到不存在的指标或将唯一标识写成标签。"""

    scrape_config = (MONITORING / "prometheus" / "prometheus.yml").read_text(encoding="utf-8")
    alert_rules = (MONITORING / "prometheus" / "rules" / "mootcourt-alerts.yml").read_text(
        encoding="utf-8"
    )

    assert "metrics_path: /metrics" in scrape_config
    assert 'targets: ["api:8000"]' in scrape_config
    assert alert_rules.count("alert: MootCourt") == 8
    for metric in (
        "mootcourt_http_requests_total",
        "mootcourt_agent_turns_total",
        "mootcourt_agent_turn_duration_seconds_bucket",
        "mootcourt_agent_provider_retries_total",
        "mootcourt_agent_provider_guard_rejections_total",
        "mootcourt_agent_output_normalizations_total",
        "mootcourt_agent_repairs_total",
    ):
        assert metric in alert_rules
    for forbidden_label in ("session_id", "request_id", "case_id", "trace_id", "evidence_id"):
        assert forbidden_label not in alert_rules


def test_grafana_dashboard_is_valid_and_uses_the_provisioned_datasource() -> None:
    dashboard_path = MONITORING / "grafana" / "dashboards" / "mootcourt-overview.json"
    dashboard = json.loads(dashboard_path.read_text(encoding="utf-8"))

    assert dashboard["uid"] == "mootcourt-api-overview"
    assert dashboard["editable"] is False
    assert len(dashboard["panels"]) >= 7
    for panel in dashboard["panels"]:
        assert panel["datasource"]["uid"] == "mootcourt-prometheus"
        assert panel["targets"]


def test_compose_keeps_monitoring_behind_an_explicit_profile() -> None:
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert 'prometheus:\n    profiles: ["monitoring"]' in compose
    assert 'grafana:\n    profiles: ["monitoring"]' in compose
    assert "prometheus-data:" in compose
    assert "grafana-data:" in compose
