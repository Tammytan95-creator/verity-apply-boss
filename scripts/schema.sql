PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS schema_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

INSERT INTO schema_meta(key, value)
VALUES ('schema_version', '2')
ON CONFLICT(key) DO UPDATE SET value = excluded.value;

CREATE TABLE IF NOT EXISTS candidates (
  id TEXT PRIMARY KEY,
  display_name TEXT,
  profile_version TEXT NOT NULL DEFAULT '1',
  onboarding_status TEXT NOT NULL DEFAULT 'IN_PROGRESS'
    CHECK (onboarding_status IN (
      'NOT_STARTED', 'IN_PROGRESS', 'FACTS_PENDING', 'CONSTRAINTS_PENDING',
      'PLATFORM_PENDING', 'CALIBRATION_PENDING', 'READY', 'PAUSED'
    )),
  profile_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS resume_versions (
  id TEXT PRIMARY KEY,
  candidate_id TEXT NOT NULL REFERENCES candidates(id),
  version_label TEXT NOT NULL,
  file_path TEXT,
  file_sha256 TEXT,
  approval_status TEXT NOT NULL DEFAULT 'PENDING'
    CHECK (approval_status IN ('PENDING', 'APPROVED', 'REJECTED', 'SUPERSEDED')),
  allowed_for_submission INTEGER NOT NULL DEFAULT 0 CHECK (allowed_for_submission IN (0, 1)),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(candidate_id, file_sha256)
);

CREATE TABLE IF NOT EXISTS candidate_facts (
  id TEXT PRIMARY KEY,
  candidate_id TEXT NOT NULL REFERENCES candidates(id),
  fact_type TEXT NOT NULL,
  fact_text TEXT NOT NULL,
  source_resume_id TEXT REFERENCES resume_versions(id),
  source_location TEXT,
  confidence REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
  verification_status TEXT NOT NULL DEFAULT 'UNVERIFIED'
    CHECK (verification_status IN ('UNVERIFIED', 'VERIFIED', 'CONFLICT', 'REVOKED')),
  sensitivity TEXT NOT NULL DEFAULT 'NORMAL'
    CHECK (sensitivity IN ('NORMAL', 'PRIVATE', 'SENSITIVE')),
  allowed_uses_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS campaigns (
  id TEXT PRIMARY KEY,
  candidate_id TEXT NOT NULL REFERENCES candidates(id),
  name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'ACTIVE'
    CHECK (status IN ('DRAFT', 'ACTIVE', 'PAUSED', 'COMPLETED', 'ARCHIVED')),
  config_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_key TEXT NOT NULL UNIQUE,
  platform TEXT NOT NULL,
  external_job_id TEXT,
  source_type TEXT,
  source_url TEXT,
  company_raw TEXT NOT NULL,
  company_normalized TEXT NOT NULL,
  title TEXT NOT NULL,
  location TEXT,
  work_mode TEXT,
  employment_type TEXT,
  salary_text TEXT,
  posted_or_updated_at TEXT,
  jd_snapshot TEXT,
  freshness_status TEXT,
  authenticity_result TEXT,
  duplicate_result TEXT,
  hard_gate_result TEXT,
  score_breakdown_json TEXT NOT NULL DEFAULT '{}',
  total_score REAL,
  decision_tier TEXT,
  current_stage TEXT,
  checked_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(platform, external_job_id)
);

CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company_normalized);
CREATE INDEX IF NOT EXISTS idx_jobs_stage ON jobs(current_stage);

CREATE TABLE IF NOT EXISTS job_evidence (
  id TEXT PRIMARY KEY,
  candidate_id TEXT NOT NULL REFERENCES candidates(id),
  job_id INTEGER NOT NULL REFERENCES jobs(id),
  requirement_text TEXT NOT NULL,
  evidence_type TEXT NOT NULL
    CHECK (evidence_type IN ('DIRECT', 'TRANSFERABLE', 'MISSING', 'CONFLICT', 'UNVERIFIED')),
  candidate_fact_id TEXT REFERENCES candidate_facts(id),
  migration_note TEXT,
  confidence REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_job_evidence_candidate_job
  ON job_evidence(candidate_id, job_id);

CREATE TABLE IF NOT EXISTS tailored_materials (
  id TEXT PRIMARY KEY,
  candidate_id TEXT NOT NULL REFERENCES candidates(id),
  job_id INTEGER NOT NULL REFERENCES jobs(id),
  material_type TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  source_resume_id TEXT REFERENCES resume_versions(id),
  content_path TEXT,
  content_hash TEXT,
  change_summary TEXT,
  evidence_json TEXT NOT NULL DEFAULT '[]',
  approval_status TEXT NOT NULL DEFAULT 'DRAFT'
    CHECK (approval_status IN ('DRAFT', 'PENDING', 'APPROVED', 'REJECTED', 'SUPERSEDED')),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  approved_at TEXT,
  UNIQUE(candidate_id, job_id, material_type, version)
);

CREATE TABLE IF NOT EXISTS authorizations (
  id TEXT PRIMARY KEY,
  candidate_id TEXT NOT NULL REFERENCES candidates(id),
  campaign_id TEXT REFERENCES campaigns(id),
  scope_type TEXT NOT NULL CHECK (scope_type IN ('JOB', 'NAMED_BATCH', 'SCHEDULED_SCOPE')),
  scope_json TEXT NOT NULL,
  actions_json TEXT NOT NULL,
  materials_json TEXT NOT NULL DEFAULT '[]',
  max_count INTEGER CHECK (max_count IS NULL OR max_count > 0),
  starts_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  expires_at TEXT,
  stop_conditions_json TEXT NOT NULL DEFAULT '[]',
  revoked_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS application_answers (
  id TEXT PRIMARY KEY,
  candidate_id TEXT NOT NULL REFERENCES candidates(id),
  question_key TEXT NOT NULL,
  answer_text TEXT NOT NULL,
  scope_json TEXT NOT NULL DEFAULT '{}',
  sensitivity TEXT NOT NULL DEFAULT 'NORMAL'
    CHECK (sensitivity IN ('NORMAL', 'PRIVATE', 'SENSITIVE')),
  confirmation_status TEXT NOT NULL DEFAULT 'CONFIRMED'
    CHECK (confirmation_status IN ('DRAFT', 'CONFIRMED', 'REVOKED', 'EXPIRED')),
  confirmed_at TEXT,
  expires_at TEXT,
  UNIQUE(candidate_id, question_key)
);

CREATE TABLE IF NOT EXISTS applications (
  id TEXT PRIMARY KEY,
  candidate_id TEXT NOT NULL REFERENCES candidates(id),
  campaign_id TEXT REFERENCES campaigns(id),
  job_id INTEGER NOT NULL REFERENCES jobs(id),
  authorization_id TEXT REFERENCES authorizations(id),
  resume_id TEXT REFERENCES resume_versions(id),
  material_id TEXT REFERENCES tailored_materials(id),
  attempt_no INTEGER NOT NULL DEFAULT 1 CHECK (attempt_no > 0),
  status TEXT NOT NULL CHECK (status IN (
    'APPROVAL_PENDING', 'READY_TO_APPLY', 'SUBMITTED_UNCONFIRMED',
    'APPLIED_CONFIRMED', 'CONTACT_CONFIRMED', 'NEEDS_USER',
    'DUPLICATE_JOB', 'DUPLICATE_COMPANY', 'CLOSED',
    'HARD_GATE_REJECTED', 'WITHDRAWN', 'FAILED'
  )),
  submitted_at TEXT,
  verification_type TEXT,
  verification_ref TEXT,
  notes TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(candidate_id, job_id, attempt_no),
  CHECK (
    status NOT IN ('APPLIED_CONFIRMED', 'CONTACT_CONFIRMED')
    OR (authorization_id IS NOT NULL AND verification_ref IS NOT NULL)
  ),
  CHECK (
    status != 'SUBMITTED_UNCONFIRMED' OR authorization_id IS NOT NULL
  )
);

CREATE INDEX IF NOT EXISTS idx_applications_candidate_status
  ON applications(candidate_id, status);

CREATE TABLE IF NOT EXISTS outreach_messages (
  id TEXT PRIMARY KEY,
  candidate_id TEXT NOT NULL REFERENCES candidates(id),
  job_id INTEGER NOT NULL REFERENCES jobs(id),
  application_id TEXT REFERENCES applications(id),
  authorization_id TEXT REFERENCES authorizations(id),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
  message_text TEXT NOT NULL,
  jd_requirements_json TEXT NOT NULL DEFAULT '[]',
  evidence_fact_ids_json TEXT NOT NULL DEFAULT '[]',
  approval_status TEXT NOT NULL DEFAULT 'DRAFT'
    CHECK (approval_status IN ('DRAFT', 'PENDING', 'APPROVED', 'REJECTED', 'SUPERSEDED')),
  delivery_status TEXT NOT NULL DEFAULT 'NOT_SENT'
    CHECK (delivery_status IN (
      'NOT_SENT', 'SENT_UNCONFIRMED', 'CUSTOM_OUTREACH_CONFIRMED',
      'DEFAULT_ONLY', 'FAILED'
    )),
  sent_at TEXT,
  verification_ref TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(candidate_id, job_id, version),
  CHECK (
    delivery_status != 'CUSTOM_OUTREACH_CONFIRMED'
    OR (
      approval_status = 'APPROVED'
      AND authorization_id IS NOT NULL
      AND verification_ref IS NOT NULL
    )
  )
);

CREATE TABLE IF NOT EXISTS workflow_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  entity_type TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  from_status TEXT,
  to_status TEXT,
  actor TEXT NOT NULL DEFAULT 'agent',
  reason TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_events_entity
  ON workflow_events(entity_type, entity_id, created_at);

CREATE TRIGGER IF NOT EXISTS workflow_events_no_update
BEFORE UPDATE ON workflow_events
BEGIN
  SELECT RAISE(ABORT, 'workflow_events is append-only');
END;

CREATE TRIGGER IF NOT EXISTS workflow_events_no_delete
BEFORE DELETE ON workflow_events
BEGIN
  SELECT RAISE(ABORT, 'workflow_events is append-only');
END;

