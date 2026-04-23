from __future__ import annotations

from unittest.mock import patch

import pytest

from server.playlist import PlaylistError, preview_playlist


class _FakeYDL:
    def __init__(self, info):
        self._info = info

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, *a, **kw):
        return self._info


def _patch_ydl(info):
    return patch("server.playlist.YoutubeDL", lambda *a, **kw: _FakeYDL(info))


def test_happy_path():
    info = {
        "_type": "playlist",
        "id": "PLxxx",
        "title": "Test Playlist",
        "uploader": "Test Channel",
        "entries": [
            {"id": "aaaa1111aaa", "title": "Vid 1", "duration": 120, "url": "https://youtu.be/aaaa1111aaa"},
            {"id": "bbbb2222bbb", "title": "Vid 2", "duration": 60, "url": "https://youtu.be/bbbb2222bbb"},
        ],
    }
    with _patch_ydl(info):
        p = preview_playlist("https://youtube.com/playlist?list=PLxxx")
    assert p.entry_count == 2
    assert len(p.entries) == 2
    assert p.entries[0].id == "aaaa1111aaa"
    assert p.failures == []


def test_hard_cap_rejects_long_playlist():
    info = {
        "_type": "playlist",
        "id": "PLlong",
        "entries": [{"id": f"vid{i:08d}", "url": f"https://youtu.be/vid{i:08d}"} for i in range(600)],
    }
    with _patch_ydl(info):
        with pytest.raises(PlaylistError, match="hard cap"):
            preview_playlist("url")


def test_channel_feed_rejected():
    # Channel feeds often have nested playlist entries.
    info = {
        "_type": "playlist",
        "entries": [{"_type": "playlist", "id": "PLinner"}],
    }
    with _patch_ydl(info):
        with pytest.raises(PlaylistError, match="channel feed"):
            preview_playlist("url")


def test_unavailable_entries_become_failures():
    info = {
        "_type": "playlist",
        "id": "PLmix",
        "entries": [
            {"id": "good0000000", "title": "Good", "duration": 10, "url": "https://youtu.be/good0000000"},
            {"id": "priv1111111", "availability": "private"},
            {"id": "subs2222222", "availability": "subscriber_only"},
        ],
    }
    with _patch_ydl(info):
        p = preview_playlist("url")
    assert len(p.entries) == 1
    assert p.entries[0].id == "good0000000"
    assert len(p.failures) == 2
    assert {f.reason for f in p.failures} == {"private", "subscriber_only"}
