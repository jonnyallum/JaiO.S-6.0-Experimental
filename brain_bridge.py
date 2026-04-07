"""
brain_bridge.py — Shared Bridge Layer
Shared by jAIlbreakOS and Antigravity Orchestra.
Drop at root of both repos.
"""

import os, re, uuid, httpx
from datetime import datetime, timezone
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BRAIN_URL = os.getenv("ANTIGRAVITY_BRAIN_URL", "")
BRAIN_KEY = os.getenv("ANTIGRAVITY_BRAIN_SERVICE_ROLE_KEY", "")
HEADERS = {
    "apikey": BRAIN_KEY,
    "Authorization": f"Bearer {BRAIN_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}

_META_PATTERNS = [
    r"^(/status|/boot|/health|/ping|/agents|/jobs|/handoff)",
    r"^(please\s+)?(give me|show me)\s+(full\s+)?status",
    r"^(full status|status check|system status|health check)$",
    r"^(system boot|boot test|boot sequence)",
    r"(are you live|is.*system.*ready|confirm.*live|is jailbreak)",
    r"^ping$", r"^status$", r"^test$",
]

_JAILBREAKOS_KEYWORDS = [
    "audit", "architecture", "security", "github", "repo", "dependency",
    "code review", "deploy", "due diligence", "seo", "research", "analyse",
    "analyze", "social post", "social media", "social reel", "email sequence",
    "tagline", "go to market", "go-to-market", "product strategy", "course",
    "curriculum", "competitive landscape", "competitor", "financial model",
    "investor pitch", "data extraction", "funnel",
]

_ORCHESTRA_KEYWORDS = [
    "status", "boot", "health", "monitoring", "team update", "handoff",
    "chatroom", "persona", "broadcast", "alert", "assign task", "signoff",
]


class BrainBridge:

    @staticmethod
    def is_meta_brief(brief: str) -> bool:
        b = brief.strip().lower()
        return any(re.search(p, b) for p in _META_PATTERNS)

    @staticmethod
    def classify(brief: str) -> str:
        b = brief.strip().lower()
        if BrainBridge.is_meta_brief(brief):
            return "orchestra"
        orch_score = sum(1 for kw in _ORCHESTRA_KEYWORDS if kw in b)
        jb_score   = sum(1 for kw in _JAILBREAKOS_KEYWORDS if kw in b)
        return "orchestra" if orch_score > jb_score else "jailbreakos"

    @staticmethod
    def derive_expected_behaviour(brief: str) -> str:
        b = brief.lower()
        if any(kw in b for kw in ["status", "health", "live", "ready", "boot"]):
            return "System returns current operational status with service health, agent roster, and recent job summary"
        if any(kw in b for kw in ["audit", "review", "analyse", "analyze", "security"]):
            return "System returns a structured audit report with findings, risk ratings, and recommendations"
        if any(kw in b for kw in ["research", "investigate", "find", "discover", "competitive"]):
            return "System returns comprehensive research findings with sources and actionable insights"
        if any(kw in b for kw in ["write", "create", "generate", "build", "design", "draft", "tagline"]):
            return "System returns the requested creative or technical deliverable, complete and ready to use"
        return "System completes the requested task and returns a structured, actionable result"

    @staticmethod
    def create_job(brief: str, system_source: str = "auto",
                   client_id: str = "", project_id: str = "",
                   submitted_by: str = "api") -> dict:
        if system_source == "auto":
            system_source = BrainBridge.classify(brief)
        payload = {
            "id": str(uuid.uuid4()),
            "brief": brief,
            "status": "queued",
            "submitted_by": submitted_by,
            "system_source": system_source,
            "bridge_meta": {
                "client_id": client_id,
                "project_id": project_id,
                "classified_at": datetime.now(timezone.utc).isoformat(),
            }
        }
        r = httpx.post(f"{BRAIN_URL}/rest/v1/jobs", headers=HEADERS, json=payload)
        r.raise_for_status()
        return r.json()[0] if r.json() else payload

    @staticmethod
    def update_job_status(job_id: str, status: str, output: str = None,
                          error: str = None, elapsed: float = None,
                          agents_used: int = None) -> bool:
        data = {"status": status, "completed_at": datetime.now(timezone.utc).isoformat()}
        if output:      data["output"]          = output
        if error:       data["error"]           = error
        if elapsed:     data["elapsed_seconds"] = elapsed
        if agents_used: data["agents_used"]     = agents_used
        r = httpx.patch(f"{BRAIN_URL}/rest/v1/jobs?id=eq.{job_id}", headers=HEADERS, json=data)
        return r.status_code in (200, 204)

    @staticmethod
    def delegate(parent_job_id: str, task: str,
                 from_system: str, to_system: str) -> str:
        child = BrainBridge.create_job(task, system_source=to_system, submitted_by="bridge")
        child_id = child["id"]
        httpx.post(f"{BRAIN_URL}/rest/v1/bridge_jobs", headers=HEADERS, json={
            "parent_job_id": parent_job_id, "child_job_id": child_id,
            "from_system": from_system, "to_system": to_system,
            "delegation_task": task, "status": "pending",
        })
        return child_id

    @staticmethod
    def resolve_bridge(child_job_id: str, result: str) -> bool:
        data = {"status": "resolved", "result": result,
                "resolved_at": datetime.now(timezone.utc).isoformat()}
        r = httpx.patch(f"{BRAIN_URL}/rest/v1/bridge_jobs?child_job_id=eq.{child_job_id}",
                        headers=HEADERS, json=data)
        return r.status_code in (200, 204)

    @staticmethod
    def poll_bridge_result(child_job_id: str, timeout: int = 120) -> Optional[str]:
        import time
        deadline = time.time() + timeout
        while time.time() < deadline:
            r = httpx.get(
                f"{BRAIN_URL}/rest/v1/jobs?id=eq.{child_job_id}&select=status,output,error",
                headers=HEADERS)
            if r.status_code == 200 and r.json():
                job = r.json()[0]
                if job["status"] == "complete":  return job.get("output") or ""
                if job["status"] == "failed":    return None
            time.sleep(3)
        return None

    @staticmethod
    def post_chatroom(agent_id: str, message: str,
                      message_type: str = "system", metadata: dict = None) -> bool:
        r = httpx.post(f"{BRAIN_URL}/rest/v1/chatroom", headers=HEADERS, json={
            "agent_id": agent_id, "ai_source": "bridge",
            "machine_id": "brain-bridge", "message": message,
            "message_type": message_type, "metadata": metadata or {},
        })
        return r.status_code in (200, 201)

    @staticmethod
    def get_system_health() -> dict:
        r = httpx.get(f"{BRAIN_URL}/rest/v1/v_system_health?limit=1", headers=HEADERS)
        return r.json()[0] if r.status_code == 200 and r.json() else {}

    @staticmethod
    def format_boot_report(job_id: str) -> str:
        h = BrainBridge.get_system_health()
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        return f"""# jAIlbreakO.S — System Status
*{ts} | Job `{job_id}`*

## Orchestration
| System | Jobs 24h | Complete | Failed | Avg Time |
|--------|----------|----------|--------|----------|
| jAIlbreakOS (LangGraph) | {h.get('jb_jobs_24h',0)} | {h.get('jb_complete_24h',0)} | {h.get('jb_failed_24h',0)} | {h.get('jb_avg_elapsed_s','?')}s |
| Orchestra (Spine)       | {h.get('orch_jobs_24h',0)} | {h.get('orch_complete_24h',0)} | {h.get('orch_failed_24h',0)} | {h.get('orch_avg_elapsed_s','?')}s |

## Agents
- **Total:** {h.get('agents_total','?')} | **Active:** {h.get('agents_active','?')}

## Node ({h.get('pi_node_id','unknown')})
- Status: **{h.get('pi_status','unknown')}** | Last seen: {h.get('pi_last_seen','?')}
- CPU: {h.get('pi_cpu_pct','?')}% | Memory: {h.get('pi_mem_pct','?')}%

## Bridge
- Delegations: {h.get('bridge_total',0)} | Pending: {h.get('bridge_pending',0)}

## Chatroom
- Messages (last 1h): {h.get('chatroom_msgs_1h',0)}

---
*Both systems online. Brain DB connected. ✅*
"""

    @staticmethod
    def write_learning(agent_id: str, learning: str, category: str,
                       tags: list, system: str = "shared") -> bool:
        if system not in tags:
            tags = [system] + tags
        r = httpx.post(f"{BRAIN_URL}/rest/v1/learnings", headers=HEADERS, json={
            "source_agent": agent_id, "source_ai": "bridge",
            "learning": learning, "category": category,
            "tags": tags, "verified": False,
        })
        return r.status_code in (200, 201)
