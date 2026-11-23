CREATE TABLE IF NOT EXISTS service_instance (
 id integer PRIMARY KEY CHECK (id=1), instance_id uuid NOT NULL, schema_revision integer NOT NULL);
CREATE TABLE IF NOT EXISTS service_settings (
 id integer PRIMARY KEY CHECK(id=1), max_concurrent_checks integer NOT NULL CHECK(max_concurrent_checks>0),
 accepting boolean NOT NULL DEFAULT true, revision bigint NOT NULL DEFAULT 1,
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(), updated_by text);
CREATE TABLE IF NOT EXISTS worker_presence (
 worker_id text PRIMARY KEY, role text NOT NULL, heartbeat_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 ready boolean NOT NULL, hard_limit integer NOT NULL, reason text);
CREATE TABLE IF NOT EXISTS delivery_clients (
 client_id text PRIMARY KEY, refreshed_at timestamptz NOT NULL DEFAULT clock_timestamp());
CREATE TABLE IF NOT EXISTS reservations (
 id uuid PRIMARY KEY, check_id uuid NOT NULL UNIQUE, client_id text NOT NULL,
 submission_id text NOT NULL, attempt bigint NOT NULL CHECK(attempt>0), manifest jsonb NOT NULL,
 state text NOT NULL CHECK(state IN ('reserved','uploading','consumed','expired','cancelled')),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), expires_at timestamptz NOT NULL,
 upload_deadline timestamptz, heartbeat_at timestamptz, upload_key text, fence bigint NOT NULL DEFAULT 0);
CREATE UNIQUE INDEX IF NOT EXISTS reservation_live_identity ON reservations(client_id,submission_id,attempt)
 WHERE state IN ('reserved','uploading');
CREATE TABLE IF NOT EXISTS checks (
 id uuid PRIMARY KEY, reservation_id uuid NOT NULL UNIQUE REFERENCES reservations(id),
 client_id text NOT NULL, submission_id text NOT NULL, attempt bigint NOT NULL,
 manifest jsonb NOT NULL, state text NOT NULL CHECK(state IN ('accepted','running','finalizing','completed','rejected','execution_failed')),
 accepted_at timestamptz NOT NULL DEFAULT clock_timestamp(), started_at timestamptz, finished_at timestamptz,
 lease_owner text, lease_until timestamptz, fence bigint NOT NULL DEFAULT 0,
 supervisor_container text, supervisor_stopped boolean NOT NULL DEFAULT false,
 candidate jsonb, grading jsonb, error jsonb, engine jsonb, normalized_hash text,
 cleanup_blocked boolean NOT NULL DEFAULT false, UNIQUE(client_id,submission_id,attempt));
CREATE TABLE IF NOT EXISTS capacity_claims (
 id uuid PRIMARY KEY, reservation_id uuid NOT NULL UNIQUE REFERENCES reservations(id),
 check_id uuid UNIQUE REFERENCES checks(id), released_at timestamptz);
CREATE TABLE IF NOT EXISTS idempotency (
 client_id text NOT NULL, operation text NOT NULL, key text NOT NULL, fingerprint text NOT NULL,
 resource_id uuid NOT NULL, expires_at timestamptz NOT NULL, PRIMARY KEY(client_id,operation,key));
CREATE TABLE IF NOT EXISTS docker_resources (
 intent_name text PRIMARY KEY, check_id uuid NOT NULL REFERENCES checks(id), fence bigint NOT NULL,
 kind text NOT NULL CHECK(kind IN ('container','network')),
 resource_role text NOT NULL DEFAULT 'executor' CHECK(resource_role IN ('executor','database','network')), resource_id text,
 operation_state text NOT NULL, deleted_at timestamptz);
CREATE TABLE IF NOT EXISTS artifacts (
 check_id uuid NOT NULL REFERENCES checks(id), kind text NOT NULL, relative_path text NOT NULL,
 size bigint NOT NULL, sha256 text NOT NULL, expires_at timestamptz NOT NULL, deleted_at timestamptz,
 PRIMARY KEY(check_id,kind));
CREATE TABLE IF NOT EXISTS outbox (
 event_id uuid PRIMARY KEY, check_id uuid NOT NULL UNIQUE REFERENCES checks(id), client_id text NOT NULL,
 payload bytea, state text NOT NULL CHECK(state IN ('pending','delivered','failed','expired')),
 attempt_count bigint NOT NULL DEFAULT 0, next_attempt_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), deadline timestamptz NOT NULL,
 lease_owner text, lease_until timestamptz, fence bigint NOT NULL DEFAULT 0, last_error text);
CREATE TABLE IF NOT EXISTS audit_log (
 id bigserial PRIMARY KEY, occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 event text NOT NULL, actor text, resource_id text);
CREATE INDEX IF NOT EXISTS claim_live ON capacity_claims(id) WHERE released_at IS NULL;
CREATE INDEX IF NOT EXISTS reservation_expiry ON reservations(state,expires_at);
CREATE INDEX IF NOT EXISTS check_recovery ON checks(state,lease_until,accepted_at);
CREATE INDEX IF NOT EXISTS outbox_pending ON outbox(state,next_attempt_at);
CREATE INDEX IF NOT EXISTS artifact_retention ON artifacts(expires_at) WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS docker_one_live_role ON docker_resources(check_id,resource_role) WHERE deleted_at IS NULL;
