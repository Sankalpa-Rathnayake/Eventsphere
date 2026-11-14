import uuid
import json
from datetime import datetime
from .models import Message, AgentState

AGENTS = {}  # agent name -> handler function


def register(name, handler):
    """Each agent registers its handle(db, msg) function here."""
    AGENTS[name] = handler


def _log(db, sender, receiver, performative, content, conv_id):
    db.add(Message(
        conversation_id=conv_id, sender=sender, receiver=receiver,
        performative=performative,
        content=json.dumps(content, default=str),
        created=datetime.utcnow(),
    ))
    db.flush()


def send(db, sender, receiver, performative, content, conv_id=None):
    """Deliver a JSON message. Returns the receiver's reply:
    {"performative": "AGREE", "content": {...}}"""
    conv_id = conv_id or str(uuid.uuid4())
    _log(db, sender, receiver, performative, content, conv_id)

    handler = AGENTS.get(receiver)
    if handler is None:
        raise ValueError(f"No agent called {receiver}")

    msg = {
        "sender": sender, "receiver": receiver,
        "performative": performative, "content": content,
        "conversation_id": conv_id,
    }
    reply = handler(db, msg)
    if reply:
        reply["conversation_id"] = conv_id
        _log(db, receiver, sender, reply["performative"], reply["content"], conv_id)
    return reply


def inform_notification(db, sender, content, conv_id=None):
    """Broadcast an INFORM event that NotificationAgent listens to."""
    return send(db, sender, "NotificationAgent", "INFORM", content, conv_id)


# ---- Persistent agent beliefs (state) ----
def get_state(db, agent, key, default=None):
    row = db.query(AgentState).filter_by(agent=agent, key=key).first()
    return json.loads(row.value) if row else default


def set_state(db, agent, key, value):
    row = db.query(AgentState).filter_by(agent=agent, key=key).first()
    if row:
        row.value = json.dumps(value, default=str)
    else:
        db.add(AgentState(agent=agent, key=key, value=json.dumps(value, default=str)))
    db.flush()



def reply(performative, **content):
    """Helper so agents can build replies: reply("AGREE", booking_id=3)"""
    return {"performative": performative, "content": content}