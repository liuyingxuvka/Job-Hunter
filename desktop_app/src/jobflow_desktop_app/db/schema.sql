PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS candidates (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  email TEXT DEFAULT '',
  base_location TEXT DEFAULT '',
  preferred_locations TEXT DEFAULT '',
  base_location_struct TEXT DEFAULT '',
  preferred_locations_struct TEXT DEFAULT '',
  target_directions TEXT DEFAULT '',
  notes TEXT DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS resumes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  candidate_id INTEGER NOT NULL,
  file_path TEXT NOT NULL,
  source_type TEXT NOT NULL DEFAULT 'file',
  raw_text TEXT DEFAULT '',
  is_active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (candidate_id) REFERENCES candidates(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS search_profiles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  candidate_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  scope_profile TEXT NOT NULL,
  target_role TEXT NOT NULL DEFAULT '',
  location_preference TEXT NOT NULL DEFAULT '',
  company_focus TEXT NOT NULL DEFAULT '',
  company_keyword_focus TEXT NOT NULL DEFAULT '',
  role_name_i18n TEXT NOT NULL DEFAULT '',
  keyword_focus TEXT NOT NULL DEFAULT '',
  company_seed_list TEXT NOT NULL DEFAULT '',
  is_active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (candidate_id) REFERENCES candidates(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS search_profile_queries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  search_profile_id INTEGER NOT NULL,
  query_text TEXT NOT NULL,
  sort_order INTEGER NOT NULL DEFAULT 0,
  is_enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (search_profile_id) REFERENCES search_profiles(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS companies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  website TEXT DEFAULT '',
  careers_url TEXT DEFAULT '',
  ats_type TEXT DEFAULT '',
  ats_id TEXT DEFAULT '',
  region_tag TEXT DEFAULT '',
  priority INTEGER NOT NULL DEFAULT 0,
  notes TEXT DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  canonical_url TEXT UNIQUE,
  title TEXT NOT NULL,
  company_name TEXT NOT NULL DEFAULT '',
  location_text TEXT DEFAULT '',
  date_posted TEXT DEFAULT '',
  first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  is_active INTEGER NOT NULL DEFAULT 1,
  source_quality TEXT DEFAULT '',
  region_tag TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS search_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  candidate_id INTEGER,
  search_profile_id INTEGER,
  run_type TEXT NOT NULL DEFAULT 'full',
  status TEXT NOT NULL DEFAULT 'queued',
  started_at TEXT DEFAULT '',
  finished_at TEXT DEFAULT '',
  jobs_found_count INTEGER NOT NULL DEFAULT 0,
  jobs_scored_count INTEGER NOT NULL DEFAULT 0,
  jobs_recommended_count INTEGER NOT NULL DEFAULT 0,
  error_message TEXT DEFAULT '',
  FOREIGN KEY (candidate_id) REFERENCES candidates(id) ON DELETE SET NULL,
  FOREIGN KEY (search_profile_id) REFERENCES search_profiles(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS job_analyses (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER NOT NULL,
  search_profile_id INTEGER NOT NULL,
  analysis_version TEXT NOT NULL DEFAULT 'v1',
  match_score INTEGER NOT NULL DEFAULT 0,
  fit_level_cn TEXT DEFAULT '',
  fit_track TEXT DEFAULT '',
  job_cluster TEXT DEFAULT '',
  industry_track_cn TEXT DEFAULT '',
  transferable_score INTEGER NOT NULL DEFAULT 0,
  domain_score INTEGER NOT NULL DEFAULT 0,
  primary_evidence_cn TEXT DEFAULT '',
  summary_cn TEXT DEFAULT '',
  recommend INTEGER NOT NULL DEFAULT 0,
  recommend_reason_cn TEXT DEFAULT '',
  is_job_posting INTEGER,
  job_posting_evidence_cn TEXT DEFAULT '',
  adjacent_direction_cn TEXT DEFAULT '',
  industry_cluster_cn TEXT DEFAULT '',
  analysis_json TEXT DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE,
  FOREIGN KEY (search_profile_id) REFERENCES search_profiles(id) ON DELETE CASCADE,
  UNIQUE (job_id, search_profile_id, analysis_version)
);

CREATE TABLE IF NOT EXISTS job_review_states (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  candidate_id INTEGER NOT NULL,
  search_profile_id INTEGER NOT NULL,
  job_id INTEGER NOT NULL,
  interest_level TEXT DEFAULT '',
  applied_status TEXT DEFAULT '',
  applied_date TEXT DEFAULT '',
  response_status TEXT DEFAULT '',
  not_interested INTEGER NOT NULL DEFAULT 0,
  notes TEXT DEFAULT '',
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (candidate_id) REFERENCES candidates(id) ON DELETE CASCADE,
  FOREIGN KEY (search_profile_id) REFERENCES search_profiles(id) ON DELETE CASCADE,
  FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE,
  UNIQUE (candidate_id, search_profile_id, job_id)
);

CREATE TABLE IF NOT EXISTS app_settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_search_profiles_candidate_id ON search_profiles(candidate_id);
CREATE INDEX IF NOT EXISTS idx_profile_queries_profile_id ON search_profile_queries(search_profile_id);
CREATE INDEX IF NOT EXISTS idx_resumes_candidate_id ON resumes(candidate_id);
CREATE INDEX IF NOT EXISTS idx_job_analyses_job_id ON job_analyses(job_id);
CREATE INDEX IF NOT EXISTS idx_job_analyses_profile_id ON job_analyses(search_profile_id);
CREATE INDEX IF NOT EXISTS idx_review_states_job_id ON job_review_states(job_id);
