CREATE TABLE IF NOT EXISTS card_templates (
    id BIGSERIAL PRIMARY KEY,
    uuid UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    school_id BIGINT NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
    name VARCHAR(120) NOT NULL,
    design JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_card_templates_school_id UNIQUE (school_id)
);

CREATE INDEX IF NOT EXISTS ix_card_templates_school_id ON card_templates (school_id);
