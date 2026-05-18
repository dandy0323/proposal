import os
import hashlib
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Form
from fastapi.responses import FileResponse, Response, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware
from backend.database import init_db
from backend.routers.projects import router as projects_router

load_dotenv()

app = FastAPI(title="AI Design Automation")

init_db()

app.include_router(projects_router)

FRONTEND_DIR = Path(__file__).parent / "frontend"

_NO_CACHE = {"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"}


def _session_token() -> str:
    password = os.environ.get("APP_PASSWORD", "")
    return hashlib.sha256(password.encode()).hexdigest()


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path == "/login" or path.startswith("/static/"):
            return await call_next(request)
        if request.cookies.get("session") != _session_token():
            return RedirectResponse("/login")
        return await call_next(request)


app.add_middleware(AuthMiddleware)


@app.get("/login")
def login_page():
    return FileResponse(FRONTEND_DIR / "templates" / "login.html", headers=_NO_CACHE)


@app.post("/login")
async def login(password: str = Form(...)):
    app_password = os.environ.get("APP_PASSWORD", "")
    if app_password and password == app_password:
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie("session", _session_token(), httponly=True, samesite="strict", max_age=86400 * 30)
        return resp
    return RedirectResponse("/login?error=1", status_code=303)


@app.post("/logout")
async def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("session")
    return resp


@app.get("/static/{filepath:path}")
def serve_static(filepath: str):
    path = FRONTEND_DIR / "static" / filepath
    if not path.exists():
        return Response(status_code=404)
    return FileResponse(path, headers=_NO_CACHE)


@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "templates" / "index.html", headers=_NO_CACHE)


@app.get("/project/{project_id}")
def project_page(project_id: int):
    return FileResponse(FRONTEND_DIR / "templates" / "project.html", headers=_NO_CACHE)
