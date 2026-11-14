# EventSphere

Venue booking and digital ticketing platform where every booking decision is made by five cooperating agents
(VenueAgent, BookingAgent, TicketAgent, WaitlistAgent, NotificationAgent) that talk through JSON messages.

Live URL: https://eventsphere-8kwv.onrender.com

## Run locally
```
python -m venv venv
venv\Scripts\activate          (Mac/Linux: source venv/bin/activate)
pip install -r requirements.txt
python seed.py
uvicorn app.main:app --reload
```
Open http://127.0.0.1:8000. SQLite is used locally (file eventsphere.db).

## Environment variables
| Name | Purpose | Default |
|---|---|---|
| DATABASE_URL | PostgreSQL URL in production | sqlite:///./eventsphere.db |
| SECRET_KEY | Signs session cookies | dev-secret-change-me |
| TZ_OFFSET_HOURS | Local time offset from UTC | 5.5 |
| HOLD_MINUTES | Waitlist seat-hold time | 15 |

## Demo accounts
| Role | Email | Password |
|---|---|---|
| Admin | admin@eventsphere.com | Admin@123 |
| Staff | staff@eventsphere.com | Staff@123 |
| Attendee | attendee@eventsphere.com | Attendee@123 |

## Run the tests
```
pytest -v
```

## Structure
app/agents: the five agents (rules written at the top of each file)
app/bus.py: message bus, logging with conversation ids, persistent agent state
app/routes_*.py: web routes; app/templates: Bootstrap pages