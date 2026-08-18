-- Public Foundry insights agent: analytical store schema.
--
-- Written to load identically under SQLite (used for verification during the
-- build) and DuckDB (the deployment target). Types are restricted to the
-- intersection of both: TEXT, INTEGER, DOUBLE.
--
-- Every table is keyed on pid6, the Census government identifier, which is the
-- only identifier present across all fiscal sources. Office data has no pid6
-- and reaches entities through office_entity_match.

DROP TABLE IF EXISTS entities;
CREATE TABLE entities (
    pid6                     TEXT PRIMARY KEY,
    legal_name               TEXT NOT NULL,   -- Census legal name, for record matching
    common_name              TEXT,            -- alias; use this in prose per §6
    gov_type_id              INTEGER,
    gov_type_name            TEXT,            -- State | County | Municipal | School District | Special District
    state_fips               TEXT,
    county_fips              TEXT,
    place_fips               TEXT,
    geoid                    TEXT,
    gidid                    TEXT,
    host_county              TEXT,
    host_county_pid6         TEXT,
    is_dependent             INTEGER,
    website                  TEXT,
    -- The city on the government's mailing address. Not a service area and not
    -- a boundary: it is where the mail goes, and it is what entity_point is
    -- derived from.
    address_city             TEXT,
    sd_lea                   TEXT,
    school_district_type     TEXT,
    source_file              TEXT NOT NULL
);

-- Guardrail state, computed once at build time so the runtime tools can return
-- a caveat in the same object as the figure. Each column maps to a named rule
-- in the system prompt.
DROP TABLE IF EXISTS entity_flags;
CREATE TABLE entity_flags (
    pid6                        TEXT PRIMARY KEY,
    prior_year_missing          INTEGER,  -- §11: priorYearScore=0 means absent, not a real move
    debt_capped                 INTEGER,  -- §8: debt component scored 0, composite caps near 60
    capital_median_degenerate   INTEGER,  -- §8: peer median at or near zero, decline the ratio
    other_share_high            INTEGER,  -- §8: "Other" over 20%, breakdown is partial
    concentration_total         INTEGER,  -- §11: one area over 90%, composition carries no signal
    peer_group_mismatch         INTEGER,  -- benchmark peer pool does not match entity type
    peer_count_disagrees        INTEGER,  -- stated peer count differs from the observed pool
    no_population_denominator   INTEGER,  -- §6: per-capita is not computable for this entity
    revenue_volatility_absent   INTEGER,  -- too few years to compute variance
    capital_share_active        INTEGER,  -- §11: capital share over 20%
    capital_share_lead          INTEGER,  -- §11: capital share over 30%
    personnel_share_unusual     INTEGER   -- §11: personnel share of operating below 35% or above 65%
);

DROP TABLE IF EXISTS fiscal_stability;
CREATE TABLE fiscal_stability (
    pid6                    TEXT PRIMARY KEY,
    year                    INTEGER,
    score                   DOUBLE,
    rating_label            TEXT,
    prior_year_score        DOUBLE,   -- NULL where the source encoded absence as 0
    yoy_change              DOUBLE,   -- NULL where prior_year_score is NULL
    structural_balance      DOUBLE,
    structural_balance_score DOUBLE,
    debt_to_revenue         DOUBLE,
    debt_to_revenue_score   DOUBLE,
    peer_group              TEXT,
    peer_count_stated       INTEGER,
    peer_count_observed     INTEGER,
    peer_min                DOUBLE,
    peer_min_name           TEXT,
    peer_median             DOUBLE,
    peer_max                DOUBLE,
    peer_max_name           TEXT
);

DROP TABLE IF EXISTS operating_vs_capital;
CREATE TABLE operating_vs_capital (
    pid6                     TEXT PRIMARY KEY,
    year                     INTEGER,
    operating_expenditure    DOUBLE,
    capital_expenditure      DOUBLE,
    total_expenditure        DOUBLE,
    operating_pct            DOUBLE,
    capital_pct              DOUBLE,
    yoy_change               DOUBLE,
    yoy_pct_change           DOUBLE,
    previous_year            INTEGER,
    peer_group               TEXT,
    peer_count_stated        INTEGER,
    peer_low                 DOUBLE,
    peer_median              DOUBLE,
    peer_high                DOUBLE,
    peer_median_degenerate   INTEGER
);

DROP TABLE IF EXISTS revenue_volatility;
CREATE TABLE revenue_volatility (
    pid6                     TEXT PRIMARY KEY,
    earliest_year            INTEGER,
    latest_year              INTEGER,
    num_yoy_pairs            INTEGER,
    volatility_score         DOUBLE,
    coefficient_of_variation DOUBLE,
    mean_yoy_change          DOUBLE,
    stddev_yoy_change        DOUBLE,
    swings_dominate_trend    INTEGER   -- §11: coefficient of variation above 1.0
);

DROP TABLE IF EXISTS revenue_volatility_yoy;
CREATE TABLE revenue_volatility_yoy (
    pid6             TEXT,
    year_pair        TEXT,
    prev_revenue     DOUBLE,
    current_revenue  DOUBLE,
    yoy_change       DOUBLE
);

-- The total the service-area shares are computed against, across every year
-- the source reports it. Deliberately its own table rather than a column on
-- financial_trends: they are different measures of "expenditure" and they
-- disagree for three quarters of the corpus, so a schema that let one stand in
-- for the other would rebuild the basis confusion this table exists to end.
DROP TABLE IF EXISTS service_area_totals;
CREATE TABLE service_area_totals (
    pid6                TEXT,
    year                INTEGER,
    total_expenditure   REAL,
    is_breakdown_year   INTEGER   -- 1 for the year the service-area split describes
);

DROP TABLE IF EXISTS spending_by_service_area;
CREATE TABLE spending_by_service_area (
    pid6          TEXT,
    year          INTEGER,
    service_area  TEXT,
    total         DOUBLE,
    percentage    DOUBLE
);

DROP TABLE IF EXISTS workforce_profile;
CREATE TABLE workforce_profile (
    pid6                    TEXT PRIMARY KEY,
    year                    INTEGER,
    total_employees         INTEGER,
    population              INTEGER,
    employees_per_1000      DOUBLE,
    total_annual_payroll    DOUBLE,
    full_time_ratio         DOUBLE,
    average_annual_wage     DOUBLE,
    personnel_cost_pct      DOUBLE,
    operating_expenditures  DOUBLE
);

DROP TABLE IF EXISTS workforce_by_service_area;
CREATE TABLE workforce_by_service_area (
    pid6          TEXT,
    year          INTEGER,
    service_area  TEXT,
    headcount     INTEGER,
    percentage    DOUBLE   -- §8: share of listed functions, not of all employees
);

DROP TABLE IF EXISTS workforce_top_functions;
CREATE TABLE workforce_top_functions (
    pid6            TEXT,
    year            INTEGER,
    function_name   TEXT,
    function_code   TEXT,
    headcount       INTEGER,
    headcount_yoy   DOUBLE,
    average_wage    DOUBLE,
    pct_of_total    DOUBLE,
    total_payroll   DOUBLE
);

-- One year per entity, so there is no per-function change over time here.
--
-- The source nests these rows under a procurement-opportunity object that also
-- carries a vendor-addressable derivation: operating and capital less personnel
-- less amounts held to be unpurchasable. That derivation is not read. It is an
-- estimate rather than a reported line, and every figure in this product is
-- operating plus capital, which is the basis the service-area shares are drawn
-- against (§8). Taking only the reported columns keeps one basis in the tree.
DROP TABLE IF EXISTS financial_functions;
CREATE TABLE financial_functions (
    pid6                      TEXT,
    year                      INTEGER,
    function_name             TEXT,
    service_area              TEXT,
    operating_expenditures    DOUBLE,
    capital_expenditures      DOUBLE,
    -- The share the source derives, kept for the record and not read by
    -- anything. It is broken for 527 of 3,645 rows: Harney County's Health is
    -- filed at -3,871% against $2.9M of positive spending, and 442 rows exceed
    -- 100%. Whatever denominator the source used, it is not one these figures
    -- are a share of.
    pct_of_entity_total       DOUBLE,
    -- The share computed here instead: this function's operating plus capital
    -- over the entity's own operating plus capital. That is the same basis the
    -- service-area shares use, so the two levels of the drill finally stack
    -- rather than being two different fractions of two different wholes.
    share_of_total            DOUBLE
);

-- The crosswalk between the finance taxonomy and the workforce taxonomy, and
-- the only join between money and people in this data. Many to many in both
-- directions: sum every employment function a financial function maps to before
-- dividing, and every financial function an employment function receives.
DROP TABLE IF EXISTS function_bridge;
CREATE TABLE function_bridge (
    service_area        TEXT,
    financial_function  TEXT,
    employment_function TEXT
);
CREATE INDEX idx_bridge_fin ON function_bridge(financial_function);
CREATE INDEX idx_bridge_emp ON function_bridge(employment_function);

DROP TABLE IF EXISTS financial_trends;
CREATE TABLE financial_trends (
    pid6            TEXT,
    year            INTEGER,
    revenue         DOUBLE,
    expenditure     DOUBLE,
    total_debt      DOUBLE,
    revenue_yoy     DOUBLE,
    expenditure_yoy DOUBLE
);

DROP TABLE IF EXISTS revenue_sources;
CREATE TABLE revenue_sources (
    pid6         TEXT,
    year         INTEGER,
    category     TEXT,
    amount       DOUBLE,
    percentage   DOUBLE,
    is_top_three INTEGER
);

-- Office roles and seats. This file carries no holder names, so any tool
-- answering §7 questions must say so rather than implying holders are unknown.
DROP TABLE IF EXISTS offices;
CREATE TABLE offices (
    office_id         INTEGER PRIMARY KEY,
    ocd_id            TEXT,
    ocd_level         TEXT,    -- place | county | school_district | special_district | board_district | ...
    role              TEXT,
    office_position   TEXT,    -- the seat, e.g. "Position 3"; distinct from role per §7
    partisan_election TEXT,
    occupation_method TEXT,    -- Election | Appointment; §7 requires stating this
    term_length       TEXT
);

-- §7: match confidence is part of the answer, so it is stored, not inferred.
DROP TABLE IF EXISTS office_entity_match;
CREATE TABLE office_entity_match (
    ocd_id       TEXT PRIMARY KEY,
    pid6         TEXT,
    match_method TEXT,     -- alias_exact | legal_name_exact | county_pattern | token_subset | unmatched
    confidence   TEXT      -- high | medium | low | none
);

DROP TABLE IF EXISTS dp03;
CREATE TABLE dp03 (
    geo_id     TEXT,
    geo_name   TEXT,
    geo_level  TEXT,     -- county | place
    vintage    INTEGER,
    variable   TEXT,
    estimate   DOUBLE,
    moe        DOUBLE
);

DROP TABLE IF EXISTS dp03_variables;
CREATE TABLE dp03_variables (
    variable TEXT,
    vintage  INTEGER,
    label    TEXT
);

-- Which entities can be drawn on a map, and on what basis.
DROP TABLE IF EXISTS geo_entity;
-- Where a government's office is, from the city in its mailing address matched
-- to a place polygon. The only locator this data has for the 1,029 special
-- districts that carry no boundary of their own, and the reason a point layer
-- exists at all.
--
-- This is an office, never a service area. A rural fire district filed at a
-- Eugene address serves ground outside Eugene entirely, and every drawing of
-- these rows carries the rule that says so.
DROP TABLE IF EXISTS entity_point;
CREATE TABLE entity_point (
    pid6        TEXT PRIMARY KEY,
    lon         DOUBLE,
    lat         DOUBLE,
    place_geoid TEXT,   -- the place whose centroid stands in for the address
    basis       TEXT    -- address_city
);

CREATE TABLE geo_entity (
    pid6         TEXT PRIMARY KEY,
    layer        TEXT,    -- county | place | school_district
    geo_id       TEXT,
    match_method TEXT,    -- geoid_exact | name_normalized | unmatched
    confidence   TEXT
);

-- Drives Appendix A preset gating: a preset is only offered when its block is
-- present. Distinguishes absent (no block) from zero (a real reported zero).
DROP TABLE IF EXISTS data_availability;
CREATE TABLE data_availability (
    pid6                    TEXT PRIMARY KEY,
    has_fiscal_stability    INTEGER,
    has_operating_vs_capital INTEGER,
    has_revenue_volatility  INTEGER,
    has_spending_breakdown  INTEGER,
    has_workforce           INTEGER,
    has_financial_functions INTEGER,
    has_financial_trends    INTEGER,
    has_revenue_sources     INTEGER,
    has_offices             INTEGER,
    has_geometry            INTEGER,
    has_dp03                INTEGER,
    has_population          INTEGER
);

CREATE INDEX idx_sba_pid ON spending_by_service_area(pid6);
CREATE INDEX idx_sat_pid ON service_area_totals(pid6);
CREATE INDEX idx_ff_pid  ON financial_functions(pid6);
CREATE INDEX idx_ft_pid  ON financial_trends(pid6);
CREATE INDEX idx_rs_pid  ON revenue_sources(pid6);
CREATE INDEX idx_wsa_pid ON workforce_by_service_area(pid6);
CREATE INDEX idx_wtf_pid ON workforce_top_functions(pid6);
CREATE INDEX idx_rvy_pid ON revenue_volatility_yoy(pid6);
CREATE INDEX idx_off_ocd ON offices(ocd_id);
CREATE INDEX idx_dp03_geo ON dp03(geo_id, vintage);
CREATE INDEX idx_ent_type ON entities(gov_type_name);
CREATE INDEX idx_ent_host ON entities(host_county_pid6);
