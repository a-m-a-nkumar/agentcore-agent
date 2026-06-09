"""
Nova — Strategic Business Analyst AI for DLX_AGENTIC_OS
Powered by AWS Bedrock (Claude Sonnet 4.5)

Start: python agent.py
Requires: pip install fastapi uvicorn boto3 python-dotenv
Credentials: place in server/.env (auto-loaded)
Agent pool: server/agents.json (loaded at startup, no hardcoded values)
"""

# ── Imports ────────────────────────────────────────────────────────────────────

import json                                      # parse/serialize JSON (agents.json, response parsing)
import os                                        # read environment variables via os.getenv()
import re                                        # regex — used to strip tags and extract JSON from Claude's reply
from pathlib import Path                         # cross-platform file path handling (works on Windows + Linux)
from fastapi import FastAPI, HTTPException       # FastAPI = web framework; HTTPException = return error responses
from fastapi.middleware.cors import CORSMiddleware  # allows browser (React) to call this API from a different origin
from pydantic import BaseModel                   # defines request body shapes with automatic validation
import boto3                                     # AWS SDK — used to call Bedrock (Claude)
from dotenv import load_dotenv                   # reads .env file and loads values into os.environ

# ── Load environment variables ─────────────────────────────────────────────────

# Looks for a .env file in the same folder as this script (server/.env)
# Must be called before any os.getenv() so values are available
load_dotenv(Path(__file__).parent / ".env")

# ── Bedrock configuration (all from .env, with safe defaults) ──────────────────

BEDROCK_MODEL_ID      = os.getenv("BEDROCK_MODEL_ID", "global.anthropic.claude-sonnet-4-5-20250929-v1:0")
# ^ Which Claude model to use. "global." prefix = cross-region inference profile (more available capacity)

BEDROCK_REGION        = os.getenv("BEDROCK_REGION", "us-east-1")
# ^ AWS region where Bedrock is enabled. Claude Sonnet 4.5 is available in us-east-1

BEDROCK_MAX_TOKENS    = int(os.getenv("BEDROCK_MAX_TOKENS", "8192"))
# ^ Max tokens Claude can generate in one response. 8192 = large enough for the full report JSON

BEDROCK_TEMPERATURE   = float(os.getenv("BEDROCK_TEMPERATURE", "0"))
# ^ 0 = deterministic / focused output. Higher = more creative/random. Nova needs 0 for consistent JSON

BEDROCK_GUARDRAIL_ARN = os.getenv("BEDROCK_GUARDRAIL_ARN", "")
# ^ Optional: ARN of a Bedrock Guardrail to apply on agent simulation calls. Empty = disabled

BEDROCK_GUARDRAIL_VER = os.getenv("BEDROCK_GUARDRAIL_VERSION", "1")
# ^ Version number of the guardrail above. Only used if BEDROCK_GUARDRAIL_ARN is set

# ── FastAPI app setup ──────────────────────────────────────────────────────────

app = FastAPI(title="Nova Agent API")
# ^ Creates the web server instance. "title" shows up in auto-generated /docs page

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],    # allows requests from any domain (React dev on localhost:5173, prod on CloudFront, etc.)
    allow_methods=["*"],    # allows GET, POST, PUT, DELETE — all HTTP methods
    allow_headers=["*"],    # allows all headers including Content-Type, Authorization
)

# ── Agent pool — loaded from agents.json at startup ────────────────────────────

_agents_path = Path(__file__).parent / "agents.json"
# ^ Builds an absolute path to agents.json sitting next to this file

try:
    AGENT_POOL: list[dict] = json.loads(_agents_path.read_text(encoding="utf-8"))
    # ^ Reads the file as a string, parses it as JSON → list of 6 agent dicts loaded into memory
except FileNotFoundError:
    AGENT_POOL = []
    # ^ If agents.json is missing, start with empty pool (Nova will say "no agents registered")


# ── Workflow pool — loaded from workflows.json at startup ──────────────────────

_workflows_path = Path(__file__).parent / "workflows.json"

try:
    WORKFLOW_POOL: list[dict] = json.loads(_workflows_path.read_text(encoding="utf-8"))
except FileNotFoundError:
    WORKFLOW_POOL = []


# ── Helper: build agent pool as readable text block for the system prompt ──────

def _build_agent_pool_block() -> str:
    """Render agent pool as a structured text block for the system prompt."""

    if not AGENT_POOL:
        return "(No agents are currently registered in the pool.)"
        # ^ Guard: if list is empty, return a fallback string so Nova knows

    lines = []
    for a in AGENT_POOL:                                    # loop through each agent dict
        caps = "\n    - ".join(a.get("capabilities", []))  # join capabilities list into a bulleted string
        lines.append(
            f"Agent ID : {a['id']}\n"                      # e.g. agent-001
            f"Name     : {a['name']}\n"                    # e.g. SMB Onboarding Agent
            f"Segment  : {a['segment']}\n"                 # e.g. Merchant Services
            f"Purpose  : {a['description']}\n"             # one-line description
            f"Trigger  : {a.get('trigger', 'n/a')}\n"      # how the agent is triggered (webhook, schedule, etc.)
            f"Capabilities:\n    - {caps}"                 # bulleted list of what the agent can do
        )
    return "\n\n---\n\n".join(lines)
    # ^ Separates each agent block with a horizontal rule so Claude reads them as distinct entries


def _build_agent_id_list() -> str:
    # Returns a short comma-separated list like: agent-001 (SMB Onboarding Agent), agent-002 (...)
    # Used in the system prompt line: "ONLY reference agents from this list: ..."
    return ", ".join(f"{a['id']} ({a['name']})" for a in AGENT_POOL)


# ── Build Nova's full system prompt (injected into every Bedrock call) ─────────

def build_nova_system_prompt() -> str:
    """Assemble Nova system prompt with live agent pool data injected."""

    agent_block   = _build_agent_pool_block()   # full text block of all agents (used inside the prompt)
    agent_id_list = _build_agent_id_list()       # short comma list (used as the "allowed agents" reference)
    agent_count   = len(AGENT_POOL)              # count e.g. "6 registered" shown in prompt header

    # This is one giant f-string — the entire persona + rules + agent data Claude receives on every call.
    # The {{ }} double braces are escaped curly braces (needed in f-strings when outputting literal JSON examples)
    return f"""You are Nova, embedded in DLX_AGENTIC_OS - Deluxe Corporation's enterprise agentic platform.

WHO YOU ARE
You are Nova - an Automation Discovery Intelligence. You help Deluxe stakeholders describe operational pain, and map it to AI agents already running on the platform, or tell them exactly what to build next.
You are NOT a generic assistant. You are sharp, specific, and grounded in Deluxe's business reality.

DELUXE CONTEXT
Deluxe Corporation serves 4M+ SMBs across four segments:
  - Merchant Services  : payment acceptance, SMB onboarding, terminal provisioning, fraud prevention
  - Print & Retention  : check printing, business forms, direct mail, subscription retention
  - B2B Payments       : invoice processing, ACH/wire reconciliation, ERP integrations, GL posting
  - Data Solutions     : SMB enrichment, firmographic profiling, signal scoring, market intelligence

ROI benchmarks (use to ground estimates):
  - Manual invoice reconciliation: ~8-5 per invoice in labour cost
  - Stalled merchant onboarding: ~,200 lost lifetime value per application
  - Churn: avg Deluxe print customer = ,400/yr; early intervention recovers ~22%
  - Enrichment: each enriched SMB record improves conversion ~8-12%
  - Fraud dispute: ~90 in ops time per case

INTENT DETECTION — handle these BEFORE the discovery flow:

1. BUILD INTENT: If the user says they want to "build an agent", "create an agent", "make an agent", or names a specific agent type (even outside Deluxe's segments) — do NOT redirect or reject. Instead, acknowledge what they said in one sentence, then ask:
   "Would you like to add more details about what this agent should do, or should I send you straight to the Agent Builder with what you've told me?"
   Then output options:
   <question_options>
   ["Add more details first", "Build it now with this description"]
   </question_options>
   - If they choose "Add more details first" → ask ONE focused question about the agent's purpose, inputs, or outputs.
   - If they choose "Build it now with this description" OR after 1-2 clarifying exchanges → output the report JSON immediately with build_recommendation set to the agent they described and coverage "none" for all needs (since it's a net-new build request, no existing agent covers it).

2. DISCOVERY FLOW (default): If the user describes a business pain or problem — run the normal discovery below.

HOW YOU BEHAVE (discovery flow)
1. Ask EXACTLY ONE focused question per turn - never stack questions.
2. Your question must uncover scale, cost, or operational impact - not just context.
3. After 2-4 exchanges you have enough. Commit to the report. Do not over-ask.
4. Acknowledge what the user said before asking your next question - 1 sentence max.
5. Be specific: name processes, dollar figures, time durations. Never say "tell me more."
6. If the user already named a segment, skip the segment question and go deeper.
7. Keep pre-report turns short: 2-3 sentences + question.

Good questions:
  - How many invoices does AP manually reconcile each week, and what is the error rate?
  - When a merchant application stalls, how long before someone follows up?
  - Is churn concentrated in a specific tier or product line?

Bad (never ask):
  - Can you tell me more?
  - What are your goals?
  - Which segment? (if they already told you)

DEPLOYED AGENTS - {agent_count} registered
ONLY reference agents from this list: {agent_id_list}

{agent_block}

MATCHING RULES
- ONLY reference agent names from the deployed list above. NEVER invent agents.
- Match against ACTUAL listed capabilities - do not assume unstated ones.
- coverage "full"    : agent capabilities cover this need end-to-end, no gaps.
- coverage "partial" : agent covers core workflow but one specific capability is missing.
- coverage "none"    : no registered agent covers this. agent field MUST be null.
- One agent per need max. Do not assign the same agent to more than 3 needs.

OUTPUT FORMATS

WHEN ASKING A QUESTION - 1-2 sentences then your question, then:
<question_options>
["Specific Option A", "Specific Option B", "Specific Option C", "Specific Option D"]
</question_options>
Options must be concrete - for volume: ["Under 500/month", "500-2,000/month", "2,000-10,000/month", "Over 10,000/month"]

WHEN READY TO REPORT - output ONLY this JSON, nothing before or after:
{{
  "type": "report",
  "summary": "One sentence: the problem, the segment, and the core cost.",
  "needs": [
    {{"need": "Verb + object 4-7 words", "agent": "Exact Agent Name or null", "coverage": "full", "note": "One sentence: what the agent covers."}},
    {{"need": "Verb + object 4-7 words", "agent": "Exact Agent Name or null", "coverage": "partial", "note": "What it covers and what specific capability is missing."}},
    {{"need": "Verb + object 4-7 words", "agent": null, "coverage": "none", "note": "Why no registered agent handles this."}}
  ],
  "gaps": [
    "Specific gap 1 - name the missing capability and its business impact.",
    "Specific gap 2 - name the missing capability and its business impact."
  ],
  "roi": ",XXX/yr - based on explicit assumption from the conversation.",
  "build_recommendation": "Agent Name - one sentence: what it does, which segment, and the outcome it unlocks."
}}

STRICT RULES:
- Generate between 3 and 7 needs — as many as the problem genuinely requires, no padding.
- agent field must be exact name from deployed list OR null - never fabricated.
- coverage must be exactly full, partial, or none.
- gaps lists only the none and partial gaps - be specific.
- build_recommendation targets the single highest-value unmet need.
- Output ONLY the raw JSON object when reporting — no prose before or after, NO markdown code fences, NO ```json wrapper, NO backticks of any kind. Just the bare {{ ... }} object.
"""

# Build the system prompt once at server startup.
# Also called again inside reload_agents() whenever agents.json is hot-reloaded.
NOVA_SYSTEM = build_nova_system_prompt()

# In-memory store for all conversation histories.
# Key = sessionId (string from frontend), Value = list of Bedrock message dicts.
# Nova sessions use the raw sessionId; agent simulation sessions use "agent:<sessionId>".
# All data is lost when the server restarts — no database.
sessions: dict[str, list] = {}


# ── Request body schemas ───────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    sessionId: str    # unique ID from the frontend to track this user's conversation
    message: str      # the text the user typed into Imagination Studio


class AgentChatRequest(BaseModel):
    sessionId: str       # same session tracking, stored under "agent:<sessionId>" key
    message: str         # user's message to the simulated agent
    systemPrompt: str    # the agent's full system prompt (built in Agent Builder Phase 1)
    agentName: str = "Agent"   # display name echoed back in the response (default: "Agent")


# ── Response parser — turns Claude's raw text into structured frontend data ────

def parse_nova_response(text: str) -> dict:

    # Step 1: Remove markdown code fences Claude sometimes wraps around JSON
    # Handles both ```json ... ``` and plain ``` ... ```
    text = re.sub(r'```(?:json)?\s*', '', text)   # strip opening fence (with or without "json" label)
    text = re.sub(r'```', '', text)                # strip closing fence

    # Step 2: Look for quick-option chips (the clickable buttons shown below Nova's questions)
    # Claude outputs them as: <question_options>["Option A", "Option B"]</question_options>
    options = None
    options_match = re.search(
        r'<question_options>\s*(\[.*?\])\s*</question_options>',
        text, re.DOTALL,   # re.DOTALL makes . match newlines too (options can span lines)
    )
    if options_match:
        try:
            options = json.loads(options_match.group(1))   # group(1) = the [...] array inside the tags
        except json.JSONDecodeError:
            options = None   # if the array is malformed, silently ignore it

    # Step 3: Look for the final report JSON block — { "type": "report", ... }
    needs_analysis = None

    # Inner function: brace-balanced extractor
    # Walks through the string character by character counting { and } depth
    # to reliably find a complete nested JSON object, regardless of how deeply nested it is
    def extract_report_json(s: str):
        idx = s.find('"type"')           # find the first occurrence of "type" in the string
        while idx != -1:
            brace_start = s.rfind('{', 0, idx)   # look backward from "type" to find its opening brace
            if brace_start == -1:
                break                            # no opening brace found, stop

            depth = 0
            for i, ch in enumerate(s[brace_start:], brace_start):   # walk forward from the opening brace
                if ch == '{': depth += 1    # every { increases depth
                elif ch == '}':
                    depth -= 1             # every } decreases depth
                    if depth == 0:         # depth hits 0 → we found the matching closing brace
                        candidate = s[brace_start:i + 1]   # extract the full object as a string
                        try:
                            obj = json.loads(candidate)           # try to parse it as JSON
                            if obj.get('type') == 'report':       # only accept if it has "type": "report"
                                return obj, brace_start, i + 1    # return (parsed object, start index, end index)
                        except json.JSONDecodeError:
                            pass   # not valid JSON, skip and keep searching
                        break      # stop inner loop, try next "type" occurrence
            idx = s.find('"type"', idx + 1)   # look for the next "type" occurrence in the string
        return None, -1, -1   # nothing found

    report_obj, rstart, rend = extract_report_json(text)
    if report_obj:
        needs_analysis = report_obj   # store the parsed report dict

    # Step 4: Fallback — check for legacy <needs_analysis> XML tag (older format, kept for compatibility)
    if not needs_analysis:
        legacy_match = re.search(
            r'<needs_analysis>\s*(\{.*?\})\s*</needs_analysis>',
            text, re.DOTALL,
        )
        if legacy_match:
            try:
                needs_analysis = json.loads(legacy_match.group(1))
            except json.JSONDecodeError:
                needs_analysis = None

    # Step 5: Build clean display text by stripping all the special tags and JSON blocks
    clean = re.sub(r'<question_options>.*?</question_options>', '', text, flags=re.DOTALL)
    # ^ Remove the <question_options> block from the display text (frontend renders buttons separately)

    clean = re.sub(r'<needs_analysis>.*?</needs_analysis>', '', clean, flags=re.DOTALL)
    # ^ Remove legacy <needs_analysis> block if present

    if needs_analysis and rstart != -1:
        clean = clean[:rstart] + clean[rend:]
        # ^ Splice out the raw JSON block using the start/end indices from the extractor
    elif needs_analysis:
        clean = re.sub(r'\{.*"type"\s*:\s*"report".*', '', clean, flags=re.DOTALL)
        # ^ Fallback: if we don't have exact indices, use regex to remove the JSON block

    clean = clean.strip()   # remove leading/trailing whitespace from the final display text

    # Return a structured dict the frontend can directly use:
    # - message      → text to show in Nova's chat bubble (null if response was only a report)
    # - options      → array of clickable option buttons (null if it was not a question)
    # - needsAnalysis → full report object (null if Claude is still asking questions)
    return {
        "message": clean or None,
        "options": options,
        "needsAnalysis": needs_analysis,
    }


# ── Create the Bedrock client (one shared instance for all requests) ───────────

bedrock = boto3.client('bedrock-runtime', region_name=BEDROCK_REGION)
# ^ boto3 automatically picks up credentials from: .env (via load_dotenv) → env vars → IAM role


# ── Endpoint 1: Nova discovery chat (Imagination Studio) ──────────────────────

@app.post("/chat")
async def chat(req: ChatRequest):
    # Create an empty history list for new sessions
    if req.sessionId not in sessions:
        sessions[req.sessionId] = []

    history = sessions[req.sessionId]   # get the existing conversation list for this session

    # Append the new user message in Bedrock's expected format
    history.append({
        "role": "user",
        "content": [{"text": req.message}],   # Bedrock requires content as a list of content blocks
    })

    try:
        # Build the Bedrock converse() call arguments
        converse_kwargs = {
            "modelId": BEDROCK_MODEL_ID,             # which Claude model to use
            "system": [{"text": NOVA_SYSTEM}],       # Nova's full persona + agent pool injected here
            "messages": history,                     # full conversation history (multi-turn memory)
            "inferenceConfig": {
                "maxTokens": BEDROCK_MAX_TOKENS,     # max response length
                "temperature": BEDROCK_TEMPERATURE,  # 0 = focused/deterministic
            },
        }
        # NOTE: No guardrailConfig here — Nova uses its system prompt as the only constraint.
        # Guardrails are only applied on /agent/chat (deployed agent simulation).

        response = bedrock.converse(**converse_kwargs)   # call AWS Bedrock synchronously
        reply = response["output"]["message"]["content"][0]["text"]
        # ^ Navigate the Bedrock response envelope to get the plain text reply string

        # Append Claude's reply to history so the next turn sees the full conversation
        history.append({
            "role": "assistant",
            "content": [{"text": reply}],
        })
        sessions[req.sessionId] = history   # save updated history back to the session store

        return parse_nova_response(reply)   # parse and return structured response to frontend

    except Exception as exc:
        sessions[req.sessionId] = history[:-1]
        # ^ Roll back: remove the user message we just added so history stays in a consistent state

        # Return a user-friendly error as a chat bubble (no HTTP 500 — frontend handles it gracefully)
        return {
            "message": (
                f"I'm having trouble reaching my analysis engine right now. "
                f"Please ensure AWS Bedrock access is configured for region us-east-1. "
                f"Error: {exc}"
            ),
            "options": None,
            "needsAnalysis": None,
            "error": str(exc),   # raw error string for debugging in browser dev tools
        }


# ── Endpoint 2: Deployed agent simulation (Agent Builder Phase 2) ──────────────

@app.post("/agent/chat")
async def agent_chat(req: AgentChatRequest):
    """Run any built agent with its own system prompt — used in Simulation & Testing phase."""

    key = f"agent:{req.sessionId}"
    # ^ Namespace agent simulation sessions separately from Nova sessions.
    #   "agent:abc-123" vs "abc-123" — both can exist for the same user without colliding.

    if key not in sessions:
        sessions[key] = []   # create fresh history for this agent simulation session

    history = sessions[key]
    history.append({
        "role": "user",
        "content": [{"text": req.message}],   # user's message to the simulated agent
    })

    try:
        converse_kwargs = {
            "modelId": BEDROCK_MODEL_ID,
            "system": [{"text": req.systemPrompt}],   # agent's own system prompt (from Agent Builder form)
            "messages": history,
            "inferenceConfig": {
                "maxTokens": BEDROCK_MAX_TOKENS,
                "temperature": BEDROCK_TEMPERATURE,
            },
        }

        # Optionally apply Bedrock Guardrails if an ARN was configured in .env
        # Guardrails add content filtering / topic blocking on top of the model's own behaviour
        if BEDROCK_GUARDRAIL_ARN:
            converse_kwargs["guardrailConfig"] = {
                "guardrailIdentifier": BEDROCK_GUARDRAIL_ARN,   # the Guardrail resource ARN
                "guardrailVersion": BEDROCK_GUARDRAIL_VER,      # version number of that guardrail
                "trace": "enabled",                             # include guardrail trace in response (for debugging)
            }

        response = bedrock.converse(**converse_kwargs)
        reply = response["output"]["message"]["content"][0]["text"]   # extract plain text reply

        history.append({
            "role": "assistant",
            "content": [{"text": reply}],
        })
        sessions[key] = history   # save updated history

        # Return plain message + agent name — no parsing needed (agent responses are plain text, not structured)
        return {"message": reply, "agentName": req.agentName}

    except Exception as exc:
        sessions[key] = history[:-1]   # roll back user message on error
        return {
            "message": f"Agent error: {exc}",
            "agentName": req.agentName,
            "error": str(exc),
        }


# ── Endpoint: Fill agent spec from documentation pages ────────────────────────

class FillFromDocsRequest(BaseModel):
    content: str          # combined text from selected Velox/Confluence pages
    existingSpec: dict = {}  # current form state — enrich this, don't replace it

@app.post("/fill-from-docs")
async def fill_from_docs(req: FillFromDocsRequest):
    """Enrich an existing agent spec with context from documentation pages. Preserves existing content."""

    existing = req.existingSpec or {}
    has_existing = any([
        existing.get("name"), existing.get("description"),
        existing.get("systemPrompt"), existing.get("skills"),
    ])

    if has_existing:
        existing_block = (
            f"EXISTING AGENT SPECIFICATION:\n"
            f"Name: {existing.get('name') or 'not set'}\n"
            f"Description: {existing.get('description') or 'not set'}\n"
            f"Segment: {existing.get('segment') or 'not set'}\n"
            f"Current Skills: {', '.join(existing.get('skills') or []) or 'none'}\n"
            f"System Prompt:\n{existing.get('systemPrompt') or 'not set'}\n"
        )
        user_message = (
            f"{existing_block}\n\n"
            f"DOCUMENTATION PAGES:\n{req.content}\n\n"
            "Using the documentation pages, produce an enriched agent spec. Rules:\n"
            "- name: keep existing unless empty\n"
            "- description: write ONE unified 1-2 sentence description that naturally combines the existing purpose with any new context from the docs. It must read as a single sentence, not two joined together.\n"
            "- segment: keep existing unless empty\n"
            "- skills: union of existing skills plus any relevant skills from docs (max 10 total)\n"
            "- systemPrompt: rewrite as ONE single coherent system prompt. Intelligently merge the existing instructions and the documentation context so the result reads as if written by one author from scratch. Do NOT include section headers like 'Additional context', 'From docs', or any dividers. Every instruction should flow naturally. Preserve all existing responsibilities, enhance with new ones, remove any redundancy."
        )
    else:
        user_message = f"Write a complete agent specification based on these documentation pages:\n\n{req.content}"

    system_prompt = (
        "You are an expert AI agent specification writer for Deluxe Corporation DLX_AGENTIC_OS. "
        "Return ONLY a valid JSON object with these exact keys: "
        "name (string), description (string), segment (one of: merchant, print, b2b, data, platform), "
        "skills (array — only from: crm-read, crm-write, email-send, kyb-verify, erp-read, gl-write, "
        "payment-match, txn-stream, anomaly-detect, hold-trigger, data-fetch, profile-update, "
        "sales-alert, report-gen, order-history, terminal-config, propensity-score, webhook-send), "
        "systemPrompt (string: a single, coherent, well-structured agent prompt with no section dividers or source markers). "
        "The output must read as a single piece of work written by one author — never two prompts stitched together. "
        "Return ONLY the JSON object, no markdown, no explanation."
    )
    try:
        response = bedrock.converse(
            modelId=BEDROCK_MODEL_ID,
            system=[{"text": system_prompt}],
            messages=[{"role": "user", "content": [{"text": user_message}]}],
            inferenceConfig={"maxTokens": 3000, "temperature": 0},
        )
        reply = response["output"]["message"]["content"][0]["text"].strip()
        # Strip markdown fences if present
        reply = re.sub(r"```(?:json)?\s*", "", reply)
        reply = re.sub(r"```", "", reply).strip()
        match = re.search(r'\{[\s\S]*\}', reply)
        if match:
            spec = json.loads(match.group(0))
            return {"success": True, "spec": spec}
        return {"success": False, "error": "Model did not return valid JSON", "raw": reply[:500]}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ── Endpoint 3: List all agents (used by frontend pages) ──────────────────────

@app.get("/agents")
async def list_agents():
    """Return the current agent pool. Used by the frontend to avoid hardcoding."""
    return AGENT_POOL
    # ^ Returns the in-memory list loaded from agents.json at startup.
    #   Called by Imagination Studio, Agent Pool, and Dashboard pages.


# ── Endpoint 4: Hot-reload agents.json without server restart ─────────────────

@app.put("/agents/reload")
async def reload_agents():
    """Hot-reload agents.json without restarting the server."""
    global AGENT_POOL, NOVA_SYSTEM
    # ^ global keyword needed to overwrite the module-level variables
    try:
        AGENT_POOL = json.loads(_agents_path.read_text(encoding="utf-8"))
        # ^ Re-read agents.json from disk — picks up any additions or edits

        NOVA_SYSTEM = build_nova_system_prompt()
        # ^ Rebuild Nova's system prompt with the updated agent list
        #   All new /chat calls will now use the refreshed prompt

        return {"status": "reloaded", "count": len(AGENT_POOL)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
        # ^ Return a proper 500 error if the file is missing or malformed


# ── Endpoint 5: Clear a session (Reset button in Imagination Studio) ───────────

@app.delete("/session/{session_id}")
async def clear_session(session_id: str):
    sessions.pop(session_id, None)
    # ^ Remove the session from the dict. The None default means no error if key doesn't exist.
    return {"status": "cleared"}


# ── Endpoint 6: Health check ───────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "sessions": len(sessions)}
    # ^ Simple liveness check. "sessions" count lets you see how many active conversations are in memory.



# ── Endpoint 7: List workflow pool ─────────────────────────────────────────────

@app.get("/workflows")
async def list_workflows():
    return WORKFLOW_POOL


# ── Endpoint 8: Workflow analysis — 3-scenario matching ───────────────────────

class WorkflowAnalyzeRequest(BaseModel):
    problem: str
    session_id: str = ""


@app.post("/workflow/analyze")
async def workflow_analyze(req: WorkflowAnalyzeRequest):
    """
    Intelligently decompose a business problem into a sequential agent chain.
    Each step is dynamically matched against the live agent pool — no hardcoded workflows.
    Returns scenario 1 / 2 / 3 with a full chain ready for visual rendering.
    """

    # Build a rich agent pool description for Nova to reason against
    agent_pool_text = ""
    for a in AGENT_POOL:
        caps  = "\n      - ".join(a.get("capabilities", []))
        tools = ", ".join(a.get("tools", []))
        solves     = ", ".join(a.get("solves", []))
        not_solves = ", ".join(a.get("doesNotSolve", []))
        agent_pool_text += (
            f"AGENT: {a['name']} (ID: {a['id']})\n"
            f"  Segment     : {a.get('segment','')}\n"
            f"  Description : {a.get('description','')}\n"
            f"  Capabilities:\n      - {caps}\n"
            f"  Tools       : {tools}\n"
            f"  Solves      : {solves}\n"
            f"  Does NOT solve: {not_solves}\n\n"
        )

    system_prompt = """You are Nova, an intelligent workflow architect inside DLX_AGENTIC_OS.

TASK
Given a business problem, you must:
1. Break it into 2–5 logical sequential STEPS that together solve the problem end-to-end.
2. For each step, find the BEST matching agent from the registered pool — or mark it as missing.
3. Return a structured JSON chain the UI can render as a visual flow diagram.

MATCHING RULES
- "full"    → an existing agent covers this step completely. Use its exact name and ID.
- "partial" → an existing agent covers it partially; name the specific gap.
- "none"    → no registered agent handles this step at all.

SCENARIO DETERMINATION
- Scenario 3 (all green)  : every step is "full"  → workflow is ready to deploy
- Scenario 2 (mixed)      : at least one step is "partial" or "none" but some are "full"
- Scenario 1 (all red)    : every step is "none"  → nothing exists, generate spec_doc for Velox

OUTPUT — return ONLY raw JSON, no markdown fences, no prose outside the object:
{
  "scenario": 2,
  "summary": "One sentence: what the problem needs, what exists, what is missing.",
  "chain": [
    {
      "step": 1,
      "title": "Short action title (4–6 words)",
      "description": "One sentence: what happens at this step and why it matters.",
      "status": "full",
      "agent_id": "agent-001",
      "agent_name": "Exact Agent Name from pool",
      "agent_role": "One sentence: what this specific agent does at this step.",
      "match_score": 94,
      "covers": "What the agent covers at this step.",
      "gap": null,
      "suggestion": null,
      "tools": ["tool-a", "tool-b"]
    },
    {
      "step": 2,
      "title": "Short action title",
      "description": "One sentence description.",
      "status": "partial",
      "agent_id": "agent-003",
      "agent_name": "Exact Agent Name",
      "agent_role": "What it does here.",
      "match_score": 65,
      "covers": "What it covers.",
      "gap": "Specific capability that is missing.",
      "suggestion": "Add X tool or extend with Y capability.",
      "tools": ["tool-c"]
    },
    {
      "step": 3,
      "title": "Short action title",
      "description": "One sentence description.",
      "status": "none",
      "agent_id": null,
      "agent_name": null,
      "agent_role": null,
      "match_score": 0,
      "covers": null,
      "gap": "No registered agent handles this.",
      "suggestion": "Build a new agent named X with tools Y and Z.",
      "tools": ["suggested-tool-1", "suggested-tool-2"]
    }
  ],
  "spec_doc": {
    "title": "Build Specification — <workflow name>",
    "problem": "Restated problem in one sentence.",
    "steps_to_build": [
      {
        "step": 3,
        "agent_name": "Suggested Agent Name",
        "role": "What it does in the workflow.",
        "tools": ["tool-1", "tool-2"],
        "capabilities": ["capability 1", "capability 2"],
        "trigger": "event/schedule/webhook",
        "estimated_roi": "e.g. saves 4 hrs/day per analyst"
      }
    ],
    "next_step": "Take this specification to the Velox platform and build the missing agents from scratch."
  }
}

STRICT RULES
- chain must have 2–5 items — exactly as many steps as the problem genuinely requires.
- agent_id and agent_name must be exact values from the pool, or null for "none" steps.
- CRITICAL: NEVER assign the same agent_id to more than one step. Every step must use a DIFFERENT agent, or null. If no other registered agent fits a step, mark it status "none" and set agent_id/agent_name to null.
- Each step must represent a DISTINCT phase of the workflow — intake, processing, decision, notification, etc. Never duplicate a step's purpose.
- Make each step title and description UNIQUE and specific to what happens at that phase.
- spec_doc.steps_to_build must ONLY include steps whose status is "none" — never "full" or "partial".
- If scenario is 3 (all full), omit spec_doc entirely.
- If scenario is 2 (mixed), omit spec_doc entirely.
- Only include spec_doc when scenario is 1.
- match_score: 85–99 for full, 40–84 for partial, 0 for none.
"""

    user_message = f"""Business problem:
{req.problem}

REGISTERED AGENT POOL ({len(AGENT_POOL)} agents):
{agent_pool_text if agent_pool_text else "(empty — no agents registered yet)"}

Decompose this problem into a sequential agent chain and return the JSON."""

    try:
        response = bedrock.converse(
            modelId=BEDROCK_MODEL_ID,
            system=[{"text": system_prompt}],
            messages=[{"role": "user", "content": [{"text": user_message}]}],
            inferenceConfig={"maxTokens": 4096, "temperature": 0},
        )
        text = response["output"]["message"]["content"][0]["text"].strip()

        # Strip markdown code fences if Claude wraps its response
        text = re.sub(r'^```(?:json)?', '', text, flags=re.MULTILINE).strip()
        text = re.sub(r'```$',          '', text, flags=re.MULTILINE).strip()

        result = json.loads(text)
        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Workflow analysis failed: {str(e)}")


# ── Entry point — only runs when executed directly (python agent.py) ───────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
    # ^ host="0.0.0.0" means accept connections from any network interface (needed for EC2/Docker)
    # ^ port=8000 — Vite proxy forwards /api/nova/* to this port
    # ^ log_level="info" — prints each request to the terminal for easy debugging
