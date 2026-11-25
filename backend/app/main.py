"""Mobisirs Fleet Operations API.

Run with:  uvicorn app.main:app --reload   (from the backend folder)
Interactive API docs are at /docs.
"""
import json
import os
import secrets
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import config
from .database import Base, SessionLocal, engine, get_db
from .models import FLEET_MODELS, FLEET_SCHEMA, AlertRead, AuditLog, GpsFix, Meta, Setting, User
from .security import (GPS_FIELDS, ROLES, VEHICLE_OPERATIONAL_FIELDS, can_delete, can_edit,
                       can_update_gps, can_view_audit, hash_password, make_token, password_problem,
                       read_token, verify_password)

TZ = ZoneInfo(os.getenv("APP_TIMEZONE", "Africa/Nairobi"))
DEFAULT_SETTINGS = {"company": "Mobisirs Bespoke Solutions", "fuelPrice": 172, "serviceInterval": 5000,
                    "budget": 3500000, "warnDays": 30}
MODULE_LABELS = {"hotels": "Hotels", "vehicles": "Vehicles", "drivers": "Drivers", "trips": "Trips & dispatch",
                 "maintenance": "Maintenance jobs", "fuel": "Fuel log", "wash": "Cleaning log",
                 "inspections": "Inspection records", "procurement": "Purchase requests",
                 "expenses": "Other expenses", "activities": "Activity feed"}
BULK_AUDIT_THRESHOLD = 50
MAX_SYNC_RECORDS = 20000

app = FastAPI(title="Mobisirs Fleet Operations API", version="1.0.0")
if config.CORS_ORIGINS:
    app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_credentials=False,
                       allow_methods=["*"], allow_headers=["Authorization", "Content-Type", "X-Tracker-Key"])


# ---------------------------------------------------------------- helpers
def now_local() -> datetime:
    return datetime.now(TZ)


def stamp(dt: datetime | None = None) -> str:
    return (dt or now_local()).strftime("%Y-%m-%dT%H:%M")


def bump_rev(db: Session) -> int:
    row = db.get(Meta, "rev")
    if not row:
        row = Meta(key="rev", value=0)
        db.add(row)
    row.value += 1
    return row.value


def current_rev(db: Session) -> int:
    row = db.get(Meta, "rev")
    return row.value if row else 0


def audit(db: Session, user: str, action: str, module: str, ref: str, details: str = "") -> None:
    db.add(AuditLog(ts=stamp(), user=user, action=action, module=module, ref=(ref or "—")[:200],
                    details=details[:4000]))


def get_settings(db: Session) -> dict:
    row = db.get(Setting, "app")
    data = dict(DEFAULT_SETTINGS)
    if row:
        data.update(json.loads(row.value))
    return data


def coerce(table: str, key: str, value: Any):
    """Convert one incoming JSON value to its column type; '' and null become NULL."""
    f = FLEET_SCHEMA[table][key]
    if value is None or value == "":
        return None
    try:
        if f.kind == "float":
            return float(value)
        if f.kind in ("int", "bigint"):
            return int(round(float(value)))
    except (TypeError, ValueError):
        raise HTTPException(422, f"{MODULE_LABELS[table]}: '{key}' must be a number.")
    text = str(value)
    if f.kind != "text" and len(text) > f.size:
        raise HTTPException(422, f"{MODULE_LABELS[table]}: '{key}' is too long (max {f.size}).")
    return text


def row_to_dict(table: str, row) -> dict:
    out = {"id": row.id}
    for key in FLEET_SCHEMA[table]:
        val = getattr(row, key)
        out[key] = "" if val is None else val
    return out


def record_ref(table: str, data: dict, regs: dict[str, str]) -> str:
    reg = regs.get(data.get("vehicleId") or "", "")
    return {
        "hotels": data.get("name"), "vehicles": data.get("reg"), "drivers": data.get("name"),
        "trips": f"{reg} {data.get('from', '')} → {data.get('to', '')}",
        "maintenance": f"{reg} {data.get('title', '')}", "fuel": f"{reg} {fmt(data.get('litres'))} L",
        "wash": f"{reg} {data.get('type', '')}", "inspections": f"{reg} {data.get('result', '')}",
        "procurement": f"{data.get('ref', '')} {data.get('item', '')}",
        "expenses": f"{data.get('category', '')} KSh {fmt(data.get('amount'))}",
        "activities": data.get("title"),
    }.get(table) or data.get("id", "—")


def fmt(v) -> str:
    if v is None or v == "":
        return "—"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


# ---------------------------------------------------------------- startup
def init_db() -> None:
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        if not db.scalar(select(func.count()).select_from(User)):
            db.add(User(id="u1", name=config.ADMIN_NAME, username=config.ADMIN_USERNAME.lower(), role="Admin",
                        status="Active", password_hash=hash_password(config.ADMIN_PASSWORD), must_change=True,
                        created=now_local().strftime("%Y-%m-%d")))
            audit(db, "System", "System", "Users", config.ADMIN_USERNAME, "Initial administrator account created")
        if not db.get(Setting, "app"):
            db.add(Setting(key="app", value=json.dumps(DEFAULT_SETTINGS)))
        if not db.get(Meta, "rev"):
            db.add(Meta(key="rev", value=1))
        db.commit()


init_db()


# ---------------------------------------------------------------- auth
DbDep = Annotated[Session, Depends(get_db)]
_failed: dict[str, list[float]] = defaultdict(list)  # sign-in throttling, per username+IP


def _user_from_header(authorization: str | None, db: Session) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Sign in required.")
    claims = read_token(authorization.split(" ", 1)[1])
    user = db.get(User, claims["sub"]) if claims else None
    if not user or user.status != "Active" or claims.get("ver") != user.token_version:
        raise HTTPException(401, "Your session has ended. Please sign in again.")
    return user


def any_user(db: DbDep, authorization: Annotated[str | None, Header()] = None) -> User:
    """Signed-in user, even one who still has a temporary password."""
    return _user_from_header(authorization, db)


def active_user(user: Annotated[User, Depends(any_user)]) -> User:
    """Signed-in user who has already replaced their temporary password."""
    if user.must_change:
        raise HTTPException(403, "password_change_required")
    return user


UserDep = Annotated[User, Depends(active_user)]


def require_admin(user: UserDep) -> User:
    if user.role != "Admin":
        raise HTTPException(403, "Only administrators can do that.")
    return user


class LoginIn(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=128)


class ChangePasswordIn(BaseModel):
    current_password: str = Field(max_length=128)
    new_password: str = Field(max_length=128)


@app.get("/api/health")
def health():
    return {"app": "mbs-fleet", "status": "ok"}


@app.post("/api/auth/login")
def login(body: LoginIn, request: Request, db: DbDep):
    key = f"{body.username.strip().lower()}|{request.client.host if request.client else ''}"
    recent = [t for t in _failed[key] if time.time() - t < 60]
    _failed[key] = recent
    if len(recent) >= 5:
        raise HTTPException(429, "Too many attempts. Wait a minute and try again.")
    user = db.scalar(select(User).where(User.username == body.username.strip().lower()))
    if not user or not verify_password(body.password, user.password_hash):
        _failed[key].append(time.time())
        raise HTTPException(401, "Username or password is incorrect.")
    if user.status != "Active":
        raise HTTPException(403, "This account is disabled. Contact an administrator.")
    _failed.pop(key, None)
    user.last_login = stamp()
    audit(db, user.name, "System", "Session", user.username, "Signed in")
    bump_rev(db)
    db.commit()
    return {"token": make_token(user.id, user.token_version), "user": user.public()}


@app.post("/api/auth/change-password")
def change_password(body: ChangePasswordIn, db: DbDep, user: Annotated[User, Depends(any_user)]):
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(400, "Current password is incorrect.")
    if problem := password_problem(body.new_password):
        raise HTTPException(400, problem)
    if verify_password(body.new_password, user.password_hash):
        raise HTTPException(400, "Choose a password different from the current one.")
    first = user.must_change
    user.password_hash = hash_password(body.new_password)
    user.must_change = False
    user.token_version += 1  # sign out other sessions
    audit(db, user.name, "Updated", "Users", user.username,
          "Password set at first sign-in" if first else "Password changed")
    bump_rev(db)
    db.commit()
    return {"token": make_token(user.id, user.token_version), "user": user.public()}


@app.post("/api/auth/logout")
def logout(db: DbDep, user: Annotated[User, Depends(any_user)]):
    audit(db, user.name, "System", "Session", user.username, "Signed out")
    db.commit()
    return {"ok": True}


@app.get("/api/auth/me")
def me(user: Annotated[User, Depends(any_user)]):
    return user.public()


# ---------------------------------------------------------------- fleet data
def recent_audit(db: Session, limit: int = 1500) -> list[dict]:
    rows = db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(limit))
    return [r.public() for r in rows]


@app.get("/api/bootstrap")
def bootstrap(db: DbDep, user: UserDep):
    """Everything the app needs after sign-in, in one request."""
    data = {t: [row_to_dict(t, r) for r in db.scalars(select(m))] for t, m in FLEET_MODELS.items()}
    users = [u.public() for u in db.scalars(select(User))] if user.role == "Admin" else [user.public()]
    return {"me": user.public(), "data": data, "users": users, "settings": get_settings(db),
            "audit": recent_audit(db) if can_view_audit(user.role) else [], "rev": current_rev(db),
            "alertReads": read_alert_keys(db, user.id)}


@app.get("/api/rev")
def rev(db: DbDep, _user: UserDep):
    return {"rev": current_rev(db)}


@app.get("/api/data/{table}")
def list_records(table: str, db: DbDep, _user: UserDep):
    if table not in FLEET_MODELS:
        raise HTTPException(404, "Unknown collection.")
    return [row_to_dict(table, r) for r in db.scalars(select(FLEET_MODELS[table]))]


class SyncIn(BaseModel):
    upserts: dict[str, list[dict[str, Any]]] = {}
    deletes: dict[str, list[str]] = {}


@app.post("/api/sync")
def sync(body: SyncIn, db: DbDep, user: UserDep):
    """Apply a batch of creates, updates and deletes from the app in one transaction.

    The server checks every record against the user's role, stores typed values,
    writes the audit trail itself, and records GPS history for position changes.
    """
    total = sum(map(len, body.upserts.values())) + sum(map(len, body.deletes.values()))
    if total > MAX_SYNC_RECORDS:
        raise HTTPException(413, "Too many changes in one request.")
    for table in list(body.upserts) + list(body.deletes):
        if table not in FLEET_MODELS:
            raise HTTPException(400, f"Unknown collection '{table}'.")
    regs = {v.id: v.reg for v in db.scalars(select(FLEET_MODELS["vehicles"]))}
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    counts: dict[tuple[str, str], int] = defaultdict(int)
    pending_audit: list[tuple[str, str, str, str]] = []

    for table, records in body.upserts.items():
        model, fields = FLEET_MODELS[table], FLEET_SCHEMA[table]
        for rec in records:
            rid = str(rec.get("id") or "").strip()
            if not rid or len(rid) > 40:
                raise HTTPException(422, f"{MODULE_LABELS[table]}: every record needs an id.")
            values = {k: coerce(table, k, rec.get(k)) for k in fields if k in rec}
            row = db.get(model, rid)
            if row is None:
                if not can_edit(user.role, table):
                    raise HTTPException(403, f"Your role cannot add {MODULE_LABELS[table].lower()}.")
                row = model(id=rid, **values)
                db.add(row)
                action, changed = "Created", list(values)
            else:
                changed = [k for k, v in values.items() if getattr(row, k) != v]
                if not changed:
                    continue
                allowed = can_edit(user.role, table) or (
                    table == "vehicles" and can_update_gps(user.role)
                    and set(changed) <= VEHICLE_OPERATIONAL_FIELDS)
                if not allowed:
                    raise HTTPException(403, f"Your role cannot change {MODULE_LABELS[table].lower()}.")
                before = {k: getattr(row, k) for k in changed}
                for k in changed:
                    setattr(row, k, values[k])
                action = "Updated"
            row.updated_at, row.updated_by = now_iso, user.username
            data = row_to_dict(table, row)
            if table == "vehicles":
                regs[rid] = data["reg"]
                if {"lat", "lng", "gpsAt"} & set(changed) and data["lat"] != "" and data["lng"] != "":
                    db.add(GpsFix(vehicle_id=rid, lat=row.lat, lng=row.lng,
                                  ts=row.gpsAt or int(time.time() * 1000), speed=row.speed,
                                  source=row.gpsSrc or "App", received_at=now_iso))
            if table == "activities":
                continue
            module = "GPS" if table == "vehicles" and action == "Updated" and set(changed) <= GPS_FIELDS \
                else MODULE_LABELS[table]
            counts[(action, module)] += 1
            details = "" if action == "Created" else "; ".join(
                f"{k}: {fmt(before[k])} → {fmt(getattr(row, k))}" for k in changed if k != "gpsAt")
            pending_audit.append((action, module, record_ref(table, data, regs), details))

    for table, ids in body.deletes.items():
        if not can_delete(user.role, table):
            raise HTTPException(403, f"Your role cannot delete {MODULE_LABELS[table].lower()}.")
        model = FLEET_MODELS[table]
        for rid in ids:
            row = db.get(model, rid)
            if row is None:
                continue
            ref = record_ref(table, row_to_dict(table, row), regs)
            db.delete(row)
            if table != "activities":
                counts[("Deleted", MODULE_LABELS[table])] += 1
                pending_audit.append(("Deleted", MODULE_LABELS[table], ref, ""))

    # Bulk loads (sample data, backup restore) get one summary line per module.
    for (action, module), n in counts.items():
        if n > BULK_AUDIT_THRESHOLD:
            audit(db, user.name, action, module, f"{n} records", "Bulk change")
    for action, module, ref, details in pending_audit:
        if counts[(action, module)] <= BULK_AUDIT_THRESHOLD:
            audit(db, user.name, action, module, ref, details)
    new_rev = bump_rev(db)
    db.commit()
    return {"ok": True, "rev": new_rev, "audit": recent_audit(db) if can_view_audit(user.role) else []}


# ---------------------------------------------------------------- users
class UserIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    username: str = Field(pattern=r"^[A-Za-z0-9._-]{3,32}$")
    email: str = Field("", max_length=200)
    phone: str = Field("", max_length=40)
    role: str
    status: str = "Active"
    password: str = Field(max_length=128)


class UserPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    username: str | None = Field(None, pattern=r"^[A-Za-z0-9._-]{3,32}$")
    email: str | None = Field(None, max_length=200)
    phone: str | None = Field(None, max_length=40)
    role: str | None = None
    status: str | None = None


class ResetIn(BaseModel):
    password: str = Field(max_length=128)


def _check_role_status(role: str | None, status: str | None) -> None:
    if role is not None and role not in ROLES:
        raise HTTPException(422, f"Role must be one of {', '.join(ROLES)}.")
    if status is not None and status not in ("Active", "Disabled"):
        raise HTTPException(422, "Status must be Active or Disabled.")


def _other_active_admins(db: Session, exclude_id: str) -> int:
    return db.scalar(select(func.count()).select_from(User).where(
        User.role == "Admin", User.status == "Active", User.id != exclude_id))


@app.get("/api/users")
def list_users(db: DbDep, _admin: Annotated[User, Depends(require_admin)]):
    return [u.public() for u in db.scalars(select(User))]


@app.post("/api/users", status_code=201)
def create_user(body: UserIn, db: DbDep, admin: Annotated[User, Depends(require_admin)]):
    _check_role_status(body.role, body.status)
    username = body.username.lower()
    if db.scalar(select(User).where(User.username == username)):
        raise HTTPException(409, "That username is already taken.")
    if problem := password_problem(body.password):
        raise HTTPException(400, "Temporary password: " + problem)
    user = User(id="u" + secrets.token_hex(6), name=body.name.strip(), username=username, email=body.email,
                phone=body.phone, role=body.role, status=body.status, password_hash=hash_password(body.password),
                must_change=True, created=now_local().strftime("%Y-%m-%d"))
    db.add(user)
    audit(db, admin.name, "Created", "Users", username, f"Role: {body.role}")
    bump_rev(db)
    db.commit()
    return user.public()


@app.patch("/api/users/{user_id}")
def update_user(user_id: str, body: UserPatch, db: DbDep, admin: Annotated[User, Depends(require_admin)]):
    user = db.get(User, user_id) or _404()
    _check_role_status(body.role, body.status)
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    if "username" in changes:
        changes["username"] = changes["username"].lower()
        clash = db.scalar(select(User).where(User.username == changes["username"], User.id != user_id))
        if clash:
            raise HTTPException(409, "That username is already taken.")
    new_role, new_status = changes.get("role", user.role), changes.get("status", user.status)
    if user.role == "Admin" and user.status == "Active" and (new_role != "Admin" or new_status != "Active") \
            and not _other_active_admins(db, user.id):
        raise HTTPException(400, "There must always be at least one active admin.")
    if user.id == admin.id and new_status != "Active":
        raise HTTPException(400, "You cannot disable your own account.")
    diff = [f"{k}: {getattr(user, k) or '—'} → {v or '—'}" for k, v in changes.items() if getattr(user, k) != v]
    for k, v in changes.items():
        setattr(user, k, v)
    if new_status != "Active" or "role" in changes:
        user.token_version += 1  # end their current sessions
    audit(db, admin.name, "Updated", "Users", user.username, "; ".join(diff) or "No changes")
    bump_rev(db)
    db.commit()
    return user.public()


@app.post("/api/users/{user_id}/reset-password")
def reset_password(user_id: str, body: ResetIn, db: DbDep, admin: Annotated[User, Depends(require_admin)]):
    user = db.get(User, user_id) or _404()
    if problem := password_problem(body.password):
        raise HTTPException(400, problem)
    user.password_hash = hash_password(body.password)
    user.must_change = True
    user.token_version += 1
    audit(db, admin.name, "Updated", "Users", user.username, "Password reset by administrator")
    bump_rev(db)
    db.commit()
    return user.public()


@app.delete("/api/users/{user_id}")
def delete_user(user_id: str, db: DbDep, admin: Annotated[User, Depends(require_admin)]):
    user = db.get(User, user_id) or _404()
    if user.id == admin.id:
        raise HTTPException(400, "You cannot delete your own account.")
    if user.role == "Admin" and user.status == "Active" and not _other_active_admins(db, user.id):
        raise HTTPException(400, "There must always be at least one active admin.")
    db.delete(user)
    for row in db.scalars(select(AlertRead).where(AlertRead.user_id == user.id)):
        db.delete(row)
    audit(db, admin.name, "Deleted", "Users", user.username, "")
    bump_rev(db)
    db.commit()
    return {"ok": True}


def _404():
    raise HTTPException(404, "Not found.")


# ---------------------------------------------------------------- settings & audit
@app.get("/api/settings")
def read_settings(db: DbDep, _user: UserDep):
    return get_settings(db)


@app.put("/api/settings")
def write_settings(body: dict[str, Any], db: DbDep, admin: Annotated[User, Depends(require_admin)]):
    current = get_settings(db)
    clean: dict[str, Any] = {}
    for key, default in DEFAULT_SETTINGS.items():
        if key not in body:
            continue
        val = body[key]
        try:
            clean[key] = str(val).strip()[:120] if isinstance(default, str) else max(0, float(val))
        except (TypeError, ValueError):
            raise HTTPException(422, f"Setting '{key}' must be a number.")
        if not isinstance(default, str) and float(clean[key]).is_integer():
            clean[key] = int(clean[key])
    diff = [f"{k}: {current[k]} → {v}" for k, v in clean.items() if current.get(k) != v]
    current.update(clean)
    row = db.get(Setting, "app")
    row.value = json.dumps(current)
    audit(db, admin.name, "Updated", "Settings", "—", "; ".join(diff) or "No changes")
    bump_rev(db)
    db.commit()
    return current


@app.get("/api/audit")
def read_audit(db: DbDep, user: UserDep, limit: int = 500):
    if not can_view_audit(user.role):
        raise HTTPException(403, "Your role cannot view audit logs.")
    return recent_audit(db, min(max(limit, 1), 5000))


# ---------------------------------------------------------------- GPS
class GpsReading(BaseModel):
    reg: str | None = Field(None, description="Registration number, e.g. KDA 123A")
    vehicleId: str | None = None
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    timestamp: str | int | float | None = Field(
        None, description="ISO time, 'YYYY-MM-DD HH:MM' (app time zone) or Unix seconds/milliseconds")
    speed: float | None = Field(None, ge=0, le=400)
    source: str | None = Field(None, max_length=40)


class GpsIn(BaseModel):
    readings: list[GpsReading] = Field(min_length=1, max_length=10000)


def parse_ts(value) -> int:
    if value in (None, ""):
        return int(time.time() * 1000)
    if isinstance(value, (int, float)) or str(value).isdigit():
        n = float(value)
        return int(n * 1000) if n < 1e11 else int(n)
    text = str(value).strip().replace(" ", "T", 1).replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return int(dt.timestamp() * 1000)


@app.post("/api/gps/positions")
def post_positions(body: GpsIn, db: DbDep, authorization: Annotated[str | None, Header()] = None,
                   x_tracker_key: Annotated[str | None, Header()] = None):
    """Receive GPS readings from trackers, a tracking provider's webhook, or the app.

    Authenticate with a signed-in user's Bearer token (Admin, Manager or Dispatcher)
    or with the X-Tracker-Key header when TRACKER_API_KEY is set on the server.
    """
    if config.TRACKER_API_KEY and x_tracker_key and secrets.compare_digest(x_tracker_key, config.TRACKER_API_KEY):
        actor, default_src = "Tracker feed", "Tracker feed"
    else:
        user = _user_from_header(authorization, db)
        if user.must_change:
            raise HTTPException(403, "password_change_required")
        if not can_update_gps(user.role):
            raise HTTPException(403, "Your role cannot update GPS positions.")
        actor, default_src = user.name, "App"
    vehicles = list(db.scalars(select(FLEET_MODELS["vehicles"])))
    by_id = {v.id: v for v in vehicles}
    by_reg = {v.reg.replace(" ", "").upper(): v for v in vehicles if v.reg}
    now_ms, now_iso = int(time.time() * 1000), datetime.now(timezone.utc).isoformat(timespec="seconds")
    applied, older, unknown, invalid = 0, 0, [], []
    touched: set[str] = set()
    for i, r in enumerate(body.readings):
        v = by_id.get(r.vehicleId or "") or by_reg.get((r.reg or "").replace(" ", "").upper())
        if not v:
            unknown.append(r.reg or r.vehicleId or f"row {i + 1}")
            continue
        try:
            ts = parse_ts(r.timestamp)
        except ValueError:
            invalid.append(i + 1)
            continue
        if ts > now_ms + 60_000:
            invalid.append(i + 1)
            continue
        src = r.source or default_src
        db.add(GpsFix(vehicle_id=v.id, lat=r.lat, lng=r.lng, ts=ts,
                      speed=None if r.speed is None else round(r.speed), source=src, received_at=now_iso))
        if v.gpsAt and ts < v.gpsAt:
            older += 1  # kept in history, but not the current position
            continue
        v.lat, v.lng, v.gpsAt, v.gpsSrc = r.lat, r.lng, ts, src
        v.speed = None if r.speed is None else round(r.speed)
        v.updated_at, v.updated_by = now_iso, actor
        applied += 1
        touched.add(v.id)
    if applied:
        audit(db, actor, "Updated", "GPS", f"{len(touched)} vehicles",
              f"{applied} positions applied; {older} older kept as history; "
              f"{len(unknown)} unknown vehicles; {len(invalid)} invalid")
        bump_rev(db)
    db.commit()
    return {"applied": applied, "vehicles": len(touched), "older": older, "unknown": unknown, "invalid": invalid}


@app.get("/api/gps/track/{vehicle_id}")
def gps_track(vehicle_id: str, db: DbDep, _user: UserDep, hours: float = 24):
    """Position history for one vehicle, oldest first."""
    since = int((time.time() - min(max(hours, 0.1), 24 * 31) * 3600) * 1000)
    rows = db.scalars(select(GpsFix).where(GpsFix.vehicle_id == vehicle_id, GpsFix.ts >= since)
                      .order_by(GpsFix.ts).limit(20000))
    return [{"lat": r.lat, "lng": r.lng, "ts": r.ts, "speed": r.speed, "source": r.source} for r in rows]


# ---------------------------------------------------------------- alert read status
MAX_READ_KEYS = 5000


class AlertReadIn(BaseModel):
    keys: list[str] = Field(min_length=1, max_length=2000)
    read: bool = True


def read_alert_keys(db: Session, user_id: str) -> list[str]:
    return list(db.scalars(select(AlertRead.key).where(AlertRead.user_id == user_id)))


@app.get("/api/alerts/read")
def get_alert_reads(db: DbDep, user: UserDep):
    """Keys of the alerts the signed-in user has marked as read."""
    return {"keys": read_alert_keys(db, user.id)}


@app.post("/api/alerts/read")
def set_alert_reads(body: AlertReadIn, db: DbDep, user: UserDep):
    """Mark alerts as read (read=true) or unread (read=false) for the signed-in user only."""
    keys = {k.strip()[:120] for k in body.keys if k and k.strip()}
    existing = set(read_alert_keys(db, user.id))
    if body.read:
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for k in keys - existing:
            db.add(AlertRead(user_id=user.id, key=k, read_at=now_iso))
    else:
        for k in keys & existing:
            db.delete(db.get(AlertRead, (user.id, k)))
    db.flush()
    # Keep the table small: drop the oldest marks beyond the cap.
    total = db.scalar(select(func.count()).select_from(AlertRead).where(AlertRead.user_id == user.id))
    if total > MAX_READ_KEYS:
        old = db.scalars(select(AlertRead).where(AlertRead.user_id == user.id)
                         .order_by(AlertRead.read_at).limit(total - MAX_READ_KEYS))
        for row in old:
            db.delete(row)
    db.commit()
    return {"keys": read_alert_keys(db, user.id)}


# ---------------------------------------------------------------- frontend
@app.get("/", include_in_schema=False)
def frontend():
    if not config.FRONTEND_FILE.exists():
        return JSONResponse({"detail": "Frontend file not found. Set FRONTEND_FILE."}, status_code=404)
    return FileResponse(config.FRONTEND_FILE, media_type="text/html", headers={"Cache-Control": "no-cache"})
