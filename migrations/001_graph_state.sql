-- Migration: 001_graph_state
-- Phase 1 — LangGraph state persistence table
-- Run this in Supabase SQL Editor (or via psql against your Supabase DB)

-- Create state persistence table for LangGraph checkpointing
CREATE TABLE IF NOT EXISTS graph_state (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id   TEXT        NOT NULL,
    state_json    JSONB       NOT NULL,
    checkpoint_id TEXT        NOT NULL,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for fast lookups
CREATE INDEX IF NOT EXISTS idx_graph_state_workflow    ON graph_state (workflow_id);
CREATE INDEX IF NOT EXISTS idx_graph_state_checkpoint  ON graph_state (checkpoint_id);
CREATE INDEX IF NOT EXISTS idx_graph_state_created_at  ON graph_state (created_at DESC);

-- Auto-update updated_at
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_graph_state_updated_at ON graph_state;
CREATE TRIGGER trigger_graph_state_updated_at
    BEFORE UPDATE ON graph_state
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- RLS: enabled but permissive for Phase 1
-- Tighten in Phase 6 with per-workflow isolation
ALTER TABLE graph_state ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "phase1_open_access" ON graph_state;
CREATE POLICY "phase1_open_access" ON graph_state
    USING (true)
    WITH CHECK (true);

-- Verify
SELECT 'graph_state table ready ✅' AS status;
