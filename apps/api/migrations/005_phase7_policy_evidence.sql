BEGIN;

CREATE TABLE IF NOT EXISTS policy_evidence_documents (
    id UUID PRIMARY KEY,
    source_key TEXT NOT NULL UNIQUE,
    policy_code TEXT NOT NULL,
    title TEXT NOT NULL,
    institution TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_type TEXT NOT NULL,
    published_on DATE,
    checked_on DATE NOT NULL,
    effective_from DATE NOT NULL,
    effective_until DATE,
    regions JSONB NOT NULL,
    content_sha256 CHAR(64) NOT NULL,
    review_status TEXT NOT NULL CHECK (review_status IN ('pending', 'approved', 'retired')),
    author TEXT NOT NULL,
    reviewed_by TEXT,
    reviewed_at TIMESTAMPTZ,
    retrieval_status TEXT NOT NULL CHECK (
        retrieval_status IN ('retrieved', 'manual_snapshot', 'unavailable')
    ),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CHECK (effective_until IS NULL OR effective_until >= effective_from),
    CHECK (
        review_status <> 'approved'
        OR (
            reviewed_by IS NOT NULL
            AND reviewed_at IS NOT NULL
            AND LOWER(author) <> LOWER(reviewed_by)
        )
    )
);

CREATE TABLE IF NOT EXISTS policy_evidence_chunks (
    id UUID PRIMARY KEY,
    document_id UUID NOT NULL REFERENCES policy_evidence_documents(id) ON DELETE CASCADE,
    chunk_key TEXT NOT NULL,
    section_heading TEXT NOT NULL,
    source_locator TEXT NOT NULL,
    chunk_text TEXT NOT NULL,
    condition_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
    token_vector JSONB NOT NULL,
    chunk_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (document_id, chunk_key, chunk_version)
);

CREATE TABLE IF NOT EXISTS policy_evidence_searches (
    id UUID PRIMARY KEY,
    query_sha256 CHAR(64) NOT NULL,
    as_of_date DATE NOT NULL,
    region TEXT,
    policy_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
    result_status TEXT NOT NULL CHECK (
        result_status IN ('EVIDENCE_FOUND', 'OFFICIAL_CHECK_NEEDED')
    ),
    result_chunk_keys JSONB NOT NULL DEFAULT '[]'::jsonb,
    retrieval_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS policy_evidence_evaluations (
    id UUID PRIMARY KEY,
    dataset_version TEXT NOT NULL,
    retrieval_version TEXT NOT NULL,
    total_cases INTEGER NOT NULL CHECK (total_cases >= 0),
    hit_rate_at_k NUMERIC(5, 4) NOT NULL CHECK (hit_rate_at_k BETWEEN 0 AND 1),
    citation_alignment NUMERIC(5, 4) NOT NULL CHECK (citation_alignment BETWEEN 0 AND 1),
    metrics JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_policy_evidence_document_policy
    ON policy_evidence_documents(policy_code, effective_from, effective_until);
CREATE INDEX IF NOT EXISTS idx_policy_evidence_document_review
    ON policy_evidence_documents(review_status, retrieval_status);
CREATE INDEX IF NOT EXISTS idx_policy_evidence_chunk_document
    ON policy_evidence_chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_policy_evidence_search_date
    ON policy_evidence_searches(as_of_date, created_at);

COMMIT;
