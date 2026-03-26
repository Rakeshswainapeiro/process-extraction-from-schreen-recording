"""
Stakeholder Simulation Testing - Round 1
Simulates 3 personas testing the Process Extractor Pro application:
1. Sarah Chen - Procurement Specialist
2. James Rodriguez - HR Specialist
3. Priya Sharma - Project Manager

Each persona performs domain-specific process recordings and provides feedback.
"""

import asyncio
import datetime
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models.database import init_db, async_session, Recording, Activity, Feedback
from app.services.auth_service import seed_test_users, get_user_by_username
from app.services.process_analyzer import analyzer


# ============================================================
# STAKEHOLDER 1: PROCUREMENT SPECIALIST (Sarah Chen)
# ============================================================
PROCUREMENT_ACTIVITIES = [
    {"activity_type": "navigation", "application": "SAP Ariba", "window_title": "SAP Ariba - Procurement Dashboard", "url": "https://procurement.ariba.com/dashboard", "element_text": "Dashboard loaded"},
    {"activity_type": "click", "application": "SAP Ariba", "window_title": "SAP Ariba - Create Purchase Requisition", "element_text": "Create Purchase Requisition", "element_type": "button"},
    {"activity_type": "typing", "application": "SAP Ariba", "window_title": "SAP Ariba - PR Form", "element_text": "PR-2024-001 - Office Supplies Order", "element_type": "input"},
    {"activity_type": "click", "application": "SAP Ariba", "window_title": "SAP Ariba - Vendor Selection", "element_text": "Select Vendor: Staples Inc.", "element_type": "dropdown"},
    {"activity_type": "typing", "application": "SAP Ariba", "window_title": "SAP Ariba - Line Items", "element_text": "Printer Paper A4 - Qty: 500 - Unit Price: $4.50", "element_type": "input"},
    {"activity_type": "typing", "application": "SAP Ariba", "window_title": "SAP Ariba - Line Items", "element_text": "Ink Cartridges HP - Qty: 20 - Unit Price: $35.00", "element_type": "input"},
    {"activity_type": "click", "application": "SAP Ariba", "window_title": "SAP Ariba - Budget Check", "element_text": "Check Budget Availability", "element_type": "button"},
    {"activity_type": "navigation", "application": "Microsoft Excel", "window_title": "Budget_Tracking_2024.xlsx", "element_text": "Switching to Excel for budget verification"},
    {"activity_type": "scroll", "application": "Microsoft Excel", "window_title": "Budget_Tracking_2024.xlsx - Q1 Sheet", "element_text": "Reviewing Q1 procurement budget"},
    {"activity_type": "typing", "application": "Microsoft Excel", "window_title": "Budget_Tracking_2024.xlsx", "element_text": "Updated remaining budget cell B15", "element_type": "cell"},
    {"activity_type": "app_switch", "application": "SAP Ariba", "window_title": "SAP Ariba - Budget Confirmation", "element_text": "Budget within limits - Approved"},
    {"activity_type": "click", "application": "SAP Ariba", "window_title": "SAP Ariba - Approval Workflow", "element_text": "Route for Approval - Manager Level", "element_type": "button"},
    {"activity_type": "navigation", "application": "Microsoft Outlook", "window_title": "Outlook - New Email", "element_text": "Compose notification email to manager"},
    {"activity_type": "typing", "application": "Microsoft Outlook", "window_title": "Outlook - PR Approval Request", "element_text": "Subject: PR-2024-001 Approval Required - Office Supplies $2,950", "element_type": "input"},
    {"activity_type": "click", "application": "Microsoft Outlook", "window_title": "Outlook - Send", "element_text": "Send", "element_type": "button"},
    {"activity_type": "app_switch", "application": "SAP Ariba", "window_title": "SAP Ariba - PR Status", "element_text": "PR submitted, awaiting approval"},
    {"activity_type": "click", "application": "SAP Ariba", "window_title": "SAP Ariba - PR Tracking", "element_text": "Save & Track PR-2024-001", "element_type": "button"},
]

PROCUREMENT_FEEDBACK = [
    {"category": "accuracy", "rating": 4, "comment": "The process map accurately captured the PR creation workflow. However, it missed the step where I cross-reference the vendor catalog before selecting a vendor. This is a critical compliance step."},
    {"category": "completeness", "rating": 3, "comment": "The L3 map is good but the L4 map needs more granularity around the budget verification steps. In procurement, we need to check both departmental budget AND project-specific budget. The SOP should mention the 3-way match requirement."},
    {"category": "usability", "rating": 4, "comment": "The interface is clean and intuitive. The recording controls are easy to use. Would like to see a pause/resume option during recording for when I get interrupted by calls."},
    {"category": "suggestion", "rating": 5, "comment": "Please add the ability to tag specific steps as 'compliance-critical' in the process map. Also, it would be great to have an export option for the process map to Visio or PowerPoint format."},
]


# ============================================================
# STAKEHOLDER 2: HR SPECIALIST (James Rodriguez)
# ============================================================
HR_ACTIVITIES = [
    {"activity_type": "navigation", "application": "Workday", "window_title": "Workday - HR Dashboard", "url": "https://hr.workday.com/dashboard", "element_text": "HR Dashboard"},
    {"activity_type": "click", "application": "Workday", "window_title": "Workday - Employee Onboarding", "element_text": "Initiate New Employee Onboarding", "element_type": "button"},
    {"activity_type": "typing", "application": "Workday", "window_title": "Workday - New Hire Form", "element_text": "Employee: John Smith, Position: Software Engineer, Start Date: 2024-04-01", "element_type": "form"},
    {"activity_type": "click", "application": "Workday", "window_title": "Workday - Document Checklist", "element_text": "Generate Document Checklist", "element_type": "button"},
    {"activity_type": "navigation", "application": "DocuSign", "window_title": "DocuSign - Prepare Documents", "url": "https://docusign.com/prepare", "element_text": "Opening DocuSign for offer letter"},
    {"activity_type": "click", "application": "DocuSign", "window_title": "DocuSign - Template Selection", "element_text": "Select Template: Standard Offer Letter - Engineering", "element_type": "dropdown"},
    {"activity_type": "typing", "application": "DocuSign", "window_title": "DocuSign - Fill Fields", "element_text": "Salary: $95,000, Benefits: Standard Package, Reporting To: Engineering Manager", "element_type": "input"},
    {"activity_type": "click", "application": "DocuSign", "window_title": "DocuSign - Send for Signature", "element_text": "Send for Signature to john.smith@email.com", "element_type": "button"},
    {"activity_type": "app_switch", "application": "ServiceNow", "window_title": "ServiceNow - IT Provisioning", "element_text": "Create IT provisioning request"},
    {"activity_type": "typing", "application": "ServiceNow", "window_title": "ServiceNow - New Request", "element_text": "Request: Laptop, Email Account, VPN Access, Badge for John Smith", "element_type": "form"},
    {"activity_type": "click", "application": "ServiceNow", "window_title": "ServiceNow - Submit", "element_text": "Submit IT Request - Priority: Standard", "element_type": "button"},
    {"activity_type": "navigation", "application": "Microsoft Teams", "window_title": "Teams - HR Channel", "element_text": "Posting onboarding announcement"},
    {"activity_type": "typing", "application": "Microsoft Teams", "window_title": "Teams - New Hire Announcement", "element_text": "Welcome John Smith joining Engineering team on April 1st!", "element_type": "message"},
    {"activity_type": "app_switch", "application": "Workday", "window_title": "Workday - Onboarding Tracker", "element_text": "Updating onboarding status"},
    {"activity_type": "click", "application": "Workday", "window_title": "Workday - Checklist Update", "element_text": "Mark: Offer Letter Sent, IT Request Created, Team Notified", "element_type": "checkbox"},
    {"activity_type": "click", "application": "Workday", "window_title": "Workday - Schedule", "element_text": "Schedule: Day 1 Orientation, Week 1 Training Plan", "element_type": "button"},
]

HR_FEEDBACK = [
    {"category": "accuracy", "rating": 4, "comment": "Good capture of the onboarding workflow. The process map correctly identified the multi-system nature of our process (Workday, DocuSign, ServiceNow, Teams). Missing the background check initiation step which happens before the offer letter."},
    {"category": "completeness", "rating": 3, "comment": "The SOP needs to include compliance checkpoints - we have mandatory steps for I-9 verification, tax form collection, and policy acknowledgments. The L4 map should show conditional paths for international hires vs domestic hires."},
    {"category": "usability", "rating": 3, "comment": "The recording worked well but I'd like to see a mini-map or thumbnail view while recording so I can confirm it's capturing the right screens. Also, the activity list in real-time would help me know if important steps are being tracked."},
    {"category": "suggestion", "rating": 4, "comment": "Add role-based process templates - HR has standard processes (onboarding, offboarding, benefits enrollment) that we could pre-load as baselines. Also need GDPR/privacy controls since we handle sensitive employee data in these processes."},
]


# ============================================================
# STAKEHOLDER 3: PROJECT MANAGER (Priya Sharma)
# ============================================================
PM_ACTIVITIES = [
    {"activity_type": "navigation", "application": "Jira", "window_title": "Jira - Sprint Board", "url": "https://company.atlassian.net/board/42", "element_text": "Sprint 24 Board"},
    {"activity_type": "click", "application": "Jira", "window_title": "Jira - Sprint Planning", "element_text": "Start Sprint Planning Session", "element_type": "button"},
    {"activity_type": "scroll", "application": "Jira", "window_title": "Jira - Backlog", "element_text": "Reviewing prioritized backlog items"},
    {"activity_type": "click", "application": "Jira", "window_title": "Jira - Story PROJ-456", "element_text": "Move PROJ-456 to Sprint 24 (8 story points)", "element_type": "drag"},
    {"activity_type": "click", "application": "Jira", "window_title": "Jira - Story PROJ-457", "element_text": "Move PROJ-457 to Sprint 24 (5 story points)", "element_type": "drag"},
    {"activity_type": "click", "application": "Jira", "window_title": "Jira - Story PROJ-458", "element_text": "Move PROJ-458 to Sprint 24 (3 story points)", "element_type": "drag"},
    {"activity_type": "navigation", "application": "Confluence", "window_title": "Confluence - Sprint Notes", "url": "https://company.atlassian.net/wiki/sprint24", "element_text": "Documenting sprint goals"},
    {"activity_type": "typing", "application": "Confluence", "window_title": "Confluence - Sprint 24 Goals", "element_text": "Sprint Goal: Complete user auth module and API integration. Capacity: 40 SP", "element_type": "editor"},
    {"activity_type": "app_switch", "application": "Microsoft Teams", "window_title": "Teams - Standup Channel", "element_text": "Posting sprint plan to team"},
    {"activity_type": "typing", "application": "Microsoft Teams", "window_title": "Teams - Sprint Kickoff", "element_text": "Sprint 24 is loaded with 40 SP. Key focus: Auth module + API. Kickoff at 10am.", "element_type": "message"},
    {"activity_type": "navigation", "application": "Google Sheets", "window_title": "Resource Allocation Matrix", "url": "https://docs.google.com/spreadsheets/resource-matrix", "element_text": "Checking resource allocation"},
    {"activity_type": "typing", "application": "Google Sheets", "window_title": "Resource Matrix - Sprint 24", "element_text": "Assigned: Dev1-Auth(8SP), Dev2-API(5SP), Dev3-Testing(3SP)", "element_type": "cell"},
    {"activity_type": "app_switch", "application": "Jira", "window_title": "Jira - Sprint Board", "element_text": "Assigning stories to developers"},
    {"activity_type": "click", "application": "Jira", "window_title": "Jira - Assign PROJ-456", "element_text": "Assign to: Developer 1", "element_type": "dropdown"},
    {"activity_type": "click", "application": "Jira", "window_title": "Jira - Start Sprint", "element_text": "Start Sprint 24 - Duration: 2 weeks", "element_type": "button"},
    {"activity_type": "navigation", "application": "Slack", "window_title": "Slack - #project-updates", "element_text": "Sending stakeholder update"},
    {"activity_type": "typing", "application": "Slack", "window_title": "Slack - Sprint Update", "element_text": "Sprint 24 kicked off. ETA for auth module: March 29. Will share demo on April 2.", "element_type": "message"},
]

PM_FEEDBACK = [
    {"category": "accuracy", "rating": 5, "comment": "Excellent capture of the sprint planning process! The tool correctly identified the multi-tool workflow across Jira, Confluence, Teams, and Sheets. The process map accurately reflects our actual sprint planning ceremony."},
    {"category": "completeness", "rating": 4, "comment": "The automation recommendations are spot-on - we do need Jira-Confluence integration. One thing missing: the risk assessment step where I check for team member PTO conflicts before finalizing sprint capacity."},
    {"category": "usability", "rating": 4, "comment": "Very user-friendly. The tab-based report is well organized. Would love to see a timeline/Gantt view of the process steps in addition to the flowchart. Also, ability to annotate specific steps post-recording would be valuable."},
    {"category": "suggestion", "rating": 5, "comment": "Add a comparison feature to overlay two recordings of the same process - this would help identify process variations between team members. Also, integrate with project management tools to auto-create improvement tasks from the recommendations."},
]


async def run_simulation():
    """Run the complete stakeholder simulation."""
    print("=" * 70)
    print("STAKEHOLDER SIMULATION TESTING - ROUND 1")
    print("=" * 70)

    await init_db()
    async with async_session() as db:
        await seed_test_users(db)

    stakeholders = [
        {
            "username": "procurement_specialist",
            "title": "Purchase Requisition Creation Process",
            "activities": PROCUREMENT_ACTIVITIES,
            "feedback": PROCUREMENT_FEEDBACK,
            "persona": "Sarah Chen (Procurement Specialist)",
        },
        {
            "username": "hr_specialist",
            "title": "Employee Onboarding Process",
            "activities": HR_ACTIVITIES,
            "feedback": HR_FEEDBACK,
            "persona": "James Rodriguez (HR Specialist)",
        },
        {
            "username": "project_manager",
            "title": "Sprint Planning Process",
            "activities": PM_ACTIVITIES,
            "feedback": PM_FEEDBACK,
            "persona": "Priya Sharma (Project Manager)",
        },
    ]

    results = {}

    for stakeholder in stakeholders:
        print(f"\n{'─' * 60}")
        print(f"TESTING: {stakeholder['persona']}")
        print(f"{'─' * 60}")

        async with async_session() as db:
            user = await get_user_by_username(db, stakeholder["username"])
            if not user:
                print(f"  ERROR: User {stakeholder['username']} not found!")
                continue

            # 1. Create recording
            print(f"  [1/4] Creating recording: {stakeholder['title']}")
            recording = Recording(
                user_id=user.id,
                title=stakeholder["title"],
                description=f"Simulated recording for {stakeholder['persona']}",
                status="recording",
                started_at=datetime.datetime.utcnow(),
            )
            db.add(recording)
            await db.commit()
            await db.refresh(recording)

            # 2. Add activities
            print(f"  [2/4] Adding {len(stakeholder['activities'])} activities...")
            for i, act_data in enumerate(stakeholder["activities"]):
                activity = Activity(
                    recording_id=recording.id,
                    timestamp=datetime.datetime.utcnow() + datetime.timedelta(seconds=i * 5),
                    activity_type=act_data["activity_type"],
                    application=act_data.get("application", ""),
                    window_title=act_data.get("window_title", ""),
                    url=act_data.get("url", ""),
                    element_text=act_data.get("element_text", ""),
                    element_type=act_data.get("element_type", ""),
                    x_coord=act_data.get("x_coord"),
                    y_coord=act_data.get("y_coord"),
                    metadata_json=json.dumps(act_data.get("metadata", {})),
                    sequence_order=i + 1,
                )
                db.add(activity)
            await db.commit()

            # 3. Analyze
            print(f"  [3/4] Running AI analysis...")
            recording.status = "processing"
            recording.ended_at = datetime.datetime.utcnow() + datetime.timedelta(seconds=len(stakeholder["activities"]) * 5)
            recording.duration_seconds = len(stakeholder["activities"]) * 5
            await db.commit()

            report = await analyzer.analyze_recording(db, recording.id)
            recording.status = "completed"
            await db.commit()

            if report:
                print(f"    Process Summary: {report.process_summary[:100]}...")
                l3_data = json.loads(report.l3_process_map) if report.l3_process_map else {}
                l4_data = json.loads(report.l4_process_map) if report.l4_process_map else {}
                print(f"    L3 Steps: {len(l3_data.get('steps', []))}")
                print(f"    L4 Steps: {len(l4_data.get('steps', []))}")
                auto_recs = json.loads(report.automation_recommendations) if report.automation_recommendations else []
                ai_recs = json.loads(report.ai_recommendations) if report.ai_recommendations else []
                print(f"    Automation Recommendations: {len(auto_recs)}")
                print(f"    AI Recommendations: {len(ai_recs)}")

            # 4. Submit feedback
            print(f"  [4/4] Submitting {len(stakeholder['feedback'])} feedback items...")
            for fb_data in stakeholder["feedback"]:
                feedback = Feedback(
                    user_id=user.id,
                    recording_id=recording.id,
                    category=fb_data["category"],
                    rating=fb_data["rating"],
                    comment=fb_data["comment"],
                )
                db.add(feedback)
            await db.commit()

            results[stakeholder["username"]] = {
                "recording_id": recording.id,
                "activities": len(stakeholder["activities"]),
                "feedback_count": len(stakeholder["feedback"]),
                "report_generated": report is not None,
            }

            print(f"  COMPLETED for {stakeholder['persona']}")

    # Summary
    print(f"\n{'=' * 70}")
    print("ROUND 1 SIMULATION RESULTS")
    print(f"{'=' * 70}")
    for username, result in results.items():
        print(f"  {username}:")
        print(f"    Recording ID: {result['recording_id']}")
        print(f"    Activities: {result['activities']}")
        print(f"    Feedback: {result['feedback_count']}")
        print(f"    Report: {'Generated' if result['report_generated'] else 'FAILED'}")

    # Aggregate feedback analysis
    print(f"\n{'=' * 70}")
    print("CONSOLIDATED FEEDBACK ANALYSIS")
    print(f"{'=' * 70}")

    all_feedback = PROCUREMENT_FEEDBACK + HR_FEEDBACK + PM_FEEDBACK
    avg_rating = sum(f["rating"] for f in all_feedback) / len(all_feedback)
    print(f"\n  Overall Average Rating: {avg_rating:.1f}/5")

    print("\n  KEY THEMES FROM FEEDBACK:")
    print("  ─────────────────────────")
    print("  1. MISSING PROCESS STEPS: All 3 users noted specific domain steps were missed")
    print("     - Procurement: vendor catalog cross-reference step")
    print("     - HR: background check & compliance checkpoints (I-9, GDPR)")
    print("     - PM: risk assessment / PTO conflict check")
    print()
    print("  2. ENHANCED RECORDING FEATURES REQUESTED:")
    print("     - Pause/Resume functionality")
    print("     - Real-time activity list during recording")
    print("     - Mini-map/thumbnail preview")
    print()
    print("  3. EXPORT & INTEGRATION:")
    print("     - Export to Visio/PowerPoint")
    print("     - Integration with project management tools")
    print("     - Process comparison between recordings")
    print()
    print("  4. DOMAIN-SPECIFIC FEATURES:")
    print("     - Compliance tagging for process steps")
    print("     - Role-based process templates")
    print("     - GDPR/privacy controls")
    print("     - Timeline/Gantt view")
    print("     - Post-recording step annotation")
    print()
    print("  5. L4 PROCESS MAP IMPROVEMENTS:")
    print("     - More granularity needed")
    print("     - Conditional/branching paths")
    print("     - Domain-specific step categorization")

    print(f"\n{'=' * 70}")
    print("ROUND 1 COMPLETE - Proceeding to incorporate feedback...")
    print(f"{'=' * 70}")

    return results


if __name__ == "__main__":
    asyncio.run(run_simulation())
