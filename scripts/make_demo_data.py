"""Generate a made-up check-in history so anyone can try His-Story without their own data.

Writes demo/data/export/ (Swarm export format) and demo/data/api_pages/ (Foursquare API format).
Every name and place pairing here is fictional; coordinates are real city centres.

    python3 scripts/make_demo_data.py
    python3 -m his_story --data demo/data import-export demo/data/export
    python3 -m his_story --data demo/data import-api-pages demo/data/api_pages --full
    python3 -m his_story --data demo/data build
"""
import calendar
import json
import pathlib
import random
from datetime import datetime, timedelta

random.seed(42)
OUT = pathlib.Path(__file__).resolve().parent.parent / "demo" / "data"

CITIES = {  # name: (lat, lng, cc, country, tz offset minutes)
    "Amsterdam": (52.3676, 4.9041, "NL", "Netherlands", 60),
    "Lisbon": (38.7223, -9.1393, "PT", "Portugal", 0),
    "New York": (40.7128, -74.0060, "US", "United States", -300),
    "Tokyo": (35.6762, 139.6503, "JP", "Japan", 540),
    "Tbilisi": (41.7151, 44.8271, "GE", "Georgia", 240),
}
CATEGORIES = ["Café", "Bar", "Restaurant", "Museum", "Park", "Bookstore", "Train Station", "Gym", "Bakery"]
WORDS = ["Blue", "Corner", "Golden", "Little", "Old", "Harbour", "Garden", "North", "Copper", "Lantern"]
FRIENDS = [("900001", "Alex", "Example"), ("900002", "Sam", "Sample"), ("900003", "Robin", "Demo")]
NOTES = ["Great coffee", "Finally here!", "Rainy afternoon", "Best view in town", None, None, None]


def make_venues():
    venues = []
    for city, (lat, lng, cc, country, tz) in CITIES.items():
        for i in range(12):
            cat = random.choice(CATEGORIES)
            venues.append({
                "id": f"demo{len(venues):020d}",
                "name": f"{random.choice(WORDS)} {cat}",
                "city": city, "cc": cc, "country": country, "tz": tz, "cat": cat,
                "lat": lat + random.uniform(-0.04, 0.04), "lng": lng + random.uniform(-0.05, 0.05),
            })
    return venues


def main():
    venues = make_venues()
    home = [v for v in venues if v["city"] == "Amsterdam"]
    checkins_export, checkins_api = [], []
    day = datetime(2016, 1, 1, 9)
    while day < datetime(2025, 12, 31):
        trip = random.random() < 0.04
        pool = home
        length = 1
        if trip:
            city = random.choice([c for c in CITIES if c != "Amsterdam"])
            pool = [v for v in venues if v["city"] == city]
            length = random.randint(4, 10)
        for d in range(length):
            for _ in range(random.randint(1, 4 if trip else 2)):
                v = random.choice(pool)
                t = day + timedelta(days=d, hours=random.randint(0, 12), minutes=random.randint(0, 59))
                unix = calendar.timegm(t.timetuple()) - v["tz"] * 60
                cid = f"c{len(checkins_export):023d}"
                note = random.choice(NOTES)
                friend = random.choice(FRIENDS) if random.random() < 0.15 else None
                shout = f"{note or ''} with {friend[1]}".strip() if friend else note
                ex = {"id": cid, "createdAt": datetime.utcfromtimestamp(unix).strftime("%Y-%m-%d %H:%M:%S.000000"),
                      "type": "checkin", "timeZoneOffset": v["tz"], "venue": {"id": v["id"], "name": v["name"]},
                      "lat": v["lat"] + random.uniform(-0.0003, 0.0003), "lng": v["lng"] + random.uniform(-0.0003, 0.0003),
                      "hacc": random.randint(3, 30)}
                api = {"id": cid, "createdAt": unix, "type": "checkin", "timeZoneOffset": v["tz"],
                       "venue": {"id": v["id"], "name": v["name"],
                                 "categories": [{"id": v["cat"].lower(), "name": v["cat"], "primary": True}],
                                 "location": {"lat": v["lat"], "lng": v["lng"], "city": v["city"],
                                              "cc": v["cc"], "country": v["country"]}},
                       "photos": {"count": 0, "items": []}}
                if shout:
                    ex["shout"] = api["shout"] = shout
                if friend:
                    api["with"] = [{"id": friend[0], "firstName": friend[1], "lastName": friend[2]}]
                checkins_export.append(ex)
                checkins_api.append(api)
        day += timedelta(days=length + random.randint(2, 9))

    (OUT / "export").mkdir(parents=True, exist_ok=True)
    (OUT / "api_pages").mkdir(parents=True, exist_ok=True)
    (OUT / "export" / "checkins1.json").write_text(json.dumps({"count": len(checkins_export), "items": checkins_export}))
    (OUT / "api_pages" / "page_00000.json").write_text(json.dumps(
        {"response": {"checkins": {"count": len(checkins_api), "items": checkins_api}}}))
    print(f"Wrote {len(checkins_export)} demo check-ins to {OUT}")


if __name__ == "__main__":
    main()
