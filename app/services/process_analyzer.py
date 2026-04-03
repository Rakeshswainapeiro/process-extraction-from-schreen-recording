from __future__ import annotations
import json
import time
import datetime
import os
from typing import Optional, Tuple

import anthropic
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import Activity, ApiAuditLog, ProcessReport, Recording
from app.services.model_resolver import resolve_model, ResolvedModel


class ProcessAnalyzer:
    """Uses AI APIs to analyze captured activities and generate process insights.
    Supports Anthropic, OpenAI, and custom OpenAI-compatible endpoints.

    Model resolution order (no hardcoded values):
      1. User's selected/default model from DB
      2. Platform admin default model
      3. Env vars (backward compat)
      4. Demo mode
    """

    def _format_activities(self, activities: list[Activity]) -> str:
        lines = []
        for act in activities:
            ts = act.timestamp.strftime("%H:%M:%S") if act.timestamp else "?"
            line = f"[{ts}] {act.activity_type.upper()}"
            if act.application:
                line += f" | App: {act.application}"
            if act.window_title:
                line += f" | Window: {act.window_title}"
            if act.url:
                line += f" | URL: {act.url}"
            if act.element_text:
                line += f" | Element: {act.element_text}"
            if act.element_type:
                line += f" | Type: {act.element_type}"
            if act.screenshot_path:
                filename = os.path.basename(act.screenshot_path)
                line += f" | Screenshot URL: /screenshots/{filename}"
            lines.append(line)
        return "\n".join(lines)

    async def analyze_recording(
        self,
        db: AsyncSession,
        recording_id: int,
        user_id: int = None,
        preferred_config_id: Optional[int] = None,
    ) -> Optional[ProcessReport]:
        result = await db.execute(
            select(Activity)
            .where(Activity.recording_id == recording_id)
            .order_by(Activity.sequence_order)
        )
        activities = result.scalars().all()

        recording_result = await db.execute(
            select(Recording).where(Recording.id == recording_id)
        )
        recording = recording_result.scalar_one_or_none()

        if not activities:
            report = self._generate_demo_report(activities, recording)
            tokens_used = 0
            resolved = None
        else:
            activities_text = self._format_activities(activities)

            # Resolve model — no hardcoded values anywhere
            resolved = await resolve_model(db, user_id, preferred_config_id) if user_id else None

            if resolved and resolved.provider != "demo":
                t0 = time.monotonic()
                status_code = 200
                error_msg = None
                tokens_used = 0

                try:
                    report, tokens_used = await self._call_provider(resolved, activities_text, recording)
                except Exception as exc:
                    status_code = 500
                    error_msg = str(exc)
                    raise
                finally:
                    latency_ms = int((time.monotonic() - t0) * 1000)
                    db.add(ApiAuditLog(
                        user_id=user_id,
                        recording_id=recording_id,
                        endpoint=f"/api/recordings/{recording_id}/stop",
                        method="POST",
                        status_code=status_code,
                        model_provider=resolved.provider,
                        model_id=resolved.model_id,
                        model_config_id=resolved.config_id,
                        tokens_used=tokens_used,
                        latency_ms=latency_ms,
                        error_message=error_msg,
                    ))

                # Deduct usage AFTER successful AI call (not on errors)
                if status_code == 200 and user_id:
                    from app.services.usage_service import check_and_deduct
                    await check_and_deduct(
                        db=db,
                        user_id=user_id,
                        recording_id=recording_id,
                        model_config_id=resolved.config_id,
                        model_provider=resolved.provider,
                        model_id=resolved.model_id,
                        tokens_used=tokens_used,
                    )
            else:
                report = self._generate_demo_report(activities, recording)
                tokens_used = 0

        process_report = ProcessReport(
            recording_id=recording_id,
            process_summary=report["summary"],
            l3_process_map=json.dumps(report["l3_process_map"]),
            l4_process_map=json.dumps(report["l4_process_map"]),
            sop_document=json.dumps(report["sop"]) if isinstance(report["sop"], (dict, list)) else report["sop"],
            automation_recommendations=json.dumps(report["automation_recommendations"]),
            ai_recommendations=json.dumps(report["ai_recommendations"]),
            mermaid_diagram=report["mermaid_diagram"],
        )

        existing = await db.execute(
            select(ProcessReport).where(ProcessReport.recording_id == recording_id)
        )
        existing_report = existing.scalar_one_or_none()
        if existing_report:
            existing_report.process_summary = process_report.process_summary
            existing_report.l3_process_map = process_report.l3_process_map
            existing_report.l4_process_map = process_report.l4_process_map
            existing_report.sop_document = process_report.sop_document
            existing_report.automation_recommendations = process_report.automation_recommendations
            existing_report.ai_recommendations = process_report.ai_recommendations
            existing_report.mermaid_diagram = process_report.mermaid_diagram
            existing_report.updated_at = datetime.datetime.utcnow()
            await db.commit()
            return existing_report
        else:
            db.add(process_report)
            await db.commit()
            await db.refresh(process_report)
            return process_report

    def _build_prompt(self, activities_text: str, recording) -> str:
        return f"""You are a senior Business Process Analyst and Solutions Architect. You are given a raw activity log from a screen recording of a user performing a business process across one or more applications.

Your job: Produce a THOROUGH, DETAILED analysis. Document every screen, every action, every decision. Your output will be used to create formal process documentation that anyone can follow without prior knowledge.

## Activity Log
{activities_text}

## Recording Info
- Title: {recording.title if recording else 'Untitled'}
- Duration: {recording.duration_seconds or 0:.0f} seconds

Respond ONLY with valid JSON in this exact format (no extra keys, no markdown, no text outside the JSON):
{{
    "summary": "3-4 sentences covering: (1) what process was performed, (2) what the user was trying to accomplish, (3) which systems/screens were involved, (4) the outcome or end state.",

    "l3_process_map": {{
        "process_name": "Descriptive name for this end-to-end process",
        "process_owner": "Inferred role or department responsible (e.g. 'Operations Team', 'Finance Department')",
        "objective": "One sentence: the business goal this process achieves",
        "steps": [
            {{
                "id": "L3-1",
                "name": "Concise phase name (e.g. 'Authentication & Access', 'Data Entry', 'Review & Approval')",
                "description": "2-3 sentences describing what happens in this phase, why it exists, and what it achieves",
                "inputs": ["Specific data, document, or information entering this phase"],
                "outputs": ["Specific result, record, or change produced by this phase"],
                "systems": ["Exact application or platform name"],
                "time_estimate": "Estimated time range (e.g. '30 seconds', '2-5 minutes')",
                "risk_level": "low OR medium OR high",
                "responsible_team": "Team or role responsible for this phase",
                "compliance_note": "Any audit trail, compliance requirement, or control relevant to this phase. null if none."
            }}
        ]
    }},

    "l4_process_map": {{
        "process_name": "Detailed process name",
        "steps": [
            {{
                "id": "L4-1",
                "parent_l3": "L3-1",
                "name": "Specific action name (verb + object, e.g. 'Click New Order Button', 'Enter Customer Name')",
                "description": "What the user did, on which screen, and why this step is necessary",
                "action_type": "click/select/data_entry/navigation/login/submit/review/decision/verify/upload/download",
                "system": "Application name",
                "screen": "Exact screen, page, or window title where this action occurs",
                "detailed_actions": [
                    "Micro-action 1: e.g. Move cursor to top-right toolbar",
                    "Micro-action 2: e.g. Click the blue 'New Order' button",
                    "Micro-action 3: e.g. Wait for the order form to load"
                ],
                "pre_condition": "What must already be true / done before this step can start",
                "post_condition": "What state exists after this step successfully completes",
                "error_handling": "What the user should do if this step fails or produces an error. null if N/A.",
                "estimated_time_seconds": 10
            }}
        ]
    }},

    "sop": {{
        "title": "Full Standard Operating Procedure title",
        "purpose": "2 sentences: the business goal and regulatory/operational reason this SOP exists",
        "scope": "Who performs this process, in which contexts, and any exclusions",
        "prerequisites": [
            "Prerequisite with specific detail — e.g. 'Valid login credentials for [System Name]'",
            "Prerequisite 2 — e.g. 'Source data prepared in the approved template'"
        ],
        "steps": [
            {{
                "step": 1,
                "title": "Screen/Page Name — Action Description (e.g. 'Login Page — Authenticate with SSO', 'Dashboard — Navigate to New Order Form')",
                "role": "Job title or team who performs this step",
                "system": "Exact application name and screen/page/window title as it appears on screen",
                "action": "3-4 sentences: Describe exactly what the user does on this screen and why. Name specific UI elements. Explain the business reason for each interaction.",
                "sub_steps": [
                    "Step-by-step micro-instruction 1 — e.g. 'Click the [Sign In] button in the top-right corner of the navigation bar'",
                    "Step-by-step micro-instruction 2 — e.g. 'Enter your corporate email address in the [Email] field'",
                    "Step-by-step micro-instruction 3 — e.g. 'Enter your password in the [Password] field'",
                    "Step-by-step micro-instruction 4 — e.g. 'Click the [Log In] button to authenticate'",
                    "Step-by-step micro-instruction 5 — e.g. 'Verify the dashboard has loaded — you should see your name in the top-right'"
                ],
                "inputs": ["Specific data item or document used in this step"],
                "outputs": ["Specific result, screen state, or record produced"],
                "tools_needed": ["Tool or access required — e.g. 'Admin access to [System]', 'Approved data template'"],
                "common_mistakes": ["A specific mistake users make on this step and how to avoid it"],
                "estimated_time": "e.g. '30 seconds' or '1-2 minutes'",
                "screenshot_url": "The EXACT path from 'Screenshot URL: /screenshots/filename.png' in the activity log closest to this step's time window. Copy path exactly. null if none.",
                "decision_point": "If [specific condition]: take [specific action]. Otherwise: take [specific alternative action]. null if no decision.",
                "tip": "Practical tip specific to this screen that helps users avoid errors or save time. null if none.",
                "warning": "Specific warning about a risk or irreversible action on this screen. null if none."
            }}
        ],
        "expected_outcome": "2-3 sentences describing what success looks like — what confirmation message appears, what record is created, what the system state is"
    }},

    "automation_recommendations": [
        {{
            "area": "Specific step or phase of the process",
            "current_state": "Exactly how this is done manually, with specific pain points",
            "recommendation": "Specific automation approach with concrete implementation detail",
            "technology": "RPA / API Integration / Workflow Engine / Script / etc",
            "possibility_score": 85,
            "rationale": "Specific reason for this score — what makes it a strong or weak automation candidate"
        }}
    ],

    "ai_recommendations": [
        {{
            "area": "Specific step or phase",
            "current_state": "Exactly how this is done today",
            "ai_solution": "Specific AI capability that addresses the pain point",
            "technology": "NLP / OCR / ML / GenAI / Computer Vision / etc",
            "possibility_score": 75,
            "rationale": "Specific reasoning — what data exists, what model would work, what the expected impact is"
        }}
    ],

    "mermaid_diagram": "A valid Mermaid flowchart. RULES: (1) Start with 'graph TD'. (2) Every distinct screen/page the user visited MUST be a node. (3) Use subgraph blocks to group nodes by application — subgraph AppName\\n  NodeA[Screen Name]\\nend. (4) Use --> arrows to show navigation between screens. (5) Use {{{{Decision?}}}} diamond shapes for decision points. (6) Add |Yes| and |No| labels on decision branches. (7) Node IDs must be short alphanumeric (A, B, C or S1, S2). (8) Node labels must include the screen name in square brackets. (9) NO semicolons at end of lines. (10) Keep labels concise (under 30 chars). Example: graph TD\\nsubgraph Browser\\n  A[Login Page]\\n  B[Dashboard]\\n  C[Order Form]\\nend\\nSTART([Start]) --> A\\nA --> B\\nB --> C\\nC --> D{{Valid?}}\\nD -->|Yes| E[Confirmation]\\nD -->|No| C\\nE --> END([End])"
}}

CRITICAL RULES:
- automation_recommendations / ai_recommendations: only include scores ≥ 60. Empty array [] if none qualify.
- L3 RULES: Create 3-6 high-level phases. Each phase groups multiple L4 steps that share a common business objective. Be specific about inputs/outputs/systems.
- L4 RULES: Create one L4 step per distinct user action (not per screen). Minimum 8-15 steps for a typical process. detailed_actions must have 2-5 micro-steps each. pre_condition and post_condition must be specific and verifiable.
- SOP RULES (CRITICAL):
  * One SOP step per distinct screen/page the user visited. Same screen visited twice = two steps.
  * sub_steps: minimum 5 per step. Must name specific UI elements (button labels, field names, menu items). Include the value entered where visible.
  * common_mistakes: at least 1 per step. Must be specific to this screen/action, not generic.
  * screenshot_url: scan ALL lines with 'Screenshot URL: /screenshots/...' in the activity log. Match timestamp to screen's time window. Copy path EXACTLY as written. null only if no screenshot exists.
  * Do NOT omit any screen. Do NOT summarize sub_steps. Completeness is mandatory.
- FLOWCHART RULES (CRITICAL):
  * Every screen = a node. Skipping screens is not allowed.
  * Must use subgraph for each application (even if only 1 app used).
  * Must show at least one decision diamond if the user made any choice.
  * Arrow labels (|label|) required on all decision branches.
  * No syntax errors — valid Mermaid only."""

    async def _call_provider(
        self, resolved: ResolvedModel, activities_text: str, recording
    ) -> Tuple[dict, int]:
        """Route to the correct AI backend. Returns (parsed_report, tokens_used)."""
        prompt = self._build_prompt(activities_text, recording)

        if resolved.provider == "anthropic":
            return await self._call_anthropic(resolved, prompt)
        else:
            return await self._call_openai_compat(resolved, prompt)

    async def _call_anthropic(self, resolved: ResolvedModel, prompt: str) -> Tuple[dict, int]:
        """Call Anthropic Claude API. Returns (parsed_report, tokens_used)."""
        client = anthropic.Anthropic(
            api_key=resolved.api_key,
            **({"base_url": resolved.base_url} if resolved.base_url else {}),
        )
        message = client.messages.create(
            model=resolved.model_id,
            max_tokens=resolved.max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        tokens = message.usage.input_tokens + message.usage.output_tokens
        return self._parse_json_response(message.content[0].text), tokens

    async def _call_openai_compat(self, resolved: ResolvedModel, prompt: str) -> Tuple[dict, int]:
        """Call any OpenAI-compatible endpoint (OpenAI, vLLM, Ollama, LiteLLM, etc.)."""
        endpoint = (resolved.base_url or "https://api.openai.com/v1").rstrip("/")
        headers = {
            "Authorization": f"Bearer {resolved.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": resolved.model_id,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": resolved.max_tokens,
            "temperature": 0.2,
        }
        async with httpx.AsyncClient(timeout=120) as http_client:
            resp = await http_client.post(f"{endpoint}/chat/completions", headers=headers, json=payload)
            resp.raise_for_status()
            result = resp.json()
            text = result["choices"][0]["message"]["content"]
            tokens = result.get("usage", {}).get("total_tokens", 0)
            return self._parse_json_response(text), tokens

    def _parse_json_response(self, response_text: str) -> dict:
        """Extract JSON from an AI response, handling code fences."""
        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0]
        elif "```" in response_text:
            response_text = response_text.split("```")[1].split("```")[0]
        try:
            return json.loads(response_text)
        except json.JSONDecodeError:
            return self._generate_demo_report([], None)

    def _generate_demo_report(self, activities: list, recording) -> dict:
        """Generate a demo report when no API key is configured."""
        num_activities = len(activities)
        apps_used = list(set(a.application for a in activities if a.application)) if activities else ["Browser"]
        title = recording.title if recording else "Business Process"

        return {
            "summary": f"The user performed a process across {len(apps_used)} application(s) ({', '.join(apps_used)}), involving {num_activities} tracked actions. The workflow involves opening the required system, entering data, reviewing entries, and submitting for processing. Configure an AI model in Settings to get intelligent process analysis from your recordings.",

            "l3_process_map": {
                "process_name": title,
                "process_owner": "Business Operations",
                "steps": [
                    {"id": "L3-1", "name": "Open & Login", "description": "Access the required application and authenticate", "inputs": ["User credentials"], "outputs": ["Authenticated session"], "systems": apps_used[:1]},
                    {"id": "L3-2", "name": "Locate / Search", "description": "Navigate to the relevant record or section", "inputs": ["Search criteria or menu path"], "outputs": ["Target screen loaded"], "systems": apps_used[:1]},
                    {"id": "L3-3", "name": "Enter / Update Data", "description": "Input or modify the required information", "inputs": ["Source data, documents"], "outputs": ["Data entered in system"], "systems": apps_used},
                    {"id": "L3-4", "name": "Review & Submit", "description": "Verify entries and submit for processing", "inputs": ["Entered data"], "outputs": ["Submitted transaction / record"], "systems": apps_used[-1:]},
                ]
            },

            "l4_process_map": {
                "process_name": f"Detailed — {title}",
                "steps": [
                    {"id": "L4-1", "parent_l3": "L3-1", "name": "Open application", "description": "Navigate to the application URL or launch the desktop app", "action_type": "navigation", "system": apps_used[0], "estimated_time_seconds": 5},
                    {"id": "L4-2", "parent_l3": "L3-1", "name": "Login", "description": "Enter credentials and authenticate", "action_type": "login", "system": apps_used[0], "estimated_time_seconds": 8},
                    {"id": "L4-3", "parent_l3": "L3-2", "name": "Navigate to section", "description": "Click through menus or search to reach the target screen", "action_type": "navigation", "system": apps_used[0], "estimated_time_seconds": 5},
                    {"id": "L4-4", "parent_l3": "L3-3", "name": "Fill form fields", "description": "Enter data into the required fields", "action_type": "data_entry", "system": apps_used[0], "estimated_time_seconds": 30},
                    {"id": "L4-5", "parent_l3": "L3-3", "name": "Select options", "description": "Choose values from dropdowns and checkboxes", "action_type": "select", "system": apps_used[0], "estimated_time_seconds": 10},
                    {"id": "L4-6", "parent_l3": "L3-4", "name": "Review entries", "description": "Verify all data is correct before submission", "action_type": "review", "system": apps_used[0], "estimated_time_seconds": 15},
                    {"id": "L4-7", "parent_l3": "L3-4", "name": "Submit", "description": "Click submit to complete the transaction", "action_type": "submit", "system": apps_used[0], "estimated_time_seconds": 3},
                ]
            },

            "sop": {
                "title": f"Standard Operating Procedure — {title}",
                "purpose": "Defines the end-to-end steps to complete this process consistently and accurately.",
                "scope": f"Applicable to users with access to: {', '.join(apps_used)}. Follow this SOP whenever performing this process.",
                "prerequisites": [
                    f"Access to: {', '.join(apps_used)}",
                    "Valid user credentials",
                    "Required source data or documents prepared",
                ],
                "steps": [
                    {
                        "step": 1,
                        "title": "Open Application & Log In",
                        "role": "Process Owner",
                        "system": apps_used[0],
                        "action": "Launch the required application and authenticate with your credentials to begin the process.",
                        "sub_steps": [
                            f"Open {apps_used[0]} via the desktop shortcut or browser",
                            "Enter your username and password",
                            "Click the Login or Sign In button",
                            "Verify the dashboard or home screen has loaded correctly",
                        ],
                        "inputs": ["User credentials (username & password)"],
                        "outputs": ["Authenticated session"],
                        "screenshot_url": None,
                        "decision_point": "If login fails: verify credentials and retry. If locked out, contact IT support.",
                        "tip": "Ensure you are using the correct environment (production vs. test) before proceeding.",
                        "warning": None,
                    },
                    {
                        "step": 2,
                        "title": "Navigate to the Relevant Section",
                        "role": "Process Owner",
                        "system": apps_used[0],
                        "action": "Use the application menu or search to locate the correct screen or record for this process.",
                        "sub_steps": [
                            "Click on the relevant module or menu item",
                            "Use the search bar to find the target record if needed",
                            "Confirm you are on the correct screen before proceeding",
                        ],
                        "inputs": ["Record ID, search criteria, or menu path"],
                        "outputs": ["Target screen loaded"],
                        "screenshot_url": None,
                        "decision_point": None,
                        "tip": "Bookmark frequently used screens to save navigation time.",
                        "warning": None,
                    },
                    {
                        "step": 3,
                        "title": "Enter Required Data",
                        "role": "Process Owner",
                        "system": apps_used[0],
                        "action": "Fill in all required form fields with the correct data from your source documents.",
                        "sub_steps": [
                            "Refer to the source document or data sheet for correct values",
                            "Enter data into each mandatory field (marked with *)",
                            "Select applicable values from dropdowns and checkboxes",
                            "Attach any required documents or files",
                        ],
                        "inputs": ["Source data, reference documents"],
                        "outputs": ["Form fields populated"],
                        "screenshot_url": None,
                        "decision_point": None,
                        "tip": "Copy-paste values where possible to avoid manual entry errors.",
                        "warning": "Do not leave mandatory fields blank — the form will not submit successfully.",
                    },
                    {
                        "step": 4,
                        "title": "Review & Submit",
                        "role": "Process Owner",
                        "system": apps_used[0],
                        "action": "Review all entered data for accuracy before submitting to ensure the transaction is processed correctly.",
                        "sub_steps": [
                            "Scroll through the entire form to check all fields",
                            "Cross-reference entries against the source document",
                            "Correct any errors before submission",
                            "Click the Submit or Save button to complete the transaction",
                            "Wait for the confirmation message to appear",
                        ],
                        "inputs": ["Populated form"],
                        "outputs": ["Submitted transaction / record created"],
                        "screenshot_url": None,
                        "decision_point": "If data is incorrect: go back to Step 3 and correct. If submission fails: check your connection and retry.",
                        "tip": None,
                        "warning": "Once submitted, changes may require an approval workflow to reverse. Double-check before clicking Submit.",
                    },
                ],
                "expected_outcome": "Transaction or record is successfully created in the system, confirmation message is displayed, and the process is complete.",
            },

            "automation_recommendations": [
                {
                    "area": "Data Entry",
                    "current_state": "Manual typing into form fields",
                    "recommendation": "Auto-populate fields from source data via RPA or API integration",
                    "technology": "RPA / API Integration",
                    "possibility_score": 80,
                    "rationale": "Repetitive form filling across standard fields is a strong RPA candidate"
                },
                {
                    "area": "Cross-System Navigation",
                    "current_state": "Manually switching between applications",
                    "recommendation": "Build an integration layer or unified dashboard",
                    "technology": "API Middleware / Workflow Engine",
                    "possibility_score": 65,
                    "rationale": "Depends on whether the systems expose APIs; worth exploring"
                },
            ],

            "ai_recommendations": [
                {
                    "area": "Data Extraction",
                    "current_state": "User manually reads source documents and types data",
                    "ai_solution": "AI-powered document extraction to read and pre-fill forms",
                    "technology": "OCR + NLP",
                    "possibility_score": 75,
                    "rationale": "If source data comes from documents/emails, AI extraction is well-proven"
                },
            ],

            "mermaid_diagram": """graph TD
    A[Start] --> B[Open Application]
    B --> C[Login]
    C --> D[Navigate to Section]
    D --> E[Enter Data]
    E --> F[Select Options]
    F --> G{Review OK?}
    G -->|No| E
    G -->|Yes| H[Submit]
    H --> I[Confirmation]
    I --> J[End]"""
        }

    async def get_consulting_advice(
        self,
        report: ProcessReport,
        db: AsyncSession = None,
        user_id: int = None,
    ) -> str:
        """Generate consulting advice based on the process report."""
        resolved = None
        if db and user_id:
            resolved = await resolve_model(db, user_id)

        if not resolved or resolved.provider == "demo":
            return self._generate_demo_consulting(report)

        prompt = f"""You are a senior Digital Transformation Consultant. Based on the following process analysis,
provide strategic consulting advice.

## Process Summary
{report.process_summary}

## Automation Recommendations
{report.automation_recommendations}

## AI Recommendations
{report.ai_recommendations}

Provide actionable consulting advice covering:
1. **Quick Wins** - Changes that can be implemented in 1-2 weeks
2. **Medium-term Improvements** - Changes for 1-3 month timeline
3. **Strategic Initiatives** - Long-term transformation opportunities
4. **Risk Assessment** - Potential risks and mitigation strategies
5. **Implementation Roadmap** - Suggested phased approach
6. **Expected Business Impact** - Quantified benefits where possible

Format as clear, professional Markdown."""

        if resolved.provider == "anthropic":
            client = anthropic.Anthropic(
                api_key=resolved.api_key,
                **({"base_url": resolved.base_url} if resolved.base_url else {}),
            )
            message = client.messages.create(
                model=resolved.model_id,
                max_tokens=min(resolved.max_tokens, 4000),
                messages=[{"role": "user", "content": prompt}],
            )
            return message.content[0].text
        else:
            endpoint = (resolved.base_url or "https://api.openai.com/v1").rstrip("/")
            headers = {
                "Authorization": f"Bearer {resolved.api_key}",
                "Content-Type": "application/json",
            }
            async with httpx.AsyncClient(timeout=120) as http_client:
                resp = await http_client.post(
                    f"{endpoint}/chat/completions",
                    headers=headers,
                    json={
                        "model": resolved.model_id,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": min(resolved.max_tokens, 4000),
                        "temperature": 0.2,
                    },
                )
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"]

    def _generate_demo_consulting(self, report: ProcessReport) -> str:
        return """# Digital Transformation Consulting Report

## Quick Wins (1-2 Weeks)
- **Auto-fill forms**: Implement browser auto-fill or bookmarklets for repetitive data entry
- **Keyboard shortcuts**: Create custom shortcuts for frequent navigation paths
- **Template standardization**: Create standard templates for common data entry scenarios

## Medium-term Improvements (1-3 Months)
- **RPA Implementation**: Deploy robotic process automation for the data entry and validation steps
- **System Integration**: Build API connections between the applications to eliminate manual data transfer
- **Workflow Automation**: Implement a workflow engine to manage approvals and routing

## Strategic Initiatives (3-12 Months)
- **AI-Powered Document Processing**: Deploy intelligent document processing to automate data extraction
- **Process Mining Platform**: Implement continuous process mining for ongoing optimization
- **Unified Digital Workspace**: Create a single interface that consolidates all required applications

## Risk Assessment
| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| User adoption resistance | Medium | High | Change management program, training |
| Integration complexity | Medium | Medium | Phased approach, POC first |
| Data quality issues | Low | High | Automated validation rules |

## Implementation Roadmap
1. **Phase 1 (Weeks 1-2)**: Quick wins and process standardization
2. **Phase 2 (Months 1-2)**: RPA bot development and testing
3. **Phase 3 (Months 2-3)**: System integration and workflow automation
4. **Phase 4 (Months 3-6)**: AI implementation and advanced analytics

## Expected Business Impact
- **Time Savings**: 40-60% reduction in process cycle time
- **Error Reduction**: 80% fewer manual data entry errors
- **Cost Savings**: Estimated 30% reduction in operational costs
- **Compliance**: Improved audit trail and process consistency
"""


analyzer = ProcessAnalyzer()
