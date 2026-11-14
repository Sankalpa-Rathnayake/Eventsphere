import os
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import RedirectResponse, HTMLResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware
from .database import engine, Base
from . import models  # noqa: F401
from . import agents  # noqa: F401  (registers the five agents on the bus)
from .agents import waitlist_agent
from .seed_data import seed
from . import routes_public, routes_admin

Base.metadata.create_all(bind=engine)


@asynccontextmanager
async def lifespan(app):
    seed()  # creates demo accounts/venues/events if the database is empty
    task = asyncio.create_task(waitlist_agent.sweep_loop())  # WaitlistAgent timer
    yield
    task.cancel()


app = FastAPI(title="EventSphere", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SECRET_KEY", "dev-secret-change-me"))
app.include_router(routes_public.router)
app.include_router(routes_admin.router)


@app.exception_handler(StarletteHTTPException)
async def http_error(request, exc):
    if exc.status_code == 401:
        return RedirectResponse("/login", status_code=303)
    return HTMLResponse(f"<div style='font-family:sans-serif;margin:2rem'><h3>{exc.status_code}: {exc.detail}</h3>"
                        "<a href='/'>Back to home</a></div>", status_code=exc.status_code)