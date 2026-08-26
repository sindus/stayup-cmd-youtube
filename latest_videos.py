#!/usr/bin/env python3
"""
Stayup — monitors YouTube channels and stores the latest videos in PostgreSQL.

For each tracked repository (YouTube channel URL), the script fetches the most
recent videos using yt-dlp. New entries are stored when videos have changed since
the last run. Videos older than config["retention_days"] are cleaned up each run.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

import psycopg2
import yt_dlp

DDL = """
CREATE TABLE IF NOT EXISTS repository (
    id          SERIAL PRIMARY KEY,
    url         TEXT NOT NULL UNIQUE,
    type        TEXT NOT NULL,
    config      JSONB NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS connector_youtube (
    id          SERIAL PRIMARY KEY,
    repository_id INTEGER NOT NULL REFERENCES repository(id),
    version     TEXT,
    content     TEXT NOT NULL,
    datetime    TIMESTAMPTZ,
    executed_at TIMESTAMPTZ NOT NULL,
    success     BOOLEAN NOT NULL
);

CREATE TABLE IF NOT EXISTS log (
    id              SERIAL PRIMARY KEY,
    repository_id   INTEGER,
    error           TEXT NOT NULL,
    executed_at     TIMESTAMPTZ NOT NULL
);

-- Registre partagé des providers : chaque collecteur y déclare son nom affiché au
-- démarrage. L'API stayup-api lit cette table pour construire une UI dynamique ;
-- elle ne connaît aucun nom de provider en dur, seulement les tables connector_*.
CREATE TABLE IF NOT EXISTS provider_registry (
    name          TEXT PRIMARY KEY,
    display_name  TEXT NOT NULL,
    sort_order    INTEGER NOT NULL DEFAULT 100,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO provider_registry (name, display_name, sort_order)
VALUES ('youtube', 'YouTube', 20)
ON CONFLICT (name) DO UPDATE SET display_name = EXCLUDED.display_name, updated_at = NOW();
"""


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


def get_db_conn() -> psycopg2.extensions.connection:
    """Return a psycopg2 connection.

    Reads DATABASE_URL first; falls back to individual DB_* environment
    variables (DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD).
    """
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        return psycopg2.connect(database_url)
    return psycopg2.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        port=int(os.environ.get("DB_PORT", 5432)),
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
    )


def init_db(conn: psycopg2.extensions.connection) -> None:
    """Create tables if they don't exist."""
    with conn.cursor() as cur:
        cur.execute(DDL)
    conn.commit()


def upsert_repository(conn: psycopg2.extensions.connection, url: str) -> int:
    """Insert a repository URL if it does not exist yet and return its id."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO repository (url, type)
            VALUES (%s, 'youtube')
            ON CONFLICT (url) DO UPDATE SET url = EXCLUDED.url
            RETURNING id
            """,
            (url,),
        )
        row = cur.fetchone()
    conn.commit()
    return row[0]


def get_repositories(conn: psycopg2.extensions.connection) -> list[tuple[int, str, dict]]:
    """Return all tracked repositories of type 'youtube' as a list of (id, url, config) tuples."""
    with conn.cursor() as cur:
        cur.execute("SELECT id, url, config FROM repository WHERE type = 'youtube' ORDER BY id")
        rows = cur.fetchall()
        return [(row[0], row[1], json.loads(row[2]) if isinstance(row[2], str) else (row[2] or {})) for row in rows]


def get_latest_entry(conn: psycopg2.extensions.connection, repository_id: int) -> tuple[str | None, str | None]:
    """Return (version, content) of the most recent successful video entry.

    Returns (None, None) if no entry exists yet.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT version, content FROM connector_youtube
            WHERE repository_id = %s AND success = TRUE
            ORDER BY executed_at DESC
            LIMIT 1
            """,
            (repository_id,),
        )
        row = cur.fetchone()
    return (row[0], row[1]) if row else (None, None)


def save_entry(
    conn: psycopg2.extensions.connection,
    repository_id: int,
    version: str | None,
    content: str,
    video_datetime: datetime | None,
    executed_at: datetime,
) -> None:
    """Persist a video entry to the database."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO connector_youtube (repository_id, version, content, datetime, executed_at, success)
            VALUES (%s, %s, %s, %s, %s, TRUE)
            """,
            (repository_id, version, content, video_datetime, executed_at),
        )
    conn.commit()


def cleanup_old_entries(conn: psycopg2.extensions.connection, repository_id: int, retention_days: int) -> None:
    """Delete video entries for a repository older than retention_days days."""
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM connector_youtube WHERE repository_id = %s AND executed_at < NOW() - %s * INTERVAL '1 day'",
            (repository_id, retention_days),
        )
    conn.commit()


def save_error(
    conn: psycopg2.extensions.connection,
    repository_id: int | None,
    error: str,
    executed_at: datetime,
) -> None:
    """Persist a retrieval error to the log table."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO log (repository_id, error, executed_at)
            VALUES (%s, %s, %s)
            """,
            (repository_id, error, executed_at),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# YouTube fetching
# ---------------------------------------------------------------------------


def fetch_video_ids(channel_url: str, count: int = 5) -> list[dict]:
    """Return the most recent video entries from a YouTube channel (up to `count`).

    Each entry is a dict with keys: video_id, url, title.
    Shorts and entries without an ID are excluded.
    """
    url = channel_url.rstrip("/")
    if not url.endswith("/videos"):
        url += "/videos"

    with yt_dlp.YoutubeDL({"quiet": True, "extract_flat": True, "playlist_items": f"1-{count}"}) as ydl:
        info = ydl.extract_info(url, download=False)

    entries = info.get("entries") or []
    result = []
    for e in entries:
        if not e.get("id"):
            continue
        raw_url = e.get("url") or ""
        if not raw_url.startswith("http"):
            raw_url = f"https://www.youtube.com/watch?v={e['id']}"
        if "/shorts/" in raw_url:
            continue
        result.append({"video_id": e["id"], "url": raw_url, "title": e.get("title") or ""})
    return result


def fetch_video_metadata(video_id: str, video_url: str | None = None) -> tuple[datetime | None, str | None]:
    """Return (upload_date, description) for a single YouTube video, or (None, None) if unavailable."""
    if video_url is None:
        video_url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "skip_download": True}) as ydl:
            full_info = ydl.extract_info(video_url, download=False)
    except Exception:
        return None, None

    upload_date = None
    raw_date = full_info.get("upload_date")
    if raw_date:
        try:
            upload_date = datetime.strptime(raw_date, "%Y%m%d").replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    description = full_info.get("description") or None
    return upload_date, description


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------


def process_repository(
    conn: psycopg2.extensions.connection, repository_id: int, repository_url: str, executed_at: datetime, config: dict
) -> None:
    """Fetch the latest videos for one repository and persist new ones.

    - If no previous entry exists, only the most recent video is stored.
    - Otherwise, iterates through the most recent videos (up to config["max_iterations"], default 5)
      and stores each new one until the last known video is found.
    - Any exception is caught, logged to the `log` table, and printed to stderr.
    """
    max_iterations = config.get("max_iterations", 5)
    try:
        videos = fetch_video_ids(repository_url, count=max_iterations)
        if not videos:
            raise RuntimeError("No video found.")

        prev_version, _ = get_latest_entry(conn, repository_id)

        if prev_version is None:
            # First run: store the most recent video using flat extraction data.
            entry = videos[0]
            upload_date, summary = fetch_video_metadata(entry["video_id"], entry["url"])
            content = json.dumps(
                {
                    "title": entry["title"],
                    "url": repository_url,
                    "link": entry["url"],
                    "publish_date": upload_date.strftime("%Y-%m-%d") if upload_date else None,
                    "thumbnail": f"https://i.ytimg.com/vi/{entry['video_id']}/hqdefault.jpg",
                    "summary": summary,
                },
                ensure_ascii=False,
            )
            save_entry(conn, repository_id, entry["video_id"], content, upload_date, executed_at)
            return

        # Subsequent runs: iterate through recent videos until we find the last known one.
        for entry in videos:
            if entry["video_id"] == prev_version:
                break
            upload_date, summary = fetch_video_metadata(entry["video_id"], entry["url"])
            content = json.dumps(
                {
                    "title": entry["title"],
                    "url": repository_url,
                    "link": entry["url"],
                    "publish_date": upload_date.strftime("%Y-%m-%d") if upload_date else None,
                    "thumbnail": f"https://i.ytimg.com/vi/{entry['video_id']}/hqdefault.jpg",
                    "summary": summary,
                },
                ensure_ascii=False,
            )
            save_entry(conn, repository_id, entry["video_id"], content, upload_date, executed_at)

    except Exception as e:
        save_error(conn, repository_id, str(e), executed_at)
        print(f"[{repository_url}] Error: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Monitor YouTube channels and store latest videos.")
    parser.add_argument("--add", metavar="URL", help="Add a repository to track and exit.")
    args = parser.parse_args()

    conn = get_db_conn()
    try:
        init_db(conn)

        if args.add:
            upsert_repository(conn, args.add)
            print(f"Repository added: {args.add}")
            return

        executed_at = datetime.now(tz=timezone.utc)
        repositories = get_repositories(conn)

        if not repositories:
            print("No repositories tracked. Use --add <url> to add one.")
            return

        for repository_id, repository_url, config in repositories:
            process_repository(conn, repository_id, repository_url, executed_at, config)
            cleanup_old_entries(conn, repository_id, config.get("retention_days", 15))

    finally:
        conn.close()


if __name__ == "__main__":
    main()
