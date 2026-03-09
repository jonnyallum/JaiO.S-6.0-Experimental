-- ════════════════════════════════════════════════════════════════════════════
-- Jai.OS 6.0 — Supabase Schema
-- Database: Antigravity Brain (lkwydqtfbdjhxaarelaz)
-- Run via: Supabase Dashboard → SQL Editor → Paste → Run
-- ════════════════════════════════════════════════════════════════════════════

-- ── graph_state ───────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS graph_state (
    id            UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id   TEXT         NOT NULL,
    checkpoint_id TEXT         NOT NULL,
    agent         TEXT         NOT NULL DEFAULT 'system',
    state_json    JSONB        NOT NULL DEFAULT '{}',
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Fast lookup by workflow
CREATE INDEX IF NOT EXISTS idx_graph_state_workflow
    ON graph_state(workflow_id);

-- Find a specific checkpoint within a workflow
CREATE INDEX IF NOT EXISTS idx_graph_state_workflow_checkpoint
    ON graph_state(workflow_id, checkpoint_id);

-- Filter by role/agent
CREATE INDEX IF NOT EXISTS idx_graph_state_agent
    ON graph_state(agent);

-- Dashboard: most recent workflows first
CREATE INDEX IF NOT EXISTS idx_graph_state_created_desc
    ON graph_state(created_at DESC);

-- Query inside JSONB (e.g. state_json->>'status' = 'completed')
CREATE INDEX IF NOT EXISTS idx_graph_state_json
    ON graph_state USING GIN (state_json);

-- Auto-update updated_at on any row change
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS graph_state_updated_at ON graph_state;
CREATE TRIGGER graph_state_updated_at
    BEFORE UPDATE ON graph_state
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- RLS: Phase 1 — allow all. Tighten per-role in Phase 6.
ALTER TABLE graph_state ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "workflow_access" ON graph_state;
CREATE POLICY "workflow_access" ON graph_state
    USING (true)
    WITH CHECK (true);

-- ── Verification ─────────────────────────────────────────────────────────────────────
SELECT 'graph_state table ready ✓' AS status;
SELECT COUNT(*) AS existing_rows FROM graph_state;
