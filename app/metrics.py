from __future__ import annotations

from dataclasses import dataclass
from prometheus_client import CollectorRegistry, Counter, Gauge


@dataclass(slots=True)
class Metrics:
    registry: CollectorRegistry
    exporter_up: Gauge
    exporter_last_success_timestamp: Gauge
    exporter_poll_duration: Gauge
    exporter_errors_total: Counter
    plant_info: Gauge
    plant_status: Gauge
    plant_total_power_kw: Gauge
    plant_realtime_power_watts: Gauge
    plant_energy_today_kwh: Gauge
    plant_energy_month_kwh: Gauge
    plant_energy_year_kwh: Gauge
    plant_energy_total_kwh: Gauge
    plant_income_today: Gauge
    plant_efficiency_percent: Gauge
    plant_realtime_timestamp_seconds: Gauge
    plant_up: Gauge
    battery_soc_percent: Gauge
    pv_power_watts: Gauge
    battery_power_watts: Gauge
    grid_power_watts: Gauge
    load_power_watts: Gauge
    generator_power_watts: Gauge
    generator_present: Gauge
    generator_on: Gauge
    microinverter_power_watts: Gauge
    microinverter_present: Gauge
    microinverter_on: Gauge
    meter_present: Gauge
    bms_comm_fault: Gauge
    flow_direction: Gauge
    usage_load_kw: Gauge
    usage_pv_kw: Gauge
    usage_battery_charge_kw: Gauge
    usage_grid_sell_kw: Gauge
    contacts_updated_timestamp_seconds: Gauge
    plants_map_status: Gauge
    plants_map_latitude: Gauge
    plants_map_longitude: Gauge
    batteries_total: Gauge
    batteries_normal: Gauge
    batteries_warning: Gauge
    batteries_fault: Gauge
    batteries_offline: Gauge
    batteries_updated_timestamp_seconds: Gauge
    inverters_total: Gauge
    inverters_normal: Gauge
    inverters_warning: Gauge
    inverters_fault: Gauge
    inverters_offline: Gauge
    inverters_updated_timestamp_seconds: Gauge


def build_metrics(registry: CollectorRegistry | None = None) -> Metrics:
    registry = registry or CollectorRegistry()
    plant_labels = ["plant_id", "plant_name"]
    map_labels = ["plant_id"]
    return Metrics(
        registry=registry,
        exporter_up=Gauge("solark_exporter_up", "1 if the last poll succeeded", registry=registry),
        exporter_last_success_timestamp=Gauge("solark_exporter_last_success_timestamp_seconds", "Unix timestamp of the last successful poll", registry=registry),
        exporter_poll_duration=Gauge("solark_exporter_poll_duration_seconds", "Duration of the last poll in seconds", registry=registry),
        exporter_errors_total=Counter("solark_exporter_errors_total", "Total exporter polling errors", registry=registry),
        plant_info=Gauge("solark_plant_info", "Static plant info metric (value always 1)", plant_labels + ["timezone", "status_code"], registry=registry),
        plant_status=Gauge("solark_plant_status", "Numeric plant status from Sol-Ark", plant_labels, registry=registry),
        plant_total_power_kw=Gauge("solark_plant_total_power_kw", "Installed total plant power from Sol-Ark metadata", plant_labels, registry=registry),
        plant_realtime_power_watts=Gauge("solark_plant_realtime_power_watts", "Current realtime plant power", plant_labels, registry=registry),
        plant_energy_today_kwh=Gauge("solark_plant_energy_today_kwh", "Today's energy from realtime payload", plant_labels, registry=registry),
        plant_energy_month_kwh=Gauge("solark_plant_energy_month_kwh", "Current month energy from realtime payload", plant_labels, registry=registry),
        plant_energy_year_kwh=Gauge("solark_plant_energy_year_kwh", "Current year energy from realtime payload", plant_labels, registry=registry),
        plant_energy_total_kwh=Gauge("solark_plant_energy_total_kwh", "Lifetime energy from realtime payload", plant_labels, registry=registry),
        plant_income_today=Gauge("solark_plant_income_today", "Today's income from realtime payload", plant_labels, registry=registry),
        plant_efficiency_percent=Gauge("solark_plant_efficiency_percent", "Realtime efficiency percent", plant_labels, registry=registry),
        plant_realtime_timestamp_seconds=Gauge("solark_plant_realtime_timestamp_seconds", "Timestamp of the latest realtime plant sample", plant_labels, registry=registry),
        plant_up=Gauge("solark_plant_up", "1 if plant realtime data is fresh", plant_labels, registry=registry),
        battery_soc_percent=Gauge("solark_battery_soc_percent", "Battery state of charge from flow data", plant_labels, registry=registry),
        pv_power_watts=Gauge("solark_pv_power_watts", "Current PV power from flow data", plant_labels, registry=registry),
        battery_power_watts=Gauge("solark_battery_power_watts", "Current battery power from flow data", plant_labels, registry=registry),
        grid_power_watts=Gauge("solark_grid_power_watts", "Current grid power from flow data", plant_labels, registry=registry),
        load_power_watts=Gauge("solark_load_power_watts", "Current load power from flow data", plant_labels, registry=registry),
        generator_power_watts=Gauge("solark_generator_power_watts", "Current generator power from flow data", plant_labels, registry=registry),
        generator_present=Gauge("solark_generator_present", "1 if a generator is configured for this plant", plant_labels, registry=registry),
        generator_on=Gauge("solark_generator_on", "1 if the generator is currently active", plant_labels, registry=registry),
        microinverter_power_watts=Gauge("solark_microinverter_power_watts", "Current microinverter power from flow data", plant_labels, registry=registry),
        microinverter_present=Gauge("solark_microinverter_present", "1 if a microinverter is configured for this plant", plant_labels, registry=registry),
        microinverter_on=Gauge("solark_microinverter_on", "1 if the microinverter is currently active", plant_labels, registry=registry),
        meter_present=Gauge("solark_meter_present", "1 if a meter is configured for this plant", plant_labels, registry=registry),
        bms_comm_fault=Gauge("solark_bms_comm_fault", "1 if a BMS communication fault is active", plant_labels, registry=registry),
        flow_direction=Gauge("solark_flow_direction", "Boolean-ish power-flow flags from the flow payload", plant_labels + ["direction"], registry=registry),
        usage_load_kw=Gauge("solark_usage_load_kw", "Current load usage from generation/use payload", plant_labels, registry=registry),
        usage_pv_kw=Gauge("solark_usage_pv_kw", "Current PV usage from generation/use payload", plant_labels, registry=registry),
        usage_battery_charge_kw=Gauge("solark_usage_battery_charge_kw", "Current battery charge usage from generation/use payload", plant_labels, registry=registry),
        usage_grid_sell_kw=Gauge("solark_usage_grid_sell_kw", "Current grid sell usage from generation/use payload", plant_labels, registry=registry),
        contacts_updated_timestamp_seconds=Gauge("solark_contacts_updated_timestamp_seconds", "Timestamp of the plant contacts payload", plant_labels, registry=registry),
        plants_map_status=Gauge("solark_plants_map_status", "Plant status from the plants/map endpoint", map_labels, registry=registry),
        plants_map_latitude=Gauge("solark_plants_map_latitude", "Plant latitude from the plants/map endpoint", map_labels, registry=registry),
        plants_map_longitude=Gauge("solark_plants_map_longitude", "Plant longitude from the plants/map endpoint", map_labels, registry=registry),
        batteries_total=Gauge("solark_batteries_total", "Total batteries count", registry=registry),
        batteries_normal=Gauge("solark_batteries_normal", "Normal batteries count", registry=registry),
        batteries_warning=Gauge("solark_batteries_warning", "Warning batteries count", registry=registry),
        batteries_fault=Gauge("solark_batteries_fault", "Fault batteries count", registry=registry),
        batteries_offline=Gauge("solark_batteries_offline", "Offline batteries count", registry=registry),
        batteries_updated_timestamp_seconds=Gauge("solark_batteries_updated_timestamp_seconds", "Timestamp of the batteries count payload", registry=registry),
        inverters_total=Gauge("solark_inverters_total", "Total inverters count", registry=registry),
        inverters_normal=Gauge("solark_inverters_normal", "Normal inverters count", registry=registry),
        inverters_warning=Gauge("solark_inverters_warning", "Warning inverters count", registry=registry),
        inverters_fault=Gauge("solark_inverters_fault", "Fault inverters count", registry=registry),
        inverters_offline=Gauge("solark_inverters_offline", "Offline inverters count", registry=registry),
        inverters_updated_timestamp_seconds=Gauge("solark_inverters_updated_timestamp_seconds", "Timestamp of the inverters count payload", registry=registry),
    )


def clear_labeled_metrics(metrics: Metrics) -> None:
    for gauge in (
        metrics.plant_info,
        metrics.plant_status,
        metrics.plant_total_power_kw,
        metrics.plant_realtime_power_watts,
        metrics.plant_energy_today_kwh,
        metrics.plant_energy_month_kwh,
        metrics.plant_energy_year_kwh,
        metrics.plant_energy_total_kwh,
        metrics.plant_income_today,
        metrics.plant_efficiency_percent,
        metrics.plant_realtime_timestamp_seconds,
        metrics.plant_up,
        metrics.battery_soc_percent,
        metrics.pv_power_watts,
        metrics.battery_power_watts,
        metrics.grid_power_watts,
        metrics.load_power_watts,
        metrics.generator_power_watts,
        metrics.generator_present,
        metrics.generator_on,
        metrics.microinverter_power_watts,
        metrics.microinverter_present,
        metrics.microinverter_on,
        metrics.meter_present,
        metrics.bms_comm_fault,
        metrics.flow_direction,
        metrics.usage_load_kw,
        metrics.usage_pv_kw,
        metrics.usage_battery_charge_kw,
        metrics.usage_grid_sell_kw,
        metrics.contacts_updated_timestamp_seconds,
        metrics.plants_map_status,
        metrics.plants_map_latitude,
        metrics.plants_map_longitude,
    ):
        gauge.clear()
