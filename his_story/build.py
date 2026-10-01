"""Build the static site: web/ files + data/checkins.json + media/ from the database.

The output folder can be served by any static web server (nginx, GitHub Pages, ...).
"""
import json
import pathlib
import shutil

WEB = pathlib.Path(__file__).resolve().parent.parent / "web"
COLUMNS = ["id", "t", "tz", "lat", "lng", "venue", "cat", "city", "cc", "country",
           "shout", "people", "photos", "private", "closed", "removed"]


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
    return {"checkins": len(rows), "photos_local": len(list(media_out.iterdir()))}


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
