from datetime import datetime
from urllib.parse import urlencode
from fastapi import APIRouter, Depends, Form, Request, HTTPException
from sqlalchemy import or_
from . import bus
from .agents.booking_agent import refund_amount
from .common import render, flash, go, qr_data_uri, CATEGORIES
from .database import get_db
from .models import (User, Venue, Event, TicketCategory, Booking, Ticket,
                     HireRequest, Waitlist, Notification)
from .security import (hash_password, verify_password, get_current_user,
                       require_role, verify_csrf)
from .timeutil import now

router = APIRouter()
login_required = require_role("attendee", "staff", "admin")


# ---------- accounts ----------
@router.get("/")
def home():
    return go("/events")


@router.get("/register")
def register_page(request: Request, db=Depends(get_db), user=Depends(get_current_user)):
    return render(request, "register.html", db, user)


@router.post("/register", dependencies=[Depends(verify_csrf)])
def register(request: Request, name: str = Form(...), email: str = Form(...),
             password: str = Form(...), db=Depends(get_db)):
    email = email.strip().lower()
    if len(password) < 6:
        flash(request, "Password must be at least 6 characters", "danger")
        return go("/register")
    if db.query(User).filter_by(email=email).first():
        flash(request, "That email is already registered", "danger")
        return go("/register")
    u = User(name=name.strip(), email=email, password_hash=hash_password(password), role="attendee")
    db.add(u)
    db.commit()
    request.session["user_id"] = u.id
    return go("/events")


@router.get("/login")
def login_page(request: Request, db=Depends(get_db), user=Depends(get_current_user)):
    return render(request, "login.html", db, user)


@router.post("/login", dependencies=[Depends(verify_csrf)])
def login(request: Request, email: str = Form(...), password: str = Form(...), db=Depends(get_db)):
    u = db.query(User).filter_by(email=email.strip().lower()).first()
    if not u or not verify_password(password, u.password_hash):
        flash(request, "Wrong email or password", "danger")
        return go("/login")
    request.session["user_id"] = u.id
    if u.role == "admin":
        return go("/admin/dashboard")
    if u.role == "staff":
        return go("/staff/scanner")
    return go("/events")


@router.post("/logout", dependencies=[Depends(verify_csrf)])
def logout(request: Request):
    request.session.clear()
    return go("/login")


# ---------- browsing ----------
@router.get("/events")
def events(request: Request, q: str = "", category: str = "",
           db=Depends(get_db), user=Depends(get_current_user)):
    query = db.query(Event).filter(Event.status == "published", Event.end >= now())
    if q:
        query = query.filter(or_(Event.title.ilike(f"%{q}%"), Event.description.ilike(f"%{q}%")))
    if category:
        query = query.filter(Event.category == category)
    cards = []
    for e in query.order_by(Event.start).all():
        cats = db.query(TicketCategory).filter_by(event_id=e.id).all()
        cards.append(dict(event=e, venue=db.get(Venue, e.venue_id),
                          left=sum(c.quantity - c.sold for c in cats),
                          from_price=min((c.price for c in cats), default=0)))
    return render(request, "events.html", db, user, cards=cards, q=q,
                  category=category, categories=CATEGORIES)


@router.get("/events/{event_id}")
def event_page(event_id: int, request: Request, db=Depends(get_db), user=Depends(get_current_user)):
    ev = db.get(Event, event_id)
    if not ev or (ev.status == "draft" and not (user and user.role == "admin")):
        raise HTTPException(404, "Event not found")
    cats = db.query(TicketCategory).filter_by(event_id=ev.id).all()
    return render(request, "event.html", db, user, ev=ev, venue=db.get(Venue, ev.venue_id), cats=cats)


# ---------- booking ----------
@router.post("/events/{event_id}/book", dependencies=[Depends(verify_csrf)])
def book(event_id: int, request: Request, category_id: int = Form(...), quantity: int = Form(...),
         db=Depends(get_db), user=Depends(login_required)):
    r = bus.send(db, "WebApp", "BookingAgent", "REQUEST",
                 {"action": "book", "user_id": user.id, "event_id": event_id,
                  "category_id": category_id, "quantity": quantity})
    db.commit()
    if r["performative"] == "AGREE":
        flash(request, f"Booked! Simulated payment reference {r['content']['payment_ref']}", "success")
        return go(f"/bookings/{r['content']['booking_id']}/tickets")
    msg = r["content"].get("reason", "Booking failed")
    if r["content"].get("suggestion") == "waitlist":
        msg += ". You can join the waitlist."
    flash(request, msg, "danger")
    return go(f"/events/{event_id}")


@router.get("/bookings/{booking_id}/tickets")
def tickets(booking_id: int, request: Request, db=Depends(get_db), user=Depends(login_required)):
    b = db.get(Booking, booking_id)
    if not b or (b.user_id != user.id and user.role != "admin"):
        raise HTTPException(404, "Booking not found")
    items = [dict(t=t, qr=qr_data_uri(t.code))
             for t in db.query(Ticket).filter_by(booking_id=b.id).all()]
    return render(request, "tickets.html", db, user, b=b, ev=db.get(Event, b.event_id), items=items)


@router.post("/bookings/{booking_id}/cancel", dependencies=[Depends(verify_csrf)])
def cancel(booking_id: int, request: Request, db=Depends(get_db), user=Depends(login_required)):
    r = bus.send(db, "WebApp", "BookingAgent", "REQUEST",
                 {"action": "cancel", "booking_id": booking_id, "user_id": user.id})
    db.commit()
    flash(request, r["content"].get("reason", "Done"),
          "success" if r["performative"] == "AGREE" else "danger")
    return go("/my")


# ---------- my account ----------
@router.get("/my")
def my(request: Request, db=Depends(get_db), user=Depends(login_required)):
    bookings = []
    for b in db.query(Booking).filter_by(user_id=user.id).order_by(Booking.id.desc()).all():
        ev = db.get(Event, b.event_id)
        bookings.append(dict(b=b, ev=ev, refund=refund_amount(b.total, ev.start),
                             can_cancel=b.status == "confirmed" and ev.start > now()))
    waits = []
    for w in db.query(Waitlist).filter(Waitlist.user_id == user.id,
                                       Waitlist.status.in_(["waiting", "offered"])).all():
        secs = None
        if w.status == "offered" and w.hold_expires_at:
            secs = max(0, int((w.hold_expires_at - now()).total_seconds()))
        waits.append(dict(w=w, ev=db.get(Event, w.event_id), cat=db.get(TicketCategory, w.category_id), secs=secs))
    hires = [dict(h=h, venue=db.get(Venue, h.venue_id))
             for h in db.query(HireRequest).filter_by(user_id=user.id).order_by(HireRequest.id.desc()).all()]
    notes = db.query(Notification).filter_by(user_id=user.id).order_by(Notification.id.desc()).limit(30).all()
    fresh = {n.id for n in notes if not n.is_read}
    for n in notes:
        n.is_read = True
    db.commit()
    return render(request, "my.html", db, user, bookings=bookings, waits=waits,
                  hires=hires, notes=notes, fresh=fresh)


@router.post("/events/{event_id}/waitlist", dependencies=[Depends(verify_csrf)])
def join_waitlist(event_id: int, request: Request, category_id: int = Form(...), quantity: int = Form(1),
                  db=Depends(get_db), user=Depends(login_required)):
    r = bus.send(db, "WebApp", "WaitlistAgent", "REQUEST",
                 {"action": "join", "user_id": user.id, "event_id": event_id,
                  "category_id": category_id, "quantity": quantity})
    db.commit()
    flash(request, r["content"].get("reason", "Done"),
          "success" if r["performative"] == "AGREE" else "warning")
    return go("/my")


@router.post("/waitlist/{wid}/claim", dependencies=[Depends(verify_csrf)])
def claim(wid: int, request: Request, db=Depends(get_db), user=Depends(login_required)):
    r = bus.send(db, "WebApp", "WaitlistAgent", "REQUEST",
                 {"action": "claim", "waitlist_id": wid, "user_id": user.id})
    db.commit()
    if r["performative"] == "AGREE":
        flash(request, "Seats claimed. Simulated payment done.", "success")
        return go(f"/bookings/{r['content']['booking_id']}/tickets")
    flash(request, r["content"].get("reason", "Could not claim"), "danger")
    return go("/my")


# ---------- private venue hire ----------
def _hire_form(request, db, user, f, proposal=None):
    venues = db.query(Venue).filter_by(open_for_hire=True).all()
    return render(request, "hire.html", db, user, venues=venues, f=f, proposal=proposal)


@router.get("/hire")
def hire_page(request: Request, venue_id: int = 0, date: str = "", start: str = "", end: str = "",
              guests: int = 10, purpose: str = "", db=Depends(get_db), user=Depends(login_required)):
    f = dict(venue_id=venue_id, date=date, start=start, end=end, guests=guests, purpose=purpose)
    return _hire_form(request, db, user, f)


@router.post("/hire", dependencies=[Depends(verify_csrf)])
def hire_submit(request: Request, venue_id: int = Form(...), date: str = Form(...),
                start: str = Form(...), end: str = Form(...), guests: int = Form(...),
                purpose: str = Form(""), db=Depends(get_db), user=Depends(login_required)):
    f = dict(venue_id=venue_id, date=date, start=start, end=end, guests=guests, purpose=purpose)
    try:
        s = datetime.fromisoformat(f"{date}T{start}")
        e = datetime.fromisoformat(f"{date}T{end}")
    except ValueError:
        flash(request, "Enter a valid date and times", "danger")
        return _hire_form(request, db, user, f)

    r = bus.send(db, "WebApp", "VenueAgent", "REQUEST",
                 {"action": "hire", "venue_id": venue_id, "start": s, "end": e, "guests": guests})
    perf, c, conv = r["performative"], r["content"], r["conversation_id"]

    if perf in ("AGREE", "INFORM"):
        db.add(HireRequest(user_id=user.id, venue_id=venue_id, start=s, end=e, purpose=purpose,
                           guests=guests, price=c["price"], status=c["status"], note=c["reason"]))
        db.flush()
        if perf == "AGREE":
            bus.inform_notification(db, "VenueAgent", {"type": "hire_confirmed", "user_id": user.id,
                                                       "price": f'{c["price"]:.2f}'}, conv)
            flash(request, f'Confirmed! Price LKR {c["price"]:.2f}. {c["reason"]}', "success")
        else:
            bus.inform_notification(db, "VenueAgent", {"type": "hire_pending", "user_id": user.id}, conv)
            bus.inform_notification(db, "VenueAgent", {"type": "hire_needs_review", "audience": "admins"}, conv)
            flash(request, f'Quoted LKR {c["price"]:.2f}. {c["reason"]}', "warning")
        db.commit()
        return go("/my")

    db.commit()
    flash(request, c["reason"], "warning" if perf == "PROPOSE" else "danger")
    proposal = None
    if perf == "PROPOSE":
        proposal = []
        alts = c.get("alternatives", {})
        for sl in alts.get("slots", []):
            a, b = datetime.fromisoformat(sl["start"]), datetime.fromisoformat(sl["end"])
            q = urlencode(dict(venue_id=venue_id, date=a.date().isoformat(), start=a.strftime("%H:%M"),
                               end=b.strftime("%H:%M"), guests=guests, purpose=purpose))
            proposal.append(dict(label=f"Same venue: {a:%a %d %b} {a:%H:%M}-{b:%H:%M}", url="/hire?" + q))
        for v in alts.get("venues", []):
            q = urlencode(dict(venue_id=v["venue_id"], date=date, start=start, end=end,
                               guests=guests, purpose=purpose))
            proposal.append(dict(label=f'{v["name"]} (capacity {v["capacity"]}), same time, LKR {v["price"]:.2f}',
                                 url="/hire?" + q))
    return _hire_form(request, db, user, f, proposal)