import os
import io
import base64
from datetime import datetime
import qrcode
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from .models import Notification
from .security import get_csrf_token

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
CATEGORIES = ["Concert", "Workshop", "Seminar", "Conference"]


def go(url):
    return RedirectResponse(url, status_code=303)


def flash(request, msg, kind="info"):
    request.session["flash"] = request.session.get("flash", []) + [[kind, msg]]


def render(request, name, db, user, **ctx):
    unread = 0
    if user:
        unread = db.query(Notification).filter_by(user_id=user.id, is_read=False).count()
    ctx.update(user=user, unread=unread, csrf_token=get_csrf_token(request),
               flashes=request.session.pop("flash", []))
    return templates.TemplateResponse(request, name, ctx)


def qr_data_uri(text):
    img = qrcode.make(text)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def fmt_alts(alts):
    """Turn a PROPOSE/REFUSE 'alternatives' dict into readable text."""
    if not alts:
        return ""
    parts = []
    for s in alts.get("slots", []):
        a, b = datetime.fromisoformat(s["start"]), datetime.fromisoformat(s["end"])
        parts.append(f"{a:%d %b %H:%M}-{b:%H:%M}")
    for v in alts.get("venues", []):
        parts.append(f'{v["name"]} (capacity {v["capacity"]})')
    return (" Alternatives: " + "; ".join(parts)) if parts else ""