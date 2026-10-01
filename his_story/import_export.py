"""Import a Swarm/Foursquare data export folder (the one with checkins1.json, visits.json, ...).

Safe to run repeatedly and with older or newer exports: nothing is ever deleted.
Deliberately NOT imported: payment profiles, devices, and personal profile fields (email, birthday).
"""
import glob
import json
import pathlib
import shutil

from . import db, merge


def _load(folder, name):
    p = folder / name
    return json.loads(p.read_text()) if p.exists() else None


def _items(data):
    return (data or {}).get("items", [])


def import_export(conn, folder, media_dir):
    folder = pathlib.Path(folder)
    run = db.start_run(conn, "export", folder.name)
    stats = {}

    # Check-ins
    counts = {"new": 0, "changed": 0, "same": 0}
    seen = []
    for f in sorted(glob.glob(str(folder / "checkins*.json"))):
        for raw in _items(json.loads(pathlib.Path(f).read_text())):
            counts[merge.upsert_checkin(conn, run, "export", raw)] += 1
            v = raw.get("venue") or {}
            merge.ensure_venue(conn, run, v.get("id"), v.get("name"))
            for e in raw.get("entities") or []:
                if e.get("type") == "user":
                    conn.execute("INSERT OR IGNORE INTO checkin_people VALUES (?, ?)", (raw["id"], e["id"]))
            seen.append(raw["id"])
    counts["marked_removed"] = merge.mark_removed(conn, run, seen)
    stats["checkins"] = counts

    # Photos (+ copy local files from pix/)
    media_dir = pathlib.Path(media_dir)
    media_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for p in _items(_load(folder, "photos1.json")):
        fname = p.get("suffix", "").rsplit("/", 1)[-1] or None
        src = folder / "pix" / fname if fname else None
        local = None
        if src and src.exists():
            shutil.copy2(src, media_dir / fname)
            local = fname
        checkin_id = (p.get("relatedItemUrl") or "").rstrip("/").rsplit("/", 1)[-1] or None
        conn.execute(
            "INSERT INTO photos (id, checkin_id, created_at, url, width, height, local_file, raw) "
            "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET local_file = COALESCE(excluded.local_file, local_file)",
            (p["id"], checkin_id, merge.parse_ts(p.get("createdAt")), p.get("fullUrl"), p.get("width"),
             p.get("height"), local, json.dumps(p, sort_keys=True)))
        n += 1
    stats["photos"] = n

    # Visits
    vs = _items(_load(folder, "visits.json"))
    conn.executemany(
        "INSERT OR IGNORE INTO visits VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ((v["id"], merge.parse_ts(v.get("timeArrived")), merge.parse_ts(v.get("timeDeparted")),
          v.get("latitude"), v.get("longitude"), v.get("city"), v.get("state"), v.get("countryCode"),
          v.get("locationType"), 1 if v.get("isTraveling") else 0, json.dumps(v, sort_keys=True)) for v in vs))
    stats["visits"] = len(vs)

    us = _items(_load(folder, "unconfirmed_visits.json"))
    conn.executemany(
        "INSERT OR IGNORE INTO unconfirmed_visits VALUES (?,?,?,?,?,?,?,?)",
        ((u["id"], merge.parse_ts(u.get("startTime")), merge.parse_ts(u.get("endTime")), u.get("venueId"),
          (u.get("venue") or {}).get("name"), u.get("lat"), u.get("lng"), json.dumps(u, sort_keys=True)) for u in us))
    stats["unconfirmed_visits"] = len(us)

    # Tips
    ts = _items(_load(folder, "tips.json"))
    for t in ts:
        v = t.get("venue") or {}
        t = {k: val for k, val in t.items() if k != "user"}  # drop embedded personal profile
        conn.execute("INSERT OR REPLACE INTO tips VALUES (?,?,?,?,?,?)",
                     (t["id"], merge.parse_ts(t.get("createdAt")), v.get("id"), v.get("name"), t.get("text"),
                      json.dumps(t, sort_keys=True)))
    stats["tips"] = len(ts)

    # Lists
    ls = _items(_load(folder, "lists.json"))
    for l in ls:
        l = {k: val for k, val in l.items() if k != "user"}
        conn.execute("INSERT OR REPLACE INTO lists VALUES (?,?,?,?)",
                     (l["id"], l.get("name"), l.get("description"), json.dumps(l, sort_keys=True)))
        for it in _items(l.get("listItems")):
            v = it.get("venue") or {}
            if v.get("id"):
                conn.execute("INSERT OR REPLACE INTO list_items VALUES (?,?,?,?)",
                             (l["id"], v["id"], v.get("name"), merge.parse_ts(it.get("createdAt"))))
    stats["lists"] = len(ls)

    # Venue ratings
    ratings = _load(folder, "venueRatings.json") or {}
    n = 0
    for key, rating in (("venueLikes", "like"), ("venueOkays", "okay"), ("venueDislikes", "dislike")):
        for v in ratings.get(key, []):
            conn.execute("INSERT OR REPLACE INTO venue_ratings VALUES (?,?,?)", (v["id"], v.get("name"), rating))
            n += 1
    stats["venue_ratings"] = n

    # People: friends / followers / following (names + ids only)
    users = _load(folder, "users.json") or {}
    n = 0
    for key, col in (("friends", "is_friend"), ("followers", "is_follower"), ("following", "is_following")):
        for u in _items(users.get(key)):
            conn.execute(
                f"INSERT INTO people (id, first_name, last_name, {col}, raw) VALUES (?,?,?,1,?) "
                f"ON CONFLICT(id) DO UPDATE SET {col} = 1",
                (u["id"], u.get("firstName"), u.get("lastName"), json.dumps(u, sort_keys=True)))
            n += 1
    stats["people"] = n

    # Comments, shares
    cs = _items(_load(folder, "comments.json"))
    conn.executemany("INSERT OR IGNORE INTO comments (created_at, user_id, text) VALUES (?,?,?)",
                     ((merge.parse_ts(c.get("time")), str(c.get("userId")), c.get("comment")) for c in cs))
    stats["comments"] = len(cs)
    ss = _items(_load(folder, "shares.json"))
    conn.executemany("INSERT OR IGNORE INTO shares VALUES (?,?,?,?)",
                     ((s["id"], s.get("sharedAt"), s.get("type"), s.get("state")) for s in ss))
    stats["shares"] = len(ss)

    db.finish_run(conn, run, stats)
    return stats
