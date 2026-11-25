"""Runtime configuration, read from environment variables."""
import os
import secrets
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent

# SQLite by default; point this at PostgreSQL/MySQL in production, e.g.
# postgresql+psycopg://user:pass@host/mbs_fleet
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BACKEND_DIR / 'mbs_fleet.db'}")

# Account created on first start when the users table is empty.
# The password is temporary: the app forces a change at first sign-in.
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "Admin@2026")
ADMIN_NAME = os.getenv("ADMIN_NAME", "Admin User")

TOKEN_HOURS = int(os.getenv("TOKEN_HOURS", "12"))

# Optional shared key that GPS trackers or a tracking provider's webhook send
# in the X-Tracker-Key header to post positions without a user account.
TRACKER_API_KEY = os.getenv("TRACKER_API_KEY", "")

# Comma-separated origins allowed to call the API from a browser, needed only
# when the frontend is hosted elsewhere (e.g. GitHub Pages).
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]

FRONTEND_FILE = Path(os.getenv("FRONTEND_FILE", str(REPO_DIR / "index.html")))


def _secret_key() -> str:
    """Use SECRET_KEY if set; otherwise create one and keep it beside the code
    so sign-in tokens survive restarts."""
    key = os.getenv("SECRET_KEY")
    if key:
        return key
    path = Path(os.getenv("SECRET_KEY_FILE", str(BACKEND_DIR / ".secret_key")))
    if path.exists():
        return path.read_text().strip()
    key = secrets.token_urlsafe(48)
    try:
        path.write_text(key)
        path.chmod(0o600)
    except OSError:
        pass
    return key


SECRET_KEY = _secret_key()
