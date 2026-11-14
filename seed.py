from app.database import engine, Base, SessionLocal
from app import models
from app.security import hash_password

Base.metadata.create_all(bind=engine)
db = SessionLocal()

accounts = [
    ("Admin", "admin@eventsphere.com", "Admin@123", "admin"),
    ("Gate Staff", "staff@eventsphere.com", "Staff@123", "staff"),
    ("Attendee", "attendee@eventsphere.com", "Attendee@123", "attendee"),
]
for name, email, pw, role in accounts:
    if not db.query(models.User).filter_by(email=email).first():
        db.add(models.User(name=name, email=email,
                           password_hash=hash_password(pw), role=role))
db.commit()

if not db.query(models.Venue).first():
    db.add_all([
        models.Venue(name="Main Auditorium", location="Block A", capacity=300,
                     description="Large hall for concerts and conferences",
                     hourly_rate=5000, open_for_hire=True),
        models.Venue(name="Seminar Room 1", location="Block B", capacity=60,
                     description="Small room for workshops",
                     hourly_rate=1500, open_for_hire=True),
        models.Venue(name="Open Courtyard", location="Central", capacity=150,
                     description="Outdoor space", hourly_rate=2500, open_for_hire=True),
    ])
    db.commit()

print("Seed done: demo accounts and venues created.")
db.close()