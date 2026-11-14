import pytest
from datetime import timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.database import Base
from app import models, agents  # noqa: F401
from app.timeutil import now


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, autoflush=False)()
    yield s
    s.close()


@pytest.fixture
def world(db):
    v = models.Venue(name="Hall", capacity=100, hourly_rate=1000, open_for_hire=True)
    v2 = models.Venue(name="Room", capacity=50, hourly_rate=500, open_for_hire=True)
    u1 = models.User(email="a@x.com", password_hash="x", role="attendee")
    u2 = models.User(email="b@x.com", password_hash="x", role="attendee")
    admin = models.User(email="admin@x.com", password_hash="x", role="admin")
    db.add_all([v, v2, u1, u2, admin])
    db.flush()
    start = (now() + timedelta(days=10)).replace(hour=10, minute=0, second=0, microsecond=0)
    ev = models.Event(title="Concert", venue_id=v.id, start=start,
                      end=start + timedelta(hours=3), status="published")
    db.add(ev)
    db.flush()
    cat = models.TicketCategory(event_id=ev.id, name="General", price=500, quantity=2)
    db.add(cat)
    db.commit()
    return dict(venue=v, venue2=v2, u1=u1, u2=u2, admin=admin, event=ev, cat=cat)