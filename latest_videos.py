#!/usr/bin/env python3
"""
Stayup — monitors YouTube channels and stores the latest videos via stayup-api.

For each tracked repository (YouTube channel URL), the script fetches the most
recent videos using yt-dlp. New entries are stored when videos have changed since
the last run.

Talks to stayup-api over HTTP (STAYUP_API_URL + STAYUP_API_KEY) — it never
touches a database directly. See stayup-api/docs/self-hosting-and-providers.md.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

import requests
import yt_dlp

PROVIDER_TYPE = "youtube"

# Display name of the provider in the apps (fallback: capitalized table name).
DISPLAY_NAME = "YouTube"

# Where this connector ranks among the others in the sidebar.
SORT_ORDER = 20

# The stayup-api instance to talk to, and the key that authenticates this
# connector for the 'youtube' provider — obtained from that instance's admin
# (see stayup-api/docs/self-hosting-and-providers.md).
API_URL = os.environ.get("STAYUP_API_URL", "http://localhost:3000").rstrip("/")
API_KEY = os.environ.get("STAYUP_API_KEY")

DEFAULT_MAX_ITERATIONS = 5

# Display manifest: how the 3 apps (ui / desktop / mobile) render this
# connector's rows, without a line of code on the app side. stayup-api relays it
# as-is from provider_registry.template, without ever interpreting it.
# Schema: see stayup-api/docs/self-hosting-and-providers.md.
#
# One entry = a video. `content` is JSON {title, thumbnail, url}, `url` being
# the channel URL; `version` is the video id, hence the embed URL rebuilt in
# `detail.embedUrl`.
DISPLAY_TEMPLATE = {
    "version": 1,
    "display": {
        "name": DISPLAY_NAME,
        # Self-describing icon (tintable SVG path). Screen + play button.
        "icon": {
            "paths": [
                "M4 5h16a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2z",
                "m10 9 5 3-5 3z",
            ],
            "viewBox": "0 0 24 24",
            "stroke": True,
        },
        "accent": "#e8a8b5",
        "sortOrder": SORT_ORDER,
        "feedLabel": {"path": "$source.url", "format": "urlSlug"},
    },
    "item": {
        "parseContentAsJson": True,
        "fields": {
            "title": "title",
            "subtitle": {"path": "url", "format": "urlSlug"},
            "image": "thumbnail",
            "url": ["link", "url"],
            "timestamp": "$row.datetime",
        },
    },
    "list": {
        "layout": "media",
        "primary": "title",
        "secondary": "subtitle",
        "meta": "timestamp",
        "thumbnail": "image",
    },
    "detail": {
        "mode": "media",
        "title": "title",
        "subtitle": {"path": "url", "format": "urlSlug"},
        "image": "thumbnail",
        "embedUrl": "https://www.youtube-nocookie.com/embed/{$row.version}",
        "openUrl": ["link", "url"],
        "openLabel": "Watch on YouTube",
    },
    "form": {
        "label": "YouTube channel (@handle or URL)",
        "placeholder": "@fireship",
        "urlTemplate": "https://www.youtube.com/@{value}",
        "transform": {
            "trim": True,
            "extract": r"youtube\.com/(?:@|channel/|user/)([^/?\s]+)",
            "stripPrefix": ["@"],
        },
    },
}


# ---------------------------------------------------------------------------
# stayup-api client
# ---------------------------------------------------------------------------


def api_request(method: str, path: str, **kwargs) -> dict | None:
    """Call one of stayup-api's /connector-api/youtube/* endpoints.

    Raises RuntimeError if STAYUP_API_KEY isn't set, or requests.HTTPError on
    a non-2xx response (via raise_for_status).
    """
    if not API_KEY:
        raise RuntimeError("STAYUP_API_KEY is not set.")
    url = f"{API_URL}/connector-api/{PROVIDER_TYPE}{path}"
    headers = {"Authorization": f"Bearer {API_KEY}"}
    response = requests.request(method, url, headers=headers, timeout=30, **kwargs)
    response.raise_for_status()
    return response.json() if response.content else None


def register_provider() -> None:
    """Self-declaration at startup — display name and display manifest."""
    api_request(
        "POST",
        "/register",
        json={
            "displayName": DISPLAY_NAME,
            "sortOrder": SORT_ORDER,
            "template": DISPLAY_TEMPLATE,
        },
    )


def add_source(url: str) -> int:
    """Track a new channel URL and return its id."""
    result = api_request("POST", "/sources", json={"url": url})
    return result["id"]


def get_sources() -> list[tuple[int, str, dict]]:
    """Return all tracked sources as (id, url, config) tuples."""
    result = api_request("GET", "/sources")
    return [(s["id"], s["url"], s.get("config") or {}) for s in result["sources"]]


def get_latest_version(repository_id: int) -> str | None:
    """Return the video id of the most recently stored entry, or None on first run."""
    result = api_request("GET", f"/sources/{repository_id}/state")
    return result["version"]


def save_entry(
    repository_id: int, version: str, content: str, video_datetime: datetime | None, executed_at: datetime
) -> None:
    """Persist a single video entry."""
    api_request(
        "POST",
        "/items",
        json={
            "items": [
                {
                    "repositoryId": repository_id,
                    "version": version,
                    "content": content,
                    "datetime": video_datetime.isoformat() if video_datetime else None,
                    "executedAt": executed_at.isoformat(),
                    "success": True,
                }
            ]
        },
    )


def save_error(repository_id: int | None, error: str, executed_at: datetime) -> None:
    """Persist a retrieval error."""
    api_request(
        "POST",
        "/errors",
        json={"repositoryId": repository_id, "error": error, "executedAt": executed_at.isoformat()},
    )


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


def _build_content(entry: dict, repository_url: str, upload_date: datetime | None, summary: str | None) -> str:
    return json.dumps(
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


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------


def process_repository(repository_id: int, repository_url: str, executed_at: datetime, config: dict) -> None:
    """Fetch the latest videos for one repository and persist new ones.

    - If no previous entry exists, only the most recent video is stored.
    - Otherwise, iterates through the most recent videos (up to config["max_iterations"], default 5)
      and stores each new one until the last known video is found.
    - Any exception is caught, logged via the API, and printed to stderr.
    """
    max_iterations = config.get("max_iterations", DEFAULT_MAX_ITERATIONS)
    try:
        videos = fetch_video_ids(repository_url, count=max_iterations)
        if not videos:
            raise RuntimeError("No video found.")

        prev_version = get_latest_version(repository_id)

        if prev_version is None:
            # First run: store the most recent video using flat extraction data.
            entry = videos[0]
            upload_date, summary = fetch_video_metadata(entry["video_id"], entry["url"])
            content = _build_content(entry, repository_url, upload_date, summary)
            save_entry(repository_id, entry["video_id"], content, upload_date, executed_at)
            return

        # Subsequent runs: iterate through recent videos until we find the last known one.
        for entry in videos:
            if entry["video_id"] == prev_version:
                break
            upload_date, summary = fetch_video_metadata(entry["video_id"], entry["url"])
            content = _build_content(entry, repository_url, upload_date, summary)
            save_entry(repository_id, entry["video_id"], content, upload_date, executed_at)

    except Exception as e:
        save_error(repository_id, str(e), executed_at)
        print(f"[{repository_url}] Error: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Monitor YouTube channels and store latest videos.")
    parser.add_argument("--add", metavar="URL", help="Add a repository to track and exit.")
    args = parser.parse_args()

    register_provider()

    if args.add:
        add_source(args.add)
        print(f"Repository added: {args.add}")
        return

    executed_at = datetime.now(tz=timezone.utc)
    sources = get_sources()

    if not sources:
        print("No repositories tracked. Use --add <url> to add one.")
        return

    for repository_id, repository_url, config in sources:
        process_repository(repository_id, repository_url, executed_at, config)


if __name__ == "__main__":
    main()
