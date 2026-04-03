# Prom_Solark

Prometheus exporter for near real-time Sol-Ark solar telemetry using the PySolark client.

The exporter is intentionally focused on Prometheus-friendly current state rather than historical reporting. It polls Sol-Ark through PySolark, converts the most recent available plant, flow, and device status data into scrapeable gauges, and exposes them over HTTP for Prometheus.

Current priorities:
- plant realtime power and freshness
- battery SOC and directional flow visibility
- PV, grid, load, and battery power flow
- current usage breakdown from generation/use
- quick counts/status for batteries and inverters
- plant metadata that helps dashboard joins

## Metrics focus

Exporter health:
- `solark_exporter_up`
- `solark_exporter_last_success_timestamp_seconds`
- `solark_exporter_poll_duration_seconds`
- `solark_exporter_errors_total`

Plant and realtime status:
- `solark_plant_info`
- `solark_plant_status`
- `solark_plant_total_power_kw`
- `solark_plant_realtime_power_watts`
- `solark_plant_energy_today_kwh`
- `solark_plant_energy_month_kwh`
- `solark_plant_energy_year_kwh`
- `solark_plant_energy_total_kwh`
- `solark_plant_income_today`
- `solark_plant_efficiency_percent`
- `solark_plant_realtime_timestamp_seconds`
- `solark_plant_up`

Realtime flow and usage:
- `solark_battery_soc_percent`
- `solark_pv_power_watts`
- `solark_battery_power_watts`
- `solark_grid_power_watts`
- `solark_load_power_watts`
- `solark_generator_power_watts`
- `solark_microinverter_power_watts`
- `solark_flow_direction{direction=...}`
- `solark_usage_load_kw`
- `solark_usage_pv_kw`
- `solark_usage_battery_charge_kw`
- `solark_usage_grid_sell_kw`

Auxiliary status:
- `solark_contacts_updated_timestamp_seconds`
- `solark_plants_map_status`
- `solark_plants_map_latitude`
- `solark_plants_map_longitude`
- `solark_batteries_total`
- `solark_batteries_normal`
- `solark_batteries_warning`
- `solark_batteries_fault`
- `solark_batteries_offline`
- `solark_batteries_updated_timestamp_seconds`
- `solark_inverters_total`
- `solark_inverters_normal`
- `solark_inverters_warning`
- `solark_inverters_fault`
- `solark_inverters_offline`
- `solark_inverters_updated_timestamp_seconds`

## Environment variables

Required:
- `SOLARK_EMAIL` or `SOLARK_USERNAME`
- `SOLARK_PASSWORD`

Optional:
- `SOLARK_PLANT_ID` - if omitted, the exporter selects the first accessible plant from `plants/map`
- `POLL_INTERVAL_SECONDS` - default `60`
- `LISTEN_PORT` - default `10112`
- `LOG_LEVEL` - default `INFO`
- `SOLARK_TIMEOUT_SECONDS` - default `30`
- `PLANT_STALE_AFTER_SECONDS` - default `900`

## Prometheus scrape config example

```yaml
- job_name: solark
  scrape_interval: 60s
  scrape_timeout: 15s
  static_configs:
    - targets:
        - 192.168.192.52:10112
      labels:
        site: home
        environment: prod
        role: energy
        service: solark
        source: cloud
```

## Docker Compose configuration notes

Use `docker-compose.yaml` when you want to self-build the exporter image from source.
Use `compose.yaml` when you want to deploy a pre-built image.

The container is configured with:
- read-only root filesystem
- tmpfs for `/tmp`
- dropped Linux capabilities
- `no-new-privileges`

## Development

```bash
uv venv .venv
. .venv/bin/activate
uv pip install -r requirements.txt
python -m pytest -q
python -m app.main
```
