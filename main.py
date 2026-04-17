import os
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from backend.database import init_db
from backend.routers.projects import router as projects_router

load_dotenv()

app = FastAPI(title="AI Design Automation")

init_db()

app.include_router(projects_router)

FRONTEND_DIR = Path(__file__).parent / "frontend"
app.mount("/static", StaticFiles(directory=FRONTEND_DIR / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "templates" / "index.html")


@app.get("/project/{project_id}")
def project_page(project_id: int):
    return FileResponse(FRONTEND_DIR / "templates" / "project.html")
