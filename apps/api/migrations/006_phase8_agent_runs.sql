BEGIN;

CREATE TABLE IF NOT EXISTS agent_runs (
    id UUID PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES anonymous_sessions(id) ON DELETE CASCADE,
    orchestration_version TEXT NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN (
            'consent', 'profile', 'candidate_input', 'extraction', 'user_confirmation',
            'missing_info_check', 'clarification', 'eligibility', 'cost_calculation',
            'ranking', 'verification', 'decision_card', 'official_check', 'failed'
        )
    ),
    context_payload JSONB NOT NULL,
    result_payload JSONB,
    limits_payload JSONB NOT NULL,
    usage_payload JSONB NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_state_transitions (
    id UUID PRIMARY KEY,
    agent_run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    from_state TEXT NOT NULL,
    to_state TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (agent_run_id, sequence)
);

CREATE TABLE IF NOT EXISTS agent_tool_calls (
    id UUID PRIMARY KEY,
    agent_run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    tool_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('completed', 'cached', 'failed', 'manual_fallback')
    ),
    input_sha256 CHAR(64) NOT NULL,
    output_sha256 CHAR(64),
    attempt SMALLINT NOT NULL CHECK (attempt >= 0),
    duration_ms INTEGER NOT NULL CHECK (duration_ms >= 0),
    error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (agent_run_id, sequence)
);

CREATE TABLE IF NOT EXISTS agent_verification_gates (
    id UUID PRIMARY KEY,
    agent_run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
    gate_code TEXT NOT NULL,
    passed BOOLEAN NOT NULL,
    blocking_state TEXT,
    reason TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (agent_run_id, gate_code)
);

CREATE TABLE IF NOT EXISTS contract_checklist_items (
    id UUID PRIMARY KEY,
    item_code TEXT NOT NULL,
    item_version TEXT NOT NULL,
    policy_code TEXT NOT NULL,
    action_text TEXT NOT NULL,
    applies_when TEXT NOT NULL,
    verification_actor TEXT NOT NULL,
    citation_payload JSONB NOT NULL,
    disclaimer TEXT NOT NULL,
    review_status TEXT NOT NULL CHECK (review_status IN ('draft', 'approved', 'retired')),
    author TEXT NOT NULL,
    reviewed_by TEXT,
    reviewed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (item_code, item_version),
    CHECK (
        review_status <> 'approved'
        OR (
            reviewed_by IS NOT NULL
            AND reviewed_at IS NOT NULL
            AND LOWER(author) <> LOWER(reviewed_by)
        )
    )
);

CREATE INDEX IF NOT EXISTS idx_agent_runs_session_state
    ON agent_runs(session_id, state, updated_at);
CREATE INDEX IF NOT EXISTS idx_agent_transitions_run
    ON agent_state_transitions(agent_run_id, sequence);
CREATE INDEX IF NOT EXISTS idx_agent_tool_calls_run
    ON agent_tool_calls(agent_run_id, sequence);
CREATE INDEX IF NOT EXISTS idx_checklist_policy_review
    ON contract_checklist_items(policy_code, review_status);

COMMIT;
