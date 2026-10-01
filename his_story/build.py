"""Build the static site: web/ files + data/checkins.json + data/visits.json + media/ from the database.

The output folder can be served by any static web server (nginx, GitHub Pages, ...).
"""
import bisect
import json
import pathlib
import shutil

WEB = pathlib.Path(__file__).resolve().parent.parent / "web"
COLUMNS = ["id", "t", "tz", "lat", "lng", "venue", "cat", "city", "cc", "country",
           "shout", "people", "photos", "private", "closed", "removed"]
VISIT_COLUMNS = ["start", "end", "tz", "lat", "lng", "label", "city", "cc"]
SAME_VISIT_SECONDS = 120     # a visit and an unconfirmed visit starting this close are one event
CHECKIN_MARGIN = 30 * 60     # a check-in at the venue this close to the visit means it's already on the map


def build_site(conn, out_dir, media_dir):
    out = pathlib.Path(out_dir)
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(WEB, out)
    (out / "data").mkdir()

    people = {}
    for r in conn.execute(
            "SELECT cp.checkin_id, TRIM(COALESCE(p.first_name,'') || ' ' || COALESCE(p.last_name,'')) AS name "
            "FROM checkin_people cp LEFT JOIN people p ON p.id = cp.person_id"):
        if r["name"]:
            people.setdefault(r["checkin_id"], []).append(r["name"])

    photos = {}
    media_out = out / "media"
    media_out.mkdir()
    for r in conn.execute("SELECT checkin_id, url, local_file FROM photos WHERE checkin_id IS NOT NULL ORDER BY created_at"):
        src = pathlib.Path(media_dir) / r["local_file"] if r["local_file"] else None
        if src and src.exists():
            shutil.copy2(src, media_out / src.name)
            photos.setdefault(r["checkin_id"], []).append(f"media/{src.name}")
        elif r["url"]:
            photos.setdefault(r["checkin_id"], []).append(r["url"])

    city_of = _nearest_city_lookup(conn)

    rows = []
    for r in conn.execute("SELECT * FROM checkins_enriched ORDER BY created_at"):
        # Map position: the venue's location keeps repeat visits on one spot; fall back to your GPS.
        lat = r["venue_lat"] if r["venue_lat"] is not None else r["lat"]
        lng = r["venue_lng"] if r["venue_lng"] is not None else r["lng"]
        if lat is None or lng is None:
            continue
        rows.append([
            r["id"], r["created_at"], r["tz_offset"] or 0, round(lat, 5), round(lng, 5),
            r["venue_name"], r["category_name"], r["city"] or city_of.get(r["venue_id"]), r["cc"], r["country"],
            r["shout"], people.get(r["id"]), photos.get(r["id"]),
            r["is_private"], r["closed"] or 0, 1 if r["removed_in_run"] else 0,
        ])

    (out / "data" / "checkins.json").write_text(
        json.dumps({"columns": COLUMNS, "rows": rows}, ensure_ascii=False, separators=(",", ":")))
    visits = build_visits(conn)
    (out / "data" / "visits.json").write_text(
        json.dumps({"columns": VISIT_COLUMNS, "rows": visits}, ensure_ascii=False, separators=(",", ":")))
    return {"checkins": len(rows), "visits": len(visits), "photos_local": len(list(media_out.iterdir()))}


def build_visits(conn):
    """Places Swarm detected you at, minus the ones you also checked in at.

    `visits` has duration + city, `unconfirmed_visits` has the guessed venue; when both
    describe the same moment they are merged into one row.
    """
    checkins = conn.execute("SELECT created_at, tz_offset, venue_id FROM checkins "
                            "WHERE removed_in_run IS NULL ORDER BY created_at").fetchall()
    times = [c["created_at"] for c in checkins]

    def tz_near(t):
        # Visits have no time zone; borrow it from the check-in closest in time.
        if not times:
            return 0
        i = bisect.bisect_left(times, t)
        best = min((j for j in (i - 1, i) if 0 <= j < len(times)), key=lambda j: abs(times[j] - t))
        return checkins[best]["tz_offset"] or 0

    def checked_in(venue_id, start, end):
        if not venue_id:
            return False
        lo = bisect.bisect_left(times, start - CHECKIN_MARGIN)
        hi = bisect.bisect_right(times, (end or start) + CHECKIN_MARGIN)
        return any(checkins[j]["venue_id"] == venue_id for j in range(lo, hi))

    # The export repeats the same moment under different ids; keep one per time + place
    # (for unconfirmed visits, preferring a record that names the venue).
    def dedupe(rows, time_col):
        best = {}
        for r in rows:
            if r["lat"] is None:
                continue
            key = (r[time_col], round(r["lat"], 3), round(r["lng"], 3))
            if key not in best or (not best[key]["venue_name"] if "venue_name" in r.keys() else False):
                best[key] = r
        return sorted(best.values(), key=lambda r: r[time_col])

    unconfirmed = dedupe(conn.execute("SELECT * FROM unconfirmed_visits").fetchall(), "start_at")
    u_starts = [u["start_at"] for u in unconfirmed]
    used = set()
    rows = []
    for v in dedupe(conn.execute("SELECT * FROM visits").fetchall(), "arrived_at"):
        match = None
        i = bisect.bisect_left(u_starts, v["arrived_at"] - SAME_VISIT_SECONDS)
        while i < len(unconfirmed) and u_starts[i] <= v["arrived_at"] + SAME_VISIT_SECONDS:
            if i not in used:
                match = i
                break
            i += 1
        venue_id = label = None
        if match is not None:
            used.add(match)
            venue_id, label = unconfirmed[match]["venue_id"], unconfirmed[match]["venue_name"]
        if checked_in(venue_id, v["arrived_at"], v["departed_at"]):
            continue
        rows.append([v["arrived_at"], v["departed_at"], tz_near(v["arrived_at"]), round(v["lat"], 5),
                     round(v["lng"], 5), label, v["city"], v["cc"]])
    emitted = {(r[0], round(r[3], 3), round(r[4], 3)) for r in rows}
    for i, u in enumerate(unconfirmed):
        if (i in used or (u["start_at"], round(u["lat"], 3), round(u["lng"], 3)) in emitted
                or checked_in(u["venue_id"], u["start_at"], u["end_at"])):
            continue
        rows.append([u["start_at"], u["end_at"], tz_near(u["start_at"]), round(u["lat"], 5),
                     round(u["lng"], 5), u["venue_name"], None, None])
    rows.sort(key=lambda r: r[0])
    return rows


def _nearest_city_lookup(conn, max_km=25):
    """Venues without a city (homes, 'City' venues) borrow the city of the nearest venue that has one.

    Display-only: the database keeps exactly what Foursquare returned.
    """
    import math
    latest = ("SELECT venue_id, city, lat, lng FROM venue_snapshots s WHERE id = "
              "(SELECT id FROM venue_snapshots WHERE venue_id = s.venue_id ORDER BY fetched_at DESC, id DESC LIMIT 1)")
    rows = [r for r in conn.execute(latest) if r["lat"] is not None]
    known = [r for r in rows if r["city"]]
    out = {}
    for r in rows:
        if r["city"]:
            continue
        best, best_km = None, max_km
        for k in known:
            dlat = math.radians(k["lat"] - r["lat"])
            dlng = math.radians(k["lng"] - r["lng"]) * math.cos(math.radians(r["lat"]))
            km = 6371 * math.hypot(dlat, dlng)
            if km < best_km:
                best, best_km = k["city"], km
        if best:
            out[r["venue_id"]] = best
    return out
