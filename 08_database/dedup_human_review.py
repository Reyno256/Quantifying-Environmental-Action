"""
De-duplicate the 17 records the denomination review flagged as "duplicate"
(but not "remove" -- those were handled by apply_human_review.py).

Investigating each flagged domain in the DB (owners of the domain, plus
coordinates of look-alike records) showed the 17 are really THREE different
data problems, so they get three different fixes. Nothing here is guessed at
runtime: every id was determined by manual inspection and is listed explicitly.

CASE A -- redundant duplicate WEBSITE ROWS on a single synagogue
    The same site is stored twice (typically an OSM/URJ row plus a later
    Google-Places row, both has_error, 0 pages). Delete the redundant row,
    keep the canonical one. The synagogue is untouched.

CASE B -- a website attached to the WRONG, genuinely-distinct congregation
    Two real synagogues hundreds of km apart share one domain because the
    site was mis-attached to the wrong one. Delete only the bad website row;
    BOTH synagogues stay.
      - templesinai-sarasota.org: belongs to Temple Sinai of Sarasota
        (sid 4530); wrongly also on a Temple Sinai in Hollywood FL (sid 1729,
        ~275 km away) -> drop the Hollywood row.
      - agudasachim.org: belongs to Agudas Achim of Columbus/Bexley
        (sid 867); wrongly also on Agudas Achim of Canton (sid 1045,
        ~163 km away) -> drop the Canton row.

CASE C -- genuine duplicate synagogue RECORDS (same location)
    Delete the redundant synagogue (cascades to its websites/pages/etc.).
      - bethtefillah.org: "Ohev Shalom Men's Mikvah" (sid 4672) is the mikvah
        facility 0.01 km from "Beth Tefillah Chabad of Georgia" (sid 368) ->
        delete 4672.
      - temple-sinai.com: "Rabbi Joseph Meszler" (sid 6412) is the clergy
        person-record at Temple Sinai, Sharon MA (sid 1264) -> delete 6412.
      - mjcnj.com: Marlboro Jewish Center / Congregation Ohev Shalom in
        Marlboro Township NJ has three rows. Keep sid 5249; delete sid 5252
        (identical NJ coords) and sid 1616 (an OSM row of the same name
        mis-geocoded to Brooklyn NY).

NOT duplicates (left untouched): rtfh.org is correctly owned by Reform Temple
of Forest Hills; thewesttemple.org's "Beth Israel - West" has no twin in OH.
The review row naming these "duplicate" reflected a mismatch in the sample,
not a DB duplicate.

Usage:
    python 08_database/dedup_human_review.py          # dry run (default)
    python 08_database/dedup_human_review.py --apply    # execute
"""

import argparse
import csv
import os
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")
BACKUP_CSV = ROOT / "08_database" / "dedup_deleted_backup.csv"

# CASE A + B: website ROWS to delete (synagogue kept). (website_id, why)
DELETE_WEBSITE_ROWS = [
    (3713, "cstsr.org: redundant Google-Places dup of wid 1491"),
    (3736, "tifereth-israel.org: redundant Google-Places dup of wid 1577"),
    (4768, "knessetisrael.org: redundant Google-Places dup of wid 1615"),
    (4315, "avodah.org: redundant Google-Places dup of wid 1803"),
    (4231, "stantonstshul.com: redundant Google-Places dup of wid 1703"),
    (4900, "shirtikvahpdx.org: redundant Google-Places dup of wid 1551"),
    (3913, "or-ami.org: redundant Google-Places dup (about-us) of wid 1622"),
    (3921, "yiml.org: redundant Google-Places dup of wid 1889"),
    (4428, "tbiport.org: erroring Google-Places dup of working tbiport.com (wid 1405)"),
    (1434, "templesinai-sarasota.org: wrongly attached to Temple Sinai Hollywood FL (sid 1729); real owner is sid 4530"),
    (959, "agudasachim.org: wrongly attached to Agudas Achim Canton OH (sid 1045); real owner is sid 867"),
]

# CASE C: whole synagogue RECORDS to delete (cascades). (synagogue_id, why)
DELETE_SYNAGOGUES = [
    (4672, "bethtefillah.org: Ohev Shalom Men's Mikvah, facility of Beth Tefillah Chabad (sid 368)"),
    (6412, "temple-sinai.com: Rabbi Joseph Meszler, clergy person-record at Temple Sinai Sharon MA (sid 1264)"),
    (5252, "mjcnj.com: Congregation Ohev Shalom, identical NJ coords to Marlboro Jewish Center (sid 5249)"),
    (1616, "mjcnj.com: Marlboro Jewish Center OSM row mis-geocoded to Brooklyn NY; dup of sid 5249"),
]


def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 5432)),
        dbname=os.getenv("DB_NAME", "synagogues"),
        user=os.getenv("DB_USER", "research"),
        password=os.getenv("DB_PASSWORD"),
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="execute (default: dry run)")
    args = ap.parse_args()

    conn = get_conn()
    cur = conn.cursor()

    wids = [w for w, _ in DELETE_WEBSITE_ROWS]
    sids = [s for s, _ in DELETE_SYNAGOGUES]

    # ---- preview + backup ----
    cur.execute(
        "SELECT w.id, w.synagogue_id, s.name, w.url, w.has_error, "
        "(SELECT count(*) FROM web_pages WHERE website_id=w.id) "
        "FROM websites w JOIN synagogues s ON w.synagogue_id=s.id "
        "WHERE w.id = ANY(%s) ORDER BY w.id", (wids,))
    web_rows = cur.fetchall()
    cur.execute(
        "SELECT id, name, state, city, source, osm_id, place_id "
        "FROM synagogues WHERE id = ANY(%s) ORDER BY id", (sids,))
    syn_rows = cur.fetchall()

    print(f"CASE A/B -- delete {len(web_rows)} redundant/mis-attached website rows (synagogues kept):")
    why_w = dict(DELETE_WEBSITE_ROWS)
    for wid, sid, name, url, herr, npg in web_rows:
        print(f"  wid={wid:5d} sid={sid:5d} pages={npg} err={herr} '{name}' {url}\n        -> {why_w[wid]}")
    print(f"\nCASE C -- delete {len(syn_rows)} duplicate synagogue records (cascades):")
    why_s = dict(DELETE_SYNAGOGUES)
    for sid, name, state, city, src, osm, pid in syn_rows:
        print(f"  sid={sid:5d} '{name}' {state}/{city} src={src} osm={osm}\n        -> {why_s[sid]}")

    found_w = {r[0] for r in web_rows}
    found_s = {r[0] for r in syn_rows}
    missing = (set(wids) - found_w, set(sids) - found_s)
    if missing[0] or missing[1]:
        print(f"\nWARNING: already-absent ids -> websites {missing[0]}  synagogues {missing[1]}")

    BACKUP_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(BACKUP_CSV, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["kind", "id", "synagogue_id", "name", "detail", "reason"])
        for wid, sid, name, url, herr, npg in web_rows:
            w.writerow(["website_row", wid, sid, name, url, why_w[wid]])
        for sid, name, state, city, src, osm, pid in syn_rows:
            w.writerow(["synagogue", sid, sid, name, f"{state}/{city} {src} osm={osm}", why_s[sid]])
    print(f"\nBackup written to {BACKUP_CSV}")

    if not args.apply:
        print("\nDRY RUN -- no changes made. Re-run with --apply to execute.")
        conn.close()
        return

    cur.execute("DELETE FROM websites WHERE id = ANY(%s)", (wids,))
    print(f"\nDeleted {cur.rowcount} website rows.")
    cur.execute("DELETE FROM synagogues WHERE id = ANY(%s)", (sids,))
    print(f"Deleted {cur.rowcount} synagogue records (cascaded).")
    conn.commit()

    cur.execute("SELECT count(*) FROM synagogues")
    print(f"\nsynagogues now: {cur.fetchone()[0]}")
    cur.close()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
