"""BookingAgent
GOAL: sell tickets fairly without overselling.

DECISION RULES
B1. Refuse a request for fewer than 1 or more than 6 tickets.
B2. Refuse if the event is not published (draft or cancelled) or has already started.
B3. Refuse if the buyer would hold more than 6 active tickets for this event.
B4. Refuse if not enough seats remain (the reason suggests the waitlist).
    Seats are taken with ONE atomic SQL statement, so two simultaneous
    buyers can never both get the last seat.
B5. Refund on cancellation: 100% if 7 or more days before the event,
    50% if 2 to 7 days, 0% if under 2 days. Event already started -> refuse.
B6. If an EVENT is cancelled, every confirmed booking gets a 100% refund.
B7. When a cancellation frees seats, ask WaitlistAgent to offer them.
"""
import secrets
from sqlalchemy import func
from .. import bus
from ..models import Event, TicketCategory, Booking, Ticket, User
from ..timeutil import now

NAME = "BookingAgent"
MAX_TICKETS = 6


def refund_amount(total, event_start, current=None):
    """Refund policy B5. Pure function so it is easy to test."""
    current = current or now()
    days = (event_start - current).total_seconds() / 86400
    if days >= 7:
        pct = 1.0
    elif days >= 2:
        pct = 0.5
    else:
        pct = 0.0
    return round(total * pct, 2)


def _bump(db, key, n=1):
    bus.set_state(db, NAME, key, bus.get_state(db, NAME, key, 0) + n)


def _take_seats(db, category_id, qty):
    """Atomic seat allocation. Returns True if the seats were taken."""
    n = db.query(TicketCategory).filter(
        TicketCategory.id == category_id,
        TicketCategory.sold + qty <= TicketCategory.quantity,
    ).update({TicketCategory.sold: TicketCategory.sold + qty}, synchronize_session="fetch")
    return n == 1


def _give_back(db, category_id, qty):
    db.query(TicketCategory).filter(TicketCategory.id == category_id).update(
        {TicketCategory.sold: TicketCategory.sold - qty}, synchronize_session="fetch")


def _book(db, msg):
    c, conv = msg["content"], msg["conversation_id"]
    qty = int(c.get("quantity", 0))
    from_hold = bool(c.get("from_hold"))
    if qty < 1 or qty > MAX_TICKETS:  # B1
        _bump(db, "requests_refused")
        return bus.reply("REFUSE", reason=f"You can book 1 to {MAX_TICKETS} tickets at a time")
    ev = db.get(Event, c["event_id"])
    cat = db.get(TicketCategory, c["category_id"])
    if not ev or ev.status != "published":  # B2
        _bump(db, "requests_refused")
        return bus.reply("REFUSE", reason="This event is not open for booking")
    if ev.start <= now():
        _bump(db, "requests_refused")
        return bus.reply("REFUSE", reason="This event has already started")
    if cat is None or cat.event_id != ev.id:
        return bus.reply("REFUSE", reason="Unknown ticket category")
    if not from_hold:
        held = db.query(func.count(Ticket.id)).join(Booking, Ticket.booking_id == Booking.id).filter(
            Booking.user_id == c["user_id"], Ticket.event_id == ev.id,
            Ticket.status != "cancelled").scalar() or 0
        if held + qty > MAX_TICKETS:  # B3
            _bump(db, "requests_refused")
            return bus.reply("REFUSE", reason=f"Limit is {MAX_TICKETS} tickets per person for one event "
                                              f"(you already have {held})")
        if not _take_seats(db, cat.id, qty):  # B4
            _bump(db, "requests_refused")
            return bus.reply("REFUSE", reason="Not enough tickets left in this category",
                             suggestion="waitlist")

    booking = Booking(user_id=c["user_id"], event_id=ev.id, status="confirmed",
                      total=round(cat.price * qty, 2),
                      payment_ref="PAY-" + secrets.token_hex(5).upper())  # simulated payment
    db.add(booking)
    db.flush()
    t = bus.send(db, NAME, "TicketAgent", "REQUEST",
                 {"action": "issue", "booking_id": booking.id, "event_id": ev.id,
                  "category_id": cat.id, "quantity": qty}, conv)
    if t["performative"] != "AGREE":
        return bus.reply("FAILURE", reason="Could not issue tickets")
    bus.inform_notification(db, NAME, {"type": "booking_confirmed", "user_id": booking.user_id,
                                       "event": ev.title, "quantity": qty,
                                       "total": f"{booking.total:.2f}", "ref": booking.payment_ref}, conv)
    _bump(db, "bookings_confirmed")
    return bus.reply("AGREE", booking_id=booking.id, payment_ref=booking.payment_ref,
                     total=booking.total, codes=t["content"]["codes"])


def _release_booking_seats(db, booking):
    rows = db.query(Ticket.category_id, func.count(Ticket.id)).filter(
        Ticket.booking_id == booking.id).group_by(Ticket.category_id).all()
    for category_id, n in rows:
        _give_back(db, category_id, n)
    db.flush()
    return rows


def _cancel(db, msg):
    c, conv = msg["content"], msg["conversation_id"]
    b = db.get(Booking, c["booking_id"])
    if not b or b.user_id != c.get("user_id"):
        return bus.reply("REFUSE", reason="Booking not found")
    if b.status != "confirmed":
        return bus.reply("REFUSE", reason="Booking is already cancelled")
    ev = db.get(Event, b.event_id)
    if ev.start <= now():
        return bus.reply("REFUSE", reason="The event has already started, so it cannot be cancelled")
    refund = refund_amount(b.total, ev.start)  # B5
    b.status = "cancelled"
    b.refund = refund
    db.flush()
    freed = _release_booking_seats(db, b)
    bus.send(db, NAME, "TicketAgent", "REQUEST", {"action": "cancel_tickets", "booking_id": b.id}, conv)
    bus.inform_notification(db, NAME, {"type": "booking_cancelled", "user_id": b.user_id,
                                       "event": ev.title, "refund": f"{refund:.2f}"}, conv)
    for category_id, _n in freed:  # B7
        bus.send(db, NAME, "WaitlistAgent", "REQUEST",
                 {"action": "seats_freed", "event_id": ev.id, "category_id": category_id}, conv)
    _bump(db, "bookings_cancelled")
    return bus.reply("AGREE", refund=refund, reason=f"Refund of {refund:.2f} per the refund policy")


def _cancel_event(db, msg):
    c, conv = msg["content"], msg["conversation_id"]
    ev = db.get(Event, c["event_id"])
    if not ev:
        return bus.reply("REFUSE", reason="Event not found")
    ev.status = "cancelled"
    db.flush()
    count = 0
    for b in db.query(Booking).filter(Booking.event_id == ev.id, Booking.status == "confirmed").all():
        b.status = "cancelled"  # B6
        b.refund = b.total
        db.flush()
        bus.send(db, NAME, "TicketAgent", "REQUEST", {"action": "cancel_tickets", "booking_id": b.id}, conv)
        bus.inform_notification(db, NAME, {"type": "event_cancelled", "user_id": b.user_id,
                                           "event": ev.title, "refund": f"{b.total:.2f}"}, conv)
        count += 1
    bus.send(db, NAME, "WaitlistAgent", "REQUEST", {"action": "event_cancelled", "event_id": ev.id}, conv)
    _bump(db, "events_cancelled")
    return bus.reply("AGREE", refunded_bookings=count)


def handle(db, msg):
    action = msg["content"].get("action")
    if action == "book":
        return _book(db, msg)
    if action == "cancel":
        return _cancel(db, msg)
    if action == "cancel_event":
        return _cancel_event(db, msg)
    if action == "reserve":  # used by WaitlistAgent to hold seats
        ok = _take_seats(db, msg["content"]["category_id"], int(msg["content"]["quantity"]))
        return bus.reply("AGREE" if ok else "REFUSE", reason="" if ok else "Seats no longer available")
    if action == "release":  # used by WaitlistAgent when a hold expires
        _give_back(db, msg["content"]["category_id"], int(msg["content"]["quantity"]))
        return bus.reply("AGREE")
    return bus.reply("FAILURE", reason=f"BookingAgent does not understand '{action}'")


bus.register(NAME, handle)