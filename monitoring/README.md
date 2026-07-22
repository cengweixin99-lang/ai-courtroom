# MootCourt Lab monitoring

`prometheus/` holds the scrape contract and alert rules. `grafana/` provisions a single
read-only overview dashboard, so a clean Grafana instance has the operational panels without a
manual import.

Start the isolated local monitoring profile only after the API is healthy:

```powershell
docker compose --profile monitoring up -d prometheus grafana
```

The default profile stays unchanged. Set `PROMETHEUS_IMAGE`, `GRAFANA_IMAGE`, and
`MONITORING_PULL_POLICY=never` in `.env` when local images must be reused. The profile exposes
Prometheus on port `9090` and Grafana on port `3000` unless their corresponding environment
variables override the ports.

The supplied scraper targets the Compose-internal `api:8000` service and assumes that `/metrics`
is reachable only inside a trusted development network. Production requires a diagnostics key. Do
not commit that key: deploy a monitoring-network proxy that adds `X-Diagnostics-Key`, or replace
the scrape config with your platform's secret-backed header injection. Alert rules and dashboards
must never include the key or any case-related identifier.

Before enabling Grafana outside a disposable local environment, replace
`GRAFANA_ADMIN_PASSWORD=change-me-now` with a secret supplied by the deployment platform.
