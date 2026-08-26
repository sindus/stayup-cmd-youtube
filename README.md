# Stayup — YouTube

[![CI](https://github.com/stayup-app/stayup-cmd-youtube/actions/workflows/ci.yml/badge.svg)](https://github.com/stayup-app/stayup-cmd-youtube/actions/workflows/ci.yml)
[![Daily YouTube check](https://github.com/stayup-app/stayup-cmd-youtube/actions/workflows/daily.yml/badge.svg)](https://github.com/stayup-app/stayup-cmd-youtube/actions/workflows/daily.yml)

**Website:** https://stayup-ui.vercel.app

Monitors YouTube channels and stores the latest video in a PostgreSQL database.

For each tracked profile, the script fetches the most recent video using yt-dlp. A new entry is only stored when the video has changed since the last run.

## Requirements

- [Docker](https://www.docker.com/) and Docker Compose

## Setup

```bash
cp .env.example .env
```

Open `.env` and configure your database connection.

### Option A — Local database (Docker)

The default values in `.env` work out of the box with the bundled `db` service. No changes needed.

### Option B — External database (Render, Railway, etc.)

Set the full connection URL in `.env`:

```env
DATABASE_URL=postgresql://user:password@host:5432/dbname
```

> **Note:** Tables are created automatically on the first run.

## Usage

**Start the database:**
```bash
docker compose up db -d
```

**Track a YouTube channel:**
```bash
docker compose run --rm latest_videos --add https://www.youtube.com/@melvynxdev
docker compose run --rm latest_videos --add https://www.youtube.com/@fireship
```

**Run the script manually:**
```bash
docker compose run --rm latest_videos
```

**Browse the database (pgAdmin):**
```bash
docker compose up pgadmin -d
```
Open [http://localhost:5050](http://localhost:5050) — credentials: `admin@admin.com` / `admin`

Connect to the server using host `db`, port `5432`, and the credentials from your `.env`.

## Automation

The script runs automatically every evening at 20:00 UTC via GitHub Actions.

To enable it on your fork, add a `DATABASE_URL` secret in:
**Settings → Secrets and variables → Actions → New repository secret**

You can also trigger the workflow manually from the **Actions → Daily YouTube check → Run workflow** tab.

## Development

**Install the pre-commit hook** (runs linter + tests before every commit):
```bash
cp scripts/pre-commit .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit
```

**Run tests:**
```bash
docker compose run --rm test
```

**Check linting:**
```bash
docker compose run --rm --entrypoint="" test sh -c "ruff check . && black --check ."
```

**Auto-format code:**
```bash
docker run --rm --entrypoint="" -v $(pwd):/app -w /app stayup-cmd-youtube-test black .
```

## Database schema

| Table | Description |
|---|---|
| `profile` | Tracked YouTube channels |
| `connector_youtube` | Stored video entries (latest per channel) |

### `connector_youtube` columns

| Column | Description |
|---|---|
| `version` | YouTube video ID (e.g. `dQw4w9WgXcQ`) |
| `content` | JSON with `title`, `thumbnail`, and `url` of the video |
| `diff` | Not used — kept for schema consistency |
| `datetime` | Video publication date |
| `executed_at` | Timestamp when the script ran |
| `success` | `false` if the fetch failed |

### `content` JSON format

```json
{
  "title": "My Video Title",
  "thumbnail": "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg",
  "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
}
```

## Project structure

```
stayup-cmd-youtube/
├── latest_videos.py        # Main script
├── tests/
│   ├── test_unit.py        # Unit tests (no external dependencies)
│   └── test_functional.py  # Functional tests (require PostgreSQL)
├── .env.example            # Configuration template
├── docker-compose.yml
├── Dockerfile
└── pyproject.toml          # Ruff + Black configuration
```
