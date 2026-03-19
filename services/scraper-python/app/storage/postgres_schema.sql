CREATE TABLE IF NOT EXISTS public.profiles (
  id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  legacy_user_id TEXT UNIQUE,
  email TEXT NOT NULL UNIQUE,
  full_name TEXT,
  phone TEXT,
  address TEXT,
  city TEXT,
  country TEXT,
  role TEXT NOT NULL DEFAULT 'user',
  level INTEGER NOT NULL DEFAULT 1,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public.products (
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
  rating DOUBLE PRECISION NOT NULL DEFAULT 0,
  review_count INTEGER NOT NULL DEFAULT 0,
  source_category_id TEXT,
  source_category TEXT,
  canonical_category_id TEXT NOT NULL DEFAULT 'others',
  canonical_category TEXT NOT NULL DEFAULT 'Others',
  category_confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
  category_scores_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  matched_terms_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  category_id TEXT NOT NULL,
  category TEXT NOT NULL,
  category_source TEXT NOT NULL DEFAULT 'rules',
  ai_category_id TEXT,
  ai_category_confidence DOUBLE PRECISION,
  ai_category_reason TEXT,
  ai_category_updated_at TIMESTAMPTZ,
  brand TEXT,
  source_image_url TEXT NOT NULL,
  image_gallery_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  family_key TEXT,
  variant_label TEXT,
  variant_attributes_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  local_image_key TEXT NOT NULL,
  image_mime TEXT NOT NULL,
  image_width INTEGER NOT NULL DEFAULT 0,
  image_height INTEGER NOT NULL DEFAULT 0,
  tags_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  raw_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  last_verified_at TIMESTAMPTZ,
  is_active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_products_category_id ON public.products(category_id);
CREATE INDEX IF NOT EXISTS idx_products_updated_at ON public.products(updated_at DESC);

CREATE TABLE IF NOT EXISTS public.queries (
  normalized_query TEXT PRIMARY KEY,
  display_query TEXT NOT NULL,
  query_kind TEXT NOT NULL DEFAULT 'search',
  category_id TEXT,
  status TEXT NOT NULL DEFAULT 'idle',
  last_requested_at TIMESTAMPTZ,
  last_started_at TIMESTAMPTZ,
  last_completed_at TIMESTAMPTZ,
  last_error TEXT,
  next_page_token_json JSONB,
  query_variants_json JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_queries_kind_category ON public.queries(query_kind, category_id);

CREATE TABLE IF NOT EXISTS public.query_products (
  normalized_query TEXT NOT NULL REFERENCES public.queries(normalized_query) ON DELETE CASCADE,
  product_id TEXT NOT NULL REFERENCES public.products(id) ON DELETE CASCADE,
  rank INTEGER NOT NULL,
  page_number INTEGER NOT NULL,
  provider TEXT NOT NULL,
  discovered_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (normalized_query, product_id, page_number)
);

CREATE INDEX IF NOT EXISTS idx_query_products_query_page ON public.query_products(normalized_query, page_number, rank);

CREATE TABLE IF NOT EXISTS public.reviews (
  id TEXT PRIMARY KEY,
  product_id TEXT NOT NULL REFERENCES public.products(id) ON DELETE CASCADE,
  author_name TEXT NOT NULL,
  rating DOUBLE PRECISION NOT NULL DEFAULT 0,
  body TEXT NOT NULL,
  published_at TIMESTAMPTZ,
  raw_json JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_reviews_product_id ON public.reviews(product_id);

CREATE TABLE IF NOT EXISTS public.related_products (
  product_id TEXT NOT NULL REFERENCES public.products(id) ON DELETE CASCADE,
  related_product_id TEXT NOT NULL REFERENCES public.products(id) ON DELETE CASCADE,
  score DOUBLE PRECISION NOT NULL,
  reason TEXT NOT NULL,
  PRIMARY KEY (product_id, related_product_id)
);

CREATE INDEX IF NOT EXISTS idx_related_products_product_id ON public.related_products(product_id, score DESC);

CREATE TABLE IF NOT EXISTS public.user_events (
  id TEXT PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
  session_id TEXT,
  event_type TEXT NOT NULL,
  product_id TEXT REFERENCES public.products(id) ON DELETE SET NULL,
  category_id TEXT,
  query_text TEXT,
  source_url TEXT,
  canonical_source_url TEXT,
  product_snapshot_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_user_events_user_created ON public.user_events(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_user_events_user_session_created ON public.user_events(user_id, session_id, created_at DESC);

CREATE TABLE IF NOT EXISTS public.user_favorites (
  user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
  product_id TEXT NOT NULL REFERENCES public.products(id) ON DELETE CASCADE,
  canonical_source_url TEXT NOT NULL,
  product_snapshot_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (user_id, product_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_user_favorites_user_url ON public.user_favorites(user_id, canonical_source_url);
CREATE INDEX IF NOT EXISTS idx_user_favorites_user_created ON public.user_favorites(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS public.user_affinities (
  user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
  affinity_type TEXT NOT NULL,
  affinity_key TEXT NOT NULL,
  score DOUBLE PRECISION NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (user_id, affinity_type, affinity_key)
);

CREATE INDEX IF NOT EXISTS idx_user_affinities_lookup ON public.user_affinities(user_id, affinity_type, score DESC);

CREATE TABLE IF NOT EXISTS public.user_recommendations (
  user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
  product_id TEXT NOT NULL REFERENCES public.products(id) ON DELETE CASCADE,
  score DOUBLE PRECISION NOT NULL,
  reason TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (user_id, product_id)
);

CREATE INDEX IF NOT EXISTS idx_user_recommendations_lookup ON public.user_recommendations(user_id, score DESC);

CREATE TABLE IF NOT EXISTS public.ai_query_cache (
  cache_key TEXT PRIMARY KEY,
  normalized_query TEXT NOT NULL,
  category_id TEXT,
  model_id TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  rewrite_json JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ai_query_cache_lookup ON public.ai_query_cache(normalized_query, category_id, expires_at DESC);

CREATE TABLE IF NOT EXISTS public.ai_runs (
  id TEXT PRIMARY KEY,
  run_type TEXT NOT NULL,
  mode TEXT NOT NULL,
  trigger_reason TEXT NOT NULL,
  model_id TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  input_json JSONB NOT NULL,
  output_json JSONB,
  status TEXT NOT NULL,
  latency_ms INTEGER,
  error_text TEXT,
  created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ai_runs_lookup ON public.ai_runs(run_type, status, created_at DESC);

CREATE TABLE IF NOT EXISTS public.discovery_queries (
  context_key TEXT NOT NULL,
  variant_text TEXT NOT NULL,
  query_text TEXT NOT NULL,
  category_id TEXT,
  provider TEXT NOT NULL DEFAULT 'apify',
  request_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  engines_json JSONB NOT NULL,
  status TEXT NOT NULL,
  last_requested_at TIMESTAMPTZ NOT NULL,
  last_completed_at TIMESTAMPTZ,
  last_error TEXT,
  PRIMARY KEY (context_key, variant_text)
);

CREATE INDEX IF NOT EXISTS idx_discovery_queries_context ON public.discovery_queries(context_key, status, last_requested_at DESC);

CREATE TABLE IF NOT EXISTS public.discovery_hits (
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
  created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_discovery_hits_context ON public.discovery_hits(context_key, variant_text, rank);
CREATE INDEX IF NOT EXISTS idx_discovery_hits_url ON public.discovery_hits(normalized_url);

CREATE TABLE IF NOT EXISTS public.discovery_cache (
  cache_key TEXT PRIMARY KEY,
  payload_json JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_discovery_cache_expiry ON public.discovery_cache(expires_at DESC);

CREATE TABLE IF NOT EXISTS public.discovery_suppression (
  normalized_url TEXT PRIMARY KEY,
  provider_name TEXT,
  failure_count INTEGER NOT NULL,
  last_failure_reason TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_discovery_suppression_provider ON public.discovery_suppression(provider_name, failure_count DESC, updated_at DESC);

ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_favorites ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_affinities ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_recommendations ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS profiles_self_select ON public.profiles;
CREATE POLICY profiles_self_select ON public.profiles
  FOR SELECT
  USING (auth.uid() = id OR EXISTS (SELECT 1 FROM public.profiles p WHERE p.id = auth.uid() AND p.role = 'admin'));

DROP POLICY IF EXISTS profiles_self_update ON public.profiles;
CREATE POLICY profiles_self_update ON public.profiles
  FOR UPDATE
  USING (auth.uid() = id OR EXISTS (SELECT 1 FROM public.profiles p WHERE p.id = auth.uid() AND p.role = 'admin'))
  WITH CHECK (auth.uid() = id OR EXISTS (SELECT 1 FROM public.profiles p WHERE p.id = auth.uid() AND p.role = 'admin'));

DROP POLICY IF EXISTS user_events_owner_all ON public.user_events;
CREATE POLICY user_events_owner_all ON public.user_events
  FOR ALL
  USING (auth.uid() = user_id OR EXISTS (SELECT 1 FROM public.profiles p WHERE p.id = auth.uid() AND p.role = 'admin'))
  WITH CHECK (auth.uid() = user_id OR EXISTS (SELECT 1 FROM public.profiles p WHERE p.id = auth.uid() AND p.role = 'admin'));

DROP POLICY IF EXISTS user_favorites_owner_all ON public.user_favorites;
CREATE POLICY user_favorites_owner_all ON public.user_favorites
  FOR ALL
  USING (auth.uid() = user_id OR EXISTS (SELECT 1 FROM public.profiles p WHERE p.id = auth.uid() AND p.role = 'admin'))
  WITH CHECK (auth.uid() = user_id OR EXISTS (SELECT 1 FROM public.profiles p WHERE p.id = auth.uid() AND p.role = 'admin'));

DROP POLICY IF EXISTS user_affinities_owner_all ON public.user_affinities;
CREATE POLICY user_affinities_owner_all ON public.user_affinities
  FOR ALL
  USING (auth.uid() = user_id OR EXISTS (SELECT 1 FROM public.profiles p WHERE p.id = auth.uid() AND p.role = 'admin'))
  WITH CHECK (auth.uid() = user_id OR EXISTS (SELECT 1 FROM public.profiles p WHERE p.id = auth.uid() AND p.role = 'admin'));

DROP POLICY IF EXISTS user_recommendations_owner_all ON public.user_recommendations;
CREATE POLICY user_recommendations_owner_all ON public.user_recommendations
  FOR ALL
  USING (auth.uid() = user_id OR EXISTS (SELECT 1 FROM public.profiles p WHERE p.id = auth.uid() AND p.role = 'admin'))
  WITH CHECK (auth.uid() = user_id OR EXISTS (SELECT 1 FROM public.profiles p WHERE p.id = auth.uid() AND p.role = 'admin'));
