"""Password hashing, sign-in tokens and role permissions."""
import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone

import jwt

from .config import SECRET_KEY, TOKEN_HOURS

PBKDF2_ROUNDS = 390_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt, digest = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(dk.hex(), digest)
    except (ValueError, TypeError):
        return False


def password_problem(password: str) -> str | None:
    if len(password) < 8 or not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        return "Use at least 8 characters with both letters and numbers."
    if len(password) > 128:
        return "Password is too long."
    return None


def make_token(user_id: str, token_version: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": user_id, "ver": token_version, "iat": now, "exp": now + timedelta(hours=TOKEN_HOURS)}
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")


def read_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


# ---- roles: these mirror the frontend so the server enforces the same rules ----
ROLES = ("Admin", "Manager", "Dispatcher", "Viewer")
DISPATCHER_EDIT = {"trips", "fuel", "wash", "maintenance", "activities"}

# Fields a dispatcher may change on a vehicle as a side effect of trips,
# maintenance, fuel and GPS updates, without full vehicle-edit rights.
VEHICLE_OPERATIONAL_FIELDS = {"status", "odometer", "nextServiceKm", "driverId",
                              "lat", "lng", "gpsAt", "speed", "gpsSrc"}
GPS_FIELDS = {"lat", "lng", "gpsAt", "speed", "gpsSrc"}


def can_edit(role: str, collection: str) -> bool:
    if role == "Admin":
        return True
    if collection in ("users", "settings"):
        return False
    if role == "Manager":
        return True
    if role == "Dispatcher":
        return collection in DISPATCHER_EDIT
    return False


def can_delete(role: str, collection: str) -> bool:
    return role in ("Admin", "Manager") and can_edit(role, collection)


def can_update_gps(role: str) -> bool:
    return role in ("Admin", "Manager", "Dispatcher")


def can_view_audit(role: str) -> bool:
    return role in ("Admin", "Manager")
