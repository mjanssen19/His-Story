"""Merging check-ins from several sources into one row.

Each source (Swarm export, Foursquare API) keeps its own raw JSON on the check-in.
The visible columns are recomputed from both, so re-importing in any order gives
the same result. Changes are tracked per source, so an export and the API that
phrase a shout differently don't produce endless history rows.
"""
import calendar
import hashlib
import json
from datetime import datetime

TRACKED = ("shout", "visibility", "venue_id", "venue_name", "is_private", "event_name")
# Where you physically were comes from the export; everything else prefers the richer API.
PREFER_EXPORT = ("lat", "lng", "hacc")
FIELDS = ("created_at", "tz_offset", "venue_id", "venue_name", "lat", "lng", "hacc",
          "shout", "visibility", "is_private", "event_name")


def parse_ts(value):
    """Export timestamps are 'YYYY-MM-DD HH:MM:SS.ffffff' in UTC; the API uses unix ints."""
    if value is None or isinstance(value, (int, float)):
        return value
    return calendar.timegm(datetime.strptime(value[:19], "%Y-%m-%d %H:%M:%S").timetuple())


def normalize_export(raw):
    venue = raw.get("venue") or {}
    return {
        "created_at": parse_ts(raw["createdAt"]),
        "tz_offset": raw.get("timeZoneOffset"),
        "venue_id": venue.get("id"),
        "venue_name": venue.get("name"),
        "lat": raw.get("lat"),
        "lng": raw.get("lng"),
        "hacc": raw.get("hacc"),
        "shout": raw.get("shout"),
        "visibility": raw.get("visibility"),
        "is_private": 1 if raw.get("private") or raw.get("visibility") == "private" else 0,
        "event_name": (raw.get("event") or {}).get("name"),
    }


def normalize_api(raw):
    venue = raw.get("venue") or {}
    loc = venue.get("location") or {}
    return {
        "created_at": raw["createdAt"],
        "tz_offset": raw.get("timeZoneOffset"),
        "venue_id": venue.get("id"),
        "venue_name": venue.get("name"),
        "lat": loc.get("lat"),
        "lng": loc.get("lng"),
        "hacc": None,
        "shout": raw.get("shout"),
        "visibility": raw.get("visibility"),
        "is_private": 1 if raw.get("private") or raw.get("visibility") == "private" else 0,
        "event_name": (raw.get("event") or {}).get("name"),
    }


NORMALIZE = {"export": normalize_export, "api": normalize_api}


def combine(export_norm, api_norm):
    out = {}
    for f in FIELDS:
        first, second = (export_norm, api_norm) if f in PREFER_EXPORT else (api_norm, export_norm)
        val = (first or {}).get(f)
        out[f] = val if val is not None else (second or {}).get(f)
    out["is_private"] = out["is_private"] or 0
    return out


def upsert_checkin(conn, run_id, source, raw):
    """Insert or update one check-in from `source` ('export' | 'api'). Returns 'new' | 'changed' | 'same'."""
    cid = raw["id"]
    raw_col = f"{source}_raw"
    raw_json = json.dumps(raw, sort_keys=True)
    row = conn.execute("SELECT export_raw, api_raw FROM checkins WHERE id = ?", (cid,)).fetchone()
    raws = {"export": None, "api": None}
    status = "new"
    if row:
        raws = {"export": row["export_raw"], "api": row["api_raw"]}
        status = "same"
        if raws[source] is not None and raws[source] != raw_json:
            status = "changed"
            old, new = NORMALIZE[source](json.loads(raws[source])), NORMALIZE[source](raw)
            for f in TRACKED:
                if old.get(f) != new.get(f):
                    conn.execute(
                        "INSERT INTO checkin_history (checkin_id, run_id, field, old_value, new_value) VALUES (?,?,?,?,?)",
                        (cid, run_id, f"{source}.{f}", _s(old.get(f)), _s(new.get(f))))
        elif raws[source] is None:
            status = "changed"
    raws[source] = raw_json
    merged = combine(*(NORMALIZE[s](json.loads(raws[s])) if raws[s] else None for s in ("export", "api")))

    if not row:
        conn.execute(
            f"INSERT INTO checkins (id, {', '.join(FIELDS)}, first_seen_run, last_seen_run, {raw_col}) "
            f"VALUES (?, {', '.join('?' * len(FIELDS))}, ?, ?, ?)",
            (cid, *(merged[f] for f in FIELDS), run_id, run_id, raw_json))
    else:
        conn.execute(
            f"UPDATE checkins SET {', '.join(f + ' = ?' for f in FIELDS)}, {raw_col} = ?, "
            f"last_seen_run = ?, removed_in_run = NULL WHERE id = ?",
            (*(merged[f] for f in FIELDS), raw_json, run_id, cid))
    return status


def mark_removed(conn, run_id, seen_ids):
    """For a FULL snapshot: check-ins it doesn't contain are marked removed (never deleted).

    Only check-ins older than the newest one in the snapshot count: an old export
    simply doesn't know about check-ins made after it was created.
    """
    if not seen_ids:
        return 0
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS seen (id TEXT PRIMARY KEY)")
    conn.execute("DELETE FROM seen")
    conn.executemany("INSERT OR IGNORE INTO seen VALUES (?)", ((i,) for i in seen_ids))
    newest = conn.execute(
        "SELECT MAX(created_at) FROM checkins WHERE id IN (SELECT id FROM seen)").fetchone()[0]
    cur = conn.execute(
        "UPDATE checkins SET removed_in_run = ? WHERE removed_in_run IS NULL "
        "AND created_at <= ? AND id NOT IN (SELECT id FROM seen)", (run_id, newest))
    return cur.rowcount


def ensure_venue(conn, run_id, venue_id, name):
    if venue_id:
        conn.execute("INSERT OR IGNORE INTO venues (id, name, first_seen_run) VALUES (?, ?, ?)",
                     (venue_id, name, run_id))


def add_venue_snapshot(conn, run_id, source, fetched_at, venue):
    """Store the venue as seen now; only adds a row if it differs from every earlier snapshot."""
    raw = json.dumps(venue, sort_keys=True)
    h = hashlib.sha1(raw.encode()).hexdigest()
    loc = venue.get("location") or {}
    cats = venue.get("categories") or []
    cat = next((c for c in cats if c.get("primary")), cats[0] if cats else {})
    cur = conn.execute(
        "INSERT OR IGNORE INTO venue_snapshots (venue_id, run_id, source, fetched_at, name, category_id, "
        "category_name, address, city, state, postal_code, cc, country, lat, lng, time_zone, closed, "
        "content_hash, raw) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (venue["id"], run_id, source, fetched_at, venue.get("name"), cat.get("id"), cat.get("name"),
         loc.get("address"), loc.get("city"), loc.get("state"), loc.get("postalCode"), loc.get("cc"),
         loc.get("country"), loc.get("lat"), loc.get("lng"), venue.get("timeZone"),
         1 if venue.get("closed") else 0, h, raw))
    return cur.rowcount


def _s(v):
    return None if v is None else str(v)
