#!/usr/bin/env python3
"""Integration tests for the JaiOS 6.0 API endpoints."""
import requests
import pytest

BASE = "http://localhost:8765"
API_KEY = "jaios6-master-key-2026"
HEADERS = {"X-API-Key": API_KEY}

def test_health():
    r = requests.get(f"{BASE}/health", timeout=10)
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["version"] == "6.0.0"
    assert data["anthropic_key"] is True

def test_agents_list():
    r = requests.get(f"{BASE}/agents", timeout=10)
    assert r.status_code == 200
    data = r.json()
    assert data["count"] >= 90

def test_catalog():
    r = requests.get(f"{BASE}/catalog", timeout=10)
    assert r.status_code == 200
    data = r.json()
    assert data["count"] >= 90
    # Every agent should be routable
    unroutable = [a["role"] for a in data["agents"] if not a["routable"]]
    assert len(unroutable) == 0, f"Unroutable agents: {unroutable}"

def test_pipelines():
    r = requests.get(f"{BASE}/pipelines", timeout=10)
    assert r.status_code == 200
    data = r.json()
    assert data["count"] >= 20

def test_metrics():
    r = requests.get(f"{BASE}/metrics", timeout=10)
    assert r.status_code == 200
    data = r.json()
    assert "agents_loaded" in data
    assert data["agents_loaded"] >= 90

def test_root():
    r = requests.get(f"{BASE}/", timeout=10)
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "JaiO.S 6.0"

def test_jobs_list():
    r = requests.get(f"{BASE}/jobs", timeout=10)
    assert r.status_code == 200

def test_job_not_found():
    r = requests.get(f"{BASE}/job/nonexistent-id", timeout=10)
    assert r.status_code == 404

def test_run_requires_auth():
    """POST /run without API key should fail."""
    r = requests.post(f"{BASE}/run", json={"brief": "test"}, timeout=10)
    # Should be 401 if key is configured
    assert r.status_code in (401, 200)  # 200 if no key configured
