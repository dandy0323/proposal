from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from backend import database as db
from backend.agents import factcheck, proposal_outline, mockup
from backend.constants import SUB_PHASES, SUB_PHASE_LABELS

router = APIRouter(prefix="/api/projects", tags=["projects"])


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
    phase: str  # "factcheck" | "proposal_outline" | "mockup"


class RunSubPhaseRequest(BaseModel):
    project_id: int
    sub_phase_key: str


class ReviewSubPhaseRequest(BaseModel):
    output_id: int
    action: str  # "approve" | "reject" | "edit"
    comment: str = ""
    edit_instruction: str = ""


class DeepDiveRequest(BaseModel):
    project_id: int
    deep_dive_request: str


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


@router.get("/{project_id}/sub-phases")
def get_sub_phases(project_id: int):
    return db.get_all_sub_phase_outputs(project_id)


# ── Sub-phase endpoints ────────────────────────────────────────────────────────

@router.post("/run-sub-phase")
async def run_sub_phase(req: RunSubPhaseRequest):
    project = db.get_project(req.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    from backend.agents import sub_phase as sp

    key = req.sub_phase_key
    form_data = project["form_data"]
    approved_outputs = db.get_approved_sub_phase_html(req.project_id)

    existing = db.get_latest_sub_phase_output(req.project_id, key)
    edit_instruction = None
    previous_html = None
    if existing and existing.get("status") == "edit_requested":
        edit_instruction = existing.get("edit_instruction")
        previous_html = existing.get("output_html")

    try:
        html = sp.run(key, form_data, approved_outputs, previous_html, edit_instruction)
    except Exception as e:
        import traceback; traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
    output_id = db.save_sub_phase_output(req.project_id, key, html)
    return {"output_id": output_id, "html": html}


@router.post("/sub-phase-review")
def review_sub_phase(req: ReviewSubPhaseRequest):
    if req.action == "approve":
        next_key = db.approve_sub_phase_output(req.output_id)
        return {"status": "ok", "next_sub_phase": next_key}
    elif req.action == "reject":
        db.reject_sub_phase_output(req.output_id, req.comment)
    elif req.action == "edit":
        db.edit_sub_phase_output(req.output_id, req.edit_instruction)
    else:
        raise HTTPException(status_code=400, detail="Invalid action")
    return {"status": "ok"}


@router.post("/deep-dive")
async def deep_dive(req: DeepDiveRequest):
    project = db.get_project(req.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    from backend.agents import sub_phase as sp

    key = "why_market"
    form_data = project["form_data"]
    approved_outputs = db.get_approved_sub_phase_html(req.project_id)

    existing = db.get_latest_sub_phase_output(req.project_id, key)
    if not existing:
        raise HTTPException(status_code=400, detail="why_market output not found. Run the agent first.")

    try:
        html = sp.run(
            key, form_data, approved_outputs,
            previous_output=existing.get("output_html"),
            deep_dive_request=req.deep_dive_request,
        )
    except Exception as e:
        import traceback; traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
    output_id = db.save_sub_phase_output(req.project_id, key, html)
    return {"output_id": output_id, "html": html}


# ── Main phase endpoints (factcheck / proposal_outline / mockup) ───────────────

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
    if existing and existing.get("status") == "edit_requested":
        edit_instruction = existing.get("edit_instruction")
        previous_html = existing.get("output_html")

    try:
        html = _run_phase_agent(phase, form_data, req.project_id, previous_html, edit_instruction)
    except HTTPException:
        raise
    except Exception as e:
        import traceback; traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
    output_id = db.save_phase_output(req.project_id, phase, html)
    return {"output_id": output_id, "html": html}


@router.post("/review")
def review_output(req: ReviewRequest):
    if req.action == "approve":
        db.approve_phase_output(req.output_id)
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
    previous_html: Optional[str],
    edit_instruction: Optional[str],
) -> str:
    if phase == "factcheck":
        approved = db.get_approved_sub_phase_html(project_id)
        if not approved:
            raise HTTPException(status_code=400, detail="承認済みの企画・検討フェーズがありません")
        planning_html = _combine_sub_phase_html(approved)
        return factcheck.run(planning_html)

    elif phase == "proposal_outline":
        approved = db.get_approved_sub_phase_html(project_id)
        if not approved:
            raise HTTPException(status_code=400, detail="承認済みの企画・検討フェーズがありません")
        planning_html = _combine_sub_phase_html(approved)
        return proposal_outline.run(form_data, planning_html, previous_html, edit_instruction)

    elif phase == "mockup":
        outline_output = db.get_latest_phase_output(project_id, "proposal_outline")
        if not outline_output:
            raise HTTPException(status_code=400, detail="提案書骨子が見つかりません")
        return mockup.run(form_data, outline_output["output_html"], previous_html, edit_instruction)

    raise HTTPException(status_code=400, detail=f"Unknown phase: {phase}")


def _combine_sub_phase_html(approved: dict) -> str:
    parts = []
    for key in SUB_PHASES:
        if key in approved:
            label = SUB_PHASE_LABELS.get(key, key)
            parts.append(f'<section class="sub-phase"><h2>{label}</h2>{approved[key]}</section>')
    return "\n\n".join(parts)
