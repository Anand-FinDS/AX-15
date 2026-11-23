"""Pydantic models for reference data (stores, CMDB assets, SLA matrix, version matrix)."""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _Row(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)

    @field_validator("*", mode="before")
    @classmethod
    def _empty_to_none(cls, v):
        return None if isinstance(v, str) and v.strip() == "" else v


class Store(_Row):
    store_id: str = Field(pattern=r"^SW-\d{4}$")
    store_name: str
    city: str | None = None
    neighborhood: str | None = None
    store_type: Literal["corporate", "franchise"]
    region_manager: str | None = None
    support_phone: str | None = None


class Asset(_Row):
    asset_id: str = Field(pattern=r"^[A-Z]{3}-\d{4}-[A-Z0-9]+$")
    store_id: str = Field(pattern=r"^SW-\d{4}$")
    asset_type: str
    make: str | None = None
    model: str | None = None
    system_version: str | None = None
    serial_number: str | None = None
    install_date: date | None = None
    last_service_date: date | None = None
    warranty_expiry: date | None = None

    @property
    def prefix(self) -> str:
        return self.asset_id.split("-", 1)[0]


class SlaRow(_Row):
    priority: Literal["P1", "P2", "P3", "P4"]
    category: str
    first_response_minutes: int = Field(gt=0)
    resolution_target_minutes: int = Field(gt=0)
    escalation_to_l2_minutes: int = Field(gt=0)


class VersionRow(_Row):
    system: str
    version: str
    release_date: date | None = None
    known_bugs: str | None = None
    patch_available: str | None = None
    notes: str | None = None
