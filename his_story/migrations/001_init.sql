-- His-Story schema v1.
-- Rule: every source record keeps its raw JSON, so fields we don't use yet are never lost.

CREATE TABLE import_runs (
  id          INTEGER PRIMARY KEY,
  source      TEXT NOT NULL,          -- 'export' | 'api_full' | 'api_incremental'
  label       TEXT,                   -- e.g. export folder name
  started_at  INTEGER NOT NULL,
  finished_at INTEGER,
  stats_json  TEXT
);

CREATE TABLE checkins (
  id              TEXT PRIMARY KEY,   -- Foursquare check-in id
  created_at      INTEGER NOT NULL,   -- unix seconds, UTC
  tz_offset       INTEGER,            -- minutes; local time = created_at + tz_offset*60
  venue_id        TEXT,
  venue_name      TEXT,               -- name as it was when checked in
  lat             REAL,               -- where you were (export); falls back to venue location
  lng             REAL,
  hacc            REAL,
  shout           TEXT,
  visibility      TEXT,
  is_private      INTEGER NOT NULL DEFAULT 0,
  event_name      TEXT,
  first_seen_run  INTEGER REFERENCES import_runs(id),
  last_seen_run   INTEGER REFERENCES import_runs(id),
  removed_in_run  INTEGER REFERENCES import_runs(id),  -- set when missing from a later full snapshot
  export_raw      TEXT,
  api_raw         TEXT
);
CREATE INDEX checkins_created ON checkins(created_at);
CREATE INDEX checkins_venue ON checkins(venue_id);

-- Old values of a check-in whenever a source reports something different.
CREATE TABLE checkin_history (
  id          INTEGER PRIMARY KEY,
  checkin_id  TEXT NOT NULL REFERENCES checkins(id),
  run_id      INTEGER NOT NULL REFERENCES import_runs(id),
  field       TEXT NOT NULL,
  old_value   TEXT,
  new_value   TEXT
);

CREATE TABLE venues (
  id              TEXT PRIMARY KEY,
  name            TEXT,
  first_seen_run  INTEGER REFERENCES import_runs(id)
);

-- Append-only: a new row only when the venue data actually changed. Venues change over time.
CREATE TABLE venue_snapshots (
  id             INTEGER PRIMARY KEY,
  venue_id       TEXT NOT NULL,
  run_id         INTEGER NOT NULL REFERENCES import_runs(id),
  source         TEXT NOT NULL,        -- 'v2_user_checkins' | 'places_api' | ...
  fetched_at     INTEGER NOT NULL,
  name           TEXT,
  category_id    TEXT,
  category_name  TEXT,
  address        TEXT,
  city           TEXT,
  state          TEXT,
  postal_code    TEXT,
  cc             TEXT,
  country        TEXT,
  lat            REAL,
  lng            REAL,
  time_zone      TEXT,
  closed         INTEGER,
  content_hash   TEXT NOT NULL,
  raw            TEXT NOT NULL,
  UNIQUE (venue_id, content_hash)
);
CREATE INDEX venue_snapshots_venue ON venue_snapshots(venue_id, fetched_at);

CREATE TABLE photos (
  id          TEXT PRIMARY KEY,
  checkin_id  TEXT,
  created_at  INTEGER,
  url         TEXT,
  width       INTEGER,
  height      INTEGER,
  local_file  TEXT,                    -- filename in <data>/media/, if we have the file
  raw         TEXT NOT NULL
);
CREATE INDEX photos_checkin ON photos(checkin_id);

CREATE TABLE people (
  id            TEXT PRIMARY KEY,
  first_name    TEXT,
  last_name     TEXT,
  is_friend     INTEGER NOT NULL DEFAULT 0,
  is_follower   INTEGER NOT NULL DEFAULT 0,
  is_following  INTEGER NOT NULL DEFAULT 0,
  raw           TEXT
);

-- Who was tagged / with you on a check-in.
CREATE TABLE checkin_people (
  checkin_id  TEXT NOT NULL REFERENCES checkins(id),
  person_id   TEXT NOT NULL,
  PRIMARY KEY (checkin_id, person_id)
);

CREATE TABLE visits (
  id             TEXT PRIMARY KEY,
  arrived_at     INTEGER,
  departed_at    INTEGER,
  lat            REAL,
  lng            REAL,
  city           TEXT,
  state          TEXT,
  cc             TEXT,
  location_type  TEXT,
  is_traveling   INTEGER,
  raw            TEXT NOT NULL
);
CREATE INDEX visits_arrived ON visits(arrived_at);

CREATE TABLE unconfirmed_visits (
  id          TEXT PRIMARY KEY,
  start_at    INTEGER,
  end_at      INTEGER,
  venue_id    TEXT,
  venue_name  TEXT,
  lat         REAL,
  lng         REAL,
  raw         TEXT NOT NULL
);
CREATE INDEX unconfirmed_visits_start ON unconfirmed_visits(start_at);

CREATE TABLE tips (
  id          TEXT PRIMARY KEY,
  created_at  INTEGER,
  venue_id    TEXT,
  venue_name  TEXT,
  text        TEXT,
  raw         TEXT NOT NULL
);

CREATE TABLE lists (
  id           TEXT PRIMARY KEY,
  name         TEXT,
  description  TEXT,
  raw          TEXT NOT NULL
);

CREATE TABLE list_items (
  list_id     TEXT NOT NULL REFERENCES lists(id),
  venue_id    TEXT NOT NULL,
  venue_name  TEXT,
  added_at    INTEGER,
  PRIMARY KEY (list_id, venue_id)
);

CREATE TABLE venue_ratings (
  venue_id    TEXT PRIMARY KEY,
  venue_name  TEXT,
  rating      TEXT NOT NULL            -- 'like' | 'okay' | 'dislike'
);

CREATE TABLE comments (
  id          INTEGER PRIMARY KEY,
  created_at  INTEGER,
  user_id     TEXT,
  text        TEXT,
  UNIQUE (created_at, user_id, text)
);

CREATE TABLE shares (
  id         TEXT PRIMARY KEY,
  shared_at  INTEGER,
  type       TEXT,
  state      TEXT
);

CREATE TABLE api_calls (
  id        INTEGER PRIMARY KEY,
  at        INTEGER NOT NULL,
  endpoint  TEXT NOT NULL,
  status    INTEGER,
  run_id    INTEGER REFERENCES import_runs(id)
);

-- Latest snapshot per venue + check-in = what the site shows.
CREATE VIEW checkins_enriched AS
SELECT c.*,
       s.category_name, s.address, s.city, s.state, s.cc, s.country, s.closed,
       s.lat AS venue_lat, s.lng AS venue_lng
FROM checkins c
LEFT JOIN venue_snapshots s ON s.id = (
  SELECT id FROM venue_snapshots WHERE venue_id = c.venue_id ORDER BY fetched_at DESC, id DESC LIMIT 1
);
