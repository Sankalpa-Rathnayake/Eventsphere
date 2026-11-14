import secrets
from fastapi import Request, Depends, HTTPException
from passlib.context import CryptContext
from .database import get_db
from .models import User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return pwd_context.verify(password, hashed)


def get_current_user(request: Request, db=Depends(get_db)):
    """Returns the logged-in User, or None."""
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return db.get(User, user_id)


def require_role(*roles):
    """Use as: user = Depends(require_role("admin"))"""
    def checker(user=Depends(get_current_user)):
        if user is None:
            raise HTTPException(status_code=401, detail="Please log in")
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="Not allowed")
        return user
    return checker


# ---- CSRF protection ----
def get_csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_hex(16)
        request.session["csrf"] = token
    return token


async def verify_csrf(request: Request):
    """Add Depends(verify_csrf) to every POST route."""
    form = await request.form()
    sent = form.get("csrf_token", "")
    expected = request.session.get("csrf", "")
    if not expected or not secrets.compare_digest(sent, expected):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")