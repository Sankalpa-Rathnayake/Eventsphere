"""TicketAgent
GOAL: admit each ticket exactly once.

DECISION RULES
T1. Every ticket gets a unique unguessable code (secrets.token_urlsafe(16)).
T2. At the door, check in this order and refuse at the first failure:
    unknown code -> cancelled -> wrong event -> already used -> outside check-in window.
T3. Check-in window: opens 2 hours before the event starts, closes when it ends.
T4. Otherwise ADMIT and mark the ticket used (scan_count = 1).
T5. Each repeated scan of a used ticket increases scan_count. From the 3rd scan
    the ticket is flagged suspicious (possible shared screenshot).
"""
import secrets
from datetime import timedelta
from .. import bus
from ..models import Ticket, Event
from ..timeutil import now

NAME = "TicketAgent"
WINDOW_BEFORE = timedelta(hours=2)
SUSPICIOUS_AFTER = 3


def _bump(db, key):
    bus.set_state(db, NAME, key, bus.get_state(db, NAME, key, 0) + 1)


def _issue(db, c):
    codes = []
    for _ in range(int(c["quantity"])):
        code = secrets.token_urlsafe(16)  # T1
        db.add(Ticket(booking_id=c["booking_id"], event_id=c["event_id"],
                      category_id=c["category_id"], code=code, status="valid"))
        codes.append(code)
    db.flush()
    bus.set_state(db, NAME, "tickets_issued", bus.get_state(db, NAME, "tickets_issued", 0) + len(codes))
    return bus.reply("AGREE", codes=codes)


def _cancel(db, c):
    tickets = db.query(Ticket).filter(Ticket.booking_id == c["booking_id"],
                                      Ticket.status == "valid").all()
    for t in tickets:
        t.status = "cancelled"
    db.flush()
    return bus.reply("AGREE", cancelled=len(tickets))


def _scan(db, c):
    code = (c.get("code") or "").strip()
    event_id = int(c["event_id"])
    t = db.query(Ticket).filter_by(code=code).first()
    if t is None:
        _bump(db, "refused_unknown")
        return bus.reply("REFUSE", result="unknown", reason="Ticket code not recognised")
    if t.status == "cancelled":
        _bump(db, "refused_cancelled")
        return bus.reply("REFUSE", result="cancelled", reason="This ticket was cancelled")
    if t.event_id != event_id:
        _bump(db, "refused_wrong_event")
        other = db.get(Event, t.event_id)
        return bus.reply("REFUSE", result="wrong_event",
                         reason=f"Ticket is for a different event: {other.title if other else '?'}")
    if t.status == "used":
        t.scan_count += 1  # T5
        _bump(db, "refused_already_used")
        db.flush()
        suspicious = t.scan_count >= SUSPICIOUS_AFTER
        if suspicious:
            flagged = bus.get_state(db, NAME, "suspicious_codes", [])
            if code not in flagged:
                flagged.append(code)
                bus.set_state(db, NAME, "suspicious_codes", flagged)
        reason = f"Already used (scanned {t.scan_count} times)"
        if suspicious:
            reason += ". SUSPICIOUS: possible shared screenshot"
        return bus.reply("REFUSE", result="already_used", reason=reason, suspicious=suspicious)
    ev = db.get(Event, t.event_id)
    n = now()
    if n < ev.start - WINDOW_BEFORE or n > ev.end:  # T3
        _bump(db, "refused_outside_time")
        return bus.reply("REFUSE", result="outside_time",
                         reason=f"Check-in is open from {(ev.start - WINDOW_BEFORE):%d %b %H:%M} to {ev.end:%H:%M}")
    t.status = "used"  # T4
    t.scan_count = 1
    db.flush()
    _bump(db, "admitted")
    return bus.reply("AGREE", result="admit", reason="Welcome! Ticket admitted", event=ev.title)


def handle(db, msg):
    c = msg["content"]
    action = c.get("action")
    if action == "issue":
        return _issue(db, c)
    if action == "cancel_tickets":
        return _cancel(db, c)
    if action == "scan":
        return _scan(db, c)
    return bus.reply("FAILURE", reason=f"TicketAgent does not understand '{action}'")


bus.register(NAME, handle)