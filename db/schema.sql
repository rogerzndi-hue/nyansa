-- Nyansa : schéma PostgreSQL 16 + PostGIS (MVP et phase 4).
-- Correspond au modèle du dossier de cadrage et à backend/nyansa/models.py.
-- Pays : charger ensuite db/seed_countries.sql (généré par scripts/gen_seed.py).

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;
CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- gen_random_uuid()

CREATE TYPE domain_code AS ENUM ('GEO','ECO','SEC','SOC','GOV','CYB','HUM');
CREATE TYPE region_code AS ENUM ('NORTH','WEST','CENTRAL','EAST','SOUTHERN');
CREATE TYPE classification AS ENUM ('PUBLIC','INTERNE','RESTREINT','CONFIDENTIEL');
CREATE TYPE deliverable_status AS ENUM ('DRAFT','IN_REVIEW','APPROVED','REJECTED','DISSEMINATED');

CREATE TABLE country (
  iso3 char(3) PRIMARY KEY,
  iso2 char(2) NOT NULL UNIQUE,
  name_fr text NOT NULL,
  name_en text NOT NULL,
  region region_code NOT NULL,
  rated boolean NOT NULL DEFAULT true,  -- false : géocodé mais jamais noté (ex. Sahara occidental)
  geom geometry(MultiPolygon, 4326)
);

CREATE TABLE source (
  id text PRIMARY KEY,
  name text NOT NULL,
  kind text NOT NULL CHECK (kind IN ('rss','api','dataset','cti','manual')),
  url text,
  language char(2),
  country_iso3 char(3) REFERENCES country,
  default_reliability char(1) NOT NULL CHECK (default_reliability IN ('A','B','C','D','E','F')),
  license_note text,
  active boolean NOT NULL DEFAULT true
);

CREATE TABLE raw_document (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id text NOT NULL REFERENCES source,
  url text,
  title text NOT NULL,
  body text NOT NULL DEFAULT '',
  language text,
  published_at timestamptz,
  fetched_at timestamptz NOT NULL DEFAULT now(),
  content_hash text NOT NULL,
  object_key text,  -- objet brut dans MinIO/S3
  routing_status text NOT NULL DEFAULT 'pending' CHECK (routing_status IN ('pending','routed','unrouted')),
  reject_reason text CHECK (reject_reason IN ('no_country','ambiguous_country','foreign_focus','stale','no_domain')),
  country_iso3 char(3) REFERENCES country,
  UNIQUE (source_id, content_hash)
);

CREATE TABLE event (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  occurred_at timestamptz NOT NULL,
  country_iso3 char(3) NOT NULL REFERENCES country,
  secondary_countries char(3)[] NOT NULL DEFAULT '{}',
  city text,
  geom geometry(Point, 4326),
  domain domain_code NOT NULL,
  secondary_domains domain_code[] NOT NULL DEFAULT '{}',
  event_type text NOT NULL,
  impact smallint NOT NULL CHECK (impact BETWEEN 1 AND 5),
  probability_override smallint CHECK (probability_override BETWEEN 1 AND 5),
  title text NOT NULL,
  summary text NOT NULL,
  language text,
  evidence text[] NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now()
);

-- Crédibilité Admiralty, probabilité et criticité sont DÉRIVÉES des sources rattachées
-- (voir backend/nyansa/models.py) : on ne les stocke pas pour éviter toute incohérence.
CREATE TABLE event_source (
  event_id uuid REFERENCES event ON DELETE CASCADE,
  document_id uuid REFERENCES raw_document,
  source_id text NOT NULL REFERENCES source,
  reliability char(1) NOT NULL CHECK (reliability IN ('A','B','C','D','E','F')),
  PRIMARY KEY (event_id, document_id)
);

CREATE TABLE risk_score (
  country_iso3 char(3) REFERENCES country,
  domain text NOT NULL DEFAULT 'ALL',
  as_of date NOT NULL,
  method_version text NOT NULL,
  score numeric(5,2) NOT NULL CHECK (score BETWEEN 0 AND 100),
  components jsonb NOT NULL,  -- contributions explicables
  PRIMARY KEY (country_iso3, domain, as_of, method_version)
);

CREATE TABLE app_user (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email text UNIQUE NOT NULL,
  role text NOT NULL CHECK (role IN ('analyst','validator','manager','executive','partner','admin')),
  active boolean NOT NULL DEFAULT true
);

CREATE TABLE audit_log (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  at timestamptz NOT NULL DEFAULT now(),
  user_id uuid REFERENCES app_user,
  action text NOT NULL,
  object_type text,
  object_id text,
  detail jsonb
);

CREATE INDEX event_country_time ON event (country_iso3, occurred_at DESC);
CREATE INDEX event_domain_time ON event (domain, occurred_at DESC);
CREATE INDEX event_geom ON event USING gist (geom);
CREATE INDEX event_title_trgm ON event USING gin (title gin_trgm_ops);
CREATE INDEX raw_document_unrouted ON raw_document (fetched_at) WHERE routing_status = 'unrouted';

-- Phase 4 : livrables ------------------------------------------------------------------------
CREATE TABLE deliverable (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  product_type text NOT NULL CHECK (product_type IN ('flash','bulletin','strategic_note','risk_map','foresight')),
  language char(2) NOT NULL,
  status deliverable_status NOT NULL DEFAULT 'DRAFT',
  classification classification NOT NULL DEFAULT 'INTERNE',
  version int NOT NULL DEFAULT 1,
  title text NOT NULL,
  body jsonb NOT NULL,
  author_id uuid NOT NULL REFERENCES app_user,
  validator_id uuid REFERENCES app_user,
  approved_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  -- jamais de diffusion sans validation humaine, ni par l'auteur lui-même
  CHECK (status NOT IN ('APPROVED','DISSEMINATED')
         OR (validator_id IS NOT NULL AND approved_at IS NOT NULL AND validator_id <> author_id))
);

CREATE TABLE deliverable_source (
  deliverable_id uuid REFERENCES deliverable ON DELETE CASCADE,
  ref_label text,
  event_id uuid REFERENCES event,
  document_id uuid REFERENCES raw_document,
  PRIMARY KEY (deliverable_id, ref_label),
  CHECK (event_id IS NOT NULL OR document_id IS NOT NULL)
);
