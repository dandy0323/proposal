import os
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, Response
from backend.database import init_db
from backend.routers.projects import router as projects_router

load_dotenv()

app = FastAPI(title="AI Design Automation")

init_db()

app.include_router(projects_router)

FRONTEND_DIR = Path(__file__).parent / "frontend"

_NO_CACHE = {"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"}


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
