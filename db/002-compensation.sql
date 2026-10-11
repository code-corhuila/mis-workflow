BEGIN;
SELECT pg_advisory_xact_lock(hashtextextended('mis-workflow-schema',0));
DO $$ BEGIN
    IF NOT EXISTS (SELECT FROM workflow.schema_version WHERE version=2) THEN
        ALTER TABLE workflow.schema_version DROP CONSTRAINT schema_version_version_check;
        ALTER TABLE workflow.schema_version ADD CHECK (version BETWEEN 1 AND 2);
        ALTER TABLE workflow.saga ADD COLUMN compensating boolean NOT NULL DEFAULT false,
            ADD COLUMN compensated text[] NOT NULL DEFAULT '{}',
            ADD COLUMN undo_failed_step varchar(40), ADD COLUMN undo_failure_code varchar(40);
        ALTER TABLE workflow.saga DROP CONSTRAINT saga_status_check;
        ALTER TABLE workflow.saga ADD CHECK (status IN ('RUNNING','COMPLETED','FAILED','COMPENSATED'));
        ALTER TABLE workflow.saga ADD CONSTRAINT saga_undo_progress CHECK (
            array_position(compensated,NULL) IS NULL AND coalesce(array_ndims(compensated),0)<=1 AND
            compensated=(CASE WHEN cardinality(completed)>=2 THEN ARRAY['cancel-entry','void-receipt']
                WHEN cardinality(completed)=1 THEN ARRAY['void-receipt'] ELSE ARRAY[]::text[] END)[1:cardinality(compensated)] AND
            cardinality(compensated)<=CASE WHEN cardinality(completed)>=2 THEN 2 ELSE cardinality(completed) END
        );
        ALTER TABLE workflow.saga ADD CONSTRAINT saga_undo_mode CHECK (
            (compensating AND failed_step IS NOT NULL AND cardinality(completed)<4) OR
            (NOT compensating AND cardinality(compensated)=0 AND undo_failed_step IS NULL AND status<>'COMPENSATED')
        );
        ALTER TABLE workflow.saga ADD CONSTRAINT saga_undo_error CHECK (
            (undo_failed_step IS NULL)=(undo_failure_code IS NULL) AND
            (undo_failure_code IS NULL OR undo_failure_code ~ '^[A-Z_]{1,40}$') AND
            (undo_failed_step IS NULL OR (compensating AND status='FAILED' AND
                cardinality(compensated)<CASE WHEN cardinality(completed)>=2 THEN 2 ELSE cardinality(completed) END AND
                undo_failed_step=(CASE WHEN cardinality(completed)>=2 THEN ARRAY['cancel-entry','void-receipt']
                    WHEN cardinality(completed)=1 THEN ARRAY['void-receipt'] ELSE ARRAY[]::text[] END)[cardinality(compensated)+1]))
        );
        ALTER TABLE workflow.saga ADD CONSTRAINT saga_undo_complete CHECK (
            status<>'COMPENSATED' OR (compensating AND undo_failed_step IS NULL AND
                compensated=CASE WHEN cardinality(completed)>=2 THEN ARRAY['cancel-entry','void-receipt']
                    WHEN cardinality(completed)=1 THEN ARRAY['void-receipt'] ELSE ARRAY[]::text[] END)
        );
        INSERT INTO workflow.schema_version VALUES (2);
    END IF;
END $$;
GRANT UPDATE (compensating,compensated,undo_failed_step,undo_failure_code) ON workflow.saga TO workflow_app;
COMMIT;
