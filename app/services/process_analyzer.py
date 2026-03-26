import json
import datetime
from typing import Optional

import anthropic
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import Activity, AIModelConfig, ProcessReport, Recording
from config import settings


class ProcessAnalyzer:
    """Uses AI APIs to analyze captured activities and generate process insights.
    Supports Anthropic, OpenAI, and custom OpenAI-compatible endpoints."""

    def __init__(self):
        self.client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY) if settings.ANTHROPIC_API_KEY else None

    async def _get_model_config(self, db: AsyncSession, user_id: int) -> Optional[AIModelConfig]:
        """Load the user's active/default AI model config from the database."""
        # Try default first
        result = await db.execute(
            select(AIModelConfig).where(
                AIModelConfig.user_id == user_id,
                AIModelConfig.is_default == True,
                AIModelConfig.is_active == True,
            )
        )
        config = result.scalar_one_or_none()
        if config:
            return config
        # Fall back to any active
        result = await db.execute(
            select(AIModelConfig).where(
                AIModelConfig.user_id == user_id,
                AIModelConfig.is_active == True,
            ).order_by(AIModelConfig.created_at.desc())
        )
        return result.scalar_one_or_none()

    def _build_client(self, config: Optional[AIModelConfig]):
        """Build the appropriate AI client from a model config."""
        if config:
            if config.provider == "anthropic":
                kwargs = {"api_key": config.api_key}
                if config.base_url:
                    kwargs["base_url"] = config.base_url
                return {"type": "anthropic", "client": anthropic.Anthropic(**kwargs), "model": config.model_id, "max_tokens": config.max_tokens}
            elif config.provider in ("openai", "custom"):
                return {"type": "openai_compat", "api_key": config.api_key, "base_url": config.base_url or "https://api.openai.com/v1", "model": config.model_id, "max_tokens": config.max_tokens}
        # Fallback to env var
        if settings.ANTHROPIC_API_KEY:
            return {"type": "anthropic", "client": anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY), "model": "claude-sonnet-4-6", "max_tokens": 8000}
        return None

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
            lines.append(line)
        return "\n".join(lines)

    async def analyze_recording(self, db: AsyncSession, recording_id: int, user_id: int = None) -> Optional[ProcessReport]:
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
            # No activities tracked — generate a demo report so the user still gets output
            report = self._generate_demo_report(activities, recording)
        else:
            activities_text = self._format_activities(activities)

            # Resolve which AI client to use: user config > env var > demo
            ai_client = None
            if user_id:
                model_config = await self._get_model_config(db, user_id)
                ai_client = self._build_client(model_config)
            if not ai_client:
                ai_client = self._build_client(None)

            if ai_client:
                report = await self._analyze_with_ai(activities_text, recording, ai_client)
            else:
                report = self._generate_demo_report(activities, recording)

        process_report = ProcessReport(
            recording_id=recording_id,
            process_summary=report["summary"],
            l3_process_map=json.dumps(report["l3_process_map"]),
            l4_process_map=json.dumps(report["l4_process_map"]),
            sop_document=report["sop"],
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
        return f"""You are a senior Process Analyst. You are given an activity log from a screen recording that captured a user performing a business process across one or more applications.

Your job: Intelligently understand the FLOW — what the user was trying to accomplish, the sequence of screens/platforms they moved through, and what actions they took at each step. Stitch the raw events into a coherent process story.

## Activity Log
{activities_text}

## Recording Info
- Title: {recording.title if recording else 'Untitled'}
- Duration: {recording.duration_seconds or 0:.0f} seconds

Respond ONLY with valid JSON in this format:
{{
    "summary": "2-3 sentences: What is this process about? What was the user trying to accomplish? What systems were involved?",

    "l3_process_map": {{
        "process_name": "Clear name for the end-to-end process",
        "process_owner": "Inferred role/department",
        "steps": [
            {{
                "id": "L3-1",
                "name": "High-level step name",
                "description": "What happens in this phase",
                "inputs": ["What data/documents/info goes IN"],
                "outputs": ["What is produced/changed/decided"],
                "systems": ["Which app/platform is used"]
            }}
        ]
    }},

    "l4_process_map": {{
        "process_name": "Detailed process name",
        "steps": [
            {{
                "id": "L4-1",
                "parent_l3": "L3-1",
                "name": "Specific action taken",
                "description": "What the user did and why",
                "action_type": "click/select/data_entry/navigation/login/submit/review/decision",
                "system": "Application or platform",
                "estimated_time_seconds": 10
            }}
        ]
    }},

    "sop": "A concise Standard Operating Procedure in Markdown: Title, Purpose, Prerequisites, numbered Steps, Decision points, Expected outcomes.",

    "automation_recommendations": [
        {{
            "area": "Which part of the process",
            "current_state": "How it is done manually today",
            "recommendation": "What could be automated and how",
            "technology": "RPA/Workflow/API Integration/Script/etc",
            "possibility_score": 85,
            "rationale": "Why this score — what makes it a good or poor fit"
        }}
    ],

    "ai_recommendations": [
        {{
            "area": "Which part of the process",
            "current_state": "How it is done today",
            "ai_solution": "What AI could do here",
            "technology": "NLP/OCR/ML/GenAI/Computer Vision/etc",
            "possibility_score": 75,
            "rationale": "Why this score — what makes it feasible or not"
        }}
    ],

    "mermaid_diagram": "A Mermaid graph TD flowchart showing the process flow across systems with decision points"
}}

IMPORTANT RULES:
- For automation_recommendations and ai_recommendations: Only include items where the possibility_score is 60 or above. Do NOT force-fit. If nothing scores above 60, return an empty array.
- possibility_score is 0-100: 60-70 = worth exploring, 70-85 = strong candidate, 85+ = high confidence.
- Focus on understanding the FLOW across screens and platforms, not individual UI clicks.
- Infer the business context from the applications used and actions taken."""

    async def _analyze_with_ai(self, activities_text: str, recording, ai_client: dict) -> dict:
        """Route to the correct AI backend based on the client config."""
        prompt = self._build_prompt(activities_text, recording)

        if ai_client["type"] == "anthropic":
            return await self._call_anthropic(prompt, ai_client)
        elif ai_client["type"] == "openai_compat":
            return await self._call_openai_compat(prompt, ai_client)
        else:
            return self._generate_demo_report([], None)

    async def _call_anthropic(self, prompt: str, ai_client: dict) -> dict:
        """Call Anthropic Claude API."""
        message = ai_client["client"].messages.create(
            model=ai_client["model"],
            max_tokens=ai_client["max_tokens"],
            messages=[{"role": "user", "content": prompt}],
        )
        return self._parse_json_response(message.content[0].text)

    async def _call_openai_compat(self, prompt: str, ai_client: dict) -> dict:
        """Call any OpenAI-compatible endpoint (OpenAI, vLLM, Ollama, LiteLLM, etc.)."""
        endpoint = ai_client["base_url"].rstrip("/")
        headers = {
            "Authorization": f"Bearer {ai_client['api_key']}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": ai_client["model"],
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": ai_client["max_tokens"],
            "temperature": 0.2,
        }
        async with httpx.AsyncClient(timeout=120) as http_client:
            resp = await http_client.post(f"{endpoint}/chat/completions", headers=headers, json=payload)
            resp.raise_for_status()
            result = resp.json()
            text = result["choices"][0]["message"]["content"]
            return self._parse_json_response(text)

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

            "sop": f"""# Standard Operating Procedure
## {title}

### Purpose
Defines the steps to complete this process consistently.

### Prerequisites
- Access to: {', '.join(apps_used)}
- Valid credentials
- Required source data

### Procedure
1. Open the application and log in
2. Navigate to the relevant section
3. Enter the required data into the form fields
4. Select applicable options from dropdowns/checkboxes
5. Review all entries for accuracy
6. Submit the form
7. Confirm the submission was successful

### Decision Points
- If data is incorrect during review → go back to step 3
- If submission fails → check connectivity and retry

### Expected Outcome
- Transaction/record successfully created in the system
""",

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

    async def get_consulting_advice(self, report: ProcessReport, db: AsyncSession = None, user_id: int = None) -> str:
        """Generate consulting advice based on the process report."""
        # Resolve AI client
        ai_client = None
        if db and user_id:
            model_config = await self._get_model_config(db, user_id)
            ai_client = self._build_client(model_config)
        if not ai_client:
            ai_client = self._build_client(None)

        if not ai_client:
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

        if ai_client["type"] == "anthropic":
            message = ai_client["client"].messages.create(
                model=ai_client["model"],
                max_tokens=min(ai_client["max_tokens"], 4000),
                messages=[{"role": "user", "content": prompt}],
            )
            return message.content[0].text
        elif ai_client["type"] == "openai_compat":
            endpoint = ai_client["base_url"].rstrip("/")
            headers = {"Authorization": f"Bearer {ai_client['api_key']}", "Content-Type": "application/json"}
            async with httpx.AsyncClient(timeout=120) as http_client:
                resp = await http_client.post(f"{endpoint}/chat/completions", headers=headers, json={
                    "model": ai_client["model"],
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": min(ai_client["max_tokens"], 4000),
                    "temperature": 0.2,
                })
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"]
        return self._generate_demo_consulting(report)

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
