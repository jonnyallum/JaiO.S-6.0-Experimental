# Phase 1 Implementation Brief

> **From:** @Marcus Cole (Orchestrator)  
> **To:** @Sebastian (Full-Stack Architect) + @Derek (Infrastructure Lead)  
> **Date:** 2026-03-09  
> **Timeline:** Week 1-2 (Complete by March 23rd)  
> **Priority:** HIGH — Foundation for entire Jai.OS 6.0 rebuild

---

## Mission

**Prove LangGraph works with the Antigravity Orchestra infrastructure.**

Build one working graph with real GitHub intelligence, state persistence to Supabase, and production-quality error handling. This proof-of-concept determines if we commit to the full 5-month migration.

---

## Success Criteria

**Must achieve all 5:**

1. ✅ **@hugo returns real GitHub intelligence** (not stub data)
2. ✅ **State persists to Supabase** (`graph_state` table)
3. ✅ **Execution completes in <30 seconds** (for single repo analysis)
4. ✅ **Memory usage <3GB** on current GCP VM (e2-medium, 4GB RAM)
5. ✅ **Error handling works** (test with invalid repo, confirm graceful failure)

**Decision point:** End of Week 2 — if all 5 pass, we commit to full migration. If any fail, we abort and stay on Jai.OS 5.0.

---

## Deliverables

### 1. Working LangGraph Installation (GCP VM)

**Owner:** @Derek

```bash
# SSH to GCP VM
ssh antigravity@35.230.148.83

# Clone repo
cd /opt/antigravity
git clone https://github.com/jonnyallum/JaiO.S-6.0-Experimental.git
cd JaiO.S-6.0-Experimental

# Install dependencies
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Verify installation
python -c "import langgraph; print(langgraph.__version__)"
```

**Expected output:** `0.2.x` or higher

**Acceptance:** LangGraph imports without errors, all dependencies installed

---

### 2. Supabase State Schema

**Owner:** @Derek (infra) + @Diana (schema design)

**Task:** Create `graph_state` table in Supabase for LangGraph checkpointing

**Schema:**

```sql
-- Create state persistence table
CREATE TABLE graph_state (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id TEXT NOT NULL,
    state_json JSONB NOT NULL,
    checkpoint_id TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for fast lookups
CREATE INDEX idx_graph_state_workflow ON graph_state(workflow_id);
CREATE INDEX idx_graph_state_checkpoint ON graph_state(checkpoint_id);

-- RLS policy: all agents can read/write their own workflows
ALTER TABLE graph_state ENABLE ROW LEVEL SECURITY;

CREATE POLICY "workflow_access" ON graph_state
    USING (true)  -- For Phase 1, allow all access. Tighten in Phase 6.
    WITH CHECK (true);
```

**Configuration file:**

```bash
# Create config directory
mkdir -p config

# Create Supabase config
cat > config/supabase.json <<EOF
{
  "url": "https://lkwydqtfbdjhxaarelaz.supabase.co",
  "anon_key": "<SUPABASE_ANON_KEY>",
  "state_table": "graph_state",
  "checkpoint_interval": "per_node"
}
EOF

# Keep secrets out of git
echo "config/supabase.json" >> .gitignore
```

**Acceptance:** Table exists, can write/read state from Python

---

### 3. Implement @hugo Agent (Real GitHub Intelligence)

**Owner:** @Sebastian

**Task:** Convert `agents/hugo.py` stub to working agent using GitHub MCP server

**Implementation:**

```python
"""@hugo — GitHub Intelligence Specialist"""

from typing_extensions import TypedDict
import structlog
import anthropic
from tools.github_tools import GitHubTools

log = structlog.get_logger()


class HugoState(TypedDict):
    """State for @hugo GitHub intelligence tasks"""
    repo_owner: str
    repo_name: str
    query: str
    intelligence: str


def hugo_node(state: HugoState) -> dict:
    """
    @hugo — GitHub Intelligence Specialist
    
    Analyzes repositories, pull requests, issues, commits.
    Provides actionable intelligence for development decisions.
    """
    log.info(
        "hugo_started",
        repo=f"{state['repo_owner']}/{state['repo_name']}",
        query=state["query"],
    )

    try:
        # Initialize tools
        github = GitHubTools()
        claude = anthropic.Anthropic()

        # Fetch repo structure
        readme = github.get_file_contents(
            owner=state["repo_owner"],
            repo=state["repo_name"],
            path="README.md"
        )

        # Get recent commits
        commits = github.list_commits(
            owner=state["repo_owner"],
            repo=state["repo_name"],
            per_page=10
        )

        # Claude analyzes
        response = claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            messages=[{
                "role": "user",
                "content": f"""
                Analyze this GitHub repository:
                
                README:
                {readme['content']}
                
                Recent commits (last 10):
                {commits}
                
                Query: {state['query']}
                
                Provide actionable intelligence for this query.
                """
            }]
        )

        intelligence = response.content[0].text

        log.info("hugo_completed", intelligence_length=len(intelligence))
        return {"intelligence": intelligence}

    except Exception as e:
        log.error("hugo_failed", error=str(e))
        raise
```

**GitHub MCP wrapper:**

```python
# tools/github_tools.py
from mcp import Client

class GitHubTools:
    def __init__(self):
        self.client = Client("github_mcp_direct")
    
    def get_file_contents(self, owner: str, repo: str, path: str) -> dict:
        return self.client.call(
            "get_file_contents",
            owner=owner,
            repo=repo,
            path=path,
            ref="main",
            sha=""
        )
    
    def list_commits(self, owner: str, repo: str, per_page: int = 10) -> list:
        return self.client.call(
            "list_commits",
            owner=owner,
            repo=repo,
            sha="",
            page=1,
            perPage=per_page,
            author=""
        )
```

**Test it:**

```python
# test_hugo.py
from agents.hugo import hugo_node

state = {
    "repo_owner": "jonnyallum",
    "repo_name": "Antigravity_Orchestra",
    "query": "What are the most critical components of this system?",
    "intelligence": ""
}

result = hugo_node(state)
print(result["intelligence"])
```

**Acceptance:** @hugo returns real analysis of Antigravity_Orchestra repo

---

### 4. Build First Graph (Simple Test)

**Owner:** @Sebastian

**Task:** Create minimal graph to test state persistence

**Implementation:**

```python
# graphs/test_graph.py
from langgraph.graph import StateGraph, START, END
from agents.hugo import hugo_node, HugoState
from langgraph.checkpoint.postgres import PostgresSaver
import psycopg2
import json

def build_test_graph():
    """Simple graph: START → @hugo → END"""
    
    # Configure Supabase checkpoint saver
    config = json.load(open("config/supabase.json"))
    conn_string = f"postgresql://postgres:[PASSWORD]@db.{config['url'].split('//')[1].split('.')[0]}.supabase.co:5432/postgres"
    
    # Create checkpointer
    conn = psycopg2.connect(conn_string)
    checkpointer = PostgresSaver(conn)
    
    # Build graph
    graph = StateGraph(HugoState)
    graph.add_node("hugo", hugo_node)
    graph.add_edge(START, "hugo")
    graph.add_edge("hugo", END)
    
    # Compile with checkpointing
    return graph.compile(checkpointer=checkpointer)


if __name__ == "__main__":
    app = build_test_graph()
    
    # Test state
    initial_state = {
        "repo_owner": "jonnyallum",
        "repo_name": "Antigravity_Orchestra",
        "query": "Summarize this repo's architecture",
        "intelligence": ""
    }
    
    # Execute
    print("Running test graph...")
    result = app.invoke(initial_state)
    
    print("\n=== RESULT ===")
    print(result["intelligence"][:500])  # First 500 chars
    
    # Verify state persisted
    print("\n=== STATE PERSISTENCE CHECK ===")
    # Query Supabase graph_state table
    # Confirm checkpoint exists
```

**Run it:**

```bash
python graphs/test_graph.py
```

**Acceptance:** 
- Graph executes successfully
- Intelligence returned
- State written to Supabase `graph_state` table

---

### 5. Performance & Memory Testing

**Owner:** @Derek

**Task:** Monitor resource usage during graph execution

**Commands:**

```bash
# Install monitoring tools
sudo apt-get install -y htop sysstat

# Monitor during execution
htop &
python graphs/test_graph.py

# Check memory usage
free -h

# Check execution time
time python graphs/test_graph.py
```

**Collect metrics:**

| Metric | Target | Actual | Pass/Fail |
|--------|--------|--------|----------|
| Peak memory usage | <3GB | ??? | ??? |
| Execution time | <30s | ??? | ??? |
| State write latency | <100ms | ??? | ??? |
| CPU usage (peak) | <80% | ??? | ??? |

**Report format:**

```markdown
## Performance Test Results

**Test date:** 2026-03-XX  
**VM:** e2-medium (4GB RAM, 2 vCPU)  
**Graph:** test_graph.py (START → @hugo → END)

### Metrics

- Peak memory: X.XXgb
- Execution time: XX.Xs
- State write latency: XXms
- CPU peak: XX%

### Verdict

✅ All targets met — proceed to Phase 2  
❌ Failed on [metric] — need VM upgrade before proceeding
```

**Acceptance:** All 4 metrics within target range

---

### 6. Error Handling Test

**Owner:** @Sebastian

**Task:** Confirm graceful failure when things go wrong

**Test cases:**

```python
# Test 1: Invalid repo
state = {
    "repo_owner": "invalid",
    "repo_name": "does-not-exist",
    "query": "test",
    "intelligence": ""
}

# Expected: Exception raised, logged, workflow stops gracefully

# Test 2: GitHub API rate limit
# (Simulate by making 60+ requests quickly)

# Expected: Exponential backoff, retry logic kicks in

# Test 3: Supabase connection failure
# (Temporarily break DB connection)

# Expected: Checkpoint fails, workflow stops, state not lost
```

**Acceptance:** All 3 test cases handled gracefully (no crashes, proper logging)

---

## Timeline

| Task | Owner | Days | Target Date |
|------|-------|------|-------------|
| Install LangGraph on VM | @Derek | 1 | March 10 |
| Create Supabase schema | @Derek + @Diana | 1 | March 11 |
| Implement @hugo | @Sebastian | 2 | March 13 |
| Build test graph | @Sebastian | 1 | March 14 |
| Performance testing | @Derek | 1 | March 15 |
| Error handling tests | @Sebastian | 1 | March 16 |
| **Final report** | @Sebastian + @Derek | 1 | **March 17** |

**Buffer:** 6 days (March 18-23) for unexpected issues

---

## Decision Matrix (End of Week 2)

### ✅ GO — Commit to Full Migration

**If:**
- All 5 success criteria met
- Performance acceptable (<3GB memory, <30s execution)
- Error handling robust
- @Sebastian + @Derek confident in architecture

**Then:**
- Upgrade VM to e2-standard-2 (8GB RAM) — +£15/mo
- Begin Phase 2: Migrate remaining 65 agents
- Target completion: August 2026

### ❌ NO-GO — Stay on Jai.OS 5.0

**If:**
- Memory usage >3.5GB (VM upgrade required immediately)
- Execution time >60s (too slow for production)
- State persistence unreliable (data loss risk)
- Complexity too high (maintenance burden)

**Then:**
- Archive JaiO.S-6.0-Experimental repo
- Stay on current Python scripts
- Revisit in 6 months (LangGraph may mature)

---

## Support & Escalation

**Blockers:** Escalate to @Marcus immediately (Telegram: @marcus_antigravity)

**Questions:**
- Technical: @Sebastian
- Infrastructure: @Derek
- Database: @Diana
- Orchestration: @Marcus

**Daily standup:** Post progress to `chatroom.md` in Antigravity_Orchestra repo

---

## Resources

**Documentation:**
- [README.md](../README.md) — System overview
- [ARCHITECTURE.md](../ARCHITECTURE.md) — Technical deep dive
- [LangGraph docs](https://github.com/langchain-ai/langgraph) — Official documentation

**Credentials:**
- Supabase: Check `.env` on GCP VM
- Claude API: Use existing `ANTHROPIC_API_KEY`
- GitHub MCP: Already configured on VM

**GCP VM Access:**
```bash
ssh antigravity@35.230.148.83
# Password in 1Password ("GCP VM - Antigravity")
```

---

## Final Note

**This is the foundation for the entire 6.0 rebuild.** If Phase 1 succeeds, we get:

- Production-grade orchestration
- Self-correcting agents
- Visual debugging
- Scalability to 100+ agents
- Mobile command interface (OpenClaw in Phase 4)
- Client AI receptionist revenue (+£600-1,800/mo in Phase 5)

If Phase 1 fails, we've lost 2 weeks and learned LangGraph isn't ready. That's acceptable.

**Take your time. Build it right. Report blockers immediately.**

---

**— @Marcus Cole**  
**Orchestrator, Antigravity Orchestra**  
**2026-03-09 20:09 GMT**
