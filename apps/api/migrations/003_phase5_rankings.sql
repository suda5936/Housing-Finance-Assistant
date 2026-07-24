BEGIN;

CREATE TABLE IF NOT EXISTS ranking_runs (
    id UUID PRIMARY KEY,
    session_id UUID REFERENCES anonymous_sessions(id) ON DELETE CASCADE,
    ranking_version TEXT NOT NULL,
    input_sha256 CHAR(64) NOT NULL,
    cost_scenario TEXT NOT NULL CHECK (cost_scenario IN ('optimistic', 'base', 'conservative')),
    weights JSONB NOT NULL,
    hard_constraints JSONB NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ranked', 'partial', 'not_comparable')),
    warnings JSONB NOT NULL,
    tradeoffs JSONB NOT NULL,
    sensitivity JSONB,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS ranking_candidate_results (
    id UUID PRIMARY KEY,
    ranking_run_id UUID NOT NULL REFERENCES ranking_runs(id) ON DELETE CASCADE,
    candidate_key TEXT NOT NULL,
    disposition TEXT NOT NULL CHECK (
        disposition IN ('ranked', 'hard_constraint_failed', 'not_comparable')
    ),
    rank SMALLINT,
    total_score NUMERIC(7, 2),
    contributions JSONB NOT NULL,
    hard_constraint_failures JSONB NOT NULL,
    missing_fields JSONB NOT NULL,
    dominated_by JSONB NOT NULL,
    reason_codes JSONB NOT NULL,
    provenance JSONB NOT NULL,
    UNIQUE (ranking_run_id, candidate_key)
);

CREATE INDEX IF NOT EXISTS idx_ranking_runs_session_id
    ON ranking_runs(session_id);
CREATE INDEX IF NOT EXISTS idx_ranking_results_run_id
    ON ranking_candidate_results(ranking_run_id);

COMMIT;
