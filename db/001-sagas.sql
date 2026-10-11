\set ON_ERROR_STOP on
BEGIN;
SELECT pg_advisory_xact_lock(hashtextextended('mis-workflow-schema', 0));
DO $$ BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='workflow_app') THEN
        RAISE EXCEPTION 'Provision workflow_app before running workflow migrations';
    END IF;
END $$;
CREATE SCHEMA IF NOT EXISTS workflow;
REVOKE ALL ON SCHEMA workflow FROM PUBLIC;
CREATE TABLE IF NOT EXISTS workflow.schema_version (
    version integer PRIMARY KEY CHECK (version=1)
);
CREATE TABLE IF NOT EXISTS workflow.saga (
    id uuid PRIMARY KEY,
    actor uuid NOT NULL,
    request_key varchar(128) NOT NULL CHECK (request_key ~ '^[A-Za-z0-9._:-]{8,128}$'),
    fingerprint char(64) NOT NULL CHECK (fingerprint ~ '^[a-f0-9]{64}$'),
    request jsonb NOT NULL CHECK (jsonb_typeof(request)='object'),
    trace varchar(128) NOT NULL CHECK (trace ~ '^[A-Za-z0-9._:-]{1,128}$'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    status varchar(16) NOT NULL DEFAULT 'RUNNING' CHECK (status IN ('RUNNING','COMPLETED','FAILED')),
    completed text[] NOT NULL DEFAULT '{}',
    receipt_id uuid,
    entry_id uuid,
    failed_step varchar(40),
    failure_code varchar(40) CHECK (failure_code ~ '^[A-Z_]{1,40}$'),
    UNIQUE (actor, request_key),
    CHECK (cardinality(completed) BETWEEN 0 AND 4),
    CHECK (array_position(completed, NULL) IS NULL AND coalesce(array_ndims(completed),0)<=1),
    CHECK (completed = (ARRAY['prepare-receipt','prepare-entry','confirm-receipt','activate-entry'])
                       [1:cardinality(completed)]),
    CHECK ((status='COMPLETED') = (cardinality(completed)=4)),
    CHECK ((receipt_id IS NOT NULL) = (cardinality(completed)>=1)),
    CHECK ((entry_id IS NOT NULL) = (cardinality(completed)>=2)),
    CHECK (status<>'FAILED' OR failed_step IS NOT NULL),
    CHECK (failed_step IS NULL OR (cardinality(completed)<4 AND
           failed_step=(ARRAY['prepare-receipt','prepare-entry','confirm-receipt','activate-entry'])
                       [cardinality(completed)+1])),
    CHECK ((failed_step IS NULL) = (failure_code IS NULL))
);
INSERT INTO workflow.schema_version VALUES (1) ON CONFLICT DO NOTHING;
REVOKE ALL ON ALL TABLES IN SCHEMA workflow FROM PUBLIC, workflow_app;
GRANT USAGE ON SCHEMA workflow TO workflow_app;
GRANT SELECT ON workflow.schema_version, workflow.saga TO workflow_app;
GRANT INSERT (id,actor,request_key,fingerprint,request,trace) ON workflow.saga TO workflow_app;
GRANT UPDATE (status,completed,receipt_id,entry_id,failed_step,failure_code,updated_at)
    ON workflow.saga TO workflow_app;
COMMIT;
