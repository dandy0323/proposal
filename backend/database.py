import sqlite3
import json
from pathlib import Path
from datetime import datetime

DB_PATH = Path(__file__).parent.parent / "data" / "projects.db"


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            form_data TEXT NOT NULL,
            current_phase TEXT NOT NULL DEFAULT 'planning',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS phase_outputs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            phase TEXT NOT NULL,
            output_html TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            review_comment TEXT,
            edit_instruction TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (project_id) REFERENCES projects(id)
        );
    """)
    conn.commit()
    conn.close()


def create_project(name: str, form_data: dict) -> int:
    conn = get_conn()
    now = datetime.now().isoformat()
    cur = conn.execute(
        "INSERT INTO projects (name, form_data, current_phase, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (name, json.dumps(form_data, ensure_ascii=False), "planning", now, now),
    )
    project_id = cur.lastrowid
    conn.commit()
    conn.close()
    return project_id


def get_project(project_id: int) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    d["form_data"] = json.loads(d["form_data"])
    return d


def list_projects() -> list[dict]:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM projects ORDER BY updated_at DESC").fetchall()
    conn.close()
    result = []
    for row in rows:
        d = dict(row)
        d["form_data"] = json.loads(d["form_data"])
        result.append(d)
    return result


def update_project_phase(project_id: int, phase: str):
    conn = get_conn()
    now = datetime.now().isoformat()
    conn.execute(
        "UPDATE projects SET current_phase = ?, updated_at = ? WHERE id = ?",
        (phase, now, project_id),
    )
    conn.commit()
    conn.close()


def save_phase_output(project_id: int, phase: str, output_html: str) -> int:
    conn = get_conn()
    now = datetime.now().isoformat()
    # deactivate previous outputs for this phase
    conn.execute(
        "UPDATE phase_outputs SET status = 'superseded' WHERE project_id = ? AND phase = ? AND status = 'pending'",
        (project_id, phase),
    )
    cur = conn.execute(
        "INSERT INTO phase_outputs (project_id, phase, output_html, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (project_id, phase, output_html, "pending", now, now),
    )
    output_id = cur.lastrowid
    conn.commit()
    conn.close()
    return output_id


def get_latest_phase_output(project_id: int, phase: str) -> dict | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM phase_outputs WHERE project_id = ? AND phase = ? AND status != 'superseded' ORDER BY created_at DESC LIMIT 1",
        (project_id, phase),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def approve_phase_output(output_id: int):
    conn = get_conn()
    now = datetime.now().isoformat()
    conn.execute(
        "UPDATE phase_outputs SET status = 'approved', updated_at = ? WHERE id = ?",
        (now, output_id),
    )
    conn.commit()
    conn.close()


def reject_phase_output(output_id: int, comment: str):
    conn = get_conn()
    now = datetime.now().isoformat()
    conn.execute(
        "UPDATE phase_outputs SET status = 'rejected', review_comment = ?, updated_at = ? WHERE id = ?",
        (comment, now, output_id),
    )
    conn.commit()
    conn.close()


def edit_phase_output(output_id: int, instruction: str):
    conn = get_conn()
    now = datetime.now().isoformat()
    conn.execute(
        "UPDATE phase_outputs SET status = 'edit_requested', edit_instruction = ?, updated_at = ? WHERE id = ?",
        (instruction, now, output_id),
    )
    conn.commit()
    conn.close()
