from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Boolean, Text
from .database import Base


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), default="")
    email = Column(String(200), unique=True, nullable=False)
    password_hash = Column(String(200), nullable=False)
    role = Column(String(20), default="attendee")  # admin / staff / attendee


class Venue(Base):
    __tablename__ = "venues"
    id = Column(Integer, primary_key=True)
    name = Column(String(150), nullable=False)
    location = Column(String(200), default="")
    capacity = Column(Integer, nullable=False)
    description = Column(Text, default="")
    image = Column(String(300), default="")
    hourly_rate = Column(Float, default=0)
    open_for_hire = Column(Boolean, default=False)


class Event(Base):
    __tablename__ = "events"
    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    category = Column(String(50), default="Concert")
    venue_id = Column(Integer, ForeignKey("venues.id"))
    start = Column(DateTime, nullable=False)
    end = Column(DateTime, nullable=False)
    description = Column(Text, default="")
    status = Column(String(20), default="draft")  # draft / published / cancelled


class TicketCategory(Base):
    __tablename__ = "ticket_categories"
    id = Column(Integer, primary_key=True)
    event_id = Column(Integer, ForeignKey("events.id"))
    name = Column(String(100), nullable=False)
    price = Column(Float, default=0)
    quantity = Column(Integer, nullable=False)
    sold = Column(Integer, default=0)


class Booking(Base):
    __tablename__ = "bookings"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    event_id = Column(Integer, ForeignKey("events.id"))
    status = Column(String(20), default="confirmed")  # confirmed / cancelled
    total = Column(Float, default=0)
    refund = Column(Float, default=0)
    payment_ref = Column(String(50), default="")
    created = Column(DateTime, default=datetime.utcnow)


class Ticket(Base):
    __tablename__ = "tickets"
    id = Column(Integer, primary_key=True)
    booking_id = Column(Integer, ForeignKey("bookings.id"))
    event_id = Column(Integer, ForeignKey("events.id"))
    category_id = Column(Integer, ForeignKey("ticket_categories.id"))
    code = Column(String(100), unique=True, nullable=False)
    status = Column(String(20), default="valid")  # valid / used / cancelled
    scan_count = Column(Integer, default=0)


class HireRequest(Base):
    __tablename__ = "hire_requests"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    venue_id = Column(Integer, ForeignKey("venues.id"))
    start = Column(DateTime, nullable=False)
    end = Column(DateTime, nullable=False)
    purpose = Column(String(300), default="")
    guests = Column(Integer, default=1)
    price = Column(Float, default=0)
    status = Column(String(20), default="pending")  # confirmed / pending / rejected
    note = Column(Text, default="")


class Waitlist(Base):
    __tablename__ = "waitlist"
    id = Column(Integer, primary_key=True)
    event_id = Column(Integer, ForeignKey("events.id"))
    category_id = Column(Integer, ForeignKey("ticket_categories.id"))
    user_id = Column(Integer, ForeignKey("users.id"))
    quantity = Column(Integer, default=1)
    status = Column(String(20), default="waiting")  # waiting / offered / claimed / expired
    hold_expires_at = Column(DateTime, nullable=True)
    created = Column(DateTime, default=datetime.utcnow)


class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True)
    conversation_id = Column(String(60), index=True)
    sender = Column(String(50))
    receiver = Column(String(50))
    performative = Column(String(20))
    content = Column(Text)  # JSON text
    created = Column(DateTime, default=datetime.utcnow)


class AgentState(Base):
    """Each agent's persistent beliefs (survive restarts)."""
    __tablename__ = "agent_state"
    id = Column(Integer, primary_key=True)
    agent = Column(String(50), index=True)
    key = Column(String(100), index=True)
    value = Column(Text, default="")


class Notification(Base):
    __tablename__ = "notifications"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    text = Column(Text)
    is_read = Column(Boolean, default=False)
    created = Column(DateTime, default=datetime.utcnow)