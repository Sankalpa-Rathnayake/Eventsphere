from datetime import datetime
from fastapi import APIRouter, Depends, Form, Request, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import func
from . import bus
from .common import render, flash, go, fmt_alts, CATEGORIES
from .database import get_db
from .models import (Venue, Event, TicketCategory, Booking, Ticket,
                     HireRequest, Message, AgentState)
from .security import require_role, verify_csrf

router = APIRouter()
admin_only = require_role("admin")
staff_or_admin = require_role("staff", "admin")


# ================= VENUES =================
@router.get("/admin/venues")
def venues(request: Request, db=Depends(get_db), user=Depends(admin_only)):
    return render(request, "admin_venues.html", db, user, venues=db.query(Venue).all(), v=None)


@router.get("/admin/venues/{vid}/edit")
def venue_edit(vid: int, request: Request, db=Depends(get_db), user=Depends(admin_only)):
    v = db.get(Venue, vid)
    if not v:
        raise HTTPException(404, "Venue not found")
    return render(request, "admin_venues.html", db, user, venues=db.query(Venue).all(), v=v)


async def _save_venue(request, db, vid=None):
    f = await request.form()
    back = f"/admin/venues/{vid}/edit" if vid else "/admin/venues"
    try:
        cap = int(f.get("capacity"))
        rate = float(f.get("hourly_rate") or 0)
    except (TypeError, ValueError):
        flash(request, "Capacity and hourly rate must be numbers", "danger")
        return go(back)
    name = (f.get("name") or "").strip()
    if not name or cap < 1 or rate < 0:
        flash(request, "Name is required, capacity must be at least 1", "danger")
        return go(back)
    v = db.get(Venue, vid) if vid else Venue()
    v.name, v.location, v.capacity = name, f.get("location") or "", cap
    v.description, v.image, v.hourly_rate = f.get("description") or "", f.get("image") or "", rate
    v.open_for_hire = f.get("open_for_hire") == "on"
    if not vid:
        db.add(v)
    db.commit()
    flash(request, "Venue saved", "success")
    return go("/admin/venues")


@router.post("/admin/venues", dependencies=[Depends(verify_csrf)])
async def venue_create(request: Request, db=Depends(get_db), user=Depends(admin_only)):
    return await _save_venue(request, db)


@router.post("/admin/venues/{vid}", dependencies=[Depends(verify_csrf)])
async def venue_update(vid: int, request: Request, db=Depends(get_db), user=Depends(admin_only)):
    return await _save_venue(request, db, vid)


# ================= EVENTS =================
def _blank():
    return dict(title="", category="Concert", venue_id=0, start="", end="", description="",
                rows=[dict(id=0, name="General", price="", qty="", sold=0),
                      dict(id=0, name="", price="", qty="", sold=0),
                      dict(id=0, name="", price="", qty="", sold=0)])


def _dict_from_event(db, ev):
    rows = [dict(id=c.id, name=c.name, price=c.price, qty=c.quantity, sold=c.sold)
            for c in db.query(TicketCategory).filter_by(event_id=ev.id).all()]
    while len(rows) < 3:
        rows.append(dict(id=0, name="", price="", qty="", sold=0))
    return dict(title=ev.title, category=ev.category, venue_id=ev.venue_id,
                start=ev.start.strftime("%Y-%m-%dT%H:%M"), end=ev.end.strftime("%Y-%m-%dT%H:%M"),
                description=ev.description, rows=rows)


@router.get("/admin/events")
def events(request: Request, db=Depends(get_db), user=Depends(admin_only)):
    rows = []
    for e in db.query(Event).order_by(Event.start.desc()).all():
        cats = db.query(TicketCategory).filter_by(event_id=e.id).all()
        rows.append(dict(e=e, venue=db.get(Venue, e.venue_id),
                         sold=sum(c.sold for c in cats), total=sum(c.quantity for c in cats)))
    return render(request, "admin_events.html", db, user, rows=rows)


@router.get("/admin/events/new")
def event_new(request: Request, db=Depends(get_db), user=Depends(admin_only)):
    return render(request, "admin_event_form.html", db, user, d=_blank(), ev=None,
                  post_url="/admin/events", venues=db.query(Venue).all(), categories=CATEGORIES)


@router.get("/admin/events/{eid}/edit")
def event_edit(eid: int, request: Request, db=Depends(get_db), user=Depends(admin_only)):
    ev = db.get(Event, eid)
    if not ev:
        raise HTTPException(404, "Event not found")
    return render(request, "admin_event_form.html", db, user, d=_dict_from_event(db, ev), ev=ev,
                  post_url=f"/admin/events/{eid}", venues=db.query(Venue).all(), categories=CATEGORIES)


async def _save_event(request, db, user, ev_id=None):
    f = await request.form()
    ev = db.get(Event, ev_id) if ev_id else None
    existing = {c.id: c for c in db.query(TicketCategory).filter_by(event_id=ev.id)} if ev else {}
    rows = []
    for i in (1, 2, 3):
        cid = int(f.get(f"cat_id_{i}") or 0)
        rows.append(dict(id=cid, name=(f.get(f"name_{i}") or "").strip(),
                         price=f.get(f"price_{i}") or "", qty=f.get(f"qty_{i}") or "",
                         sold=existing[cid].sold if cid in existing else 0))
    d = dict(title=(f.get("title") or "").strip(), category=f.get("category") or "Concert",
             venue_id=int(f.get("venue_id") or 0), start=f.get("start") or "", end=f.get("end") or "",
             description=f.get("description") or "", rows=rows)

    errors, active = [], []
    venue = db.get(Venue, d["venue_id"])
    if ev and ev.status == "cancelled":
        errors.append("A cancelled event cannot be edited")
    if not d["title"]:
        errors.append("Title is required")
    if not venue:
        errors.append("Choose a venue")
    try:
        start, end = datetime.fromisoformat(d["start"]), datetime.fromisoformat(d["end"])
    except ValueError:
        start = end = None
        errors.append("Enter valid start and end times")
    for r in rows:
        if not r["name"]:
            continue
        try:
            r["price"], r["qty"] = float(r["price"] or 0), int(r["qty"])
        except ValueError:
            errors.append(f'Check price and quantity for "{r["name"]}"')
            continue
        if r["qty"] < max(1, r["sold"]) or r["price"] < 0:
            errors.append(f'"{r["name"]}": quantity must be at least {max(1, r["sold"])}, price cannot be negative')
        active.append(r)
    if not active:
        errors.append("Add at least one ticket category")

    if not errors:  # ask VenueAgent (by message) about the slot and capacity
        r = bus.send(db, "WebApp", "VenueAgent", "REQUEST",
                     {"action": "check_slot", "venue_id": venue.id, "start": start, "end": end,
                      "tickets_total": sum(x["qty"] for x in active),
                      "exclude_event_id": ev.id if ev else None})
        if r["performative"] != "AGREE":
            errors.append("VenueAgent refused: " + r["content"]["reason"]
                          + fmt_alts(r["content"].get("alternatives")))
    if errors:
        db.commit()  # keep the agent message log
        for e in errors:
            flash(request, e, "danger")
        return render(request, "admin_event_form.html", db, user, d=d, ev=ev,
                      post_url=f"/admin/events/{ev_id}" if ev_id else "/admin/events",
                      venues=db.query(Venue).all(), categories=CATEGORIES)

    if ev is None:
        ev = Event(status="draft")
        db.add(ev)
    ev.title, ev.category, ev.venue_id = d["title"], d["category"], venue.id
    ev.start, ev.end, ev.description = start, end, d["description"]
    if f.get("action") == "publish":
        ev.status = "published"
    db.flush()
    for r in active:
        cat = existing.get(r["id"])
        if cat:
            cat.name, cat.price, cat.quantity = r["name"], r["price"], r["qty"]
        else:
            db.add(TicketCategory(event_id=ev.id, name=r["name"], price=r["price"], quantity=r["qty"]))
    db.commit()
    flash(request, f'Event saved as {ev.status}', "success")
    return go("/admin/events")


@router.post("/admin/events", dependencies=[Depends(verify_csrf)])
async def event_create(request: Request, db=Depends(get_db), user=Depends(admin_only)):
    return await _save_event(request, db, user)


@router.post("/admin/events/{eid}", dependencies=[Depends(verify_csrf)])
async def event_update(eid: int, request: Request, db=Depends(get_db), user=Depends(admin_only)):
    return await _save_event(request, db, user, eid)


@router.post("/admin/events/{eid}/publish", dependencies=[Depends(verify_csrf)])
def event_publish(eid: int, request: Request, db=Depends(get_db), user=Depends(admin_only)):
    ev = db.get(Event, eid)
    if ev and ev.status == "draft":
        ev.status = "published"
        db.commit()
        flash(request, "Event published", "success")
    return go("/admin/events")


@router.post("/admin/events/{eid}/cancel", dependencies=[Depends(verify_csrf)])
def event_cancel(eid: int, request: Request, db=Depends(get_db), user=Depends(admin_only)):
    r = bus.send(db, "WebApp", "BookingAgent", "REQUEST", {"action": "cancel_event", "event_id": eid})
    db.commit()
    flash(request, f'Event cancelled. {r["content"].get("refunded_bookings", 0)} booking(s) fully refunded.', "warning")
    return go("/admin/events")


# ================= HIRE APPROVALS =================
@router.get("/admin/hires")
def hires(request: Request, db=Depends(get_db), user=Depends(admin_only)):
    rows = [dict(h=h, venue=db.get(Venue, h.venue_id))
            for h in db.query(HireRequest).order_by(HireRequest.status.desc(), HireRequest.id.desc()).all()]
    return render(request, "admin_hires.html", db, user, rows=rows)


@router.post("/admin/hires/{hid}/{decision}", dependencies=[Depends(verify_csrf)])
def hire_decide(hid: int, decision: str, request: Request, note: str = Form(""),
                db=Depends(get_db), user=Depends(admin_only)):
    h = db.get(HireRequest, hid)
    if decision not in ("approve", "reject") or not h:
        raise HTTPException(404, "Not found")
    if h.status != "pending":
        flash(request, "That request was already decided", "warning")
        return go("/admin/hires")
    if decision == "approve":
        h.status = "confirmed"
        bus.inform_notification(db, "Admin", {"type": "hire_confirmed", "user_id": h.user_id,
                                              "price": f"{h.price:.2f}"})
    else:
        h.status, h.note = "rejected", note
        bus.inform_notification(db, "Admin", {"type": "hire_rejected", "user_id": h.user_id, "text": note})
    db.commit()
    flash(request, f"Request {h.status}", "success")
    return go("/admin/hires")


# ================= DASHBOARD =================
@router.get("/admin/dashboard")
def dashboard(request: Request, conv: str = "", db=Depends(get_db), user=Depends(admin_only)):
    rows = []
    for e in db.query(Event).order_by(Event.start.desc()).all():
        cats = db.query(TicketCategory).filter_by(event_id=e.id).all()
        venue = db.get(Venue, e.venue_id)
        revenue = db.query(func.coalesce(func.sum(Booking.total - Booking.refund), 0)
                           ).filter(Booking.event_id == e.id).scalar()
        rows.append(dict(e=e, capacity=venue.capacity if venue else 0,
                         total=sum(c.quantity for c in cats), sold=sum(c.sold for c in cats),
                         revenue=float(revenue or 0),
                         checked=db.query(Ticket).filter(Ticket.event_id == e.id, Ticket.status == "used").count()))
    q = db.query(Message)
    msgs = (q.filter(Message.conversation_id == conv).order_by(Message.id).all() if conv
            else q.order_by(Message.id.desc()).limit(150).all())
    states = db.query(AgentState).order_by(AgentState.agent, AgentState.key).all()
    return render(request, "dashboard.html", db, user, rows=rows, msgs=msgs, states=states, conv=conv,
                  labels=[r["e"].title for r in rows], sold_data=[r["sold"] for r in rows],
                  checked_data=[r["checked"] for r in rows])


# ================= STAFF SCANNER =================
@router.get("/staff/scanner")
def scanner(request: Request, db=Depends(get_db), user=Depends(staff_or_admin)):
    evs = db.query(Event).filter(Event.status == "published").order_by(Event.start).all()
    return render(request, "scanner.html", db, user, events=evs)


@router.post("/staff/scan", dependencies=[Depends(verify_csrf)])
def scan(code: str = Form(...), event_id: int = Form(...), db=Depends(get_db), user=Depends(staff_or_admin)):
    r = bus.send(db, "Scanner", "TicketAgent", "REQUEST", {"action": "scan", "code": code, "event_id": event_id})
    db.commit()
    return JSONResponse({"ok": r["performative"] == "AGREE", **r["content"]})