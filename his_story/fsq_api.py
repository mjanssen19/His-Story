"""Foursquare v2 API with your own OAuth token: login, download check-ins, import them.

Every API response is saved to disk unchanged before it is imported, so the database
can always be rebuilt even if the API disappears.
"""
import datetime
import json
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

from . import db, merge

API = "https://api.foursquare.com/v2"
API_VERSION = "20261001"
REDIRECT = "http://localhost:8765/callback"
PAGE = 250


# --- .env handling -----------------------------------------------------------

def read_env(path):
    path = pathlib.Path(path)
    if not path.exists():
        return {}
    return dict(l.split("=", 1) for l in path.read_text().splitlines() if "=" in l and not l.startswith("#"))


def write_env_value(path, key, value):
    path = pathlib.Path(path)
    lines = [l for l in (path.read_text().splitlines() if path.exists() else []) if not l.startswith(key + "=")]
    lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n")
    path.chmod(0o600)


# --- OAuth login ---------------------------------------------------------------

def login(env_path):
    env = read_env(env_path)
    cid, secret = env["FSQ_CLIENT_ID"], env["FSQ_CLIENT_SECRET"]
    result = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            result["code"] = q.get("code", [None])[0]
            result["error"] = q.get("error", [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Done, you can close this tab.")

        def log_message(self, *a):
            pass

    webbrowser.open("https://foursquare.com/oauth2/authenticate?" + urllib.parse.urlencode(
        {"client_id": cid, "response_type": "code", "redirect_uri": REDIRECT}))
    print("Log in to Foursquare in the browser window that just opened...")
    HTTPServer(("localhost", 8765), Handler).handle_request()
    if not result.get("code"):
        raise SystemExit(f"No code received (error: {result.get('error')})")
    with urllib.request.urlopen("https://foursquare.com/oauth2/access_token?" + urllib.parse.urlencode({
            "client_id": cid, "client_secret": secret, "grant_type": "authorization_code",
            "redirect_uri": REDIRECT, "code": result["code"]})) as r:
        token = json.load(r).get("access_token")
    if not token:
        raise SystemExit("Token exchange failed: no access_token in response")
    write_env_value(env_path, "FSQ_ACCESS_TOKEN", token)
    print("Access token saved.")


# --- Download ----------------------------------------------------------------

def _get(conn, run_id, token, endpoint, params):
    q = urllib.parse.urlencode({"oauth_token": token, "v": API_VERSION, **params})
    status = None
    try:
        with urllib.request.urlopen(f"{API}{endpoint}?{q}", timeout=60) as r:
            status = r.status
            return json.load(r)
    except urllib.error.HTTPError as e:
        status = e.code
        raise SystemExit(f"Foursquare API error {e.code} on {endpoint}: {e.read()[:300].decode(errors='replace')}")
    finally:
        conn.execute("INSERT INTO api_calls (at, endpoint, status, run_id) VALUES (?,?,?,?)",
                     (int(time.time()), endpoint, status, run_id))
        conn.commit()


def fetch_checkins(conn, env_path, pages_root, full):
    """Download check-ins to pages_root/<timestamp>_<kind>/ and import them.

    full=True: everything (≈1 call per 250 check-ins); detects edits and deletions.
    full=False: only check-ins since the newest one we have (minus 2 days, to catch late edits).
    """
    token = read_env(env_path).get("FSQ_ACCESS_TOKEN")
    if not token:
        raise SystemExit("No FSQ_ACCESS_TOKEN; run `his-story login` first.")
    kind = "full" if full else "incremental"
    run = db.start_run(conn, f"api_{kind}")
    out = pathlib.Path(pages_root) / f"{datetime.datetime.now():%Y-%m-%d_%H%M%S}_{kind}"
    out.mkdir(parents=True)

    params = {"limit": PAGE}
    if not full:
        newest = conn.execute("SELECT MAX(created_at) FROM checkins").fetchone()[0] or 0
        params["afterTimestamp"] = max(0, newest - 2 * 86400)
    offset, total = 0, None
    while total is None or offset < total:
        data = _get(conn, run, token, "/users/self/checkins", {**params, "offset": offset})
        c = data["response"]["checkins"]
        total = c["count"]
        (out / f"page_{offset:05d}.json").write_text(json.dumps(data))
        if not c["items"]:
            break
        offset += PAGE
        time.sleep(0.5)
    db.finish_run(conn, run, {"downloaded_to": str(out)})
    return import_pages(conn, out, full=full, run_id=run)


# --- Import saved pages -------------------------------------------------------

def import_pages(conn, folder, full, run_id=None):
    folder = pathlib.Path(folder)
    run = run_id or db.start_run(conn, "api_full" if full else "api_incremental", folder.name)
    fetched_at = int(folder.stat().st_mtime)
    counts = {"new": 0, "changed": 0, "same": 0, "venue_snapshots_added": 0, "photos": 0}
    seen = []
    for f in sorted(folder.glob("page_*.json")):
        for raw in json.loads(f.read_text())["response"]["checkins"]["items"]:
            counts[merge.upsert_checkin(conn, run, "api", raw)] += 1
            seen.append(raw["id"])
            venue = raw.get("venue")
            if venue and venue.get("id"):
                merge.ensure_venue(conn, run, venue["id"], venue.get("name"))
                counts["venue_snapshots_added"] += merge.add_venue_snapshot(conn, run, "v2_user_checkins",
                                                                             fetched_at, venue)
            for p in raw.get("with") or []:
                conn.execute("INSERT OR IGNORE INTO people (id, first_name, last_name) VALUES (?,?,?)",
                             (p["id"], p.get("firstName"), p.get("lastName")))
                conn.execute("INSERT OR IGNORE INTO checkin_people VALUES (?, ?)", (raw["id"], p["id"]))
            for e in raw.get("entities") or []:
                if e.get("type") == "user":
                    conn.execute("INSERT OR IGNORE INTO checkin_people VALUES (?, ?)", (raw["id"], e["id"]))
            for p in (raw.get("photos") or {}).get("items", []):
                url = f"{p.get('prefix')}original{p.get('suffix')}"
                conn.execute(
                    "INSERT INTO photos (id, checkin_id, created_at, url, width, height, raw) VALUES (?,?,?,?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET url = excluded.url, raw = excluded.raw",
                    (p["id"], raw["id"], p.get("createdAt"), url, p.get("width"), p.get("height"),
                     json.dumps(p, sort_keys=True)))
                counts["photos"] += 1
    if full:
        counts["marked_removed"] = merge.mark_removed(conn, run, seen)
    db.finish_run(conn, run, counts)
    return counts
