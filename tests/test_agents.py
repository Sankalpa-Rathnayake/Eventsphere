from datetime import timedelta
from app import bus, models
from app.agents.booking_agent import refund_amount
from app.timeutil import now


def book(db, w, user, qty=1):
    return bus.send(db, "Test", "BookingAgent", "REQUEST",
                    {"action": "book", "user_id": user.id, "event_id": w["event"].id,
                     "category_id": w["cat"].id, "quantity": qty})


def scan(db, w, code, event_id=None):
    return bus.send(db, "Test", "TicketAgent", "REQUEST",
                    {"action": "scan", "code": code, "event_id": event_id or w["event"].id})


def make_event_soon(db, w):
    w["event"].start = now() + timedelta(hours=1)
    w["event"].end = now() + timedelta(hours=4)
    db.flush()


# ---- BookingAgent ----
def test_overbooking_last_seats_sold_once(db, world):
    assert book(db, world, world["u1"], 2)["performative"] == "AGREE"
    r = book(db, world, world["u2"], 1)
    assert r["performative"] == "REFUSE"
    assert r["content"]["suggestion"] == "waitlist"
    db.refresh(world["cat"])
    assert world["cat"].sold == 2


def test_booking_agent_refuses_more_than_6(db, world):
    r = book(db, world, world["u1"], 7)
    assert r["performative"] == "REFUSE"
    assert "6" in r["content"]["reason"]


def test_refund_policy():
    start = now() + timedelta(days=10)
    assert refund_amount(100, start) == 100
    assert refund_amount(100, now() + timedelta(days=3)) == 50
    assert refund_amount(100, now() + timedelta(days=1)) == 0


def test_cancelled_event_gives_full_refund(db, world):
    r = book(db, world, world["u1"], 1)
    bus.send(db, "Test", "BookingAgent", "REQUEST",
             {"action": "cancel_event", "event_id": world["event"].id})
    b = db.get(models.Booking, r["content"]["booking_id"])
    assert b.status == "cancelled" and b.refund == b.total


# ---- TicketAgent ----
def test_double_checkin_rejected(db, world):
    make_event_soon(db, world)
    code = book(db, world, world["u1"], 1)["content"]["codes"][0]
    assert scan(db, world, code)["performative"] == "AGREE"
    second = scan(db, world, code)
    assert second["performative"] == "REFUSE"
    assert second["content"]["result"] == "already_used"


def test_wrong_event_rejected(db, world):
    make_event_soon(db, world)
    code = book(db, world, world["u1"], 1)["content"]["codes"][0]
    r = scan(db, world, code, event_id=world["event"].id + 99)
    assert r["content"]["result"] == "wrong_event"


def test_cancelled_ticket_rejected(db, world):
    make_event_soon(db, world)
    r = book(db, world, world["u1"], 1)
    code = r["content"]["codes"][0]
    # event is within 2 days, so refund is 0 but cancellation is still allowed
    bus.send(db, "Test", "BookingAgent", "REQUEST",
             {"action": "cancel", "booking_id": r["content"]["booking_id"], "user_id": world["u1"].id})
    assert scan(db, world, code)["content"]["result"] == "cancelled"


# ---- VenueAgent ----
def check(db, w, start, end, total=10):
    return bus.send(db, "Test", "VenueAgent", "REQUEST",
                    {"action": "check_slot", "venue_id": w["venue"].id, "start": start,
                     "end": end, "tickets_total": total})


def test_venue_refuses_overlap_and_buffer(db, world):
    ev = world["event"]
    assert check(db, world, ev.start, ev.end)["performative"] == "REFUSE"
    # starts 10 minutes after the event ends: blocked by the 30-minute buffer
    r = check(db, world, ev.end + timedelta(minutes=10), ev.end + timedelta(hours=2))
    assert r["performative"] == "REFUSE"
    assert "Clashes" in r["content"]["reason"]


def test_venue_proposes_alternatives_for_hire(db, world):
    ev = world["event"]
    r = bus.send(db, "Test", "VenueAgent", "REQUEST",
                 {"action": "hire", "venue_id": world["venue"].id, "start": ev.start,
                  "end": ev.start + timedelta(hours=2), "guests": 10})
    assert r["performative"] == "PROPOSE"
    alts = r["content"]["alternatives"]
    assert alts["slots"] or alts["venues"]


def test_venue_auto_confirms_routine_hire(db, world):
    start = (now() + timedelta(days=30)).replace(hour=14, minute=0, second=0, microsecond=0)
    r = bus.send(db, "Test", "VenueAgent", "REQUEST",
                 {"action": "hire", "venue_id": world["venue"].id, "start": start,
                  "end": start + timedelta(hours=2), "guests": 10})
    assert r["performative"] == "AGREE"
    assert r["content"]["price"] in (2000.0, 2500.0)  # 2500 if weekend rate


# ---- WaitlistAgent ----
def test_waitlist_offer_then_expiry_reoffers(db, world):
    b = book(db, world, world["u1"], 2)
    j = bus.send(db, "Test", "WaitlistAgent", "REQUEST",
                 {"action": "join", "user_id": world["u2"].id, "event_id": world["event"].id,
                  "category_id": world["cat"].id, "quantity": 1})
    assert j["performative"] == "AGREE"
    bus.send(db, "Test", "BookingAgent", "REQUEST",
             {"action": "cancel", "booking_id": b["content"]["booking_id"], "user_id": world["u1"].id})
    w = db.query(models.Waitlist).first()
    assert w.status == "offered"
    w.hold_expires_at = now() - timedelta(minutes=1)
    db.flush()
    r = bus.send(db, "Scheduler", "WaitlistAgent", "REQUEST", {"action": "sweep"})
    assert r["content"]["expired"] == 1
    assert w.status == "expired"
    db.refresh(world["cat"])
    assert world["cat"].sold == 0  # seats went back on sale


# ---- NotificationAgent ----
def test_notification_agent_creates_notification(db, world):
    bus.inform_notification(db, "Test", {"type": "booking_cancelled", "user_id": world["u1"].id,
                                         "event": "Concert", "refund": "100"})
    n = db.query(models.Notification).filter_by(user_id=world["u1"].id).first()
    assert "Concert" in n.text


def test_notification_agent_rejects_non_inform(db, world):
    r = bus.send(db, "Test", "NotificationAgent", "REQUEST", {"user_id": world["u1"].id})
    assert r["performative"] == "FAILURE"


# ---- Traceability ----
def test_messages_share_conversation_id(db, world):
    r = book(db, world, world["u1"], 1)
    conv = r["conversation_id"]
    rows = db.query(models.Message).filter_by(conversation_id=conv).all()
    agents_seen = {m.receiver for m in rows}
    assert {"BookingAgent", "TicketAgent", "NotificationAgent"} <= agents_seen