"""
One-time migration: add census_tract_demographics table.

Run after load_data.py has populated census_tracts:
    python migrate_add_census_demographics.py
"""

import os

import psycopg2
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())

conn = psycopg2.connect(
    host=os.getenv("DB_HOST", "localhost"),
    port=int(os.getenv("DB_PORT", 5432)),
    dbname=os.getenv("DB_NAME", "synagogues"),
    user=os.getenv("DB_USER", "research"),
    password=os.getenv("DB_PASSWORD"),
)
conn.autocommit = True
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS census_tract_demographics (
    geoid                    CHAR(11) PRIMARY KEY REFERENCES census_tracts(geoid),

    -- ACS 5-Year income
    median_household_income  INTEGER,          -- B19013_001E; NULL = no data / -666666666

    -- ACS 5-Year language spoken at home (C16001), raw counts
    lang_total               INTEGER,          -- C16001_001E  (population 5+)
    lang_english_only        INTEGER,          -- C16001_002E
    lang_spanish             INTEGER,          -- C16001_003E + C16001_004E
    lang_indo_european       INTEGER,          -- C16001_005E..012E (French, German, Slavic, other IE)
    lang_asian_pacific       INTEGER,          -- C16001_013E..022E (Korean, Chinese, Vietnamese, Tagalog, other AP)
    lang_arabic              INTEGER,          -- C16001_023E + C16001_024E
    lang_other               INTEGER,          -- C16001_025E + C16001_026E

    -- USDA ERS Rural-Urban Commuting Area codes (2020)
    -- 1=Urban core, 2=Urban commuter, 3=Small town commuter,
    -- 4-6=Large/medium/small rural, 7-10=Isolated rural
    ruca_code                SMALLINT,
    ruca_secondary_code      NUMERIC(4,1),

    acs_year                 SMALLINT NOT NULL DEFAULT 2023
)
""")
print("Table census_tract_demographics created (or already exists).")

cur.execute("CREATE INDEX IF NOT EXISTS ctd_ruca_idx ON census_tract_demographics(ruca_code)")
print("Index on ruca_code ready.")

cur.execute("SELECT COUNT(*) FROM census_tract_demographics")
print(f"Rows currently in table: {cur.fetchone()[0]:,}")

cur.close()
conn.close()
print("Done.")
