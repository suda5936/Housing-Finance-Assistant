BEGIN;

CREATE TABLE IF NOT EXISTS anonymous_sessions (
    id UUID PRIMARY KEY,
    access_token_hash TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    deleted_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS consent_records (
    session_id UUID PRIMARY KEY REFERENCES anonymous_sessions(id) ON DELETE CASCADE,
    version TEXT NOT NULL,
    privacy_notice_accepted BOOLEAN NOT NULL,
    sensitive_data_notice_accepted BOOLEAN NOT NULL,
    accepted_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS user_profiles (
    id UUID PRIMARY KEY,
    session_id UUID UNIQUE NOT NULL REFERENCES anonymous_sessions(id) ON DELETE CASCADE,
    age_years SMALLINT NOT NULL CHECK (age_years BETWEEN 19 AND 100),
    monthly_net_income NUMERIC(18, 2) NOT NULL CHECK (monthly_net_income >= 0),
    liquid_assets NUMERIC(18, 2) NOT NULL CHECK (liquid_assets >= 0),
    available_deposit NUMERIC(18, 2) NOT NULL CHECK (available_deposit >= 0),
    currency CHAR(3) NOT NULL DEFAULT 'KRW' CHECK (currency = 'KRW'),
    household_type TEXT NOT NULL,
    is_homeless BOOLEAN NOT NULL,
    workplace_district TEXT,
    input_version TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS housing_candidates (
    id UUID PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES anonymous_sessions(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    district TEXT NOT NULL,
    deposit NUMERIC(18, 2) NOT NULL CHECK (deposit >= 0),
    monthly_rent NUMERIC(18, 2) NOT NULL CHECK (monthly_rent >= 0),
    monthly_maintenance NUMERIC(18, 2) CHECK (monthly_maintenance >= 0),
    currency CHAR(3) NOT NULL DEFAULT 'KRW' CHECK (currency = 'KRW'),
    area_sqm NUMERIC(8, 2) NOT NULL CHECK (area_sqm > 0),
    contract_months SMALLINT NOT NULL CHECK (contract_months BETWEEN 1 AND 120),
    commute_minutes_one_way SMALLINT CHECK (commute_minutes_one_way >= 0),
    monthly_commute_cost NUMERIC(18, 2) CHECK (monthly_commute_cost >= 0),
    input_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS source_documents (
    id UUID PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES anonymous_sessions(id) ON DELETE CASCADE,
    original_filename TEXT NOT NULL,
    storage_key TEXT NOT NULL,
    sha256 CHAR(64) NOT NULL,
    media_type TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    masked BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS extracted_fields (
    id UUID PRIMARY KEY,
    document_id UUID NOT NULL REFERENCES source_documents(id) ON DELETE CASCADE,
    field_name TEXT NOT NULL,
    extracted_value TEXT NOT NULL,
    confirmed_value TEXT,
    source_reference TEXT,
    confidence NUMERIC(4, 3) CHECK (confidence BETWEEN 0 AND 1),
    confirmed_at TIMESTAMPTZ,
    extraction_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS policy_documents (
    id UUID PRIMARY KEY,
    policy_code TEXT NOT NULL,
    title TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_sha256 CHAR(64) NOT NULL,
    effective_from DATE NOT NULL,
    effective_until DATE,
    checked_at TIMESTAMPTZ NOT NULL,
    document_version TEXT NOT NULL,
    UNIQUE (policy_code, document_version)
);

CREATE TABLE IF NOT EXISTS policy_rules (
    id UUID PRIMARY KEY,
    policy_document_id UUID NOT NULL REFERENCES policy_documents(id),
    rule_payload JSONB NOT NULL,
    rule_version TEXT NOT NULL,
    reviewed_by TEXT NOT NULL,
    reviewed_at TIMESTAMPTZ NOT NULL,
    UNIQUE (policy_document_id, rule_version)
);

CREATE TABLE IF NOT EXISTS analysis_snapshots (
    id UUID PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES anonymous_sessions(id) ON DELETE CASCADE,
    input_payload JSONB NOT NULL,
    input_sha256 CHAR(64) NOT NULL,
    policy_version TEXT NOT NULL,
    rule_version TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    model_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON anonymous_sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_candidates_session_id ON housing_candidates(session_id);
CREATE INDEX IF NOT EXISTS idx_documents_session_id ON source_documents(session_id);
CREATE INDEX IF NOT EXISTS idx_snapshots_session_id ON analysis_snapshots(session_id);

COMMIT;

