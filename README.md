# Stayup — YouTube

[![CI](https://github.com/stayup-app/stayup-cmd-youtube/actions/workflows/ci.yml/badge.svg)](https://github.com/stayup-app/stayup-cmd-youtube/actions/workflows/ci.yml)
[![Daily YouTube check](https://github.com/stayup-app/stayup-cmd-youtube/actions/workflows/daily.yml/badge.svg)](https://github.com/stayup-app/stayup-cmd-youtube/actions/workflows/daily.yml)

**Website:** https://stayup-ui.vercel.app

Monitors YouTube channels and stores the latest videos via [stayup-api](https://github.com/stayup-app/stayup-api) — this script never touches a database directly, it only calls `stayup-api`'s `/connector-api/youtube/*` endpoints.

For each tracked channel, the script fetches the most recent videos using yt-dlp. A new entry is only stored when the video has changed since the last run.

## Requirements

- Python 3.13, or [Docker](https://www.docker.com/)
- A `stayup-api` instance (the public one, or your own — see [self-hosting-and-providers.md](https://github.com/stayup-app/stayup-api/blob/main/docs/self-hosting-and-providers.md))
- An API key for the `youtube` provider, created from that instance's admin panel (Connector keys → New key, provider `youtube`). The key is shown once — copy it right away.

## Setup

```bash
git clone https://github.com/stayup-app/stayup-cmd-youtube.git
cd stayup-cmd-youtube
cp .env.example .env
```

Open `.env` and set `STAYUP_API_URL` (your `stayup-api` instance) and `STAYUP_API_KEY` (the key you created for `youtube`).

> **Note:** the provider registers itself automatically on every run — nothing to create by hand beyond the key.

## Usage

**Track a YouTube channel:**
```bash
docker compose run --rm latest_videos --add https://www.youtube.com/@melvynxdev
docker compose run --rm latest_videos --add https://www.youtube.com/@fireship
```

**Run the script manually:**
```bash
docker compose run --rm latest_videos
```

Without Docker:
```bash
pip install -r requirements.txt
STAYUP_API_URL=... STAYUP_API_KEY=... python latest_videos.py
```

## Automation

The script runs automatically every evening at 20:00 UTC via GitHub Actions.

To enable it on your fork, add `STAYUP_API_URL` and `STAYUP_API_KEY` secrets in:
**Settings → Secrets and variables → Actions → New repository secret**

You can also trigger the workflow manually from the **Actions → Daily YouTube check → Run workflow** tab.

## Development

**Install the pre-commit hook** (runs linter + tests before every commit):
```bash
cp scripts/pre-commit .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit
```

**Run tests** (no external dependencies — `stayup-api` and yt-dlp calls are mocked):
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

## What gets stored

Each stored entry is a JSON `content` blob, keyed by the YouTube video id (`version`):

```json
{
  "title": "My Video Title",
  "thumbnail": "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg",
  "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
}
```

See `stayup-api`'s `connector-api` docs for the full contract.

## Project structure

```
stayup-cmd-youtube/
├── latest_videos.py    # Main script
├── tests/
│   └── test_unit.py    # Tests — stayup-api and yt-dlp calls are mocked
├── .env.example        # Configuration template
├── docker-compose.yml
├── Dockerfile
└── pyproject.toml      # Ruff + Black configuration
```
