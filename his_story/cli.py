"""his-story command line.

All personal data lives in one data folder (default ./data, or $HIS_STORY_DATA):
  his-story.sqlite   the database (source of truth)
  .env               Foursquare app credentials + access token
  api_pages/         raw API responses, kept forever
  media/             photo files
  site/              the generated static website
"""
import argparse
import json
import os
import pathlib

from . import build, db, fsq_api, import_export


def main(argv=None):
    ap = argparse.ArgumentParser(prog="his-story", description="Your Swarm check-in history as a searchable map.")
    ap.add_argument("--data", default=os.environ.get("HIS_STORY_DATA", "data"), help="data folder (default: ./data)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("import-export", help="import a Swarm data export folder")
    p.add_argument("folder")
    p = sub.add_parser("import-api-pages", help="import previously downloaded API pages")
    p.add_argument("folder")
    p.add_argument("--full", action="store_true", help="the folder holds your complete history (enables removal detection)")
    sub.add_parser("login", help="one-time Foursquare login (needs FSQ_CLIENT_ID/SECRET in <data>/.env)")
    p = sub.add_parser("sync", help="fetch new check-ins from the Foursquare API")
    p.add_argument("--full", action="store_true", help="re-download everything to detect edits and deletions")
    p = sub.add_parser("build", help="generate the static website")
    p.add_argument("--out", help="output folder (default: <data>/site)")
    sub.add_parser("stats", help="show what's in the database")
    args = ap.parse_args(argv)

    data = pathlib.Path(args.data)
    data.mkdir(parents=True, exist_ok=True)
    env = data / ".env"
    if args.cmd == "login":
        return fsq_api.login(env)

    conn = db.connect(data / "his-story.sqlite")
    with conn:
        if args.cmd == "import-export":
            result = import_export.import_export(conn, args.folder, data / "media")
        elif args.cmd == "import-api-pages":
            result = fsq_api.import_pages(conn, args.folder, full=args.full)
        elif args.cmd == "sync":
            result = fsq_api.fetch_checkins(conn, env, data / "api_pages", full=args.full)
        elif args.cmd == "build":
            result = build.build_site(conn, args.out or data / "site", data / "media")
        elif args.cmd == "stats":
            result = stats(conn)
    print(json.dumps(result, indent=2, ensure_ascii=False))


def stats(conn):
    one = lambda sql: conn.execute(sql).fetchone()[0]
    return {
        "checkins": one("SELECT COUNT(*) FROM checkins"),
        "checkins_removed": one("SELECT COUNT(*) FROM checkins WHERE removed_in_run IS NOT NULL"),
        "first": one("SELECT date(MIN(created_at), 'unixepoch') FROM checkins"),
        "last": one("SELECT date(MAX(created_at), 'unixepoch') FROM checkins"),
        "venues": one("SELECT COUNT(*) FROM venues"),
        "venues_with_snapshot": one("SELECT COUNT(DISTINCT venue_id) FROM venue_snapshots"),
        "countries": one("SELECT COUNT(DISTINCT cc) FROM checkins_enriched WHERE cc IS NOT NULL"),
        "checkins_without_city": one("SELECT COUNT(*) FROM checkins_enriched WHERE city IS NULL"),
        "checkins_with_shout": one("SELECT COUNT(*) FROM checkins WHERE shout IS NOT NULL AND shout != ''"),
        "photos": one("SELECT COUNT(*) FROM photos"),
        "people": one("SELECT COUNT(*) FROM people"),
        "visits": one("SELECT COUNT(*) FROM visits"),
        "unconfirmed_visits": one("SELECT COUNT(*) FROM unconfirmed_visits"),
        "history_rows": one("SELECT COUNT(*) FROM checkin_history"),
        "import_runs": one("SELECT COUNT(*) FROM import_runs"),
        "api_calls": one("SELECT COUNT(*) FROM api_calls"),
    }


if __name__ == "__main__":
    main()
