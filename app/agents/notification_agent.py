"""NotificationAgent
GOAL: tell the right person what affects them.

DECISION RULES
N1. Only reacts to INFORM messages (listener). Anything else -> FAILURE.
N2. Picks a message template by content["type"].
N3. Recipient is content["user_id"]; if content["audience"] == "admins",
    every admin is notified.
"""
from collections import defaultdict
from .. import bus
from ..models import Notification, User

NAME = "NotificationAgent"

TEMPLATES = {
    "booking_confirmed": "Booking confirmed: {quantity} ticket(s) for {event}. Total {total}. Payment ref {ref}.",
    "booking_cancelled": "Your booking for {event} was cancelled. Refund: {refund}.",
    "event_cancelled": "{event} has been cancelled. You receive a full refund of {refund}.",
    "hold_offered": "Good news! Seats opened up for {event}. They are held for you until {expires}. Claim them on your waitlist page.",
    "hold_expired": "Your seat hold for {event} expired and was offered to the next person.",
    "waitlist_closed": "{event} was cancelled, so your waitlist entry was closed.",
    "hire_confirmed": "Your venue hire request is confirmed. Price: {price}.",
    "hire_pending": "Your venue hire request needs admin approval. We will notify you.",
    "hire_rejected": "Your venue hire request was rejected. {text}",
    "hire_needs_review": "A venue hire request is waiting for your approval.",
}


def handle(db, msg):
    if msg["performative"] != "INFORM":  # N1
        return bus.reply("FAILURE", reason="NotificationAgent only accepts INFORM messages")
    c = msg["content"]
    text = TEMPLATES.get(c.get("type"), "{text}").format_map(defaultdict(str, c))  # N2
    if c.get("audience") == "admins":  # N3
        ids = [u.id for u in db.query(User).filter_by(role="admin").all()]
    else:
        ids = [c["user_id"]] if c.get("user_id") else []
    for uid in ids:
        db.add(Notification(user_id=uid, text=text))
    db.flush()
    bus.set_state(db, NAME, "sent_total", bus.get_state(db, NAME, "sent_total", 0) + len(ids))
    return bus.reply("INFORM", notified=ids)


bus.register(NAME, handle)