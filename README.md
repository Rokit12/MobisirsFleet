# Mobisirs Fleet Operations

Fleet management for Mobisirs Bespoke Solutions. It covers vehicles, drivers, hotel allocations, live GPS tracking, maintenance, NTSA inspections, fuel, cleaning, procurement, finance, reports, users and audit logs.

The project has two parts:

| Part | What it is |
|---|---|
| `backend/` | **FastAPI** server with a **SQLAlchemy** database (SQLite by default; PostgreSQL or MySQL by changing one setting). It handles sign-in, roles, all fleet records, the audit trail and GPS history. |
| `index.html` | The web app: one file, no build step. [Leaflet](https://leafletjs.com) 1.9.4 (BSD-2-Clause) is included inside it. The backend serves it at `/`. |

## Quick start

You need Python 3.11 or newer.

```bash
cd backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open http://localhost:8000 and sign in. Interactive API documentation is at http://localhost:8000/docs.

On first start the server creates the database file `backend/mbs_fleet.db` and one administrator account. The database starts empty. On the dashboard, an admin can choose **Load sample data** to add the demo fleet (4 hotels, 100 vehicles, 100 drivers and about three months of records), or start by adding hotels and vehicles.

### With Docker

```bash
docker build -t mbs-fleet .
docker run -p 8000:8000 -v mbs-data:/data -e TRACKER_API_KEY=change-me mbs-fleet
```

The database and the token signing key are kept in the `/data` volume, so they survive rebuilds.

## Signing in

| | |
|---|---|
| Username | `admin` |
| Temporary password | `Admin@2026` (change it with the `ADMIN_PASSWORD` setting before first start) |

The server forces the temporary password to be replaced at first sign-in. Until it is replaced, the account can do nothing else. New passwords need at least 8 characters with letters and numbers. After five wrong attempts within a minute, sign-in is paused for that username.

### Users and roles

Admins add people under **Users & Access → Add user**. The form generates a temporary password for you to copy and give to the person, who must replace it when they first sign in. Admins can also reset passwords, disable accounts and change roles. Disabling a user, changing their role or resetting their password ends their open sessions. The server always keeps at least one active admin.

| Role | Can see | Can change |
|---|---|---|
| Admin | Everything | Everything, including users, settings and data management |
| Manager | All operations, finance, reports and audit logs | All fleet records, purchases and expenses; can delete records |
| Dispatcher | Dashboard, tracking, fleet, hotels, drivers, maintenance, fuel, cleaning, inspections | Trips, GPS positions, fuel, cleaning and maintenance jobs; a vehicle's status, odometer and position as a result |
| Viewer | Operations, finance and reports | Nothing (read-only) |

The server enforces these rules on every request, not only the app. It writes the audit trail itself: every create, update and delete is recorded, with the user's name and the fields that changed, along with sign-ins and sign-outs.

**Security:** passwords are stored as salted PBKDF2-SHA256 hashes, and sessions use signed tokens that expire after 12 hours. In production, serve the app over HTTPS, for example behind a reverse proxy, and set your own `SECRET_KEY` and `ADMIN_PASSWORD`.

## Live GPS tracking

The **Live Tracking** page and the dashboard show an **OpenStreetMap** street map (© OpenStreetMap contributors), with hotel pins and every vehicle at its latest GPS position. Tapping a vehicle shows:

- how old its position is: *Live* (15 minutes or less), *Last hour* or *Stale*
- speed
- distance to the nearest landmark
- an *Update position* button

Open pages check the server every 15 seconds, so new positions and other people's changes appear without reloading.

### Sending positions from trackers

Set `TRACKER_API_KEY` on the server. Then your tracking system, your provider's webhook, or a small forwarding script can post readings:

```bash
curl -X POST http://localhost:8000/api/gps/positions \
  -H "X-Tracker-Key: change-me" -H "Content-Type: application/json" \
  -d '{"readings":[{"reg":"KDA 123A","lat":-1.29890,"lng":36.81580,"timestamp":"2026-11-25T09:30:00+03:00","speed":42}]}'
```

- Identify each vehicle by `reg`; spacing and letter case don't matter. You can send `vehicleId` instead.
- `timestamp` can be an ISO time, `YYYY-MM-DD HH:MM` (in `APP_TIMEZONE`), or Unix seconds or milliseconds. Leave it out to use the time the server receives the reading.
- Up to 10,000 readings fit in one request.
- Every reading is kept as history. A reading older than the vehicle's current position goes into history only.
- `GET /api/gps/track/{vehicleId}?hours=24` returns a vehicle's route history.

Signed-in admins, managers and dispatchers can also import a tracker's CSV export (*Live Tracking → Import tracker feed*) or set a position by hand (*Update*, then type the coordinates or *Pick on map*).

## Configuration

All settings are environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite:///backend/mbs_fleet.db` | Any SQLAlchemy URL, e.g. `postgresql+psycopg://user:pass@host/mbs_fleet` (install the driver, e.g. `psycopg[binary]`) |
| `SECRET_KEY` | generated and stored in `SECRET_KEY_FILE` | Signs sign-in tokens |
| `SECRET_KEY_FILE` | `backend/.secret_key` | Where the generated key is kept |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` / `ADMIN_NAME` | `admin` / `Admin@2026` / `Admin User` | First administrator, created only when there are no users |
| `TRACKER_API_KEY` | empty (disabled) | Key trackers send in `X-Tracker-Key` |
| `TOKEN_HOURS` | `12` | Session length |
| `APP_TIMEZONE` | `Africa/Nairobi` | Time zone for audit times and tracker times without an offset |
| `CORS_ORIGINS` | empty | Comma-separated origins allowed to call the API when `index.html` is hosted elsewhere |
| `FRONTEND_FILE` | `index.html` in the repo root | Page served at `/` |

To host `index.html` somewhere other than the backend (for example GitHub Pages), put the server address in the page's `<meta name="mbs-api" content="https://your-server">` tag and add that page's origin to `CORS_ORIGINS`.

## API overview

| Method and path | Purpose |
|---|---|
| `POST /api/auth/login` | Sign in; returns a token |
| `POST /api/auth/change-password` | Replace a temporary or current password |
| `GET /api/bootstrap` | All records the user may see, plus settings and audit |
| `POST /api/sync` | Create, update and delete records in one transaction |
| `GET /api/data/{collection}` | Read one collection, e.g. `vehicles`, `trips`, `fuel` |
| `GET/POST/PATCH/DELETE /api/users…` | Manage users (admin only) |
| `GET/PUT /api/settings` | Company settings (changes by admin only) |
| `GET /api/audit` | Audit trail (admins and managers) |
| `POST /api/gps/positions` | Receive GPS readings |
| `GET /api/gps/track/{vehicleId}` | Position history |
| `GET/POST /api/alerts/read` | The signed-in user's read and unread alerts |

The full schema is in `/docs`.

## Database tables

`hotels`, `vehicles`, `drivers`, `trips`, `maintenance`, `fuel`, `wash`, `inspections`, `procurement`, `expenses`, `activities`, `users`, `settings`, `audit_log`, `gps_fixes`, `alert_reads` and `meta`. New tables are created automatically when the server starts. The fleet tables are defined in one schema in `backend/app/models.py`. Every fleet row records who last changed it and when.

## Tests

```bash
cd backend
pip install -r requirements-dev.txt
pytest -q
```

## Offline mode

If `index.html` is opened without the backend (as a local file, on a static host, or as a claude.ai artifact), it still works. In that case records are stored in the browser (or in the claude.ai page). Sign-in then only controls what each person sees and can change in the app; it does not protect the data. Use the backend for real multi-user work.

## Features

- Dashboard with KPIs, live street map, recent activity, expenditure donut, maintenance alerts, NTSA countdown, weekly fuel and cleaning chart, and hotel allocation
- Fleet management: add, edit and delete vehicles; dispatch trips and end them (ending a trip adds the distance to the odometer)
- Maintenance workflow, from *Scheduled* to *In progress* to *Completed*; vehicle status and next service update automatically
- NTSA inspection records and a due schedule; a failed inspection marks the vehicle out of service
- Fuel log with km/L efficiency, cleaning log with a *cleaning due* list, and procurement approvals (Requested → Approved → Ordered → Received)
- Finance ledger built automatically from every module, monthly budget and period comparison
- Reports by vehicle, hotel, driver and month, with CSV export from every table
- Alerts bell covering high-priority repairs, services due, inspections, licence and insurance expiry, and pending approvals. Each person can mark alerts as read or unread, one at a time or all at once, and the bell counts only unread alerts. With the backend, read status is saved per user in the database (`/api/alerts/read`); offline, it is kept in the browser. An alert becomes unread again when it gets worse, for example when a service goes from due to overdue.
- Works on phones and tablets: on phones, tables become stacked cards and forms open as bottom sheets; light and dark themes
