"""
Functional tests — require a running PostgreSQL instance.

Connection is configured via environment variables:
  DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
"""

import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import psycopg2
import pytest

from latest_videos import (
    DISPLAY_TEMPLATE,
    cleanup_old_entries,
    get_repositories,
    init_db,
    process_repository,
    save_entry,
    save_error,
    upsert_repository,
)


class TestInitDb:
    def test_registers_name_and_display_template(self, db_conn):
        init_db(db_conn)
        with db_conn.cursor() as cur:
            cur.execute("SELECT display_name, sort_order, template FROM provider_registry WHERE name = 'youtube'")
            display_name, sort_order, template = cur.fetchone()
        assert (display_name, sort_order) == ("YouTube", 20)
        assert template == DISPLAY_TEMPLATE  # psycopg2 decodes JSONB

    def test_is_idempotent(self, db_conn):
        init_db(db_conn)
        init_db(db_conn)
        with db_conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM provider_registry WHERE name = 'youtube'")
            assert cur.fetchone()[0] == 1


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_conn():
    try:
        return psycopg2.connect(
            host=os.environ.get("DB_HOST", "localhost"),
            port=int(os.environ.get("DB_PORT", 5432)),
            dbname=os.environ.get("DB_NAME", "stayup"),
            user=os.environ.get("DB_USER", "stayup"),
            password=os.environ.get("DB_PASSWORD", "stayup"),
        )
    except psycopg2.OperationalError as e:
        pytest.skip(f"PostgreSQL unavailable: {e}")


@pytest.fixture(scope="session", autouse=True)
def setup_db():
    """Create tables once for the whole test session."""
    conn = make_conn()
    init_db(conn)
    conn.close()


@pytest.fixture
def db_conn():
    """Fresh connection per test to guarantee isolation."""
    conn = make_conn()
    yield conn
    conn.rollback()
    with conn.cursor() as cur:
        cur.execute("TRUNCATE connector_youtube, log, repository RESTART IDENTITY CASCADE")
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# repository
# ---------------------------------------------------------------------------


class TestUpsertRepositoryFunctional:
    def test_creates_new_profile(self, db_conn):
        repository_id = upsert_repository(db_conn, "https://www.youtube.com/@melvynxdev")
        assert isinstance(repository_id, int)
        with db_conn.cursor() as cur:
            cur.execute("SELECT url FROM repository WHERE id = %s", (repository_id,))
            row = cur.fetchone()
        assert row[0] == "https://www.youtube.com/@melvynxdev"

    def test_returns_same_id_on_duplicate(self, db_conn):
        id1 = upsert_repository(db_conn, "https://www.youtube.com/@melvynxdev")
        id2 = upsert_repository(db_conn, "https://www.youtube.com/@melvynxdev")
        assert id1 == id2

    def test_different_urls_get_different_ids(self, db_conn):
        id1 = upsert_repository(db_conn, "https://www.youtube.com/@channel1")
        id2 = upsert_repository(db_conn, "https://www.youtube.com/@channel2")
        assert id1 != id2

    def test_type_is_youtube(self, db_conn):
        repository_id = upsert_repository(db_conn, "https://www.youtube.com/@melvynxdev")
        with db_conn.cursor() as cur:
            cur.execute("SELECT type FROM repository WHERE id = %s", (repository_id,))
            row = cur.fetchone()
        assert row[0] == "youtube"


class TestGetRepositoriesFunctional:
    def test_returns_only_youtube_repositories(self, db_conn):
        id1 = upsert_repository(db_conn, "https://www.youtube.com/@channel1")
        id2 = upsert_repository(db_conn, "https://www.youtube.com/@channel2")
        # Insert a non-youtube repository directly
        with db_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO repository (url, type) VALUES (%s, %s) RETURNING id",
                ("https://example.com/feed", "rss"),
            )
        db_conn.commit()

        repositories = get_repositories(db_conn)
        repository_ids = [p[0] for p in repositories]
        assert id1 in repository_ids
        assert id2 in repository_ids
        assert all(p[0] in (id1, id2) for p in repositories)


# ---------------------------------------------------------------------------
# connector_youtube
# ---------------------------------------------------------------------------


class TestSaveEntryFunctional:
    def test_row_is_persisted_with_video_id(self, db_conn):
        repository_id = upsert_repository(db_conn, "https://www.youtube.com/@melvynxdev")
        executed_at = datetime.now(tz=timezone.utc)
        content = json.dumps(
            {
                "title": "My Video",
                "thumbnail": "https://i.ytimg.com/vi/abc/hqdefault.jpg",
                "url": "https://www.youtube.com/watch?v=abc",
            }
        )
        save_entry(db_conn, repository_id, "abc123", content, None, executed_at)

        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT version, content, success FROM connector_youtube WHERE repository_id = %s",
                (repository_id,),
            )
            row = cur.fetchone()
        assert row[0] == "abc123"
        assert json.loads(row[1])["title"] == "My Video"
        assert row[2] is True

    def test_row_is_persisted_without_version(self, db_conn):
        repository_id = upsert_repository(db_conn, "https://www.youtube.com/@melvynxdev")
        save_entry(db_conn, repository_id, None, '{"title": "test"}', None, datetime.now(tz=timezone.utc))

        with db_conn.cursor() as cur:
            cur.execute("SELECT version, content FROM connector_youtube WHERE repository_id = %s", (repository_id,))
            row = cur.fetchone()
        assert row[0] is None
        assert "test" in row[1]

    def test_datetime_stored(self, db_conn):
        repository_id = upsert_repository(db_conn, "https://www.youtube.com/@melvynxdev")
        video_date = datetime(2024, 6, 15, 0, 0, 0, tzinfo=timezone.utc)
        save_entry(db_conn, repository_id, "abc", "{}", video_date, datetime.now(tz=timezone.utc))

        with db_conn.cursor() as cur:
            cur.execute("SELECT datetime FROM connector_youtube WHERE repository_id = %s", (repository_id,))
            row = cur.fetchone()
        assert row[0].replace(tzinfo=timezone.utc) == video_date


# ---------------------------------------------------------------------------
# cleanup
# ---------------------------------------------------------------------------


class TestCleanupOldEntriesFunctional:
    def test_deletes_videos_older_than_retention_days(self, db_conn):
        repository_id = upsert_repository(db_conn, "https://www.youtube.com/@melvynxdev")
        old_date = datetime.now(tz=timezone.utc) - timedelta(days=20)

        with db_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO connector_youtube (repository_id, version, content, executed_at, success)"
                " VALUES (%s, %s, %s, %s, TRUE)",
                (repository_id, "old_vid", "{}", old_date),
            )
        db_conn.commit()

        save_entry(db_conn, repository_id, "recent_vid", "{}", None, datetime.now(tz=timezone.utc))

        cleanup_old_entries(db_conn, repository_id, 15)

        with db_conn.cursor() as cur:
            cur.execute("SELECT version FROM connector_youtube WHERE repository_id = %s", (repository_id,))
            versions = [r[0] for r in cur.fetchall()]
        assert "old_vid" not in versions
        assert "recent_vid" in versions

    def test_does_not_delete_recent_videos(self, db_conn):
        repository_id = upsert_repository(db_conn, "https://www.youtube.com/@melvynxdev")
        save_entry(db_conn, repository_id, "vid_001", '{"title": "v1"}', None, datetime.now(tz=timezone.utc))

        cleanup_old_entries(db_conn, repository_id, 15)

        with db_conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM connector_youtube WHERE repository_id = %s", (repository_id,))
            count = cur.fetchone()[0]
        assert count == 1


# ---------------------------------------------------------------------------
# log
# ---------------------------------------------------------------------------


class TestSaveErrorFunctional:
    def test_error_is_persisted(self, db_conn):
        repository_id = upsert_repository(db_conn, "https://www.youtube.com/@melvynxdev")
        executed_at = datetime.now(tz=timezone.utc)
        save_error(db_conn, repository_id, "No video found.", executed_at)

        with db_conn.cursor() as cur:
            cur.execute("SELECT error, repository_id FROM log WHERE repository_id = %s", (repository_id,))
            row = cur.fetchone()
        assert row[0] == "No video found."
        assert row[1] == repository_id

    def test_error_without_profile(self, db_conn):
        save_error(db_conn, None, "yt-dlp network error", datetime.now(tz=timezone.utc))

        with db_conn.cursor() as cur:
            cur.execute("SELECT error FROM log WHERE repository_id IS NULL")
            row = cur.fetchone()
        assert row[0] == "yt-dlp network error"


# ---------------------------------------------------------------------------
# End-to-end
# ---------------------------------------------------------------------------


def make_entry(vid_id, title=""):
    return {"video_id": vid_id, "url": f"https://www.youtube.com/watch?v={vid_id}", "title": title}


class TestEndToEnd:
    @patch("latest_videos.fetch_video_metadata")
    @patch("latest_videos.fetch_video_ids")
    def test_process_profile_first_run(self, mock_ids, mock_meta, db_conn):
        """First run — stores the most recent video with success=True."""
        mock_ids.return_value = [make_entry("dQw4w9WgXcQ", "Never Gonna Give You Up")]
        mock_meta.return_value = (datetime(2009, 2, 25, tzinfo=timezone.utc), None)
        repository_id = upsert_repository(db_conn, "https://www.youtube.com/@rick")
        process_repository(db_conn, repository_id, "https://www.youtube.com/@rick", datetime.now(tz=timezone.utc), {})

        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT version, content, success FROM connector_youtube WHERE repository_id = %s",
                (repository_id,),
            )
            row = cur.fetchone()
        assert row[0] == "dQw4w9WgXcQ"
        assert json.loads(row[1])["title"] == "Never Gonna Give You Up"
        assert row[2] is True

    @patch("latest_videos.fetch_video_metadata")
    @patch("latest_videos.fetch_video_ids")
    def test_process_profile_no_insert_when_same_video(self, mock_ids, mock_meta, db_conn):
        """Same video_id — no new entry is inserted."""
        mock_ids.return_value = [make_entry("vid001", "My Video")]
        mock_meta.return_value = (None, None)
        channel_url = "https://www.youtube.com/@channel"
        repository_id = upsert_repository(db_conn, channel_url)
        executed_at = datetime.now(tz=timezone.utc)
        process_repository(db_conn, repository_id, channel_url, executed_at, {})
        process_repository(db_conn, repository_id, channel_url, datetime.now(tz=timezone.utc), {})

        with db_conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM connector_youtube WHERE repository_id = %s", (repository_id,))
            count = cur.fetchone()[0]
        assert count == 1

    @patch("latest_videos.fetch_video_metadata")
    @patch("latest_videos.fetch_video_ids")
    def test_process_profile_saves_new_video(self, mock_ids, mock_meta, db_conn):
        """New video_id — a new entry is inserted."""
        channel_url = "https://www.youtube.com/@channel"
        repository_id = upsert_repository(db_conn, channel_url)

        mock_ids.return_value = [make_entry("vid001", "First Video")]
        mock_meta.return_value = (None, None)
        process_repository(db_conn, repository_id, channel_url, datetime.now(tz=timezone.utc), {})

        mock_ids.return_value = [make_entry("vid002", "Second Video"), make_entry("vid001", "First Video")]
        process_repository(db_conn, repository_id, channel_url, datetime.now(tz=timezone.utc), {})

        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT version FROM connector_youtube WHERE repository_id = %s ORDER BY executed_at DESC LIMIT 1",
                (repository_id,),
            )
            row = cur.fetchone()
        assert row[0] == "vid002"

    @patch("latest_videos.fetch_video_ids")
    def test_process_profile_logs_error_on_failure(self, mock_ids, db_conn):
        """yt-dlp error — logged to the log table."""
        channel_url = "https://www.youtube.com/@channel"
        mock_ids.side_effect = Exception("yt-dlp network error")
        repository_id = upsert_repository(db_conn, channel_url)
        process_repository(db_conn, repository_id, channel_url, datetime.now(tz=timezone.utc), {})

        with db_conn.cursor() as cur:
            cur.execute("SELECT error FROM log WHERE repository_id = %s", (repository_id,))
            row = cur.fetchone()
        assert "yt-dlp network error" in row[0]

    @patch("latest_videos.fetch_video_ids")
    def test_process_profile_logs_error_when_no_video(self, mock_ids, db_conn):
        """No video returned — logged to the log table."""
        mock_ids.return_value = []
        repository_id = upsert_repository(db_conn, "https://www.youtube.com/@empty")
        process_repository(db_conn, repository_id, "https://www.youtube.com/@empty", datetime.now(tz=timezone.utc), {})

        with db_conn.cursor() as cur:
            cur.execute("SELECT error FROM log WHERE repository_id = %s", (repository_id,))
            row = cur.fetchone()
        assert row is not None

    @patch("latest_videos.fetch_video_metadata")
    @patch("latest_videos.fetch_video_ids")
    def test_process_profile_iterates_new_videos(self, mock_ids, mock_meta, db_conn):
        """Multiple new videos — each is saved until the known video is found."""
        channel_url = "https://www.youtube.com/@channel"
        repository_id = upsert_repository(db_conn, channel_url)

        mock_ids.return_value = [make_entry("vid001")]
        mock_meta.return_value = (None, None)
        process_repository(db_conn, repository_id, channel_url, datetime.now(tz=timezone.utc), {})

        # 3 new videos appeared since last run
        mock_ids.return_value = [
            make_entry("vid004"),
            make_entry("vid003"),
            make_entry("vid002"),
            make_entry("vid001"),
        ]
        process_repository(db_conn, repository_id, channel_url, datetime.now(tz=timezone.utc), {})

        with db_conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM connector_youtube WHERE repository_id = %s", (repository_id,))
            count = cur.fetchone()[0]
        assert count == 4  # vid001 (initial) + vid002, vid003, vid004 (new)
