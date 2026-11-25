# Mobisirs Fleet Operations

A single-page fleet management app for Mobisirs Bespoke Solutions. It covers vehicles, drivers, hotel allocations, GPS tracking, maintenance, NTSA inspections, fuel, cleaning, procurement, finance, reports, users and audit logs.

Everything is in one file, `index.html`. There is no build step or server. [Leaflet](https://leafletjs.com) 1.9.4 (BSD-2-Clause) is included inside the file. The only things loaded from the internet are the Inter font and the OpenStreetMap map tiles.

## Running it

- **Locally:** open `index.html` in a browser.
- **GitHub Pages:** go to *Settings → Pages*, choose the `main` branch and the root folder, then open the published URL.
- **Any static host:** upload `index.html`.

On first load the app creates a sample fleet: 4 hotels, 100 vehicles, 100 drivers and about three months of trips, fuel, cleaning, maintenance and inspections. To start from your own data, go to *Settings → Clear all data*.

## Signing in

The app opens on a landing page with a sign-in form.

| | |
|---|---|
| Username | `admin` |
| Temporary password | `Admin@2026` |

You must replace the temporary password on first sign-in. New passwords need at least 8 characters with letters and numbers. After five wrong attempts, sign-in is paused for 30 seconds.

### Users and roles

Admins add people under **Users & Access → Add user**. The form generates a temporary password for you to copy and give to the person. They must replace it when they first sign in. Admins can also reset passwords, disable accounts and change roles. The app always keeps at least one active admin.

| Role | Can see | Can change |
|---|---|---|
| Admin | Everything | Everything, including users, settings and data backup |
| Manager | All operations, finance, reports and audit logs | All fleet records, purchases and expenses; can delete records |
| Dispatcher | Dashboard, tracking, fleet, hotels, drivers, maintenance, fuel, cleaning, inspections | Trips, GPS positions, fuel, cleaning and maintenance jobs |
| Viewer | Operations, finance and reports | Nothing (read-only) |

Every create, update, delete, sign-in and sign-out is written to **Audit Logs** with the name of the user who did it.

> **Security note:** sign-in is enforced inside the page. It controls what each person sees and can change in the app. It does not protect the data itself: anyone who can open the page can read the stored records in its source. Passwords are stored as salted, iterated SHA-256 hashes, never in plain text. For real data protection, host the app behind proper authentication or move the data to a server with its own access control.

## Live GPS tracking

The **Live Tracking** page and the dashboard show a real **OpenStreetMap** street map (© OpenStreetMap contributors). It has zoom, pan, a scale bar and pins for each hotel. Each vehicle is drawn at its latest GPS position. Tapping a vehicle opens a popup with its details and an *Update position* button. Each vehicle shows:

- how old the position is: *Live* (15 minutes or less), *Last hour* or *Stale*
- speed
- straight-line distance to the nearest hotel or landmark
- a scale bar

If street map tiles can't load (for example offline, or inside a claude.ai artifact, which blocks outside images), the app switches automatically to a drawn map of Nairobi. That map still places vehicles at their exact coordinates, but its roads are only approximate.

Positions come from one of these sources:

1. **Tracker feed import.** Go to *Live Tracking → Import tracker feed* and paste or upload a CSV export from your GPS tracking system. Click *Check file* to preview what will change, then *Apply updates*.

   ```csv
   reg,latitude,longitude,timestamp,speed
   KDA 123A,-1.29890,36.81580,2026-11-25 09:30,0
   KDB 456B,-1.31920,36.92780,2026-11-25 09:31,48
   ```

   - Column names are flexible: `plate`/`registration`, `lat`/`lng`/`lon`, and `time`/`datetime` all work.
   - Commas, semicolons and tabs all work as separators.
   - Timestamps can be ISO dates or Unix seconds or milliseconds.
   - Readings older than a vehicle's current position are skipped.

2. **Manual update.** Click *Update* on a vehicle, or *Update position* in its map popup. Then either type the coordinates or choose *Pick on map* and tap the vehicle's location on the street map.

The app does not stream positions from trackers by itself. For fully live positions, connect your tracking provider's API to the import format above, for example with a small script that writes the CSV on a schedule.

## Where data is stored

- **Static host or local file:** records are saved in the browser's `localStorage`. Each browser has its own copy, so use *Settings → Export backup / Import backup* to move data between browsers.
- **Claude artifact:** the app saves records into the published page itself, so everyone who opens it sees the same data. Saving requires edit access to the artifact.

## Features

- Dashboard with KPIs, OpenStreetMap live map, recent activity, expenditure donut, maintenance alerts, NTSA countdown, weekly fuel and cleaning chart, and hotel allocation
- Fleet management: add, edit and delete vehicles; dispatch trips and end them (ending a trip adds the distance to the odometer)
- Maintenance workflow, from *Scheduled* to *In progress* to *Completed*; vehicle status and next service update automatically
- NTSA inspection records and a due schedule; a failed inspection marks the vehicle out of service
- Fuel log with km/L efficiency, cleaning log with *cleaning due* list, and procurement approvals (Requested → Approved → Ordered → Received)
- Finance ledger built automatically from every module, monthly budget and period comparison
- Reports by vehicle, hotel, driver and month, with CSV export from every table
- Alerts bell covering high-priority repairs, services due, inspections, licence and insurance expiry, and pending approvals
- Mobile-friendly layout: on phones, tables become stacked cards with action buttons, forms open as bottom sheets, the header stays visible while scrolling and the menu slides in; tablets get a 3-column KPI grid; light and dark themes
