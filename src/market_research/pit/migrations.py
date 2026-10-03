"""Numbered SQL migration; checksum is checked on every connection."""
MIGRATIONS = [(1, """
CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, checksum TEXT NOT NULL, applied_at TEXT NOT NULL);
CREATE TABLE ingest_runs(
 run_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
 status TEXT NOT NULL CHECK(status IN ('RUNNING','PASS','PARTIAL','INTERRUPTED')),
 input_root TEXT NOT NULL, input_domain TEXT NOT NULL CHECK(input_domain IN ('market','synthetic')),
 importer_version TEXT NOT NULL, code_sha256 TEXT NOT NULL, summary_json TEXT);
CREATE TABLE ingest_issues(
 issue_id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES ingest_runs,
 manifest_path TEXT NOT NULL, manifest_sha256 TEXT, reason TEXT NOT NULL);
CREATE TABLE raw_receipts(
 receipt_id TEXT PRIMARY KEY, source TEXT NOT NULL, request_id TEXT NOT NULL,
 request_json TEXT NOT NULL, received_at TEXT, received_original TEXT,
 network_performed INTEGER CHECK(network_performed IN (0,1)), receipt_evidence TEXT NOT NULL,
 http_status INTEGER, status TEXT NOT NULL, raw_path TEXT, raw_sha256 TEXT, file_size INTEGER,
 input_domain TEXT NOT NULL CHECK(input_domain IN ('market','synthetic')), ingested_at TEXT NOT NULL,
 CHECK((raw_path IS NULL) = (raw_sha256 IS NULL)));
CREATE INDEX receipt_request_time ON raw_receipts(request_id,received_at);
CREATE TABLE manifest_links(
 manifest_sha256 TEXT PRIMARY KEY, manifest_path TEXT NOT NULL, receipt_id TEXT NOT NULL REFERENCES raw_receipts,
 manifest_json TEXT NOT NULL, network_performed INTEGER, run_id TEXT NOT NULL REFERENCES ingest_runs);
CREATE TABLE normalized_artifacts(
 artifact_id TEXT PRIMARY KEY, raw_sha256 TEXT NOT NULL, normalized_sha256 TEXT NOT NULL,
 normalized_path TEXT NOT NULL, schema_version TEXT NOT NULL, normalizer_version TEXT NOT NULL,
 normalizer_code_sha256 TEXT NOT NULL, importer_version TEXT NOT NULL,
 hash_verification TEXT NOT NULL, input_domain TEXT NOT NULL, ingested_at TEXT NOT NULL,
 UNIQUE(normalized_sha256,normalizer_code_sha256,schema_version,input_domain));
CREATE TABLE artifact_receipts(
 artifact_id TEXT NOT NULL REFERENCES normalized_artifacts, receipt_id TEXT NOT NULL REFERENCES raw_receipts,
 manifest_sha256 TEXT NOT NULL REFERENCES manifest_links, parsing_completed_at TEXT,
 validation_completed_at TEXT NOT NULL, usable_at TEXT NOT NULL, usable_evidence TEXT NOT NULL,
 PRIMARY KEY(artifact_id,receipt_id,manifest_sha256));
CREATE TABLE security_records(
 security_record_id TEXT PRIMARY KEY, source TEXT NOT NULL, local_label TEXT NOT NULL,
 external_security_id TEXT, issuer_id TEXT, share_class TEXT,
 identifier_verification TEXT NOT NULL CHECK(identifier_verification IN ('temporary','verified')),
 identity_evidence TEXT, input_domain TEXT NOT NULL,
 CHECK(identifier_verification != 'verified' OR (external_security_id IS NOT NULL AND identity_evidence IS NOT NULL)));
CREATE TABLE symbol_assertions(
 assertion_id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL REFERENCES normalized_artifacts,
 security_record_id TEXT NOT NULL REFERENCES security_records, source TEXT NOT NULL,
 symbol TEXT NOT NULL, exchange TEXT NOT NULL, effective_from TEXT, effective_to TEXT,
 temporal_json TEXT NOT NULL, evidence_json TEXT NOT NULL, record_state TEXT NOT NULL,
 CHECK(effective_to IS NULL OR effective_from IS NULL OR effective_to>effective_from));
CREATE INDEX symbol_lookup ON symbol_assertions(symbol,effective_from,effective_to);
CREATE TABLE daily_price_versions(
 version_id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL REFERENCES normalized_artifacts,
 security_record_id TEXT NOT NULL REFERENCES security_records, source TEXT NOT NULL,
 requested_symbol TEXT NOT NULL, trading_date TEXT NOT NULL, session_scope TEXT NOT NULL,
 price_semantics TEXT NOT NULL CHECK(price_semantics IN ('as_traded','split_adjusted','total_return_adjusted','unknown')),
 price_kind TEXT NOT NULL CHECK(price_kind IN ('ohlcv','close_only')), currency TEXT,
 open REAL, high REAL, low REAL, close REAL, volume INTEGER CHECK(volume IS NULL OR volume>=0),
 volume_unit TEXT NOT NULL, volume_adjustment_basis TEXT NOT NULL, semantics_evidence TEXT NOT NULL,
 observation_end TEXT, observation_end_evidence TEXT, temporal_json TEXT NOT NULL, evidence_json TEXT NOT NULL,
 record_state TEXT NOT NULL CHECK(record_state IN ('active','withdrawn')),
 value_sha256 TEXT NOT NULL, previous_version_id TEXT REFERENCES daily_price_versions, previous_relation TEXT,
 UNIQUE(artifact_id,security_record_id,source,trading_date,session_scope,price_semantics,price_kind));
CREATE INDEX price_lookup ON daily_price_versions(requested_symbol,trading_date,source,price_semantics);
CREATE TABLE corporate_action_versions(
 version_id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL REFERENCES normalized_artifacts,
 security_record_id TEXT NOT NULL REFERENCES security_records, source TEXT NOT NULL, requested_symbol TEXT NOT NULL,
 event_key TEXT NOT NULL, event_type TEXT NOT NULL, event_date TEXT NOT NULL,
 declaration_date TEXT, effective_date TEXT, ex_date TEXT, record_date TEXT, payment_date TEXT,
 amount REAL, currency TEXT, numerator REAL, denominator REAL, amount_semantics TEXT NOT NULL,
 temporal_json TEXT NOT NULL, evidence_json TEXT NOT NULL, record_state TEXT NOT NULL CHECK(record_state IN ('active','withdrawn')),
 value_sha256 TEXT NOT NULL, previous_version_id TEXT REFERENCES corporate_action_versions, previous_relation TEXT,
 UNIQUE(artifact_id,security_record_id,event_key));
CREATE TABLE trading_session_versions(
 session_version_id TEXT PRIMARY KEY, calendar_version TEXT NOT NULL, exchange TEXT NOT NULL,
 trading_date TEXT NOT NULL, open_utc TEXT NOT NULL, close_utc TEXT NOT NULL, timezone TEXT NOT NULL,
 calendar_source TEXT NOT NULL, library_version TEXT, artifact_path TEXT NOT NULL, artifact_sha256 TEXT NOT NULL,
 known_at TEXT NOT NULL, publication_at TEXT, historical_evidence_json TEXT NOT NULL,
 input_domain TEXT NOT NULL, UNIQUE(calendar_version,exchange,trading_date), CHECK(close_utc>open_utc));
CREATE TABLE query_snapshots(
 snapshot_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, cutoff TEXT NOT NULL, policy TEXT NOT NULL,
 content_sha256 TEXT NOT NULL UNIQUE, payload_json TEXT NOT NULL, schema_version INTEGER NOT NULL,
 policy_version TEXT NOT NULL, code_sha256 TEXT NOT NULL);
CREATE TABLE snapshot_items(
 snapshot_id TEXT NOT NULL REFERENCES query_snapshots, ordinal INTEGER NOT NULL,
 price_version_id TEXT REFERENCES daily_price_versions, action_version_id TEXT REFERENCES corporate_action_versions,
 artifact_id TEXT NOT NULL REFERENCES normalized_artifacts, receipt_id TEXT NOT NULL REFERENCES raw_receipts,
 PRIMARY KEY(snapshot_id,ordinal), CHECK((price_version_id IS NULL)!=(action_version_id IS NULL)));
"""), (2, """
CREATE INDEX price_version_key ON daily_price_versions(source,security_record_id,trading_date,session_scope,price_semantics,price_kind);
CREATE INDEX action_version_key ON corporate_action_versions(source,security_record_id,event_key);
CREATE TABLE version_edges(
 edge_id TEXT PRIMARY KEY, later_price_id TEXT REFERENCES daily_price_versions, earlier_price_id TEXT REFERENCES daily_price_versions,
 later_action_id TEXT REFERENCES corporate_action_versions, earlier_action_id TEXT REFERENCES corporate_action_versions,
 relation TEXT NOT NULL, evidence_json TEXT NOT NULL, recorded_at TEXT NOT NULL,
 CHECK((later_price_id IS NOT NULL AND earlier_price_id IS NOT NULL AND later_action_id IS NULL AND earlier_action_id IS NULL)
    OR (later_action_id IS NOT NULL AND earlier_action_id IS NOT NULL AND later_price_id IS NULL AND earlier_price_id IS NULL)));
CREATE TRIGGER version_edges_no_update BEFORE UPDATE ON version_edges BEGIN SELECT RAISE(ABORT,'immutable_version'); END;
CREATE TRIGGER version_edges_no_delete BEFORE DELETE ON version_edges BEGIN SELECT RAISE(ABORT,'immutable_version'); END;
""")]
