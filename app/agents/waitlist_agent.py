"""WaitlistAgent
GOAL: pass freed seats to waiting customers fairly.

DECISION RULES
W1. Refuse to join the waitlist if enough seats are still available
    (the person should just book) or if they are already waiting.
W2. When seats are freed, offer them to the earliest person whose quantity fits
    (first come, first served; someone who needs too many is skipped, not removed).
W3. A seat offer is a HOLD: seats are reserved (via BookingAgent) for 15 minutes.
W4. TIMER: every 30 seconds the agent sweeps. Any hold past its time is expired,
    its seats are released, and they are re-offered to the next person.
W5. The person claims a hold before it expires, otherwise they lose it.
"""
import os
import asyncio
from datetime import timedelta
from .. import bus
from ..models import Waitlist, TicketCategory, Event
from ..database import SessionLocal
from ..timeutil import now

NAME = "WaitlistAgent"
HOLD_MINUTES = int(os.getenv("HOLD_MINUTES", "15"))


def _bump(db, key, n=1):
    bus.set_state(db, NAME, key, bus.get_state(db, NAME, key, 0) + n)


def _join(db, c):
    cat = db.get(TicketCategory, c["category_id"])
    qty = int(c.get("quantity", 1))
    if cat is None:
        return bus.reply("REFUSE", reason="Unknown ticket category")
    db.refresh(cat)
    if cat.quantity - cat.sold >= qty:  # W1
        return bus.reply("REFUSE", reason="Tickets are still available, please book directly")
    exists = db.query(Waitlist).filter(Waitlist.user_id == c["user_id"],
                                       Waitlist.category_id == cat.id,
                                       Waitlist.status.in_(["waiting", "offered"])).first()
    if exists:
        return bus.reply("REFUSE", reason="You are already on the waitlist for this category")
    w = Waitlist(event_id=cat.event_id, category_id=cat.id, user_id=c["user_id"],
                 quantity=qty, status="waiting", created=now())
    db.add(w)
    db.flush()
    position = db.query(Waitlist).filter(Waitlist.category_id == cat.id,
                                         Waitlist.status == "waiting",
                                         Waitlist.id <= w.id).count()
    _bump(db, "joined")
    return bus.reply("AGREE", waitlist_id=w.id, position=position,
                     reason=f"You are number {position} on the waitlist")


def _offer_next(db, category_id, conv):
    offered = 0
    while True:
        cat = db.get(TicketCategory, category_id)
        db.refresh(cat)
        available = cat.quantity - cat.sold
        if available < 1:
            break
        waiting = db.query(Waitlist).filter(Waitlist.category_id == category_id,
                                            Waitlist.status == "waiting"
                                            ).order_by(Waitlist.created, Waitlist.id).all()
        entry = next((w for w in waiting if w.quantity <= available), None)  # W2
        if entry is None:
            break
        r = bus.send(db, NAME, "BookingAgent", "REQUEST",
                     {"action": "reserve", "category_id": category_id, "quantity": entry.quantity}, conv)
        if r["performative"] != "AGREE":
            break
        entry.status = "offered"  # W3
        entry.hold_expires_at = now() + timedelta(minutes=HOLD_MINUTES)
        db.flush()
        ev = db.get(Event, entry.event_id)
        bus.inform_notification(db, NAME, {"type": "hold_offered", "user_id": entry.user_id,
                                           "event": ev.title,
                                           "expires": f"{entry.hold_expires_at:%H:%M}"}, conv)
        offered += 1
        _bump(db, "holds_offered")
    return offered


def _claim(db, msg):
    c, conv = msg["content"], msg["conversation_id"]
    w = db.get(Waitlist, c["waitlist_id"])
    if not w or w.user_id != c["user_id"] or w.status != "offered":
        return bus.reply("REFUSE", reason="You have no active seat hold")
    if w.hold_expires_at < now():
        return bus.reply("REFUSE", reason="Your hold has expired")  # W5
    r = bus.send(db, NAME, "BookingAgent", "REQUEST",
                 {"action": "book", "user_id": w.user_id, "event_id": w.event_id,
                  "category_id": w.category_id, "quantity": w.quantity, "from_hold": True}, conv)
    if r["performative"] == "AGREE":
        w.status = "claimed"
        db.flush()
        _bump(db, "holds_claimed")
    return r


def _sweep(db, conv):
    """W4: proactive. Expire overdue holds, release seats, re-offer."""
    overdue = db.query(Waitlist).filter(Waitlist.status == "offered",
                                        Waitlist.hold_expires_at < now()).all()
    categories = set()
    for w in overdue:
        w.status = "expired"
        db.flush()
        bus.send(db, NAME, "BookingAgent", "REQUEST",
                 {"action": "release", "category_id": w.category_id, "quantity": w.quantity}, conv)
        ev = db.get(Event, w.event_id)
        bus.inform_notification(db, NAME, {"type": "hold_expired", "user_id": w.user_id,
                                           "event": ev.title}, conv)
        categories.add(w.category_id)
    reoffered = sum(_offer_next(db, cid, conv) for cid in categories)
    _bump(db, "holds_expired", len(overdue))
    bus.set_state(db, NAME, "last_sweep", now().isoformat())
    return bus.reply("INFORM", expired=len(overdue), reoffered=reoffered)


def _event_cancelled(db, c, conv):
    entries = db.query(Waitlist).filter(Waitlist.event_id == c["event_id"],
                                        Waitlist.status.in_(["waiting", "offered"])).all()
    ev = db.get(Event, c["event_id"])
    for w in entries:
        w.status = "expired"
        bus.inform_notification(db, NAME, {"type": "waitlist_closed", "user_id": w.user_id,
                                           "event": ev.title}, conv)
    db.flush()
    return bus.reply("INFORM", closed=len(entries))


def handle(db, msg):
    c = msg["content"]
    action = c.get("action")
    if action == "join":
        return _join(db, c)
    if action == "seats_freed":
        return bus.reply("INFORM", offered=_offer_next(db, c["category_id"], msg["conversation_id"]))
    if action == "claim":
        return _claim(db, msg)
    if action == "sweep":
        return _sweep(db, msg["conversation_id"])
    if action == "event_cancelled":
        return _event_cancelled(db, c, msg["conversation_id"])
    return bus.reply("FAILURE", reason=f"WaitlistAgent does not understand '{action}'")


bus.register(NAME, handle)


# ---------- The timer (proactive behaviour, no user action needed) ----------
def _sweep_once():
    db = SessionLocal()
    try:
        due = db.query(Waitlist).filter(Waitlist.status == "offered",
                                        Waitlist.hold_expires_at < now()).count()
        if due:  # only log a message when there is something to do
            bus.send(db, "Scheduler", NAME, "REQUEST", {"action": "sweep"})
            db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


async def sweep_loop(interval=30):
    while True:
        await asyncio.sleep(interval)
        await asyncio.to_thread(_sweep_once)