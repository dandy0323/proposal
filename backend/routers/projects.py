from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from backend import database as db
from backend.agents import planning, factcheck, proposal_outline, mockup

router = APIRouter(prefix="/api/projects", tags=["projects"])

PHASE_ORDER = ["planning", "factcheck", "proposal_outline", "mockup", "done"]


class CreateProjectRequest(BaseModel):
    project_name: str
    system_type: str
    industry: str
    background: str
    target_business: str
    target_users: str
    competitors: str = ""
    budget: str
    roadmap: str
    system_components: str = ""
    overview: str
    notes: str = ""


class ReviewRequest(BaseModel):
    output_id: int
    action: str  # "approve" | "reject" | "edit"
    comment: str = ""
    edit_instruction: str = ""


class RunAgentRequest(BaseModel):
    project_id: int
    phase: str  # "planning" | "factcheck" | "proposal_outline" | "mockup"


@router.get("")
def list_projects():
    return db.list_projects()


@router.post("")
def create_project(req: CreateProjectRequest):
    form_data = req.model_dump()
    project_id = db.create_project(req.project_name, form_data)
    return {"project_id": project_id}


@router.get("/{project_id}")
def get_project(project_id: int):
    project = db.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.get("/{project_id}/outputs/{phase}")
def get_phase_output(project_id: int, phase: str):
    output = db.get_latest_phase_output(project_id, phase)
    return output or {}


@router.post("/run")
async def run_agent(req: RunAgentRequest):
    project = db.get_project(req.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    form_data = project["form_data"]
    phase = req.phase

    existing = db.get_latest_phase_output(req.project_id, phase)
    edit_instruction = None
    previous_html = None
    if existing and existing.get("status") in ("edit_requested",):
        edit_instruction = existing.get("edit_instruction")
        previous_html = existing.get("output_html")

    html = _run_phase_agent(phase, form_data, req.project_id, previous_html, edit_instruction)
    output_id = db.save_phase_output(req.project_id, phase, html)
    return {"output_id": output_id, "html": html}


@router.post("/review")
def review_output(req: ReviewRequest):
    if req.action == "approve":
        db.approve_phase_output(req.output_id)
        # advance project phase
        _advance_phase_after_approve(req.output_id)
    elif req.action == "reject":
        db.reject_phase_output(req.output_id, req.comment)
    elif req.action == "edit":
        db.edit_phase_output(req.output_id, req.edit_instruction)
    else:
        raise HTTPException(status_code=400, detail="Invalid action")
    return {"status": "ok"}


def _run_phase_agent(
    phase: str,
    form_data: dict,
    project_id: int,
    previous_html: str | None,
    edit_instruction: str | None,
) -> str:
    if phase == "planning":
        return planning.run(form_data, previous_html, edit_instruction)

    elif phase == "factcheck":
        planning_output = db.get_latest_phase_output(project_id, "planning")
        if not planning_output:
            raise HTTPException(status_code=400, detail="Planning output not found")
        return factcheck.run(planning_output["output_html"])

    elif phase == "proposal_outline":
        planning_output = db.get_latest_phase_output(project_id, "planning")
        if not planning_output:
            raise HTTPException(status_code=400, detail="Planning output not found")
        return proposal_outline.run(form_data, planning_output["output_html"], previous_html, edit_instruction)

    elif phase == "mockup":
        outline_output = db.get_latest_phase_output(project_id, "proposal_outline")
        if not outline_output:
            raise HTTPException(status_code=400, detail="Proposal outline not found")
        return mockup.run(form_data, outline_output["output_html"], previous_html, edit_instruction)

    raise HTTPException(status_code=400, detail=f"Unknown phase: {phase}")


def _advance_phase_after_approve(output_id: int):
    conn = db.get_conn()
    row = conn.execute(
        "SELECT project_id, phase FROM phase_outputs WHERE id = ?", (output_id,)
    ).fetchone()
    conn.close()
    if not row:
        return
    project_id, phase = row["project_id"], row["phase"]
    idx = PHASE_ORDER.index(phase) if phase in PHASE_ORDER else -1
    if idx >= 0 and idx + 1 < len(PHASE_ORDER):
        next_phase = PHASE_ORDER[idx + 1]
        db.update_project_phase(project_id, next_phase)
