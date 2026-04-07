from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from .config import AppConfig
from .metrics import Metrics, clear_labeled_metrics

logger = logging.getLogger(__name__)


FLOW_DIRECTIONS = {
    "to_load": "to_load",
    "to_grid": "to_grid",
    "to_battery": "to_battery",
    "from_battery": "from_battery",
    "from_grid": "from_grid",
    "generator_to": "generator_to",
}



def _ts(value: datetime | None) -> float | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.timestamp()



def _coerce_float(value: Any) -> float | None:
    if value in (None, "", "null"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class SolArkCollector:
    _TOKEN_EXPIRY_MARGIN = timedelta(seconds=300)

    def __init__(self, client: Any, config: AppConfig, metrics: Metrics) -> None:
        self.client = client
        self.config = config
        self.metrics = metrics
        self._plant_id = config.plant_id

    def login(self) -> None:
        self.client.login()

    def _token_near_expiry(self) -> bool:
        token = self.client.token
        if token is None:
            return True
        if token.expires_at is None:
            return False
        return datetime.now(timezone.utc) >= (token.expires_at - self._TOKEN_EXPIRY_MARGIN)

    def resolve_plant_id(self) -> int:
        if self._plant_id is not None:
            return self._plant_id
        plants = self.client.list_plants()
        if not plants:
            raise RuntimeError("No Sol-Ark plants available to the configured account")
        self._plant_id = int(plants[0].plant_id)
        return self._plant_id

    def collect_once(self) -> None:
        if self._token_near_expiry():
            logger.info("Token near expiry, re-authenticating proactively")
            self.login()
        plant_id = self.resolve_plant_id()
        clear_labeled_metrics(self.metrics)
        plant = self.client.get_plant(plant_id)
        realtime = self.client.get_plant_realtime(plant_id)
        flow = self.client.get_plant_energy_flow(plant_id)
        usage = self.client.get_plant_generation_use(plant_id)
        contacts = self.client.get_plant_contacts(plant_id)
        plants_map = self.client.get_plants_map()
        batteries = self.client.get_batteries_count()
        inverters = self.client.get_inverters_count()
        self._record_plant_metrics(plant, realtime)
        self._record_flow_metrics(plant, flow)
        self._record_usage_metrics(plant, usage)
        self._record_contacts_metrics(plant, contacts)
        self._record_map_metrics(plants_map)
        self._record_count_metrics(batteries, inverters)

    def _plant_labels(self, plant: Any) -> dict[str, str]:
        return {
            "plant_id": str(plant.plant_id),
            "plant_name": plant.name or f"plant_{plant.plant_id}",
        }

    def _record_plant_metrics(self, plant: Any, realtime: Any) -> None:
        labels = self._plant_labels(plant)
        self.metrics.plant_info.labels(
            **labels,
            timezone=(plant.timezone.code if plant.timezone else ""),
            status_code=str(plant.status if plant.status is not None else ""),
        ).set(1)
        if plant.status is not None:
            self.metrics.plant_status.labels(**labels).set(float(plant.status))
        if plant.total_power is not None:
            self.metrics.plant_total_power_kw.labels(**labels).set(float(plant.total_power))
        for gauge, value in (
            (self.metrics.plant_realtime_power_watts, realtime.pac),
            (self.metrics.plant_energy_today_kwh, realtime.etoday),
            (self.metrics.plant_energy_month_kwh, realtime.emonth),
            (self.metrics.plant_energy_year_kwh, realtime.eyear),
            (self.metrics.plant_energy_total_kwh, realtime.etotal),
            (self.metrics.plant_income_today, realtime.income),
            (self.metrics.plant_efficiency_percent, realtime.efficiency),
        ):
            numeric = _coerce_float(value)
            if numeric is not None:
                gauge.labels(**labels).set(numeric)
        realtime_ts = _ts(realtime.updated_at)
        if realtime_ts is not None:
            self.metrics.plant_realtime_timestamp_seconds.labels(**labels).set(realtime_ts)
            is_up = 1 if datetime.now(tz=UTC).timestamp() - realtime_ts <= self.config.plant_stale_after_seconds else 0
            self.metrics.plant_up.labels(**labels).set(is_up)

    def _record_flow_metrics(self, plant: Any, flow: Any) -> None:
        labels = self._plant_labels(plant)
        for gauge, value in (
            (self.metrics.battery_soc_percent, flow.soc),
            (self.metrics.pv_power_watts, flow.pv_power),
            (self.metrics.battery_power_watts, flow.battery_power),
            (self.metrics.grid_power_watts, flow.grid_power),
            (self.metrics.load_power_watts, flow.load_power),
            (self.metrics.generator_power_watts, flow.generator_power),
            (self.metrics.microinverter_power_watts, flow.microinverter_power),
        ):
            numeric = _coerce_float(value)
            if numeric is not None:
                gauge.labels(**labels).set(numeric)
        for attr, metric_label in FLOW_DIRECTIONS.items():
            value = getattr(flow, attr)
            if value is not None:
                self.metrics.flow_direction.labels(**labels, direction=metric_label).set(1 if value else 0)
        for gauge, attr in (
            (self.metrics.generator_present, "exists_generator"),
            (self.metrics.generator_on, "generator_on"),
            (self.metrics.microinverter_present, "exists_microinverter"),
            (self.metrics.microinverter_on, "microinverter_on"),
            (self.metrics.meter_present, "exists_meter"),
            (self.metrics.bms_comm_fault, "bms_comm_fault"),
        ):
            value = getattr(flow, attr)
            if value is not None:
                gauge.labels(**labels).set(1 if value else 0)

    def _record_usage_metrics(self, plant: Any, usage: Any) -> None:
        labels = self._plant_labels(plant)
        for gauge, value in (
            (self.metrics.usage_load_kw, usage.load),
            (self.metrics.usage_pv_kw, usage.pv),
            (self.metrics.usage_battery_charge_kw, usage.battery_charge),
            (self.metrics.usage_grid_sell_kw, usage.grid_sell),
        ):
            numeric = _coerce_float(value)
            if numeric is not None:
                gauge.labels(**labels).set(numeric)

    def _record_contacts_metrics(self, plant: Any, contacts: Any) -> None:
        labels = self._plant_labels(plant)
        updated_ts = _ts(contacts.updated_at)
        if updated_ts is not None:
            self.metrics.contacts_updated_timestamp_seconds.labels(**labels).set(updated_ts)

    def _record_map_metrics(self, plants_map: list[Any]) -> None:
        for point in plants_map:
            labels = {"plant_id": str(point.plant_id)}
            if point.status is not None:
                self.metrics.plants_map_status.labels(**labels).set(float(point.status))
            if point.latitude is not None:
                self.metrics.plants_map_latitude.labels(**labels).set(float(point.latitude))
            if point.longitude is not None:
                self.metrics.plants_map_longitude.labels(**labels).set(float(point.longitude))

    def _record_count_metrics(self, batteries: Any, inverters: Any) -> None:
        for prefix, counts in (("batteries", batteries), ("inverters", inverters)):
            for suffix in ("total", "normal", "warning", "fault", "offline"):
                value = getattr(counts, suffix)
                if value is not None:
                    getattr(self.metrics, f"{prefix}_{suffix}").set(float(value))
            updated_ts = _ts(counts.updated_at)
            if updated_ts is not None:
                getattr(self.metrics, f"{prefix}_updated_timestamp_seconds").set(updated_ts)
