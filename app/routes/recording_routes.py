import datetime
import json
import base64
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import Activity, Recording, get_db
from app.routes.auth_routes import require_user
from app.services.process_analyzer import analyzer
from config import settings

router = APIRouter(prefix="/api/recordings")


@router.post("/start")
async def start_recording(request: Request, db: AsyncSession = Depends(get_db),
                          user=Depends(require_user)):
    data = await request.json()
    recording = Recording(
        user_id=user.id,
        title=data.get("title", f"Recording {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}"),
        description=data.get("description", ""),
        status="recording",
        started_at=datetime.datetime.utcnow(),
    )
    db.add(recording)
    await db.commit()
    await db.refresh(recording)
    return JSONResponse({"id": recording.id, "status": "recording"})


@router.post("/{recording_id}/activity")
async def add_activity(recording_id: int, request: Request,
                       db: AsyncSession = Depends(get_db), user=Depends(require_user)):
    data = await request.json()

    result = await db.execute(select(Recording).where(Recording.id == recording_id))
    recording = result.scalar_one_or_none()
    if not recording or recording.user_id != user.id:
        raise HTTPException(status_code=404, detail="Recording not found")

    # Get current activity count for sequence ordering
    count_result = await db.execute(
        select(Activity).where(Activity.recording_id == recording_id)
    )
    sequence = len(count_result.scalars().all()) + 1

    # Handle screenshot if provided
    screenshot_path = None
    screenshot_data = data.get("screenshot")
    if screenshot_data and screenshot_data.startswith("data:image"):
        try:
            img_data = screenshot_data.split(",")[1]
            img_bytes = base64.b64decode(img_data)
            filename = f"rec_{recording_id}_act_{sequence}.png"
            screenshot_path = os.path.join(settings.SCREENSHOTS_DIR, filename)
            with open(screenshot_path, "wb") as f:
                f.write(img_bytes)
        except Exception:
            screenshot_path = None

    activity = Activity(
        recording_id=recording_id,
        timestamp=datetime.datetime.utcnow(),
        activity_type=data.get("activity_type", "click"),
        application=data.get("application", ""),
        window_title=data.get("window_title", ""),
        url=data.get("url", ""),
        element_text=data.get("element_text", ""),
        element_type=data.get("element_type", ""),
        screenshot_path=screenshot_path,
        x_coord=data.get("x_coord"),
        y_coord=data.get("y_coord"),
        metadata_json=json.dumps(data.get("metadata", {})),
        sequence_order=sequence,
    )
    db.add(activity)
    await db.commit()
    return JSONResponse({"status": "ok", "sequence": sequence})


@router.post("/{recording_id}/batch-activities")
async def add_batch_activities(recording_id: int, request: Request,
                               db: AsyncSession = Depends(get_db), user=Depends(require_user)):
    data = await request.json()
    activities_data = data.get("activities", [])

    result = await db.execute(select(Recording).where(Recording.id == recording_id))
    recording = result.scalar_one_or_none()
    if not recording or recording.user_id != user.id:
        raise HTTPException(status_code=404, detail="Recording not found")

    count_result = await db.execute(
        select(Activity).where(Activity.recording_id == recording_id)
    )
    sequence = len(count_result.scalars().all()) + 1

    for act_data in activities_data:
        activity = Activity(
            recording_id=recording_id,
            timestamp=datetime.datetime.fromisoformat(act_data.get("timestamp", datetime.datetime.utcnow().isoformat())),
            activity_type=act_data.get("activity_type", "click"),
            application=act_data.get("application", ""),
            window_title=act_data.get("window_title", ""),
            url=act_data.get("url", ""),
            element_text=act_data.get("element_text", ""),
            element_type=act_data.get("element_type", ""),
            x_coord=act_data.get("x_coord"),
            y_coord=act_data.get("y_coord"),
            metadata_json=json.dumps(act_data.get("metadata", {})),
            sequence_order=sequence,
        )
        db.add(activity)
        sequence += 1

    await db.commit()
    return JSONResponse({"status": "ok", "count": len(activities_data)})


@router.post("/{recording_id}/stop")
async def stop_recording(recording_id: int, db: AsyncSession = Depends(get_db),
                         user=Depends(require_user)):
    result = await db.execute(select(Recording).where(Recording.id == recording_id))
    recording = result.scalar_one_or_none()
    if not recording or recording.user_id != user.id:
        raise HTTPException(status_code=404, detail="Recording not found")

    recording.status = "processing"
    recording.ended_at = datetime.datetime.utcnow()
    if recording.started_at:
        recording.duration_seconds = (recording.ended_at - recording.started_at).total_seconds()
    await db.commit()

    # Analyze the recording
    try:
        report = await analyzer.analyze_recording(db, recording_id, user_id=user.id)
        recording.status = "completed"
        await db.commit()
        return JSONResponse({
            "status": "completed",
            "report_id": report.id if report else None,
            "recording_id": recording_id,
        })
    except Exception as e:
        recording.status = "failed"
        await db.commit()
        return JSONResponse({"status": "failed", "error": str(e)}, status_code=500)


@router.get("/{recording_id}")
async def get_recording(recording_id: int, db: AsyncSession = Depends(get_db),
                        user=Depends(require_user)):
    result = await db.execute(select(Recording).where(Recording.id == recording_id))
    recording = result.scalar_one_or_none()
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")

    activities_result = await db.execute(
        select(Activity).where(Activity.recording_id == recording_id).order_by(Activity.sequence_order)
    )
    activities = activities_result.scalars().all()

    return JSONResponse({
        "id": recording.id,
        "title": recording.title,
        "description": recording.description,
        "status": recording.status,
        "started_at": recording.started_at.isoformat() if recording.started_at else None,
        "ended_at": recording.ended_at.isoformat() if recording.ended_at else None,
        "duration_seconds": recording.duration_seconds,
        "activity_count": len(activities),
    })


@router.get("")
async def list_recordings(db: AsyncSession = Depends(get_db), user=Depends(require_user)):
    result = await db.execute(
        select(Recording).where(Recording.user_id == user.id).order_by(Recording.created_at.desc())
    )
    recordings = result.scalars().all()
    return JSONResponse([{
        "id": r.id,
        "title": r.title,
        "status": r.status,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "duration_seconds": r.duration_seconds,
    } for r in recordings])
