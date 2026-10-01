# His-Story

Turn your Foursquare **Swarm** check-in history into a private, searchable map and globe.

- Search by venue, city, country, people or your own notes
- Select a date range, or drag across the timeline, to see where you were in that period
- Switch between a 3D globe and a flat map, and draw your route
- Everything is stored in one **SQLite** database that you own; the website is static files

Your data never leaves your machine unless you publish the generated site yourself.

## How it works

```
Swarm data export ─┐                      ┌─► his-story.sqlite  (source of truth, never deletes)
                   ├─► his-story import ──┤
Foursquare API ────┘   (your own login)   └─► his-story build ─► static site (HTML + JSON)
```

- **Imports are safe to repeat.** Check-ins are matched by id. Check-ins you deleted in Swarm stay in your archive, marked as removed. Edited notes keep their old version in `checkin_history`.
- **Raw data is kept.** Every record stores its original JSON, and every API response is saved to disk before it is imported, so nothing is lost if the Foursquare API changes or disappears.
- **Venues are versioned.** Places close, move and get renamed; `venue_snapshots` is append-only.

## Try it with demo data

Needs Python 3.10+. No other dependencies.

```bash
python3 scripts/make_demo_data.py
python3 -m his_story --data demo/data import-export demo/data/export
python3 -m his_story --data demo/data import-api-pages demo/data/api_pages --full
python3 -m his_story --data demo/data build
python3 -m http.server 8000 --directory demo/data/site
```

Open http://localhost:8000.

## Use it with your own data

1. **Request your data** in Swarm or at foursquare.com (Settings → Privacy → Download your data). Unzip it.
2. **Import the export:**
   ```bash
   python3 -m his_story --data ~/his-story-data import-export ~/Downloads/data-export-XXXX
   ```
3. **Optional but recommended: enrich via the Foursquare API.** The export has no city, country or category; the API adds those, plus notes and people that the export often lacks.
   - Create a project at [foursquare.com/developers](https://foursquare.com/developers), and in its OAuth settings add the redirect URI `http://localhost:8765/callback`.
   - Put the Client ID and Secret in `<data>/.env`:
     ```
     FSQ_CLIENT_ID=...
     FSQ_CLIENT_SECRET=...
     ```
   - Log in once, then download your full history (about one API call per 250 check-ins):
     ```bash
     python3 -m his_story --data ~/his-story-data login
     python3 -m his_story --data ~/his-story-data sync --full
     ```
4. **Build the site:** `python3 -m his_story --data ~/his-story-data build` → `<data>/site/`.

### Keeping it up to date

```bash
python3 -m his_story --data ~/his-story-data sync          # new check-ins only (usually 1 call)
python3 -m his_story --data ~/his-story-data sync --full   # occasionally: detect edits and deletions
python3 -m his_story --data ~/his-story-data build
```

Background location visits (`visits.json`) are only in the data export, so request a new export now and then and import it the same way. Re-importing is always safe.

## Hosting

The site is plain static files: any web server works. **It contains your location history, so put it behind authentication** (e.g. Cloudflare Access, HTTP basic auth). Never put the `.sqlite` file or `.env` in the web root. See [`deploy/`](deploy/) for an nginx example and a publish script.

## Data folder layout

```
<data>/
  his-story.sqlite   database
  .env               Foursquare credentials + token (chmod 600)
  api_pages/         raw API responses, one folder per download
  media/             photos from the export
  site/              generated website
```

## Development

```bash
uv run --with pytest pytest
```

## License

MIT
