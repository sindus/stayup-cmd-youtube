"""Unit tests — no external dependencies. stayup-api itself is mocked
(unittest.mock.patch on `requests.request`); its actual behavior is covered
by stayup-api's own test suite. yt-dlp is mocked too."""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from latest_videos import (
    DISPLAY_TEMPLATE,
    add_source,
    cleanup_old_entries,
    fetch_video_ids,
    fetch_video_metadata,
    get_latest_version,
    get_sources,
    process_repository,
    register_provider,
    save_entry,
    save_error,
)

# ---------------------------------------------------------------------------
# api_request helpers
# ---------------------------------------------------------------------------


def mock_response(json_body=None, status=200):
    response = MagicMock()
    response.status_code = status
    response.content = b"{}" if json_body is not None else b""
    response.json.return_value = json_body
    response.raise_for_status.return_value = None
    return response


@patch("latest_videos.API_KEY", "test-key")
class TestRegisterProvider:
    @patch("latest_videos.requests.request")
    def test_posts_display_name_sort_order_and_template(self, mock_request):
        mock_request.return_value = mock_response()
        register_provider()
        method, url = mock_request.call_args[0]
        assert method == "POST"
        assert url.endswith("/connector-api/youtube/register")
        body = mock_request.call_args.kwargs["json"]
        assert body["displayName"] == "YouTube"
        assert body["sortOrder"] == 20
        assert body["template"] == DISPLAY_TEMPLATE


class TestApiRequestWithoutKey:
    @patch("latest_videos.API_KEY", None)
    def test_raises_when_no_api_key_is_configured(self):
        with pytest.raises(RuntimeError, match="STAYUP_API_KEY"):
            register_provider()


@patch("latest_videos.API_KEY", "test-key")
class TestAddSource:
    @patch("latest_videos.requests.request")
    def test_posts_the_url_and_returns_the_id(self, mock_request):
        mock_request.return_value = mock_response({"id": 7, "url": "https://www.youtube.com/@melvynxdev"})
        result = add_source("https://www.youtube.com/@melvynxdev")
        assert result == 7
        method, url = mock_request.call_args[0]
        assert method == "POST"
        assert url.endswith("/connector-api/youtube/sources")


@patch("latest_videos.API_KEY", "test-key")
class TestGetSources:
    @patch("latest_videos.requests.request")
    def test_returns_id_url_config_tuples(self, mock_request):
        mock_request.return_value = mock_response(
            {"sources": [{"id": 1, "url": "https://www.youtube.com/@a", "config": {"max_iterations": 3}}]}
        )
        assert get_sources() == [(1, "https://www.youtube.com/@a", {"max_iterations": 3})]


@patch("latest_videos.API_KEY", "test-key")
class TestGetLatestVersion:
    @patch("latest_videos.requests.request")
    def test_returns_none_on_first_run(self, mock_request):
        mock_request.return_value = mock_response({"version": None})
        assert get_latest_version(1) is None

    @patch("latest_videos.requests.request")
    def test_returns_the_version(self, mock_request):
        mock_request.return_value = mock_response({"version": "dQw4w9WgXcQ"})
        assert get_latest_version(1) == "dQw4w9WgXcQ"
        url = mock_request.call_args[0][1]
        assert url.endswith("/connector-api/youtube/sources/1/state")


@patch("latest_videos.API_KEY", "test-key")
class TestSaveEntry:
    @patch("latest_videos.requests.request")
    def test_posts_a_single_item_with_version(self, mock_request):
        mock_request.return_value = mock_response({"success": True})
        executed_at = datetime.now(tz=timezone.utc)
        save_entry(1, "vid123", '{"title": "test"}', None, executed_at)
        method, url = mock_request.call_args[0]
        assert method == "POST"
        assert url.endswith("/connector-api/youtube/items")
        body = mock_request.call_args.kwargs["json"]
        assert len(body["items"]) == 1
        item = body["items"][0]
        assert item["repositoryId"] == 1
        assert item["version"] == "vid123"
        assert item["success"] is True

    @patch("latest_videos.requests.request")
    def test_accepts_a_none_version(self, mock_request):
        mock_request.return_value = mock_response({"success": True})
        save_entry(1, None, "{}", None, datetime.now(tz=timezone.utc))
        assert mock_request.call_args.kwargs["json"]["items"][0]["version"] is None

    @patch("latest_videos.requests.request")
    def test_serializes_the_video_datetime(self, mock_request):
        mock_request.return_value = mock_response({"success": True})
        video_date = datetime(2024, 6, 15, tzinfo=timezone.utc)
        save_entry(1, "abc", "{}", video_date, datetime.now(tz=timezone.utc))
        item = mock_request.call_args.kwargs["json"]["items"][0]
        assert item["datetime"] == video_date.isoformat()


@patch("latest_videos.API_KEY", "test-key")
class TestSaveError:
    @patch("latest_videos.requests.request")
    def test_posts_the_error(self, mock_request):
        mock_request.return_value = mock_response({"success": True})
        executed_at = datetime.now(tz=timezone.utc)
        save_error(5, "something went wrong", executed_at)
        body = mock_request.call_args.kwargs["json"]
        assert body == {"repositoryId": 5, "error": "something went wrong", "executedAt": executed_at.isoformat()}

    @patch("latest_videos.requests.request")
    def test_accepts_none_repository_id(self, mock_request):
        mock_request.return_value = mock_response({"success": True})
        save_error(None, "error", datetime.now(tz=timezone.utc))
        assert mock_request.call_args.kwargs["json"]["repositoryId"] is None


@patch("latest_videos.API_KEY", "test-key")
class TestCleanupOldEntries:
    @patch("latest_videos.requests.request")
    def test_sends_retention_days_as_a_query_param(self, mock_request):
        mock_request.return_value = mock_response({"success": True})
        cleanup_old_entries(7, 30)
        method, url = mock_request.call_args[0]
        assert method == "DELETE"
        assert url.endswith("/connector-api/youtube/sources/7/old-items")
        assert mock_request.call_args.kwargs["params"] == {"retentionDays": 30}


class TestDisplayTemplate:
    def test_round_trips_through_json_unchanged(self):
        assert json.loads(json.dumps(DISPLAY_TEMPLATE)) == DISPLAY_TEMPLATE

    def test_ships_a_self_describing_icon(self):
        icon = DISPLAY_TEMPLATE["display"]["icon"]
        assert isinstance(icon, dict)
        assert icon["paths"]
        assert all(p[:1] in ("M", "m") for p in icon["paths"])
        assert icon["viewBox"] == "0 0 24 24"

    def test_media_detail_with_reconstructed_embed_url(self):
        assert DISPLAY_TEMPLATE["detail"]["mode"] == "media"
        assert DISPLAY_TEMPLATE["detail"]["embedUrl"].endswith("/embed/{$row.version}")
        assert DISPLAY_TEMPLATE["list"]["layout"] == "media"


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


# ---------------------------------------------------------------------------
# process_repository — end to end, stayup-api and yt-dlp mocked
# ---------------------------------------------------------------------------


def make_entry(vid_id, title=""):
    return {"video_id": vid_id, "url": f"https://www.youtube.com/watch?v={vid_id}", "title": title}


@patch("latest_videos.API_KEY", "test-key")
class TestProcessRepository:
    @patch("latest_videos.save_error")
    @patch("latest_videos.save_entry")
    @patch("latest_videos.get_latest_version")
    @patch("latest_videos.fetch_video_metadata")
    @patch("latest_videos.fetch_video_ids")
    def test_first_run_stores_only_latest(self, mock_ids, mock_meta, mock_get_latest, mock_save, mock_save_error):
        mock_ids.return_value = [make_entry("dQw4w9WgXcQ", "Never Gonna Give You Up")]
        mock_meta.return_value = (datetime(2009, 2, 25, tzinfo=timezone.utc), None)
        mock_get_latest.return_value = None
        executed_at = datetime.now(tz=timezone.utc)
        process_repository(1, "https://www.youtube.com/@rick", executed_at, {})

        mock_save.assert_called_once()
        repository_id, version, content, video_datetime, _ = mock_save.call_args[0]
        assert version == "dQw4w9WgXcQ"
        assert json.loads(content)["title"] == "Never Gonna Give You Up"
        mock_save_error.assert_not_called()

    @patch("latest_videos.save_error")
    @patch("latest_videos.save_entry")
    @patch("latest_videos.get_latest_version")
    @patch("latest_videos.fetch_video_metadata")
    @patch("latest_videos.fetch_video_ids")
    def test_no_insert_when_same_video(self, mock_ids, mock_meta, mock_get_latest, mock_save, _err):
        mock_ids.return_value = [make_entry("vid001", "My Video")]
        mock_meta.return_value = (None, None)
        mock_get_latest.return_value = "vid001"
        process_repository(1, "https://www.youtube.com/@channel", datetime.now(tz=timezone.utc), {})
        mock_save.assert_not_called()

    @patch("latest_videos.save_error")
    @patch("latest_videos.save_entry")
    @patch("latest_videos.get_latest_version")
    @patch("latest_videos.fetch_video_metadata")
    @patch("latest_videos.fetch_video_ids")
    def test_iterates_new_videos_until_the_known_one(self, mock_ids, mock_meta, mock_get_latest, mock_save, _err):
        mock_ids.return_value = [
            make_entry("vid004"),
            make_entry("vid003"),
            make_entry("vid002"),
            make_entry("vid001"),
        ]
        mock_meta.return_value = (None, None)
        mock_get_latest.return_value = "vid001"
        process_repository(1, "https://www.youtube.com/@channel", datetime.now(tz=timezone.utc), {})

        saved_versions = [call.args[1] for call in mock_save.call_args_list]
        assert saved_versions == ["vid004", "vid003", "vid002"]

    @patch("latest_videos.save_error")
    @patch("latest_videos.fetch_video_ids")
    def test_logs_error_on_failure(self, mock_ids, mock_save_error):
        mock_ids.side_effect = Exception("yt-dlp network error")
        executed_at = datetime.now(tz=timezone.utc)
        process_repository(1, "https://www.youtube.com/@channel", executed_at, {})
        mock_save_error.assert_called_once_with(1, "yt-dlp network error", executed_at)

    @patch("latest_videos.save_error")
    @patch("latest_videos.fetch_video_ids")
    def test_logs_error_when_no_video(self, mock_ids, mock_save_error):
        mock_ids.return_value = []
        process_repository(1, "https://www.youtube.com/@empty", datetime.now(tz=timezone.utc), {})
        mock_save_error.assert_called_once()
