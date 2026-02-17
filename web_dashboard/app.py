"""FastAPI application entry point for the Web Dashboard.

Registers auth and chat routers, mounts static files, configures
Jinja2 templates, and provides page routes.

Requirements: 7.4
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from web_dashboard.routers import auth, chat

_BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="GPU Scheduler Web Dashboard")

# --- Routers ---
app.include_router(auth.router)
app.include_router(chat.router)

# --- Static files ---
app.mount("/static", StaticFiles(directory=str(_BASE_DIR / "static")), name="static")

# --- Templates ---
templates = Jinja2Templates(directory=str(_BASE_DIR / "templates"))


# --- Page routes ---

@app.get("/", include_in_schema=False)
async def root():
    """Redirect root to chat page."""
    return RedirectResponse(url="/chat")


@app.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_page(request: Request):
    """Render the login page."""
    return templates.TemplateResponse("login.html", {"request": request})


@app.get("/chat", response_class=HTMLResponse, include_in_schema=False)
async def chat_page(request: Request):
    """Render the chat page."""
    return templates.TemplateResponse("chat.html", {"request": request})
