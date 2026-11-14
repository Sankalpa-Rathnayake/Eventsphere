from datetime import timedelta
from .database import engine, Base, SessionLocal
from . import models
from .security import hash_password
from .timeutil import now


def seed():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        for name, email, pw, role in [
            ("Admin", "admin@eventsphere.com", "Admin@123", "admin"),
            ("Gate Staff", "staff@eventsphere.com", "Staff@123", "staff"),
            ("Attendee", "attendee@eventsphere.com", "Attendee@123", "attendee"),
        ]:
            if not db.query(models.User).filter_by(email=email).first():
                db.add(models.User(name=name, email=email, password_hash=hash_password(pw), role=role))
        if not db.query(models.Venue).first():
            db.add_all([
                models.Venue(name="Main Auditorium", location="Block A", capacity=300,
                             description="Large hall for concerts and conferences",
                             hourly_rate=5000, open_for_hire=True),
                models.Venue(name="Seminar Room 1", location="Block B", capacity=60,
                             description="Small room for workshops", hourly_rate=1500, open_for_hire=True),
                models.Venue(name="Open Courtyard", location="Central", capacity=150,
                             description="Outdoor space", hourly_rate=2500, open_for_hire=True),
            ])
        db.commit()
        if not db.query(models.Event).first():
            v = db.query(models.Venue).order_by(models.Venue.id).all()
            base = (now() + timedelta(days=10)).replace(hour=18, minute=0, second=0, microsecond=0)
            e1 = models.Event(title="Welcome Concert", category="Concert", venue_id=v[0].id, start=base,
                              end=base + timedelta(hours=3), status="published",
                              description="Live music night to start the semester.")
            e2 = models.Event(title="Python Workshop", category="Workshop", venue_id=v[1].id,
                              start=base + timedelta(days=1), end=base + timedelta(days=1, hours=3),
                              status="published", description="Hands-on beginner workshop.")
            db.add_all([e1, e2])
            db.flush()
            db.add_all([
                models.TicketCategory(event_id=e1.id, name="General", price=1000, quantity=100),
                models.TicketCategory(event_id=e1.id, name="VIP", price=3000, quantity=20),
                models.TicketCategory(event_id=e2.id, name="Standard", price=500, quantity=30),
            ])
            db.commit()
    finally:
        db.close()