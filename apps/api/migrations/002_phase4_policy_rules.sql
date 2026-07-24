BEGIN;

ALTER TABLE policy_documents
    ALTER COLUMN source_sha256 DROP NOT NULL,
    ADD COLUMN IF NOT EXISTS operator TEXT,
    ADD COLUMN IF NOT EXISTS published_on DATE,
    ADD COLUMN IF NOT EXISTS source_locator TEXT,
    ADD COLUMN IF NOT EXISTS application_from DATE,
    ADD COLUMN IF NOT EXISTS application_until DATE;

ALTER TABLE policy_rules
    ALTER COLUMN reviewed_by DROP NOT NULL,
    ALTER COLUMN reviewed_at DROP NOT NULL,
    ADD COLUMN IF NOT EXISTS author TEXT,
    ADD COLUMN IF NOT EXISTS review_status TEXT NOT NULL DEFAULT 'draft',
    ADD COLUMN IF NOT EXISTS retired_at TIMESTAMPTZ,
    ADD CONSTRAINT policy_rules_review_status_check
        CHECK (review_status IN ('draft', 'approved', 'retired')),
    ADD CONSTRAINT policy_rules_approval_check CHECK (
        review_status <> 'approved'
        OR (
            author IS NOT NULL
            AND reviewed_by IS NOT NULL
            AND reviewed_at IS NOT NULL
            AND LOWER(author) <> LOWER(reviewed_by)
        )
    );

CREATE TABLE IF NOT EXISTS policy_checklist_items (
    id UUID PRIMARY KEY,
    policy_document_id UUID NOT NULL REFERENCES policy_documents(id),
    policy_rule_id UUID NOT NULL REFERENCES policy_rules(id),
    item_code TEXT NOT NULL,
    action_text TEXT NOT NULL,
    source_section TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (policy_rule_id, item_code)
);

CREATE TABLE IF NOT EXISTS policy_eligibility_results (
    id UUID PRIMARY KEY,
    session_id UUID REFERENCES anonymous_sessions(id) ON DELETE CASCADE,
    policy_document_id UUID NOT NULL REFERENCES policy_documents(id),
    policy_rule_id UUID NOT NULL REFERENCES policy_rules(id),
    status TEXT NOT NULL CHECK (
        status IN (
            'ELIGIBLE',
            'INELIGIBLE',
            'MISSING_INFORMATION',
            'OFFICIAL_CHECK_NEEDED',
            'EXPIRED'
        )
    ),
    input_sha256 CHAR(64) NOT NULL,
    reason_codes JSONB NOT NULL,
    check_results JSONB NOT NULL,
    evaluated_as_of DATE NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_policy_documents_code_version
    ON policy_documents(policy_code, document_version);
CREATE INDEX IF NOT EXISTS idx_policy_rules_document_version
    ON policy_rules(policy_document_id, rule_version);
CREATE INDEX IF NOT EXISTS idx_policy_results_rule
    ON policy_eligibility_results(policy_rule_id);

COMMIT;
