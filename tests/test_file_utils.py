"""Tests for file_utils.py — atomic writes, locking, load/save."""

import json
import threading
import pytest
from pathlib import Path
from lib.file_utils import load_json, save_json, now_iso


class TestLoadJson:
    def test_load_valid_json(self, tmp_path):
        f = tmp_path / "test.json"
        f.write_text('{"key": "value"}')
        assert load_json(f) == {"key": "value"}

    def test_load_missing_file_returns_default(self, tmp_path):
        f = tmp_path / "nonexistent.json"
        assert load_json(f) == {}

    def test_load_missing_file_custom_default(self, tmp_path):
        f = tmp_path / "nonexistent.json"
        assert load_json(f, default=[]) == []

    def test_load_corrupt_json_returns_default(self, tmp_path):
        f = tmp_path / "corrupt.json"
        f.write_text("{invalid json")
        assert load_json(f) == {}

    def test_load_empty_file_returns_default(self, tmp_path):
        f = tmp_path / "empty.json"
        f.write_text("")
        assert load_json(f) == {}


class TestSaveJson:
    def test_basic_save_and_load(self, tmp_path):
        f = tmp_path / "test.json"
        data = {"sources": [{"id": "src_001"}]}
        save_json(f, data)
        loaded = load_json(f)
        assert loaded == data

    def test_atomic_write_complete(self, tmp_path):
        """Verify file is complete (not truncated) after write."""
        f = tmp_path / "test.json"
        data = {"items": list(range(1000))}
        save_json(f, data)
        content = f.read_text()
        # Should end with newline (our format)
        assert content.endswith("\n")
        # Should be valid JSON
        parsed = json.loads(content)
        assert len(parsed["items"]) == 1000

    def test_unicode_preserved(self, tmp_path):
        f = tmp_path / "test.json"
        data = {"title": "Caf\u00e9 \u2014 \u00dcbersicht"}
        save_json(f, data)
        loaded = load_json(f)
        assert loaded["title"] == "Caf\u00e9 \u2014 \u00dcbersicht"

    def test_no_tmp_file_left(self, tmp_path):
        f = tmp_path / "test.json"
        save_json(f, {"key": "value"})
        tmp = f.with_suffix(".json.tmp")
        assert not tmp.exists()

    def test_concurrent_writes_no_corruption(self, tmp_path):
        """Two threads writing same file should not corrupt data."""
        f = tmp_path / "test.json"
        save_json(f, {"value": 0})
        errors = []

        def writer(n):
            try:
                for i in range(50):
                    save_json(f, {"value": n * 1000 + i})
            except Exception as e:
                errors.append(str(e))

        t1 = threading.Thread(target=writer, args=(1,))
        t2 = threading.Thread(target=writer, args=(2,))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert not errors, f"Concurrent write errors: {errors}"
        # File should be valid JSON after concurrent writes
        loaded = load_json(f)
        assert "value" in loaded

    def test_roundtrip_preserves_data(self, tmp_path):
        f = tmp_path / "test.json"
        data = {
            "sources": [
                {"id": "src_001", "url": "https://example.com", "tags": ["ai", "ml"]},
                {"id": "src_002", "url": None, "file_path": "/path/to/file.pdf"},
            ],
            "metadata": {"count": 2, "updated_at": "2026-02-27T10:00:00Z"},
        }
        save_json(f, data)
        loaded = load_json(f)
        assert loaded == data


class TestNowIso:
    def test_format(self):
        result = now_iso()
        assert result.endswith("Z")
        assert "T" in result
        assert len(result) == 20  # "YYYY-MM-DDTHH:MM:SSZ"

    def test_is_utc(self):
        result = now_iso()
        # Should be a valid ISO timestamp
        from datetime import datetime
        dt = datetime.fromisoformat(result.replace("Z", "+00:00"))
        assert dt.tzinfo is not None
