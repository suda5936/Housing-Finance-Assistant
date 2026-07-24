BEGIN;

ALTER TABLE source_documents
    ADD COLUMN IF NOT EXISTS size_bytes BIGINT,
    ADD COLUMN IF NOT EXISTS page_count SMALLINT,
    ADD COLUMN IF NOT EXISTS image_width INTEGER,
    ADD COLUMN IF NOT EXISTS image_height INTEGER,
    ADD COLUMN IF NOT EXISTS processing_status TEXT NOT NULL DEFAULT 'stored',
    ADD COLUMN IF NOT EXISTS extractor TEXT,
    ADD COLUMN IF NOT EXISTS extraction_version TEXT,
    ADD COLUMN IF NOT EXISTS warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS injection_detected BOOLEAN NOT NULL DEFAULT FALSE,
    ADD CONSTRAINT source_documents_processing_status_check CHECK (
        processing_status IN ('stored', 'extracted', 'manual_required', 'failed')
    );

CREATE TABLE IF NOT EXISTS document_text_blocks (
    id UUID PRIMARY KEY,
    document_id UUID NOT NULL REFERENCES source_documents(id) ON DELETE CASCADE,
    block_key TEXT NOT NULL,
    page_number SMALLINT NOT NULL CHECK (page_number >= 1),
    masked_text TEXT NOT NULL,
    confidence NUMERIC(5, 4) CHECK (confidence BETWEEN 0 AND 1),
    bbox_x INTEGER,
    bbox_y INTEGER,
    bbox_width INTEGER,
    bbox_height INTEGER,
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (document_id, block_key)
);

ALTER TABLE extracted_fields
    ADD COLUMN IF NOT EXISTS normalized_value TEXT,
    ADD COLUMN IF NOT EXISTS value_unit TEXT,
    ADD COLUMN IF NOT EXISTS review_status TEXT NOT NULL DEFAULT 'proposed',
    ADD COLUMN IF NOT EXISTS source_block_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD CONSTRAINT extracted_fields_review_status_check CHECK (
        review_status IN ('proposed', 'needs_review', 'confirmed', 'corrected')
    );

CREATE TABLE IF NOT EXISTS document_extraction_evaluations (
    id UUID PRIMARY KEY,
    extraction_version TEXT NOT NULL,
    dataset_version TEXT NOT NULL,
    document_count INTEGER NOT NULL CHECK (document_count > 0),
    field_metrics JSONB NOT NULL,
    macro_accuracy NUMERIC(5, 4) NOT NULL CHECK (macro_accuracy BETWEEN 0 AND 1),
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_document_blocks_document_id
    ON document_text_blocks(document_id);
CREATE INDEX IF NOT EXISTS idx_extracted_fields_review_status
    ON extracted_fields(review_status);

COMMIT;
