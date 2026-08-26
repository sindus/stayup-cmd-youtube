"""Unit tests — no external dependencies (DB, network)."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from latest_videos import (
    cleanup_old_entries,
    fetch_video_ids,
    fetch_video_metadata,
    get_latest_entry,
    init_db,
    save_entry,
    save_error,
    upsert_repository,
)

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def make_conn_mock():
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    return conn, cursor


class TestInitDb:
    def test_executes_ddl_and_commits(self):
        conn, cursor = make_conn_mock()
        init_db(conn)
        assert cursor.execute.call_count == 1
        conn.commit.assert_called_once()

    def test_ddl_registers_the_provider(self):
        conn, cursor = make_conn_mock()
        init_db(conn)
        sql = cursor.execute.call_args[0][0]
        assert "CREATE TABLE IF NOT EXISTS provider_registry" in sql
        assert "INSERT INTO provider_registry" in sql
        assert "'youtube', 'YouTube'" in sql


class TestUpsertRepository:
    def test_returns_id(self):
        conn, cursor = make_conn_mock()
        cursor.fetchone.return_value = (7,)
        result = upsert_repository(conn, "https://www.youtube.com/@melvynxdev")
        assert result == 7
        sql = cursor.execute.call_args[0][0]
        assert "INSERT INTO repository" in sql
        assert "ON CONFLICT" in sql

    def test_passes_url_as_parameter(self):
        conn, cursor = make_conn_mock()
        cursor.fetchone.return_value = (1,)
        upsert_repository(conn, "https://www.youtube.com/@melvynxdev")
        params = cursor.execute.call_args[0][1]
        assert params == ("https://www.youtube.com/@melvynxdev",)

    def test_inserts_type_youtube(self):
        conn, cursor = make_conn_mock()
        cursor.fetchone.return_value = (1,)
        upsert_repository(conn, "https://www.youtube.com/@melvynxdev")
        sql = cursor.execute.call_args[0][0]
        assert "youtube" in sql


class TestGetLatestEntry:
    def test_returns_none_when_no_row(self):
        conn, cursor = make_conn_mock()
        cursor.fetchone.return_value = None
        version, content = get_latest_entry(conn, 1)
        assert version is None
        assert content is None

    def test_returns_tuple_when_row_exists(self):
        conn, cursor = make_conn_mock()
        cursor.fetchone.return_value = ("dQw4w9WgXcQ", '{"title": "My Video"}')
        version, content = get_latest_entry(conn, 1)
        assert version == "dQw4w9WgXcQ"
        assert content == '{"title": "My Video"}'

    def test_queries_by_repository_id(self):
        conn, cursor = make_conn_mock()
        cursor.fetchone.return_value = None
        get_latest_entry(conn, 42)
        params = cursor.execute.call_args[0][1]
        assert params == (42,)


class TestSaveEntry:
    def test_inserts_with_version_and_commits(self):
        conn, cursor = make_conn_mock()
        executed_at = datetime.now(tz=timezone.utc)
        save_entry(conn, 1, "vid123", '{"title": "test"}', None, executed_at)
        cursor.execute.assert_called_once()
        conn.commit.assert_called_once()
        params = cursor.execute.call_args[0][1]
        assert params[0] == 1  # repository_id
        assert params[1] == "vid123"  # version
        assert params[4] == executed_at

    def test_success_flag_in_sql(self):
        conn, cursor = make_conn_mock()
        save_entry(conn, 1, None, "{}", None, datetime.now(tz=timezone.utc))
        sql = cursor.execute.call_args[0][0]
        assert "TRUE" in sql


class TestSaveError:
    def test_inserts_error_and_commits(self):
        conn, cursor = make_conn_mock()
        executed_at = datetime.now(tz=timezone.utc)
        save_error(conn, 5, "something went wrong", executed_at)
        cursor.execute.assert_called_once()
        conn.commit.assert_called_once()
        params = cursor.execute.call_args[0][1]
        assert params == (5, "something went wrong", executed_at)

    def test_accepts_none_repository_id(self):
        conn, cursor = make_conn_mock()
        save_error(conn, None, "error", datetime.now(tz=timezone.utc))
        params = cursor.execute.call_args[0][1]
        assert params[0] is None


class TestCleanupOldEntries:
    def test_executes_delete_and_commits(self):
        conn, cursor = make_conn_mock()
        cleanup_old_entries(conn, 1, 15)
        cursor.execute.assert_called_once()
        conn.commit.assert_called_once()
        sql = cursor.execute.call_args[0][0]
        assert "DELETE FROM connector_youtube" in sql
        assert "executed_at" in sql

    def test_uses_repository_id_and_retention_days(self):
        conn, cursor = make_conn_mock()
        cleanup_old_entries(conn, 7, 30)
        params = cursor.execute.call_args[0][1]
        assert params == (7, 30)


# ---------------------------------------------------------------------------
# fetch_video_ids
# ---------------------------------------------------------------------------


class TestFetchVideoIds:
    @patch("latest_videos.yt_dlp.YoutubeDL")
    def test_returns_list_of_ids(self, mock_ydl_cls):
        mock_ydl = MagicMock()
        mock_ydl_cls.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info.return_value = {
            "entries": [
                {"id": "abc123", "url": "https://www.youtube.com/watch?v=abc123"},
                {"id": "def456", "url": "https://www.youtube.com/watch?v=def456"},
            ]
        }
        result = fetch_video_ids("https://www.youtube.com/@channel")
        assert result == [
            {"video_id": "abc123", "url": "https://www.youtube.com/watch?v=abc123", "title": ""},
            {"video_id": "def456", "url": "https://www.youtube.com/watch?v=def456", "title": ""},
        ]

    @patch("latest_videos.yt_dlp.YoutubeDL")
    def test_returns_empty_when_no_entries(self, mock_ydl_cls):
        mock_ydl = MagicMock()
        mock_ydl_cls.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info.return_value = {"entries": []}
        result = fetch_video_ids("https://www.youtube.com/@nobody")
        assert result == []

    @patch("latest_videos.yt_dlp.YoutubeDL")
    def test_appends_videos_to_url(self, mock_ydl_cls):
        mock_ydl = MagicMock()
        mock_ydl_cls.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info.return_value = {"entries": [{"id": "abc"}]}
        fetch_video_ids("https://www.youtube.com/@melvynxdev")
        call_url = mock_ydl.extract_info.call_args_list[0][0][0]
        assert call_url.endswith("/videos")

    @patch("latest_videos.yt_dlp.YoutubeDL")
    def test_does_not_duplicate_videos_suffix(self, mock_ydl_cls):
        mock_ydl = MagicMock()
        mock_ydl_cls.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info.return_value = {"entries": [{"id": "abc"}]}
        fetch_video_ids("https://www.youtube.com/@melvynxdev/videos")
        call_url = mock_ydl.extract_info.call_args_list[0][0][0]
        assert call_url.count("/videos") == 1


# ---------------------------------------------------------------------------
# fetch_video_metadata
# ---------------------------------------------------------------------------


class TestFetchVideoMetadata:
    @patch("latest_videos.yt_dlp.YoutubeDL")
    def test_returns_none_on_exception(self, mock_ydl_cls):
        mock_ydl = MagicMock()
        mock_ydl_cls.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info.side_effect = Exception("network error")
        result = fetch_video_metadata("abc123")
        assert result == (None, None)

    @patch("latest_videos.yt_dlp.YoutubeDL")
    def test_parses_upload_date(self, mock_ydl_cls):
        mock_ydl = MagicMock()
        mock_ydl_cls.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info.return_value = {"upload_date": "20240615", "description": None}
        upload_date, summary = fetch_video_metadata("xyz")
        assert upload_date == datetime(2024, 6, 15, tzinfo=timezone.utc)
        assert summary is None

    @patch("latest_videos.yt_dlp.YoutubeDL")
    def test_upload_date_none_when_missing(self, mock_ydl_cls):
        mock_ydl = MagicMock()
        mock_ydl_cls.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info.return_value = {"upload_date": None, "description": None}
        upload_date, summary = fetch_video_metadata("xyz")
        assert upload_date is None
        assert summary is None

    @patch("latest_videos.yt_dlp.YoutubeDL")
    def test_returns_description(self, mock_ydl_cls):
        mock_ydl = MagicMock()
        mock_ydl_cls.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info.return_value = {"upload_date": "20240615", "description": "A great video"}
        upload_date, summary = fetch_video_metadata("xyz")
        assert upload_date == datetime(2024, 6, 15, tzinfo=timezone.utc)
        assert summary == "A great video"
