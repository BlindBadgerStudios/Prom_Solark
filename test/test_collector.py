from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pysolark.models import (
    SolArkCurrency,
    SolArkDeviceCount,
    SolArkEnergyFlow,
    SolArkGenerationUse,
    SolArkPlant,
    SolArkPlantContacts,
    SolArkPlantMapPoint,
    SolArkRealtime,
    SolArkTimezone,
    SolArkToken,
)

from app.collector import SolArkCollector
from app.config import AppConfig
from app.metrics import build_metrics


class FakeClient:
    token: SolArkToken | None = None

    def login(self):
        return None

    def list_plants(self):
        return [SolArkPlantMapPoint(plant_id=123, longitude=-122.4, latitude=37.7, status=1)]

    def get_plant(self, plant_id):
        return SolArkPlant(
            plant_id=plant_id,
            name="Example Plant",
            total_power=21.9,
            thumb_url=None,
            join_date=datetime(2024, 8, 28, tzinfo=UTC),
            plant_type=2,
            status=1,
            longitude=-122.4,
            latitude=37.7,
            address="123 Example Ave",
            currency=SolArkCurrency(currency_id=251, code="USD", text="$"),
            timezone=SolArkTimezone(timezone_id=327, code="America/Los_Angeles", text="Pacific"),
            charges=[],
            realtime=None,
        )

    def get_plant_realtime(self, plant_id):
        return SolArkRealtime(
            pac=8701,
            etoday=13.7,
            emonth=75.1,
            eyear=4064.5,
            etotal=42781.9,
            income=2.1,
            efficiency=39.7,
            updated_at=datetime.now(tz=UTC) - timedelta(minutes=1),
            currency=SolArkCurrency(currency_id=251, code="USD", text="$"),
            total_power=21.9,
        )

    def get_plant_energy_flow(self, plant_id):
        return SolArkEnergyFlow(
            pv_power=9974,
            battery_power=64,
            grid_power=7905,
            load_power=1653,
            generator_power=0,
            microinverter_power=0,
            soc=100.0,
            to_load=True,
            to_grid=True,
            to_battery=True,
            from_battery=False,
            from_grid=False,
            generator_to=False,
            exists_generator=False,
            exists_microinverter=False,
            generator_on=False,
            microinverter_on=False,
            exists_meter=True,
            meter_code=0,
            bms_comm_fault=False,
            raw={},
        )

    def get_plant_generation_use(self, plant_id):
        return SolArkGenerationUse(load=5.9, pv=13.8, battery_charge=0.2, grid_sell=7.7, raw={})

    def get_plant_contacts(self, plant_id):
        return SolArkPlantContacts(plant_id=plant_id, name=None, updated_at=datetime.now(tz=UTC) - timedelta(minutes=1), raw={})

    def get_plants_map(self):
        return [SolArkPlantMapPoint(plant_id=123, longitude=-122.4, latitude=37.7, status=1, raw={})]

    def get_batteries_count(self):
        return SolArkDeviceCount(total=0, normal=0, warning=0, fault=0, offline=0, updated_at=datetime.now(tz=UTC), raw={})

    def get_inverters_count(self):
        return SolArkDeviceCount(total=0, normal=0, warning=0, fault=0, offline=0, updated_at=datetime.now(tz=UTC), raw={})



def test_collect_once_populates_metrics():
    metrics = build_metrics()
    config = AppConfig(username="user", password="secret", plant_id=123)
    collector = SolArkCollector(client=FakeClient(), config=config, metrics=metrics)

    collector.collect_once()

    assert metrics.registry.get_sample_value(
        "solark_plant_realtime_power_watts",
        labels={"plant_id": "123", "plant_name": "Example Plant"},
    ) == 8701.0
    assert metrics.registry.get_sample_value(
        "solark_battery_soc_percent",
        labels={"plant_id": "123", "plant_name": "Example Plant"},
    ) == 100.0
    assert metrics.registry.get_sample_value(
        "solark_flow_direction",
        labels={"plant_id": "123", "plant_name": "Example Plant", "direction": "to_grid"},
    ) == 1.0
    assert metrics.registry.get_sample_value(
        "solark_usage_pv_kw",
        labels={"plant_id": "123", "plant_name": "Example Plant"},
    ) == 13.8
    assert metrics.registry.get_sample_value(
        "solark_plants_map_status",
        labels={"plant_id": "123"},
    ) == 1.0
    assert metrics.registry.get_sample_value("solark_batteries_total") == 0.0
    assert metrics.registry.get_sample_value("solark_inverters_total") == 0.0
