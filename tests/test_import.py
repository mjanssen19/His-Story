"""Tests for the import rules that protect years of history.

Each test names the promise it guards: re-imports never lose or duplicate data,
deleted check-ins are kept, and old exports don't wipe newer check-ins.
"""
import json

import pytest

from his_story import build, db, fsq_api, import_export


def export_checkin(cid, ts, shout=None, venue=("v1", "Cafe")):
    c = {"id": cid, "createdAt": ts, "type": "checkin", "timeZoneOffset": 120,
         "venue": {"id": venue[0], "name": venue[1]}, "lat": 52.37, "lng": 4.89, "hacc": 5}
    if shout:
        c["shout"] = shout
    return c


def api_checkin(cid, unix, shout=None, city="Amsterdam", venue_name="Cafe"):
    c = {"id": cid, "createdAt": unix, "type": "checkin", "timeZoneOffset": 120,
         "venue": {"id": "v1", "name": venue_name, "categories": [{"id": "c", "name": "Café", "primary": True}],
                   "location": {"lat": 52.371, "lng": 4.891, "city": city, "cc": "NL", "country": "Netherlands"}},
         "photos": {"count": 0, "items": []}}
    if shout:
        c["shout"] = shout
    return c


def write_export(folder, checkins):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "checkins1.json").write_text(json.dumps({"count": len(checkins), "items": checkins}))
    return folder


def write_pages(folder, checkins):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "page_00000.json").write_text(json.dumps(
        {"response": {"checkins": {"count": len(checkins), "items": checkins}}}))
    return folder


@pytest.fixture
def conn(tmp_path):
    return db.connect(tmp_path / "test.sqlite")


def count(conn, sql="SELECT COUNT(*) FROM checkins"):
    return conn.execute(sql).fetchone()[0]


def test_reimporting_same_export_changes_nothing(conn, tmp_path):
    # Running the importer twice (or by accident) must never duplicate or alter history.
    ex = write_export(tmp_path / "ex", [export_checkin("a", "2020-01-01 10:00:00.000000"),
                                        export_checkin("b", "2020-01-02 10:00:00.000000")])
    import_export.import_export(conn, ex, tmp_path / "media")
    second = import_export.import_export(conn, ex, tmp_path / "media")
    assert second["checkins"] == {"new": 0, "changed": 0, "same": 2, "marked_removed": 0}
    assert count(conn) == 2
    assert count(conn, "SELECT COUNT(*) FROM checkin_history") == 0


def test_checkin_deleted_in_swarm_is_kept_and_marked(conn, tmp_path):
    # Deleting something in Swarm must not erase it from your own archive.
    old = write_export(tmp_path / "old", [export_checkin("a", "2020-01-01 10:00:00.000000"),
                                          export_checkin("b", "2020-01-02 10:00:00.000000")])
    new = write_export(tmp_path / "new", [export_checkin("b", "2020-01-02 10:00:00.000000")])
    import_export.import_export(conn, old, tmp_path / "media")
    import_export.import_export(conn, new, tmp_path / "media")
    row = conn.execute("SELECT removed_in_run FROM checkins WHERE id = 'a'").fetchone()
    assert row is not None and row["removed_in_run"] is not None
    assert count(conn) == 2


def test_older_export_does_not_mark_newer_checkins_removed(conn, tmp_path):
    # An export made before a check-in existed knows nothing about it; that is not a deletion.
    newer = write_pages(tmp_path / "api", [api_checkin("late", 1600000000)])  # 2020-09-13
    fsq_api.import_pages(conn, newer, full=True)
    old_export = write_export(tmp_path / "old", [export_checkin("early", "2020-01-01 10:00:00.000000")])
    import_export.import_export(conn, old_export, tmp_path / "media")
    assert conn.execute("SELECT removed_in_run FROM checkins WHERE id = 'late'").fetchone()[0] is None


def test_reappearing_checkin_is_unmarked(conn, tmp_path):
    a, b = export_checkin("a", "2020-01-01 10:00:00.000000"), export_checkin("b", "2020-01-02 10:00:00.000000")
    import_export.import_export(conn, write_export(tmp_path / "1", [a, b]), tmp_path / "m")
    import_export.import_export(conn, write_export(tmp_path / "2", [b]), tmp_path / "m")
    import_export.import_export(conn, write_export(tmp_path / "3", [a, b]), tmp_path / "m")
    assert conn.execute("SELECT removed_in_run FROM checkins WHERE id = 'a'").fetchone()[0] is None


def test_edited_shout_keeps_old_version_in_history(conn, tmp_path):
    # Editing a note in Swarm should not silently overwrite what you wrote back then.
    import_export.import_export(conn, write_export(tmp_path / "1", [export_checkin("a", "2020-01-01 10:00:00.000000", "first")]), tmp_path / "m")
    import_export.import_export(conn, write_export(tmp_path / "2", [export_checkin("a", "2020-01-01 10:00:00.000000", "edited")]), tmp_path / "m")
    h = conn.execute("SELECT field, old_value, new_value FROM checkin_history").fetchall()
    assert [tuple(r) for r in h] == [("export.shout", "first", "edited")]
    assert conn.execute("SELECT shout FROM checkins").fetchone()[0] == "edited"


def test_export_and_api_wording_differences_do_not_create_history(conn, tmp_path):
    # The API often has a shout the export lacks; alternating imports must not flip-flop.
    ex = write_export(tmp_path / "ex", [export_checkin("a", "2020-01-01 10:00:00.000000")])
    api = write_pages(tmp_path / "api", [api_checkin("a", 1577872800, shout="with Anna")])
    for _ in range(2):
        import_export.import_export(conn, ex, tmp_path / "m")
        fsq_api.import_pages(conn, api, full=True)
    assert count(conn, "SELECT COUNT(*) FROM checkin_history") == 0
    assert conn.execute("SELECT shout FROM checkins").fetchone()[0] == "with Anna"


def test_export_gps_position_wins_over_venue_location(conn, tmp_path):
    # Where you actually stood (export) is more truthful than the venue pin (API).
    import_export.import_export(conn, write_export(tmp_path / "ex", [export_checkin("a", "2020-01-01 10:00:00.000000")]), tmp_path / "m")
    fsq_api.import_pages(conn, write_pages(tmp_path / "api", [api_checkin("a", 1577872800)]), full=True)
    assert conn.execute("SELECT lat FROM checkins").fetchone()[0] == 52.37


def test_venue_change_adds_snapshot_but_keeps_old_one(conn, tmp_path):
    # Venues get renamed and move; the past version must stay available.
    fsq_api.import_pages(conn, write_pages(tmp_path / "1", [api_checkin("a", 1577872800, venue_name="Cafe")]), full=True)
    fsq_api.import_pages(conn, write_pages(tmp_path / "2", [api_checkin("a", 1577872800, venue_name="Cafe")]), full=True)
    assert count(conn, "SELECT COUNT(*) FROM venue_snapshots") == 1
    fsq_api.import_pages(conn, write_pages(tmp_path / "3", [api_checkin("a", 1577872800, venue_name="Cafe Nieuw")]), full=True)
    names = [r[0] for r in conn.execute("SELECT name FROM venue_snapshots ORDER BY id")]
    assert names == ["Cafe", "Cafe Nieuw"]


def test_build_hides_nothing_but_flags_removed(conn, tmp_path):
    a, b = export_checkin("a", "2020-01-01 10:00:00.000000"), export_checkin("b", "2020-01-02 10:00:00.000000")
    import_export.import_export(conn, write_export(tmp_path / "1", [a, b]), tmp_path / "m")
    import_export.import_export(conn, write_export(tmp_path / "2", [b]), tmp_path / "m")
    build.build_site(conn, tmp_path / "site", tmp_path / "m")
    data = json.loads((tmp_path / "site" / "data" / "checkins.json").read_text())
    removed = {r[0]: r[data["columns"].index("removed")] for r in data["rows"]}
    assert removed == {"a": 1, "b": 0}
