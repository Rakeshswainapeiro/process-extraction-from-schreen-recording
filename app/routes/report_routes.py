import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import ProcessReport, Feedback, get_db
from app.routes.auth_routes import require_user
from app.services.process_analyzer import analyzer

router = APIRouter(prefix="/api/reports")


def _parse_sop(sop_document):
    """Return SOP as parsed JSON object if possible, otherwise return raw string."""
    if not sop_document:
        return None
    try:
        return json.loads(sop_document)
    except (json.JSONDecodeError, TypeError):
        return sop_document


@router.post("/{recording_id}/reanalyze")
async def reanalyze_report(recording_id: int, db: AsyncSession = Depends(get_db),
                           user=Depends(require_user)):
    """Re-run analysis for a recording that has no report or needs a fresh analysis."""
    from app.models.database import Recording
    from app.services.process_analyzer import analyzer

    result = await db.execute(
        select(Recording).where(Recording.id == recording_id)
    )
    recording = result.scalar_one_or_none()
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")

    report = await analyzer.analyze_recording(db, recording_id, user_id=user.id)
    if recording.status != "completed":
        recording.status = "completed"
        await db.commit()

    if report:
        return JSONResponse({"status": "ok", "report_id": report.id})
    else:
        return JSONResponse({"status": "error", "message": "Analysis produced no report"}, status_code=500)


@router.get("/{recording_id}")
async def get_report(recording_id: int, db: AsyncSession = Depends(get_db),
                     user=Depends(require_user)):
    result = await db.execute(
        select(ProcessReport).where(ProcessReport.recording_id == recording_id)
    )
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found. Click 'Re-analyze' to generate.")

    return JSONResponse({
        "id": report.id,
        "recording_id": report.recording_id,
        "summary": report.process_summary,
        "l3_process_map": json.loads(report.l3_process_map) if report.l3_process_map else None,
        "l4_process_map": json.loads(report.l4_process_map) if report.l4_process_map else None,
        "sop": _parse_sop(report.sop_document),
        "automation_recommendations": json.loads(report.automation_recommendations) if report.automation_recommendations else [],
        "ai_recommendations": json.loads(report.ai_recommendations) if report.ai_recommendations else [],
        "mermaid_diagram": report.mermaid_diagram,
        "created_at": report.created_at.isoformat() if report.created_at else None,
    })


@router.get("/{recording_id}/consulting")
async def get_consulting_advice(recording_id: int, db: AsyncSession = Depends(get_db),
                                user=Depends(require_user)):
    result = await db.execute(
        select(ProcessReport).where(ProcessReport.recording_id == recording_id)
    )
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    advice = await analyzer.get_consulting_advice(report, db=db, user_id=user.id)
    return JSONResponse({"advice": advice})


@router.post("/feedback")
async def create_feedback(request: Request, db: AsyncSession = Depends(get_db),
                          user=Depends(require_user)):
    data = await request.json()
    feedback = Feedback(
        user_id=user.id,
        recording_id=data.get("recording_id"),
        category=data.get("category", "suggestion"),
        rating=data.get("rating"),
        comment=data.get("comment", ""),
    )
    db.add(feedback)
    await db.commit()
    await db.refresh(feedback)
    return JSONResponse({"id": feedback.id, "status": "submitted"})


@router.get("/feedback/all")
async def get_all_feedback(db: AsyncSession = Depends(get_db), user=Depends(require_user)):
    result = await db.execute(
        select(Feedback).order_by(Feedback.created_at.desc())
    )
    feedbacks = result.scalars().all()
    return JSONResponse([{
        "id": f.id,
        "user_id": f.user_id,
        "recording_id": f.recording_id,
        "category": f.category,
        "rating": f.rating,
        "comment": f.comment,
        "status": f.status,
        "created_at": f.created_at.isoformat() if f.created_at else None,
    } for f in feedbacks])
