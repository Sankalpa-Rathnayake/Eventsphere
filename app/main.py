import os
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware
from .database import engine, Base
from . import models  # noqa: F401
from . import agents  # noqa: F401  (registers all five agents on the bus)
from .agents import waitlist_agent

Base.metadata.create_all(bind=engine)


@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(waitlist_agent.sweep_loop())  # WaitlistAgent timer
    yield
    task.cancel()


app = FastAPI(title="EventSphere", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SECRET_KEY", "dev-secret-change-me"))


@app.get("/")
def home():
    return {"message": "EventSphere is running"}