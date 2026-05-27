import sqlite3
import json
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict

from backend.constants import SUB_PHASES, MAIN_PHASE_ORDER

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
            current_sub_phase TEXT NOT NULL DEFAULT 'why_background',
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

        CREATE TABLE IF NOT EXISTS sub_phase_outputs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            sub_phase_key TEXT NOT NULL,
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

    # Migration: add columns to existing projects tables
    for sql in [
        "ALTER TABLE projects ADD COLUMN current_sub_phase TEXT DEFAULT 'why_background'",
        "ALTER TABLE projects ADD COLUMN sort_order INTEGER DEFAULT 0",
    ]:
        try:
            conn.execute(sql)
            conn.commit()
        except Exception:
            pass

    # Initialize sort_order for existing rows that have 0
    conn.execute("UPDATE projects SET sort_order = id WHERE sort_order = 0")
    conn.commit()

    # Migration: add is_truncated column to sub_phase_outputs
    try:
        conn.execute("ALTER TABLE sub_phase_outputs ADD COLUMN is_truncated INTEGER DEFAULT 0")
        conn.commit()
    except Exception:
        pass

    # Migration: projects stuck at removed 'factcheck' phase → advance to proposal_outline
    conn.execute(
        "UPDATE projects SET current_phase = 'proposal_outline', updated_at = datetime('now') WHERE current_phase = 'factcheck'"
    )
    conn.commit()
    conn.close()


# ── Project CRUD ──────────────────────────────────────────────────────────────

def create_project(name: str, form_data: dict) -> int:
    conn = get_conn()
    now = datetime.now().isoformat()
    cur = conn.execute(
        "INSERT INTO projects (name, form_data, current_phase, current_sub_phase, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (name, json.dumps(form_data, ensure_ascii=False), "planning", "why_background", now, now),
    )
    project_id = cur.lastrowid
    conn.commit()
    conn.close()
    return project_id


def get_project(project_id: int) -> Optional[dict]:
    conn = get_conn()
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    d["form_data"] = json.loads(d["form_data"])
    return d


def list_projects() -> List[dict]:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM projects ORDER BY sort_order ASC, id ASC").fetchall()
    conn.close()
    result = []
    for row in rows:
        d = dict(row)
        d["form_data"] = json.loads(d["form_data"])
        result.append(d)
    return result


def delete_project(project_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM sub_phase_outputs WHERE project_id = ?", (project_id,))
    conn.execute("DELETE FROM phase_outputs WHERE project_id = ?", (project_id,))
    conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    conn.commit()
    conn.close()


def move_project(project_id: int, direction: str):
    """Move a project up or down in sort_order."""
    conn = get_conn()
    rows = conn.execute("SELECT id, sort_order FROM projects ORDER BY sort_order ASC, id ASC").fetchall()
    ids = [r["id"] for r in rows]
    if project_id not in ids:
        conn.close()
        return
    idx = ids.index(project_id)
    if direction == "up" and idx > 0:
        swap_id = ids[idx - 1]
    elif direction == "down" and idx < len(ids) - 1:
        swap_id = ids[idx + 1]
    else:
        conn.close()
        return
    orders = {r["id"]: r["sort_order"] for r in rows}
    conn.execute("UPDATE projects SET sort_order = ? WHERE id = ?", (orders[swap_id], project_id))
    conn.execute("UPDATE projects SET sort_order = ? WHERE id = ?", (orders[project_id], swap_id))
    conn.commit()
    conn.close()


def reorder_projects(ordered_ids: List[int]):
    """Set sort_order based on the provided ordered list of project IDs."""
    conn = get_conn()
    for pos, pid in enumerate(ordered_ids):
        conn.execute("UPDATE projects SET sort_order = ? WHERE id = ?", (pos, pid))
    conn.commit()
    conn.close()


def update_project_phase(project_id: int, phase: str):
    conn = get_conn()
    now = datetime.now().isoformat()
    conn.execute(
        "UPDATE projects SET current_phase = ?, updated_at = ? WHERE id = ?",
        (phase, now, project_id),
    )
    conn.commit()
    conn.close()


def update_project_sub_phase(project_id: int, sub_phase_key: str):
    conn = get_conn()
    now = datetime.now().isoformat()
    conn.execute(
        "UPDATE projects SET current_sub_phase = ?, updated_at = ? WHERE id = ?",
        (sub_phase_key, now, project_id),
    )
    conn.commit()
    conn.close()


# ── Phase outputs (factcheck / proposal_outline / mockup) ────────────────────

def save_phase_output(project_id: int, phase: str, output_html: str) -> int:
    conn = get_conn()
    now = datetime.now().isoformat()
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


def get_latest_phase_output(project_id: int, phase: str) -> Optional[dict]:
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
    row = conn.execute("SELECT project_id, phase FROM phase_outputs WHERE id = ?", (output_id,)).fetchone()
    conn.execute("UPDATE phase_outputs SET status = 'approved', updated_at = ? WHERE id = ?", (now, output_id))
    conn.commit()
    if row:
        project_id, phase = row["project_id"], row["phase"]
        idx = MAIN_PHASE_ORDER.index(phase) if phase in MAIN_PHASE_ORDER else -1
        if idx >= 0 and idx + 1 < len(MAIN_PHASE_ORDER):
            next_phase = MAIN_PHASE_ORDER[idx + 1]
            conn.execute("UPDATE projects SET current_phase = ?, updated_at = ? WHERE id = ?", (next_phase, now, project_id))
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


# ── Sub-phase outputs (planning phase) ───────────────────────────────────────

def save_sub_phase_output(project_id: int, key: str, html: str, is_truncated: bool = False) -> int:
    conn = get_conn()
    now = datetime.now().isoformat()
    conn.execute(
        "UPDATE sub_phase_outputs SET status = 'superseded' WHERE project_id = ? AND sub_phase_key = ? AND status IN ('pending', 'edit_requested')",
        (project_id, key),
    )
    cur = conn.execute(
        "INSERT INTO sub_phase_outputs (project_id, sub_phase_key, output_html, status, is_truncated, created_at, updated_at) VALUES (?, ?, ?, 'pending', ?, ?, ?)",
        (project_id, key, html, 1 if is_truncated else 0, now, now),
    )
    output_id = cur.lastrowid
    conn.commit()
    conn.close()
    return output_id


def get_latest_sub_phase_output(project_id: int, key: str) -> Optional[dict]:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM sub_phase_outputs WHERE project_id = ? AND sub_phase_key = ? AND status != 'superseded' ORDER BY created_at DESC LIMIT 1",
        (project_id, key),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_sub_phase_outputs(project_id: int) -> Dict[str, dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM sub_phase_outputs WHERE project_id = ? AND status != 'superseded' ORDER BY created_at DESC",
        (project_id,),
    ).fetchall()
    conn.close()
    result = {}
    for row in rows:
        d = dict(row)
        key = d["sub_phase_key"]
        if key not in result:
            result[key] = d
    return result


def get_approved_sub_phase_html(project_id: int) -> Dict[str, str]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT sub_phase_key, output_html FROM sub_phase_outputs WHERE project_id = ? AND status = 'approved'",
        (project_id,),
    ).fetchall()
    conn.close()
    return {row["sub_phase_key"]: row["output_html"] for row in rows}


def approve_sub_phase_output(output_id: int) -> str:
    """Approve sub-phase output. Returns next sub-phase key or 'done'."""
    conn = get_conn()
    now = datetime.now().isoformat()
    row = conn.execute("SELECT project_id, sub_phase_key FROM sub_phase_outputs WHERE id = ?", (output_id,)).fetchone()
    conn.execute("UPDATE sub_phase_outputs SET status = 'approved', updated_at = ? WHERE id = ?", (now, output_id))
    conn.commit()

    next_key = "done"
    if row:
        project_id = row["project_id"]
        key = row["sub_phase_key"]
        idx = SUB_PHASES.index(key) if key in SUB_PHASES else -1
        if idx >= 0 and idx + 1 < len(SUB_PHASES):
            next_key = SUB_PHASES[idx + 1]
            conn.execute("UPDATE projects SET current_sub_phase = ?, updated_at = ? WHERE id = ?", (next_key, now, project_id))
        else:
            # All sub-phases done → advance to proposal_outline
            conn.execute("UPDATE projects SET current_phase = 'proposal_outline', current_sub_phase = 'why_background', updated_at = ? WHERE id = ?", (now, project_id))
            next_key = "done"
        conn.commit()
    conn.close()
    return next_key


def skip_sub_phase(project_id: int, key: str) -> str:
    """Mark sub-phase as skipped and advance to next. Returns next sub-phase key or 'done'."""
    conn = get_conn()
    now = datetime.now().isoformat()
    conn.execute(
        "INSERT INTO sub_phase_outputs (project_id, sub_phase_key, output_html, status, is_truncated, created_at, updated_at) VALUES (?, ?, NULL, 'skipped', 0, ?, ?)",
        (project_id, key, now, now),
    )
    idx = SUB_PHASES.index(key) if key in SUB_PHASES else -1
    next_key = "done"
    if idx >= 0 and idx + 1 < len(SUB_PHASES):
        next_key = SUB_PHASES[idx + 1]
        conn.execute("UPDATE projects SET current_sub_phase = ?, updated_at = ? WHERE id = ?", (next_key, now, project_id))
    else:
        conn.execute("UPDATE projects SET current_phase = 'proposal_outline', current_sub_phase = 'why_background', updated_at = ? WHERE id = ?", (now, project_id))
        next_key = "done"
    conn.commit()
    conn.close()
    return next_key


def reject_sub_phase_output(output_id: int, comment: str):
    conn = get_conn()
    now = datetime.now().isoformat()
    conn.execute(
        "UPDATE sub_phase_outputs SET status = 'rejected', review_comment = ?, updated_at = ? WHERE id = ?",
        (comment, now, output_id),
    )
    conn.commit()
    conn.close()


def edit_sub_phase_output(output_id: int, instruction: str):
    conn = get_conn()
    now = datetime.now().isoformat()
    conn.execute(
        "UPDATE sub_phase_outputs SET status = 'edit_requested', edit_instruction = ?, updated_at = ? WHERE id = ?",
        (instruction, now, output_id),
    )
    conn.commit()
    conn.close()


def get_sub_phase_output_history(project_id: int, key: str) -> List[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, status, created_at, output_html, edit_instruction, review_comment "
        "FROM sub_phase_outputs WHERE project_id = ? AND sub_phase_key = ? ORDER BY created_at DESC",
        (project_id, key),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
