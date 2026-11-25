"""Database tables.

Fleet tables are declared once in FLEET_SCHEMA so the table definitions, the
JSON field names the frontend uses, and input validation all come from one
place. JSON keys stay camelCase (hotelId); database columns are snake_case
(hotel_id). Dates are ISO strings (YYYY-MM-DD, or YYYY-MM-DDTHH:MM for times)
and gpsAt is a Unix timestamp in milliseconds.
"""
import re
from dataclasses import dataclass

from sqlalchemy import BigInteger, Boolean, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


@dataclass(frozen=True)
class F:
    """A fleet field: kind is str, text, float, int, bigint, date or ts."""
    kind: str
    size: int = 120
    index: bool = False


S, TXT, FLT, INT, BIG = F("str"), F("text"), F("float"), F("int"), F("bigint")
DATE, TS = F("date", 10, True), F("ts", 16, True)
REF = F("str", 40, True)  # id of another record

FLEET_SCHEMA: dict[str, dict[str, F]] = {
    "hotels": dict(name=S, area=S, lat=FLT, lng=FLT, manager=S, phone=F("str", 40), status=F("str", 20)),
    "vehicles": dict(reg=F("str", 20, True), make=S, model=S, type=F("str", 30), year=INT,
                     hotelId=REF, driverId=REF, status=F("str", 20, True), odometer=INT,
                     nextServiceKm=INT, fuelType=F("str", 20), insuranceExpiry=DATE, createdAt=DATE,
                     lat=FLT, lng=FLT, gpsAt=BIG, speed=INT, gpsSrc=F("str", 40)),
    "drivers": dict(name=S, phone=F("str", 40), licence=F("str", 40), licenceExpiry=DATE,
                    hotelId=REF, status=F("str", 20), joined=DATE),
    "trips": dict(vehicleId=REF, driverId=REF, **{"from": S, "to": S}, purpose=F("str", 200),
                  start=TS, end=TS, status=F("str", 20, True), km=FLT),
    "maintenance": dict(vehicleId=REF, title=F("str", 200), kind=F("str", 40), priority=F("str", 20),
                        status=F("str", 20, True), dueKm=INT, dueDate=DATE, garage=S, cost=FLT,
                        date=DATE, notes=TXT),
    "fuel": dict(date=DATE, vehicleId=REF, litres=FLT, price=FLT, cost=FLT, station=S, odometer=INT),
    "wash": dict(date=DATE, vehicleId=REF, type=F("str", 40), location=S, cost=FLT, notes=TXT),
    "inspections": dict(vehicleId=REF, date=DATE, centre=S, result=F("str", 20), certNo=F("str", 60),
                        nextDate=DATE, notes=TXT),
    "procurement": dict(date=DATE, ref=F("str", 60), item=F("str", 200), category=F("str", 40),
                        supplier=S, vehicleId=REF, qty=INT, unitCost=FLT, status=F("str", 20, True)),
    "expenses": dict(date=DATE, category=F("str", 60), description=F("str", 300), vehicleId=REF,
                     amount=FLT, ref=F("str", 60)),
    "activities": dict(ts=TS, kind=F("str", 20), title=F("str", 200), sub=F("str", 200), badge=F("str", 20)),
}

# SQL-reserved or ambiguous names get clearer column names.
COLUMN_NAMES = {"from": "origin", "to": "destination", "start": "start_time", "end": "end_time"}


def snake(name: str) -> str:
    return COLUMN_NAMES.get(name) or re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _sql_type(f: F):
    return {"float": Float, "int": Integer, "bigint": BigInteger, "text": Text}.get(f.kind) or String(f.size)


def _build(table: str, fields: dict[str, F]):
    attrs = {
        "__tablename__": table,
        "id": mapped_column(String(40), primary_key=True),
        "updated_at": mapped_column(String(32), nullable=True),
        "updated_by": mapped_column(String(80), nullable=True),
    }
    for key, f in fields.items():
        attrs[key] = mapped_column(snake(key), _sql_type(f), nullable=True, index=f.index)
    cls_name = "".join(p.capitalize() for p in table.split("_")) + "Record"
    return type(cls_name, (Base,), attrs)


FLEET_MODELS = {table: _build(table, fields) for table, fields in FLEET_SCHEMA.items()}


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    username: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    role: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="Active")
    password_hash: Mapped[str] = mapped_column(String(200))
    must_change: Mapped[bool] = mapped_column(Boolean, default=True)
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    created: Mapped[str | None] = mapped_column(String(10), nullable=True)
    last_login: Mapped[str | None] = mapped_column(String(16), nullable=True)

    def public(self) -> dict:
        """Fields safe to send to the browser (never the password hash)."""
        return {"id": self.id, "name": self.name, "username": self.username, "email": self.email or "",
                "phone": self.phone or "", "role": self.role, "status": self.status,
                "mustChange": self.must_change, "created": self.created or "", "lastLogin": self.last_login or ""}


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(Text)  # JSON


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[str] = mapped_column(String(16), index=True)
    user: Mapped[str] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(20))
    module: Mapped[str] = mapped_column(String(60))
    ref: Mapped[str] = mapped_column(String(200))
    details: Mapped[str] = mapped_column(Text, default="")

    def public(self) -> dict:
        return {"id": f"a{self.id}", "ts": self.ts, "user": self.user, "action": self.action,
                "module": self.module, "ref": self.ref, "details": self.details}


class GpsFix(Base):
    """Every position received, kept as history for routes and audits."""
    __tablename__ = "gps_fixes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vehicle_id: Mapped[str] = mapped_column(String(40), index=True)
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    ts: Mapped[int] = mapped_column(BigInteger, index=True)  # ms since epoch
    speed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    received_at: Mapped[str] = mapped_column(String(32))


Index("ix_gps_vehicle_ts", GpsFix.vehicle_id, GpsFix.ts)


class Meta(Base):
    """Single-row counters; rev increases on every write so browsers can poll cheaply."""
    __tablename__ = "meta"
    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[int] = mapped_column(BigInteger, default=0)
