PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS products (
  id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  source_url TEXT NOT NULL,
  canonical_source_url TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  slug TEXT NOT NULL,
  description TEXT NOT NULL,
  price_cents INTEGER NOT NULL,
  original_price_cents INTEGER,
  currency TEXT NOT NULL,
  rating REAL NOT NULL DEFAULT 0,
  review_count INTEGER NOT NULL DEFAULT 0,
  source_category_id TEXT,
  source_category TEXT,
  canonical_category_id TEXT NOT NULL DEFAULT 'others',
  canonical_category TEXT NOT NULL DEFAULT 'Others',
  category_confidence REAL NOT NULL DEFAULT 0,
  category_scores_json TEXT NOT NULL DEFAULT '{}',
  matched_terms_json TEXT NOT NULL DEFAULT '[]',
  category_id TEXT NOT NULL,
  category TEXT NOT NULL,
  category_source TEXT NOT NULL DEFAULT 'rules',
  ai_category_id TEXT,
  ai_category_confidence REAL,
  ai_category_reason TEXT,
  ai_category_updated_at TEXT,
  brand TEXT,
  collection_code TEXT,
  source_image_url TEXT NOT NULL,
  image_gallery_json TEXT NOT NULL DEFAULT '[]',
  family_key TEXT,
  variant_label TEXT,
  variant_attributes_json TEXT NOT NULL DEFAULT '{}',
  local_image_key TEXT NOT NULL,
  image_mime TEXT NOT NULL,
  image_width INTEGER NOT NULL DEFAULT 0,
  image_height INTEGER NOT NULL DEFAULT 0,
  tags_json TEXT NOT NULL DEFAULT '[]',
  raw_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_verified_at TEXT,
  is_active INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_products_category_id ON products(category_id);
CREATE INDEX IF NOT EXISTS idx_products_updated_at ON products(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_products_collection_code ON products(collection_code);

CREATE TABLE IF NOT EXISTS queries (
  normalized_query TEXT PRIMARY KEY,
  display_query TEXT NOT NULL,
  query_kind TEXT NOT NULL DEFAULT 'search',
  category_id TEXT,
  status TEXT NOT NULL DEFAULT 'idle',
  last_requested_at TEXT,
  last_started_at TEXT,
  last_completed_at TEXT,
  last_error TEXT,
  next_page_token_json TEXT,
  query_variants_json TEXT NOT NULL DEFAULT '[]',
  active_collection_code TEXT
);

CREATE TABLE IF NOT EXISTS query_products (
  normalized_query TEXT NOT NULL,
  product_id TEXT NOT NULL,
  rank INTEGER NOT NULL,
  page_number INTEGER NOT NULL,
  provider TEXT NOT NULL,
  discovered_at TEXT NOT NULL,
  PRIMARY KEY (normalized_query, product_id, page_number),
  FOREIGN KEY (normalized_query) REFERENCES queries(normalized_query),
  FOREIGN KEY (product_id) REFERENCES products(id)
);

CREATE INDEX IF NOT EXISTS idx_query_products_query_page ON query_products(normalized_query, page_number, rank);

CREATE TABLE IF NOT EXISTS collection_groups (
  code TEXT PRIMARY KEY,
  context_key TEXT NOT NULL,
  display_query TEXT NOT NULL,
  query_kind TEXT NOT NULL,
  requested_category_id TEXT,
  resolved_category_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_collection_groups_context ON collection_groups(context_key, updated_at DESC);

CREATE TABLE IF NOT EXISTS collection_group_products (
  group_code TEXT NOT NULL,
  product_id TEXT NOT NULL,
  rank INTEGER NOT NULL,
  page_number INTEGER NOT NULL,
  provider TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (group_code, product_id),
  FOREIGN KEY (group_code) REFERENCES collection_groups(code),
  FOREIGN KEY (product_id) REFERENCES products(id)
);

CREATE INDEX IF NOT EXISTS idx_collection_group_products_group_rank
  ON collection_group_products(group_code, page_number, rank);

CREATE TABLE IF NOT EXISTS reviews (
  id TEXT PRIMARY KEY,
  product_id TEXT NOT NULL,
  author_name TEXT NOT NULL,
  rating REAL NOT NULL DEFAULT 0,
  body TEXT NOT NULL,
  published_at TEXT,
  raw_json TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY (product_id) REFERENCES products(id)
);

CREATE INDEX IF NOT EXISTS idx_reviews_product_id ON reviews(product_id);

CREATE TABLE IF NOT EXISTS related_products (
  product_id TEXT NOT NULL,
  related_product_id TEXT NOT NULL,
  score REAL NOT NULL,
  reason TEXT NOT NULL,
  PRIMARY KEY (product_id, related_product_id),
  FOREIGN KEY (product_id) REFERENCES products(id),
  FOREIGN KEY (related_product_id) REFERENCES products(id)
);

CREATE INDEX IF NOT EXISTS idx_related_products_product_id ON related_products(product_id, score DESC);

CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  password_salt TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_login_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  expires_at TEXT,
  is_active INTEGER NOT NULL DEFAULT 1,
  FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_sessions_user_active ON sessions(user_id, is_active);

CREATE TABLE IF NOT EXISTS user_events (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  session_id TEXT,
  event_type TEXT NOT NULL,
  product_id TEXT,
  category_id TEXT,
  query_text TEXT,
  source_url TEXT,
  canonical_source_url TEXT,
  product_snapshot_json TEXT NOT NULL DEFAULT '{}',
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  FOREIGN KEY (user_id) REFERENCES users(id),
  FOREIGN KEY (product_id) REFERENCES products(id)
);

CREATE INDEX IF NOT EXISTS idx_user_events_user_created ON user_events(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS user_favorites (
  user_id TEXT NOT NULL,
  product_id TEXT NOT NULL,
  canonical_source_url TEXT NOT NULL,
  product_snapshot_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (user_id, product_id),
  FOREIGN KEY (user_id) REFERENCES users(id),
  FOREIGN KEY (product_id) REFERENCES products(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_user_favorites_user_url ON user_favorites(user_id, canonical_source_url);
CREATE INDEX IF NOT EXISTS idx_user_favorites_user_created ON user_favorites(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS user_affinities (
  user_id TEXT NOT NULL,
  affinity_type TEXT NOT NULL,
  affinity_key TEXT NOT NULL,
  score REAL NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (user_id, affinity_type, affinity_key),
  FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_user_affinities_lookup ON user_affinities(user_id, affinity_type, score DESC);

CREATE TABLE IF NOT EXISTS user_recommendations (
  user_id TEXT NOT NULL,
  product_id TEXT NOT NULL,
  score REAL NOT NULL,
  reason TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (user_id, product_id),
  FOREIGN KEY (user_id) REFERENCES users(id),
  FOREIGN KEY (product_id) REFERENCES products(id)
);

CREATE INDEX IF NOT EXISTS idx_user_recommendations_lookup ON user_recommendations(user_id, score DESC);

CREATE TABLE IF NOT EXISTS featured_offer_snapshots (
  period_key TEXT PRIMARY KEY,
  product_ids_json TEXT NOT NULL,
  generated_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_featured_offer_snapshots_expires ON featured_offer_snapshots(expires_at);

CREATE TABLE IF NOT EXISTS ai_query_cache (
  cache_key TEXT PRIMARY KEY,
  normalized_query TEXT NOT NULL,
  category_id TEXT,
  model_id TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  rewrite_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ai_query_cache_lookup ON ai_query_cache(normalized_query, category_id, expires_at DESC);

CREATE TABLE IF NOT EXISTS ai_runs (
  id TEXT PRIMARY KEY,
  run_type TEXT NOT NULL,
  mode TEXT NOT NULL,
  trigger_reason TEXT NOT NULL,
  model_id TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  input_json TEXT NOT NULL,
  output_json TEXT,
  status TEXT NOT NULL,
  latency_ms INTEGER,
  error_text TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ai_runs_lookup ON ai_runs(run_type, status, created_at DESC);

CREATE TABLE IF NOT EXISTS discovery_queries (
  context_key TEXT NOT NULL,
  variant_text TEXT NOT NULL,
  query_text TEXT NOT NULL,
  category_id TEXT,
  provider TEXT NOT NULL DEFAULT 'apify',
  request_json TEXT NOT NULL DEFAULT '{}',
  engines_json TEXT NOT NULL,
  status TEXT NOT NULL,
  last_requested_at TEXT NOT NULL,
  last_completed_at TEXT,
  last_error TEXT,
  PRIMARY KEY (context_key, variant_text)
);

CREATE INDEX IF NOT EXISTS idx_discovery_queries_context ON discovery_queries(context_key, status, last_requested_at DESC);

CREATE TABLE IF NOT EXISTS discovery_hits (
  id TEXT PRIMARY KEY,
  context_key TEXT NOT NULL,
  variant_text TEXT NOT NULL,
  rank INTEGER NOT NULL,
  engine TEXT,
  source TEXT,
  source_title TEXT,
  source_snippet TEXT,
  source_rank INTEGER,
  domain TEXT NOT NULL,
  title TEXT NOT NULL,
  snippet TEXT,
  url TEXT NOT NULL,
  normalized_url TEXT NOT NULL,
  provider_name TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_discovery_hits_context ON discovery_hits(context_key, variant_text, rank);
CREATE INDEX IF NOT EXISTS idx_discovery_hits_url ON discovery_hits(normalized_url);

CREATE TABLE IF NOT EXISTS discovery_cache (
  cache_key TEXT PRIMARY KEY,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_discovery_cache_expiry ON discovery_cache(expires_at DESC);

CREATE TABLE IF NOT EXISTS discovery_suppression (
  normalized_url TEXT PRIMARY KEY,
  provider_name TEXT,
  failure_count INTEGER NOT NULL,
  last_failure_reason TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_discovery_suppression_provider ON discovery_suppression(provider_name, failure_count DESC, updated_at DESC);
