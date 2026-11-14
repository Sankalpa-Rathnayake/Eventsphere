"""VenueAgent
GOAL: keep every venue's calendar free of clashes.

DECISION RULES
V1. Refuse an event slot if end <= start.
V2. Refuse an event slot if total tickets > venue capacity.
V3. Refuse an event slot if it overlaps another event or hire at the same venue,
    including a 30-minute turnaround buffer. The reason names the clash.
V4. Hire: refuse if the venue is not open for hire, the time is in the past,
    or guests < 1.
V5. Hire: if guests > capacity, or the slot clashes, PROPOSE alternatives
    (3 free same-day slots, 2 other free venues big enough). Refuse only if
    there are no alternatives.
V6. Hire price = hours x hourly rate, x1.25 on Saturday/Sunday (peak pricing).
V7. Hire of <= 4 hours AND guests <= 80% of capacity -> AGREE (auto-confirm).
    Otherwise INFORM the requester that it is escalated to the admin.
"""
from datetime import datetime, timedelta
from .. import bus
from ..models import Venue, Event, HireRequest
from ..timeutil import now

NAME = "VenueAgent"
BUFFER = timedelta(minutes=30)
AUTO_MAX_HOURS = 4
ESCALATE_FILL = 0.8
WEEKEND_MULTIPLIER = 1.25


def _dt(v):
    return v if isinstance(v, datetime) else datetime.fromisoformat(str(v))


def _bump(db, key):
    bus.set_state(db, NAME, key, bus.get_state(db, NAME, key, 0) + 1)


def _busy(db, venue_id, exclude_event_id=None):
    out = []
    events = db.query(Event).filter(Event.venue_id == venue_id,
                                    Event.status.in_(["draft", "published"]))
    for e in events:
        if exclude_event_id and e.id == int(exclude_event_id):
            continue
        out.append((e.start, e.end, f'event "{e.title}"'))
    hires = db.query(HireRequest).filter(HireRequest.venue_id == venue_id,
                                         HireRequest.status.in_(["confirmed", "pending"]))
    for h in hires:
        out.append((h.start, h.end, "a private hire"))
    return out


def _clash(busy, start, end):
    """Return a text description of the first clash, or None."""
    for s, e, label in busy:
        if start < e + BUFFER and end + BUFFER > s:
            return f"{label} ({s:%d %b %H:%M} to {e:%H:%M}, plus 30 min turnaround)"
    return None


def _price(venue, start, end):
    hours = (end - start).total_seconds() / 3600
    weekend = start.weekday() >= 5
    price = hours * venue.hourly_rate * (WEEKEND_MULTIPLIER if weekend else 1)
    return round(price, 2), hours, weekend


def _alt_slots(db, venue_id, start, end):
    busy = _busy(db, venue_id)
    duration = end - start
    day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    slots = []
    for h in range(8, 23):
        s = day + timedelta(hours=h)
        e = s + duration
        if e > day + timedelta(hours=23):
            break
        if s == start or s <= now():
            continue
        if _clash(busy, s, e) is None:
            slots.append({"start": s.isoformat(), "end": e.isoformat()})
        if len(slots) == 3:
            break
    return slots


def _alt_venues(db, start, end, min_capacity, exclude_id, only_hire):
    out = []
    q = db.query(Venue).filter(Venue.id != exclude_id, Venue.capacity >= min_capacity)
    if only_hire:
        q = q.filter(Venue.open_for_hire == True)  # noqa: E712
    for v in q:
        if _clash(_busy(db, v.id), start, end) is None:
            out.append({"venue_id": v.id, "name": v.name, "capacity": v.capacity,
                        "price": _price(v, start, end)[0]})
        if len(out) == 2:
            break
    return out


def _check_slot(db, c):
    venue = db.get(Venue, c.get("venue_id"))
    if not venue:
        return bus.reply("REFUSE", reason="Venue does not exist")
    start, end = _dt(c["start"]), _dt(c["end"])
    if end <= start:
        return bus.reply("REFUSE", reason="End time must be after start time")  # V1
    total = int(c.get("tickets_total", 0))
    if total > venue.capacity:  # V2
        _bump(db, "slots_refused")
        return bus.reply("REFUSE",
                         reason=f"Total tickets ({total}) exceed venue capacity ({venue.capacity})")
    clash = _clash(_busy(db, venue.id, c.get("exclude_event_id")), start, end)
    if clash:  # V3
        _bump(db, "slots_refused")
        return bus.reply("REFUSE", reason=f"Clashes with {clash}",
                         alternatives={"slots": _alt_slots(db, venue.id, start, end),
                                       "venues": _alt_venues(db, start, end, max(total, 1),
                                                             venue.id, only_hire=False)})
    _bump(db, "slots_accepted")
    return bus.reply("AGREE", reason="Slot is free")


def _hire(db, c):
    venue = db.get(Venue, c.get("venue_id"))
    if not venue or not venue.open_for_hire:  # V4
        return bus.reply("REFUSE", reason="This venue is not open for private hire")
    start, end = _dt(c["start"]), _dt(c["end"])
    guests = int(c.get("guests", 0))
    if end <= start:
        return bus.reply("REFUSE", reason="End time must be after start time")
    if start <= now():
        return bus.reply("REFUSE", reason="The requested time is in the past")
    if guests < 1:
        return bus.reply("REFUSE", reason="Guest count must be at least 1")

    problem = None
    if guests > venue.capacity:  # V5
        problem = f"{guests} guests exceed capacity ({venue.capacity})"
    else:
        clash = _clash(_busy(db, venue.id), start, end)
        if clash:
            problem = f"Venue is not free: clashes with {clash}"
    if problem:
        slots = [] if guests > venue.capacity else _alt_slots(db, venue.id, start, end)
        venues = _alt_venues(db, start, end, guests, venue.id, only_hire=True)
        _bump(db, "hires_countered")
        if slots or venues:
            return bus.reply("PROPOSE", reason=problem,
                             alternatives={"slots": slots, "venues": venues})
        return bus.reply("REFUSE", reason=problem + ". No alternatives are available.")

    price, hours, weekend = _price(venue, start, end)  # V6
    note = " (weekend rate x1.25)" if weekend else ""
    if hours > AUTO_MAX_HOURS or guests > ESCALATE_FILL * venue.capacity:  # V7
        _bump(db, "hires_escalated")
        return bus.reply("INFORM", status="pending", price=price,
                         reason=f"Needs admin approval (long booking or more than 80% of capacity){note}")
    _bump(db, "hires_confirmed")
    return bus.reply("AGREE", status="confirmed", price=price,
                     reason=f"Routine request, auto-confirmed{note}")


def handle(db, msg):
    c = msg["content"]
    _bump(db, "messages_handled")
    action = c.get("action")
    if action == "check_slot":
        return _check_slot(db, c)
    if action == "hire":
        return _hire(db, c)
    return bus.reply("FAILURE", reason=f"VenueAgent does not understand '{action}'")


bus.register(NAME, handle)