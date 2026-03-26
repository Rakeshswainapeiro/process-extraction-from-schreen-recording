"""Export routes for process reports - Added based on stakeholder feedback."""
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import ProcessReport, Recording, get_db
from app.routes.auth_routes import require_user

router = APIRouter(prefix="/api/reports")


@router.get("/{recording_id}/export")
async def export_report_html(recording_id: int, db: AsyncSession = Depends(get_db),
                              user=Depends(require_user)):
    """Export a process report as a standalone HTML file."""
    result = await db.execute(
        select(ProcessReport).where(ProcessReport.recording_id == recording_id)
    )
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    rec_result = await db.execute(select(Recording).where(Recording.id == recording_id))
    recording = rec_result.scalar_one_or_none()

    l3_data = json.loads(report.l3_process_map) if report.l3_process_map else {}
    l4_data = json.loads(report.l4_process_map) if report.l4_process_map else {}
    auto_recs = json.loads(report.automation_recommendations) if report.automation_recommendations else []
    ai_recs = json.loads(report.ai_recommendations) if report.ai_recommendations else []

    # Build L3 table
    l3_rows = ""
    for step in l3_data.get("steps", []):
        l3_rows += f"""<tr>
            <td>{step.get('id','')}</td>
            <td><strong>{step.get('name','')}</strong></td>
            <td>{step.get('description','')}</td>
            <td>{', '.join(step.get('inputs',[]))}</td>
            <td>{', '.join(step.get('outputs',[]))}</td>
            <td>{', '.join(step.get('systems',[]))}</td>
        </tr>"""

    # Build L4 table
    l4_rows = ""
    for step in l4_data.get("steps", []):
        l4_rows += f"""<tr>
            <td>{step.get('id','')}</td>
            <td>{step.get('parent_l3','')}</td>
            <td><strong>{step.get('name','')}</strong></td>
            <td>{step.get('description','')}</td>
            <td>{step.get('action_type','')}</td>
            <td>{step.get('system','')}</td>
            <td>{step.get('estimated_time_seconds','')}s</td>
        </tr>"""

    # Build automation table
    auto_rows = ""
    for rec in auto_recs:
        auto_rows += f"""<tr>
            <td><strong>{rec.get('area','')}</strong></td>
            <td>{rec.get('current_state','')}</td>
            <td>{rec.get('recommendation','')}</td>
            <td>{rec.get('technology','')}</td>
            <td>{rec.get('effort','')}</td>
            <td>{rec.get('impact','')}</td>
            <td>{rec.get('priority','')}</td>
        </tr>"""

    # Build AI table
    ai_rows = ""
    for rec in ai_recs:
        ai_rows += f"""<tr>
            <td><strong>{rec.get('area','')}</strong></td>
            <td>{rec.get('ai_solution','')}</td>
            <td>{rec.get('technology','')}</td>
            <td>{rec.get('use_case','')}</td>
            <td>{rec.get('complexity','')}</td>
            <td>{rec.get('business_value','')}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Process Report - {recording.title if recording else 'Export'}</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 1000px; margin: 0 auto; padding: 40px 20px; color: #1F2937; line-height: 1.6; }}
        h1 {{ color: #4F46E5; border-bottom: 2px solid #E5E7EB; padding-bottom: 12px; }}
        h2 {{ color: #4338CA; margin-top: 32px; }}
        table {{ width: 100%; border-collapse: collapse; margin: 16px 0; font-size: 13px; }}
        th {{ background: #F9FAFB; text-align: left; padding: 10px; border-bottom: 2px solid #E5E7EB; font-weight: 600; }}
        td {{ padding: 10px; border-bottom: 1px solid #F3F4F6; vertical-align: top; }}
        .badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 500; }}
        .sop {{ background: #F9FAFB; padding: 24px; border-radius: 8px; margin: 16px 0; }}
        .sop h3 {{ color: #4F46E5; }}
        .mermaid-code {{ background: #F3F4F6; padding: 16px; border-radius: 8px; font-family: monospace; font-size: 12px; white-space: pre-wrap; }}
        .footer {{ margin-top: 40px; padding-top: 20px; border-top: 1px solid #E5E7EB; font-size: 12px; color: #9CA3AF; text-align: center; }}
        @media print {{ body {{ max-width: none; }} }}
    </style>
</head>
<body>
    <h1>Process Analysis Report</h1>
    <p><strong>Process:</strong> {recording.title if recording else 'N/A'}</p>
    <p><strong>Duration:</strong> {recording.duration_seconds or 0:.0f} seconds</p>
    <p><strong>Generated:</strong> {report.created_at.strftime('%Y-%m-%d %H:%M') if report.created_at else 'N/A'}</p>

    <h2>1. Process Summary</h2>
    <p>{report.process_summary or 'N/A'}</p>

    <h2>2. L3 Process Map (High-Level)</h2>
    <p><strong>Process:</strong> {l3_data.get('process_name', 'N/A')} | <strong>Owner:</strong> {l3_data.get('process_owner', 'N/A')}</p>
    <table>
        <tr><th>ID</th><th>Step</th><th>Description</th><th>Inputs</th><th>Outputs</th><th>Systems</th></tr>
        {l3_rows}
    </table>

    <h2>3. L4 Process Map (Detailed)</h2>
    <table>
        <tr><th>ID</th><th>Parent</th><th>Step</th><th>Description</th><th>Action</th><th>System</th><th>Time</th></tr>
        {l4_rows}
    </table>

    <h2>4. Process Flowchart</h2>
    <div class="mermaid-code">{report.mermaid_diagram or 'N/A'}</div>
    <p style="font-size:12px; color:#9CA3AF;">Copy the above code to <a href="https://mermaid.live">mermaid.live</a> to render the diagram.</p>

    <h2>5. Standard Operating Procedure</h2>
    <div class="sop">{report.sop_document or 'N/A'}</div>

    <h2>6. Automation Recommendations</h2>
    <table>
        <tr><th>Area</th><th>Current</th><th>Recommendation</th><th>Technology</th><th>Effort</th><th>Impact</th><th>Priority</th></tr>
        {auto_rows}
    </table>

    <h2>7. AI Implementation Opportunities</h2>
    <table>
        <tr><th>Area</th><th>AI Solution</th><th>Technology</th><th>Use Case</th><th>Complexity</th><th>Value</th></tr>
        {ai_rows}
    </table>

    <div class="footer">
        Generated by Process Extractor Pro | Screen-to-Process Intelligence Platform
    </div>
</body>
</html>"""

    return HTMLResponse(content=html, headers={
        "Content-Disposition": f"attachment; filename=process_report_{recording_id}.html"
    })
