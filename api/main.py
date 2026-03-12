#!/usr/bin/env python3
# Load .env before anything else — agents rely on os.environ for API keys
from dotenv import load_dotenv
load_dotenv()
"""
JaiO.S 6.0 — API Entry Point

FastAPI server that receives job requests, routes them through the
LangGraph supervisor, logs state to Supabase, and returns results.

Modes:
  1. FastAPI (uvicorn): POST /run, GET /job/{id}, GET /jobs, GET /health
  2. Daemon: polls Supabase jobs queue every 30s
  3. CLI:   python3 -m api.main submit "brief" / status <id> / daemon

Endpoints:
  POST /run          — submit a job (async background or sync)
  GET  /job/{id}     — get job status + output
  GET  /jobs         — list recent jobs
  GET  /agents       — list all 61 registered agents
  GET  /health       — liveness check
"""

import os
import sys
import json
import uuid
import time
import logging
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from contextlib import asynccontextmanager

import requests
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from config.settings import settings
from graphs.supervisor import run_supervisor

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [API] %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/api.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("api")

# ── structlog → Python logging bridge ─────────────────────────────────────────
# Route structlog through Python stdlib logging so it uses our FileHandler
import structlog
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.StackInfoRenderer(),
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

VERSION   = "6.0.0"
BOOT_TIME = datetime.now(timezone.utc)

Path("logs").mkdir(exist_ok=True)


# ── Supabase helpers ──────────────────────────────────────────────────────────

def _headers() -> dict:
    return {
        "apikey":        settings.brain_service_role_key,
        "Authorization": f"Bearer {settings.brain_service_role_key}",
        "Content-Type":  "application/json",
        "Prefer":        "return=representation",
    }

def _get(table: str, params: dict = None) -> list:
    try:
        r = requests.get(
            f"{settings.brain_url}/rest/v1/{table}",
            headers=_headers(), params=params, timeout=15,
        )
        return r.json() if r.status_code == 200 else []
    except Exception as exc:
        log.error(f"supabase GET {table}: {exc}")
        return []

def _post(table: str, data: dict) -> Optional[dict]:
    try:
        r = requests.post(
            f"{settings.brain_url}/rest/v1/{table}",
            headers=_headers(), json=data, timeout=15,
        )
        if r.status_code in (200, 201):
            result = r.json()
            return result[0] if isinstance(result, list) and result else result
        log.error(f"supabase POST {table}: {r.status_code} {r.text[:200]}")
    except Exception as exc:
        log.error(f"supabase POST {table}: {exc}")
    return None

def _patch(table: str, filters: dict, data: dict) -> bool:
    try:
        params = {k: f"eq.{v}" for k, v in filters.items()}
        r = requests.patch(
            f"{settings.brain_url}/rest/v1/{table}",
            headers=_headers(), params=params, json=data, timeout=15,
        )
        return r.status_code in (200, 204)
    except Exception as exc:
        log.error(f"supabase PATCH {table}: {exc}")
        return False


# ── Job execution ─────────────────────────────────────────────────────────────

def _execute_job(job_id: str, brief: str) -> dict:
    """Run a job through the LangGraph supervisor. Writes state to Supabase."""
    log.info(f"JOB START {job_id} | {brief[:80]}")
    start = time.time()

    _patch("jobs", {"id": job_id}, {
        "status":     "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
    })

    try:
        result = run_supervisor({"request": brief, "workflow_id": job_id})
        elapsed = round(time.time() - start, 1)

        _patch("jobs", {"id": job_id}, {
            "status":           "complete",
            "output":           json.dumps(result)[:50000],
            "completed_at":     datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds":  elapsed,
        })

        log.info(f"JOB DONE {job_id} | {elapsed}s")
        return {"status": "complete", "job_id": job_id, "elapsed": elapsed, "output": result}

    except Exception as exc:
        tb = traceback.format_exc()
        log.error(f"JOB FAILED {job_id}: {exc}\n{tb}")
        _patch("jobs", {"id": job_id}, {
            "status":       "failed",
            "error":        str(exc)[:2000],
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })
        return {"status": "failed", "job_id": job_id, "error": str(exc)}


def _submit_job(brief: str, submitted_by: str = "api") -> str:
    """Create a job record in Supabase and return the job_id."""
    job_id = str(uuid.uuid4())
    result = _post("jobs", {
        "id":           job_id,
        "brief":        brief,
        "status":       "queued",
        "submitted_by": submitted_by,
        "created_at":   datetime.now(timezone.utc).isoformat(),
    })
    if result:
        log.info(f"JOB QUEUED {job_id}")
        return job_id
    raise RuntimeError("Failed to create job in Supabase")


# ── Background daemon ─────────────────────────────────────────────────────────

def _daemon_loop():
    """Poll Supabase for queued jobs every 30s and execute them."""
    log.info("Daemon started — polling every 30s")
    while True:
        try:
            queued = _get("jobs", {
                "status": "eq.queued",
                "order":  "created_at.asc",
                "limit":  "1",
            })
            if queued:
                job = queued[0]
                log.info(f"Daemon picked up job {job['id']}")
                _execute_job(job["id"], job["brief"])
        except Exception as exc:
            log.error(f"Daemon error: {exc}")
        time.sleep(30)


# ── FastAPI app ───────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(application: FastAPI):
    log.info(f"JaiO.S {VERSION} starting")
    t = threading.Thread(target=_daemon_loop, daemon=True, name="daemon")
    t.start()
    yield
    log.info("JaiO.S shutting down")


app = FastAPI(
    title="JaiO.S 6.0",
    description="AI agency operating system — 61-agent LangGraph stack",
    version=VERSION,
    lifespan=lifespan,
)


class RunRequest(BaseModel):
    brief:        str
    submitted_by: str  = "api"
    sync:         bool = False   # True = block until done; False = background


@app.get("/health")
def health():
    uptime = (datetime.now(timezone.utc) - BOOT_TIME).total_seconds()
    return {
        "status":         "ok",
        "version":        VERSION,
        "uptime_seconds": round(uptime),
        "boot_time":      BOOT_TIME.isoformat(),
        "supabase":       settings.brain_url,
        "anthropic_key":  bool(settings.anthropic_api_key),
    }


@app.post("/run")
def run_job(req: RunRequest, background_tasks: BackgroundTasks):
    """Submit a job. sync=True blocks until complete; sync=False runs in background."""
    try:
        job_id = _submit_job(req.brief, req.submitted_by)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    if req.sync:
        return _execute_job(job_id, req.brief)

    background_tasks.add_task(_execute_job, job_id, req.brief)
    return {
        "status":       "queued",
        "job_id":       job_id,
        "check_status": f"/job/{job_id}",
    }


@app.get("/job/{job_id}")
def get_job(job_id: str):
    jobs = _get("jobs", {"id": f"eq.{job_id}"})
    if not jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    job = jobs[0]
    return {
        "id":              job["id"],
        "brief":           job.get("brief"),
        "status":          job.get("status"),
        "submitted_by":    job.get("submitted_by"),
        "output":          job.get("output"),
        "error":           job.get("error"),
        "elapsed_seconds": job.get("elapsed_seconds"),
        "created_at":      job.get("created_at"),
        "started_at":      job.get("started_at"),
        "completed_at":    job.get("completed_at"),
    }


@app.get("/jobs")
def list_jobs(limit: int = 20, status: Optional[str] = None):
    params = {"order": "created_at.desc", "limit": str(limit)}
    if status:
        params["status"] = f"eq.{status}"
    jobs = _get("jobs", params)
    return [{"id": j["id"], "brief": j.get("brief", "")[:100],
             "status": j.get("status"), "elapsed_seconds": j.get("elapsed_seconds"),
             "created_at": j.get("created_at")} for j in jobs]


@app.get("/agents")
def list_agents():
    from agents import __all__
    nodes   = [x for x in __all__ if x.endswith("_node")]
    return {"count": len(nodes), "agents": nodes}


@app.get("/")
def root():
    return {
        "name":    "JaiO.S 6.0",
        "version": VERSION,
        "endpoints": {
            "POST /run":       "Submit a job",
            "GET  /job/{id}": "Check job status + output",
            "GET  /jobs":     "List recent jobs",
            "GET  /agents":   "List all 61 agents",
            "GET  /health":   "Liveness check",
        },
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cli():
    import argparse
    parser = argparse.ArgumentParser(description="JaiO.S 6.0 CLI")
    sub    = parser.add_subparsers(dest="cmd")

    s = sub.add_parser("submit");  s.add_argument("brief")
    s = sub.add_parser("status");  s.add_argument("job_id")
    sub.add_parser("list")
    sub.add_parser("daemon")

    args = parser.parse_args()

    if args.cmd == "submit":
        job_id = _submit_job(args.brief, "cli")
        result = _execute_job(job_id, args.brief)
        print(json.dumps(result, indent=2, default=str))

    elif args.cmd == "status":
        jobs = _get("jobs", {"id": f"eq.{args.job_id}"})
        print(json.dumps(jobs[0] if jobs else {"error": "not found"}, indent=2, default=str))

    elif args.cmd == "list":
        jobs = _get("jobs", {"order": "created_at.desc", "limit": "10"})
        for j in jobs:
            print(f"{j['id'][:8]}  {j['status']:10}  {j.get('brief','')[:70]}")

    elif args.cmd == "daemon":
        _daemon_loop()

    else:
        parser.print_help()


if __name__ == "__main__":
    _cli()
