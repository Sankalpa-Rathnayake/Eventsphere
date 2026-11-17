# EventSphere

A mobile-friendly venue booking and digital ticketing platform where **every booking decision is made by five cooperating software agents** that talk to each other through logged JSON messages.

Built for IT4123 Agent Based Computing Practical, ICA 02.

- **Live site:** https://YOUR-APP.onrender.com
- **Repository:** https://github.com/Sankalpa-Rathnayake/Eventsphere

> The free hosting tier sleeps when idle. The first page load can take up to a minute.

## What it does

A university publishes events (concerts, workshops, seminars, conferences, sports events) and hires out its venues. Attendees browse and book tickets from their phone, then walk in with a QR code. Gate staff scan tickets at the entrance. Admins manage venues, events, hire requests and watch sales.

Payments are **simulated**. No real bank, card or payment gateway is used.

### Users

| Role | Can do |
|---|---|
| Attendee | Register, browse and search events, book and cancel tickets, join waitlists, request private venue hire |
| Gate staff | Scan tickets with the phone camera or enter a code by hand |
| Admin | Create and edit venues and events, publish or cancel events, approve hire requests, view the dashboard and agent message log |

## The five agents

Agents never call each other's functions. They send JSON messages through a message bus (`app/bus.py`). Each message has a sender, receiver, performative (`REQUEST`, `AGREE`, `REFUSE`, `PROPOSE`, `INFORM`, `FAILURE`), content and a conversation id. Every message is stored in the `messages` table and shown on the admin dashboard.

| Agent | Goal | Examples of its decisions |
|---|---|---|
| VenueAgent | Keep venue calendars free of clashes | Refuses overlapping slots (30 minute buffer), prices hires, auto-confirms routine hires, proposes alternative times or venues |
| BookingAgent | Sell tickets without overselling | Limits of 6 tickets, atomic seat allocation, refund policy, full refunds on event cancellation |
| TicketAgent | Admit each ticket exactly once | Unguessable codes, admit or reject (used, cancelled, wrong event, outside time), flags suspicious repeated scans |
| WaitlistAgent | Pass freed seats to waiting customers | First come first served, 15 minute seat holds, **timer** that expires holds every 30 seconds |
| NotificationAgent | Tell the right person what affects them | Listens to INFORM messages and creates user notifications |

The full decision rules are written at the top of each file in `app/agents/`.

**Reactive behaviour:** NotificationAgent reacts to broadcast INFORM messages.
**Proactive behaviour:** the WaitlistAgent sweep runs on a timer with no user action.

## Features

- Registration and login, bcrypt-hashed passwords, roles with server-side checks (403 for the wrong role)
- CSRF token on every form
- Venue and event management, draft, publish and cancel
- Event search by keyword and filter by category (Concert, Workshop, Seminar, Conference, Sports event)
- Booking with a simulated payment reference, booking history, and cancellation with a refund policy
- One unique QR code per ticket, staff scanner with camera and manual fallback
- Private venue hire with instant decision, price, alternatives on a clash, and admin approval
- Waitlist with timed seat holds and a visible countdown
- Weekend venue pricing (x1.25)
- Suspicious repeated-scan detection
- Organiser dashboard with totals, a chart and the agent message log
- Mobile-first interface: bottom navigation bar, large touch targets, card layouts instead of tables on phones, works from 375 px wide

## Refund policy

| When the booking is cancelled | Refund |
|---|---|
| 7 or more days before the event | 100% |
| 2 to 7 days before | 50% |
| Less than 2 days before | 0% |
| The organiser cancels the event | 100% to everyone |

## Preventing overselling

Seats are taken with one atomic SQL statement:

```sql
UPDATE ticket_categories
SET sold = sold + :qty
WHERE id = :id AND sold + :qty <= quantity
```

If two people buy the last seat at the same moment, the database lets only one update succeed. The other gets a `REFUSE`. This is covered by `test_overbooking_last_seats_sold_once`.

## Tech stack

| Part | Technology |
|---|---|
| Backend | Python 3.12, FastAPI |
| Database | PostgreSQL in production, SQLite locally (SQLAlchemy) |
| Frontend | HTML, CSS, JavaScript, Jinja2, Bootstrap 5 |
| QR codes | `qrcode` (generate), `html5-qrcode` (scan) |
| Charts | Chart.js |
| Tests | Pytest |
| Hosting | Render |

## Run locally

You need Python 3.11 or newer and Git.

```
git clone https://github.com/Sankalpa-Rathnayake/Eventsphere.git
cd Eventsphere
python -m venv venv
venv\Scripts\activate          (Mac/Linux: source venv/bin/activate)
pip install -r requirements.txt
python seed.py
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000. A local SQLite file `eventsphere.db` is created automatically.

`seed.py` creates the demo accounts, three venues and two events. The app also seeds itself on startup if the database is empty.

## Demo accounts

| Role | Email | Password |
|---|---|---|
| Admin | admin@eventsphere.com | Admin@123 |
| Staff | staff@eventsphere.com | Staff@123 |
| Attendee | attendee@eventsphere.com | Attendee@123 |

These are demo accounts for marking only.

## Environment variables

| Name | Purpose | Default |
|---|---|---|
| `DATABASE_URL` | PostgreSQL URL in production | `sqlite:///./eventsphere.db` |
| `SECRET_KEY` | Signs session cookies. Set a long random value in production | `dev-secret-change-me` |
| `TZ_OFFSET_HOURS` | Local time offset from UTC (Sri Lanka is 5.5) | `5.5` |
| `HOLD_MINUTES` | Waitlist seat-hold time in minutes | `15` |

No secrets are stored in the repository.

## Run the tests

```
pytest -v
```

There are 20 tests covering all five agents, overbooking, double check-in, refunds, role checks, password hashing and CSRF protection. They run on a temporary in-memory database.

To run one test:

```
pytest -v tests/test_agents.py::test_overbooking_last_seats_sold_once
pytest -v tests/test_agents.py::test_double_checkin_rejected
```

## Try the scanner

Ticket check-in opens 2 hours before an event starts and closes when it ends. The seeded events are 10 days away, so to test scanning:

1. Log in as admin and create an event that starts about 30 minutes from now. Publish it.
2. Log in as the attendee and book a ticket.
3. Log in as staff, open **Scan**, choose the event, and scan the QR code or paste the ticket code.
4. Scan it again to see the rejection.

The phone camera needs HTTPS, so test it on the live site.

## Deploy on Render

1. Create a **Postgres** database (free plan) and copy its **Internal Database URL**.
2. Create a **Web Service** from this repository.
   - Build command: `pip install -r requirements.txt`
   - Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
3. Add the environment variables `DATABASE_URL`, `SECRET_KEY`, `PYTHON_VERSION` (`3.12.8`) and `TZ_OFFSET_HOURS` (`5.5`).

The app selects the `psycopg2` PostgreSQL driver explicitly in `app/database.py`.

## Project structure

```
app/
  agents/            the five agents (rules written at the top of each file)
  bus.py             message bus, message log, persistent agent state
  database.py        database connection
  models.py          database tables
  security.py        password hashing, role checks, CSRF
  routes_public.py   login, events, booking, tickets, waitlist, venue hire
  routes_admin.py    venues, events, hire approvals, dashboard, scanner
  common.py          shared helpers, event categories, QR images
  seed_data.py       demo accounts, venues and events
  timeutil.py        local clock with a configurable offset
  templates/         Jinja2 and Bootstrap pages
  static/            style.css
tests/               Pytest tests
seed.py              creates demo data
requirements.txt     Python dependencies
```

## Limitations

- Venue images are URLs, not uploads.
- The event form supports up to three ticket categories at once.
- Agents communicate through an in-process message bus, not separate HTTP endpoints.
- Notifications appear inside the app. Email is not sent.
- The free hosting tier sleeps when idle.
