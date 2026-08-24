-- ============================================================
-- Synagogue Environmental Research Database
-- PostgreSQL schema — 3NF compliant
-- ============================================================

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS vector;

-- ── geographic dimensions (must exist before synagogues) ─────

CREATE TABLE IF NOT EXISTS congressional_districts (
    district_id      VARCHAR(10) PRIMARY KEY,  -- e.g. 'CA-50', 'NY-12', 'IL-9'
    state_po         CHAR(2)  NOT NULL,
    district_number  SMALLINT NOT NULL,         -- 0 = at-large
    congress_session SMALLINT NOT NULL DEFAULT 119,
    boundary         GEOMETRY(MultiPolygon, 4326)  -- TIGER/Line 118th Congress boundary
);

CREATE TABLE IF NOT EXISTS census_tracts (
    geoid        CHAR(11) PRIMARY KEY,   -- 11-digit FIPS e.g. '06073008376'
    state_fips   CHAR(2),
    county_fips  CHAR(3),
    tract_number VARCHAR(10),
    name         TEXT,                  -- 'Census Tract 83.76, CA'
    boundary     GEOMETRY(MultiPolygon, 4326)  -- TIGER/Line 2023 boundary (filtered to referenced tracts)
);

CREATE TABLE IF NOT EXISTS census_tract_demographics (
    geoid                    CHAR(11) PRIMARY KEY REFERENCES census_tracts(geoid),

    -- ACS 5-Year 2023 income (B19013)
    median_household_income  INTEGER,          -- NULL when Census suppresses small-population tracts

    -- ACS 5-Year 2023 language spoken at home (C16001), raw counts
    lang_total               INTEGER,          -- C16001_001E  (population 5+)
    lang_english_only        INTEGER,          -- C16001_002E
    lang_spanish             INTEGER,          -- C16001_003E + C16001_004E
    lang_indo_european       INTEGER,          -- C16001_005E..012E (French, German, Slavic, other IE)
    lang_asian_pacific       INTEGER,          -- C16001_013E..022E (Korean, Chinese, Vietnamese, Tagalog, other AP)
    lang_arabic              INTEGER,          -- C16001_023E + C16001_024E
    lang_other               INTEGER,          -- C16001_025E + C16001_026E

    -- USDA ERS Rural-Urban Commuting Area codes (2020 delineations)
    -- 1=Urban core  2-3=Urban commuter  4-6=Large/medium/small rural  7-10=Isolated rural
    ruca_code                SMALLINT,
    ruca_secondary_code      NUMERIC(4,1),

    acs_year                 SMALLINT NOT NULL DEFAULT 2023
);

CREATE INDEX IF NOT EXISTS ctd_ruca_idx ON census_tract_demographics(ruca_code);

-- ── core entity ───────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS synagogues (
    id                     SERIAL PRIMARY KEY,
    osm_id                 BIGINT UNIQUE,           -- null for Google-Places-only entries
    osm_type               VARCHAR(10),             -- 'node', 'way', 'relation'
    place_id               VARCHAR(255) UNIQUE,     -- Google Places ID; null for OSM-only entries
    name                   TEXT NOT NULL,
    lat                    DOUBLE PRECISION,
    lon                    DOUBLE PRECISION,
    city                   VARCHAR(100),
    state                  CHAR(2),
    denomination           VARCHAR(50),
    address                TEXT,                    -- formatted address from Google Places
    business_status        VARCHAR(50),             -- 'OPERATIONAL', 'CLOSED_PERMANENTLY', etc.
    source                 VARCHAR(20),             -- 'OSM', 'Google-Places', 'Both'
    congressional_district VARCHAR(10) REFERENCES congressional_districts(district_id),
    census_tract_geoid     CHAR(11)    REFERENCES census_tracts(geoid),
    location               GEOMETRY(Point, 4326),  -- derived from lat/lon; populated by load_data.py
    denomination_canonical VARCHAR(50),             -- normalized denomination; backfill_denomination_from_*.py
    denomination_source    VARCHAR(20)              -- where denomination_canonical came from, e.g. 'America_Orig', 'url', 'LLM'
);

CREATE INDEX IF NOT EXISTS synagogues_district_idx   ON synagogues(congressional_district);
CREATE INDEX IF NOT EXISTS synagogues_tract_idx      ON synagogues(census_tract_geoid);
CREATE INDEX IF NOT EXISTS synagogues_state_idx      ON synagogues(state);
CREATE INDEX IF NOT EXISTS synagogues_location_idx   ON synagogues USING GIST(location);
CREATE INDEX IF NOT EXISTS districts_boundary_idx    ON congressional_districts USING GIST(boundary);
CREATE INDEX IF NOT EXISTS tracts_boundary_idx       ON census_tracts USING GIST(boundary);

-- LLM-inferred denomination, one row per synagogue. Populated when
-- backfill_denomination_from_url.py / from_america.py can't resolve a
-- canonical label and an LLM call is used instead.

CREATE TABLE IF NOT EXISTS denomination_llm_classifications (
    id            SERIAL PRIMARY KEY,
    synagogue_id  INTEGER UNIQUE NOT NULL REFERENCES synagogues(id) ON DELETE CASCADE,
    label         VARCHAR(50),
    raw_response  TEXT,
    model_used    VARCHAR(50),
    temperature   DOUBLE PRECISION,
    n_pages       INTEGER,
    error         TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── website & crawl data ──────────────────────────────────────

CREATE TABLE IF NOT EXISTS websites (
    id              SERIAL PRIMARY KEY,
    synagogue_id    INTEGER REFERENCES synagogues(id) ON DELETE CASCADE,
    url             TEXT UNIQUE NOT NULL,
    -- 'domain' is derived from 'url' but stored for query convenience
    -- (it is the join key for env_keyword_analysis and llm_classifications)
    domain          TEXT NOT NULL,
    website_source  VARCHAR(30),    -- 'OSM', 'Wikidata-coord', 'Wikidata', etc.
    in_common_crawl BOOLEAN,        -- whether url was found in a Common Crawl index
    last_crawled    TIMESTAMP,      -- when load_crawl_results.py last processed this site
    http_status     SMALLINT,       -- HTTP status code from the crawl attempt
    crawl_id        VARCHAR(30),    -- Common Crawl index id used, e.g. 'CC-MAIN-2024-46'
    last_modified   TEXT,           -- most recent <lastmod> from sitemap.xml
    -- Crawl outcome: TRUE when a crawl attempt produced no usable pages.
    -- A site with >=1 row in web_pages is always has_error = FALSE.
    has_error       BOOLEAN NOT NULL DEFAULT FALSE,
    error_text      TEXT            -- failure reason; NULL unless has_error
);

CREATE INDEX IF NOT EXISTS websites_synagogue_idx ON websites(synagogue_id);
CREATE INDEX IF NOT EXISTS websites_domain_idx    ON websites(domain);

-- Raw HTML stays on disk (~100 KB/file, ~2-3 GB total).
-- Extracted plain text is stored here for full-text search via tsvector.
CREATE TABLE IF NOT EXISTS web_pages (
    id                 SERIAL PRIMARY KEY,
    website_id         INTEGER NOT NULL REFERENCES websites(id) ON DELETE CASCADE,
    page_path          TEXT NOT NULL,  -- e.g. 'bethshalomnb.org/pray.html'
    has_env_keywords   BOOLEAN NOT NULL DEFAULT FALSE,
    -- Plain text extracted from HTML (BeautifulSoup); stored for full-text search
    content_text       TEXT,
    -- Generated tsvector — PostgreSQL maintains this automatically
    content_tsv        TSVECTOR GENERATED ALWAYS AS
                           (to_tsvector('english', COALESCE(content_text, ''))) STORED,
    -- all-MiniLM-L6-v2 embedding (384-dim, L2-normalised); populated by embed_pages.py
    embedding          vector(384),
    UNIQUE (website_id, page_path)
);

-- GIN index enables fast @@ to_tsquery() across all 23k pages
CREATE INDEX IF NOT EXISTS web_pages_tsv_idx      ON web_pages USING GIN (content_tsv);
CREATE INDEX IF NOT EXISTS web_pages_website_idx  ON web_pages(website_id);
-- ivfflat index for cosine similarity search (build after embeddings are populated)
CREATE INDEX IF NOT EXISTS web_pages_embedding_idx ON web_pages
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX IF NOT EXISTS web_pages_env_idx      ON web_pages(has_env_keywords) WHERE has_env_keywords;

-- ── per-page keyword match offsets ───────────────────────────
-- Populated by 06_environmental_classification/classify_environmental.py.
-- One row per keyword occurrence; start_offset/end_offset are character
-- positions into web_pages.content_text (0-based, end is exclusive).
-- Used downstream to build dynamic context windows for LLM classification.

CREATE TABLE IF NOT EXISTS page_keyword_matches (
    id           SERIAL PRIMARY KEY,
    web_page_id  INTEGER NOT NULL REFERENCES web_pages(id) ON DELETE CASCADE,
    keyword      TEXT    NOT NULL,   -- matched token, lowercased
    start_offset INTEGER NOT NULL,   -- char offset into content_text (0-based)
    end_offset   INTEGER NOT NULL    -- exclusive end offset
);

CREATE INDEX IF NOT EXISTS pkm_page_idx    ON page_keyword_matches(web_page_id);
CREATE INDEX IF NOT EXISTS pkm_keyword_idx ON page_keyword_matches(keyword);

-- ── environmental & LLM analysis ──────────────────────────────

CREATE TABLE IF NOT EXISTS env_keyword_analysis (
    id                        SERIAL PRIMARY KEY,
    website_id                INTEGER UNIQUE NOT NULL REFERENCES websites(id) ON DELETE CASCADE,
    pages_with_hits           INTEGER NOT NULL DEFAULT 0,
    total_pages               INTEGER NOT NULL DEFAULT 0,
    total_keyword_occurrences INTEGER NOT NULL DEFAULT 0,
    -- Generated column: avoids storing a derived value
    hit_rate                  NUMERIC(6,5) GENERATED ALWAYS AS (
                                  CASE WHEN total_pages > 0
                                  THEN pages_with_hits::NUMERIC / total_pages
                                  ELSE 0 END
                              ) STORED,
    -- Populated after LLM classification run completes
    top_llm_category          VARCHAR(100),
    llm_category_distribution JSONB,    -- e.g. {"Waste": 12, "Energy": 5, "Other": 2}
    llm_pages_classified      INTEGER
);

CREATE TABLE IF NOT EXISTS llm_classifications (
    id            SERIAL PRIMARY KEY,
    web_page_id   INTEGER NOT NULL REFERENCES web_pages(id) ON DELETE CASCADE,
    category      VARCHAR(100),  -- 'Community','Spirituality and Worship','Kitchen',
                                 -- 'Waste','Energy','Operations and Maintenance',
                                 -- 'Environmental and Climate Justice','Water','Other'
    model_used    VARCHAR(60),   -- e.g. 'claude-haiku-4-5', 'nvidia/nemotron-3-super-120b-a12b:free'
    temperature   NUMERIC(4,3),  -- e.g. 0.0, 0.7 — NULL means model default was used
    error         TEXT,
    classified_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS llm_page_idx     ON llm_classifications(web_page_id);
CREATE INDEX IF NOT EXISTS llm_category_idx ON llm_classifications(category);

-- Chunk-level classifications: one row per page_keyword_matches occurrence.
-- chunk_text stores the exact excerpt sent to the LLM (window around the keyword hit)
-- so results are auditable without re-joining to web_pages.content_text.

CREATE TABLE IF NOT EXISTS llm_chunk_classifications (
    id              SERIAL PRIMARY KEY,
    match_id        INTEGER NOT NULL REFERENCES page_keyword_matches(id) ON DELETE CASCADE,
    chunk_text      TEXT,           -- text excerpt sent to the LLM
    category        TEXT,          -- one of the 76 labels or "N/A" (full label can exceed 100 chars)
    major_category  TEXT,          -- one of the 9 major categories bucketed from `category`;
                                    -- NULL when category = 'N/A'. See
                                    -- 08_database/migrate_add_major_category.py and
                                    -- 11_analysis/recreate_existing/recreate_common.py (classify_action).
    model_used      VARCHAR(60),   -- e.g. 'claude-haiku-4-5'
    temperature     NUMERIC(4,3),  -- NULL means model default was used
    error           TEXT,
    classified_at   TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS lcc_match_idx          ON llm_chunk_classifications(match_id);
CREATE INDEX IF NOT EXISTS lcc_category_idx       ON llm_chunk_classifications(category);
CREATE INDEX IF NOT EXISTS lcc_major_category_idx ON llm_chunk_classifications(major_category);

-- Framing classifications: one row per llm_chunk_classifications row whose
-- category is a confirmed action (category != 'N/A'). Reproduces the
-- Implicit/Explicit/Embedded "framing" axis from Baugh (2019) and
-- Caldwell et al. (2022), as used in Shore (2025) MES thesis.

CREATE TABLE IF NOT EXISTS llm_chunk_framing (
    id            SERIAL PRIMARY KEY,
    chunk_id      INTEGER NOT NULL REFERENCES llm_chunk_classifications(id) ON DELETE CASCADE,
    framing       TEXT,          -- 'Explicit', 'Embedded', or 'Implicit'
    model_used    VARCHAR(60),
    temperature   NUMERIC(4,3),
    error         TEXT,
    classified_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS lcf_chunk_unique ON llm_chunk_framing(chunk_id);
CREATE INDEX IF NOT EXISTS lcf_framing_idx ON llm_chunk_framing(framing);

-- Page-chunks method: every crawled page is split into fixed 500-char
-- (whitespace-snapped) windows and each is classified independently, unlike
-- llm_chunk_classifications which only gets a row for pages with a keyword
-- hit. The point is to also catch actions the keyword scan missed entirely.
-- chunk_text() lives in 06_environmental_classification/chunk_common.py
-- (stdlib-only) and is shared by the production script and the research
-- scripts that validated the method. Parallel to llm_chunk_classifications /
-- llm_chunk_framing above; see 11_analysis/KNOWN_DATA_ISSUES.md for how the
-- two methods' populations differ.

CREATE TABLE IF NOT EXISTS page_chunk_classifications (
    id             SERIAL PRIMARY KEY,
    web_page_id    INTEGER NOT NULL REFERENCES web_pages(id) ON DELETE CASCADE,
    chunk_index    INTEGER NOT NULL,   -- 0-based index within the page's
                                        -- deterministic chunk_text() sequence
    chunk_text     TEXT,               -- exact excerpt sent to the LLM
    category       TEXT,               -- one of the 76 labels, or 'N/A'
    major_category TEXT,               -- 9-bucket rollup of category;
                                        -- NULL when category = 'N/A'
    model_used     VARCHAR(60),
    temperature    NUMERIC(4,3),
    error          TEXT,
    classified_at  TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (web_page_id, chunk_index)
);

-- No separate index on web_page_id alone: the UNIQUE(web_page_id, chunk_index)
-- constraint's composite btree already indexes WHERE web_page_id = ... lookups.
CREATE INDEX IF NOT EXISTS pcc_category_idx       ON page_chunk_classifications(category);
CREATE INDEX IF NOT EXISTS pcc_major_category_idx ON page_chunk_classifications(major_category);

CREATE TABLE IF NOT EXISTS page_chunk_framing (
    id            SERIAL PRIMARY KEY,
    chunk_id      INTEGER NOT NULL REFERENCES page_chunk_classifications(id) ON DELETE CASCADE,
    framing       TEXT,          -- 'Explicit', 'Embedded', or 'Implicit'
    model_used    VARCHAR(60),
    temperature   NUMERIC(4,3),
    error         TEXT,
    classified_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS pcf_chunk_unique ON page_chunk_framing(chunk_id);
CREATE INDEX IF NOT EXISTS pcf_framing_idx ON page_chunk_framing(framing);

-- ── election data (Harvard Dataverse DOI:10.7910/DVN/IG0UN2) ─
-- Split to eliminate the 3NF violation in the raw .tab file where
-- totalvotes repeats on every candidate row within the same race.

CREATE TABLE IF NOT EXISTS election_races (
    id          SERIAL PRIMARY KEY,
    district_id VARCHAR(10) NOT NULL REFERENCES congressional_districts(district_id),
    year        SMALLINT    NOT NULL,
    stage       VARCHAR(5)  NOT NULL,  -- 'gen' (general) or 'pri' (primary)
    special     BOOLEAN     NOT NULL DEFAULT FALSE,
    runoff      BOOLEAN     NOT NULL DEFAULT FALSE,
    mode        VARCHAR(30) NOT NULL,  -- 'total', 'mail', 'election day', etc.
    totalvotes  INTEGER,
    UNIQUE (district_id, year, stage, mode)
);

CREATE INDEX IF NOT EXISTS election_races_district_year ON election_races(district_id, year, stage);

CREATE TABLE IF NOT EXISTS election_candidates (
    id             SERIAL PRIMARY KEY,
    race_id        INTEGER NOT NULL REFERENCES election_races(id) ON DELETE CASCADE,
    candidate      TEXT,
    party          VARCHAR(50),
    candidatevotes INTEGER,
    vote_share     NUMERIC(8,6),  -- populated at load time: candidatevotes / race.totalvotes
    writein        BOOLEAN NOT NULL DEFAULT FALSE,
    fusion_ticket  BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS election_candidates_race_idx  ON election_candidates(race_id);
CREATE INDEX IF NOT EXISTS election_candidates_party_idx ON election_candidates(party);

-- ── EPA Superfund sites (ArcGIS FeatureServer) ───────────────
-- Populated by 09_figures/superfund_similarity_correlation.py
-- Source: FAC_Superfund_Site_Boundaries_EPA_Public / FeatureServer/0

CREATE TABLE IF NOT EXISTS superfund_sites (
    id         SERIAL PRIMARY KEY,
    epa_id     TEXT,
    site_name  TEXT,
    state      TEXT,
    npl_status TEXT,
    geom       GEOMETRY(Geometry, 4326)
);

CREATE INDEX IF NOT EXISTS superfund_geom_idx ON superfund_sites USING GIST(geom);

-- ── raw OSM import (osm2pgsql --flex, osm_flex.lua) ───────────
-- Populated by import_osm.sh. Kept in its own schema since it is a
-- generic OSM dump, not modeled around the synagogues research schema.

CREATE SCHEMA IF NOT EXISTS osm;

CREATE TABLE IF NOT EXISTS osm.osm_points (
    osm_id       BIGINT NOT NULL,
    feature_type TEXT   NOT NULL,
    name         TEXT,
    tags         JSONB,
    geom         GEOMETRY(Point, 4326)
);

CREATE TABLE IF NOT EXISTS osm.osm_lines (
    osm_id       BIGINT NOT NULL,
    feature_type TEXT   NOT NULL,
    name         TEXT,
    tags         JSONB,
    geom         GEOMETRY(LineString, 4326)
);

CREATE TABLE IF NOT EXISTS osm.osm_polygons (
    osm_id       BIGINT NOT NULL,
    feature_type TEXT   NOT NULL,
    name         TEXT,
    tags         JSONB,
    geom         GEOMETRY(Geometry, 4326)
);

CREATE INDEX IF NOT EXISTS osm_points_geom_idx   ON osm.osm_points   USING GIST(geom);
CREATE INDEX IF NOT EXISTS osm_points_osm_id_idx ON osm.osm_points   (osm_id);
CREATE INDEX IF NOT EXISTS osm_points_type_idx   ON osm.osm_points   (feature_type);
CREATE INDEX IF NOT EXISTS osm_lines_geom_idx    ON osm.osm_lines    USING GIST(geom);
CREATE INDEX IF NOT EXISTS osm_lines_osm_id_idx  ON osm.osm_lines    (osm_id);
CREATE INDEX IF NOT EXISTS osm_lines_type_idx    ON osm.osm_lines    (feature_type);
CREATE INDEX IF NOT EXISTS osm_polygons_geom_idx   ON osm.osm_polygons USING GIST(geom);
CREATE INDEX IF NOT EXISTS osm_polygons_osm_id_idx ON osm.osm_polygons (osm_id);
CREATE INDEX IF NOT EXISTS osm_polygons_type_idx   ON osm.osm_polygons (feature_type);

-- ── loading-time helper: district_id from Harvard columns ─────
-- district_id = state_po || '-' || CAST(district::INTEGER AS TEXT)
-- e.g. state_po='CA', district='050' → 'CA-50'

-- ── example queries ───────────────────────────────────────────
-- Full-text search across all pages for a keyword:
--   SELECT wp.page_path, s.name, s.congressional_district
--   FROM web_pages wp
--   JOIN websites w ON w.id = wp.website_id
--   JOIN synagogues s ON s.id = w.synagogue_id
--   WHERE wp.content_tsv @@ to_tsquery('solar & energy');
--
-- Average env hit rate per district × 2024 general election winner:
--   SELECT s.congressional_district, AVG(ek.hit_rate),
--          ec.candidate AS winner, ec.vote_share
--   FROM synagogues s
--   JOIN websites w ON w.synagogue_id = s.id
--   JOIN env_keyword_analysis ek ON ek.website_id = w.id
--   JOIN election_races er ON er.district_id = s.congressional_district
--     AND er.year = 2024 AND er.stage = 'gen'
--   JOIN election_candidates ec ON ec.race_id = er.id
--   WHERE ec.vote_share = (
--       SELECT MAX(ec2.vote_share) FROM election_candidates ec2 WHERE ec2.race_id = er.id
--   )
--   GROUP BY s.congressional_district, ec.candidate, ec.vote_share;
