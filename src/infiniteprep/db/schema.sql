-- InfinitePrep SQLite Schema
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS source_groups (
    id TEXT PRIMARY KEY,
    user_label TEXT NOT NULL,
    grouping_basis TEXT NOT NULL,
    original_folder TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT 'unknown',
    notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS file_assets (
    id TEXT PRIMARY KEY,
    current_path TEXT NOT NULL UNIQUE,
    original_path TEXT NOT NULL,
    file_size INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    format TEXT NOT NULL,
    duration REAL,
    original_tags TEXT NOT NULL DEFAULT '{}',
    import_batch_id TEXT NOT NULL,
    source_group_id TEXT,
    suspected_multitrack INTEGER NOT NULL DEFAULT 0,
    task_status TEXT NOT NULL DEFAULT 'idle',
    review_status TEXT NOT NULL DEFAULT 'unprocessed',
    error_message TEXT,
    imported_at TEXT NOT NULL,
    FOREIGN KEY (source_group_id) REFERENCES source_groups(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_file_assets_hash ON file_assets(content_hash);
CREATE INDEX IF NOT EXISTS idx_file_assets_source_group ON file_assets(source_group_id);
CREATE INDEX IF NOT EXISTS idx_file_assets_review_status ON file_assets(review_status);

CREATE TABLE IF NOT EXISTS candidate_matches (
    id TEXT PRIMARY KEY,
    file_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    provider_entity_id TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    artist TEXT,
    title TEXT,
    mix_name TEXT,
    remixer TEXT,
    duration REAL,
    release_details TEXT NOT NULL DEFAULT '{}',
    match_evidence TEXT NOT NULL DEFAULT '{}',
    match_score REAL NOT NULL DEFAULT 0.0,
    retrieved_at TEXT NOT NULL,
    FOREIGN KEY (file_id) REFERENCES file_assets(id) ON DELETE CASCADE,
    UNIQUE(file_id, provider, provider_entity_id, entity_type)
);

CREATE INDEX IF NOT EXISTS idx_candidate_matches_file ON candidate_matches(file_id);

CREATE TABLE IF NOT EXISTS field_proposals (
    id TEXT PRIMARY KEY,
    file_id TEXT NOT NULL,
    field_name TEXT NOT NULL,
    proposed_value TEXT NOT NULL,
    provider TEXT NOT NULL,
    provider_entity_id TEXT NOT NULL,
    evidence TEXT NOT NULL DEFAULT '{}',
    provider_confidence REAL,
    application_match_score REAL,
    review_status TEXT NOT NULL DEFAULT 'pending',
    retrieved_at TEXT NOT NULL,
    FOREIGN KEY (file_id) REFERENCES file_assets(id) ON DELETE CASCADE,
    UNIQUE(file_id, field_name, proposed_value, provider, provider_entity_id)
);

CREATE INDEX IF NOT EXISTS idx_field_proposals_file ON field_proposals(file_id);

CREATE TABLE IF NOT EXISTS analysis_results (
    id TEXT PRIMARY KEY,
    file_id TEXT NOT NULL,
    analyzer_name TEXT NOT NULL,
    analyzer_version TEXT NOT NULL,
    configuration TEXT NOT NULL DEFAULT '{}',
    bpm REAL,
    key_tonic TEXT,
    key_mode TEXT,
    analysis_strength REAL,
    beat_positions_optional TEXT,
    warnings TEXT NOT NULL DEFAULT '[]',
    analyzed_at TEXT NOT NULL,
    FOREIGN KEY (file_id) REFERENCES file_assets(id) ON DELETE CASCADE,
    UNIQUE(file_id, analyzer_name, analyzer_version)
);

CREATE INDEX IF NOT EXISTS idx_analysis_results_file ON analysis_results(file_id);

CREATE TABLE IF NOT EXISTS approved_fields (
    file_id TEXT NOT NULL,
    field_name TEXT NOT NULL,
    approved_value TEXT NOT NULL,
    approval_origin TEXT NOT NULL,
    approved_at TEXT NOT NULL,
    locked INTEGER NOT NULL DEFAULT 0,
    provider_attribution TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (file_id, field_name),
    FOREIGN KEY (file_id) REFERENCES file_assets(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS change_records (
    operation_id TEXT PRIMARY KEY,
    batch_operation_id TEXT,
    file_id TEXT NOT NULL,
    previous_path TEXT NOT NULL,
    new_path TEXT NOT NULL,
    pre_write_hash TEXT,
    post_write_hash TEXT,
    previous_tags TEXT NOT NULL DEFAULT '{}',
    written_tags TEXT NOT NULL DEFAULT '{}',
    backup_reference TEXT NOT NULL,
    operation_status TEXT NOT NULL,
    error_message TEXT,
    timestamp TEXT NOT NULL,
    FOREIGN KEY (file_id) REFERENCES file_assets(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_change_records_file ON change_records(file_id);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    file_id TEXT NOT NULL,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    payload TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (file_id) REFERENCES file_assets(id) ON DELETE CASCADE,
    UNIQUE(file_id, job_type)
);

CREATE TABLE IF NOT EXISTS provider_cache (
    cache_key TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
