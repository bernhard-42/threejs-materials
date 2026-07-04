"""Tests for threejs_materials.sources.ambientcg fetch fallback logic."""

import io
import zipfile
from unittest.mock import MagicMock, patch

import pytest

from threejs_materials.sources import ambientcg


def _make_mtlx_zip() -> bytes:
    """Build a minimal ZIP containing a placeholder .mtlx file."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("material.mtlx", b"<?xml version='1.0'?><materialx/>")
    return buf.getvalue()


def _resp(status_code: int, content: bytes = b"") -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    r.content = content
    if status_code >= 400:
        import requests
        r.raise_for_status.side_effect = requests.HTTPError(f"{status_code}")
    else:
        r.raise_for_status.return_value = None
    return r


class TestAmbientCgFallback:
    def test_png_success(self, tmp_path):
        """PNG variant returns 200 — JPG is never queried."""
        zip_bytes = _make_mtlx_zip()
        with patch("threejs_materials.sources.ambientcg.requests.get") as mock_get:
            mock_get.return_value = _resp(200, zip_bytes)
            result = ambientcg.fetch("Onyx015", "1K", tmp_path)

        assert mock_get.call_count == 1
        assert "PNG" in mock_get.call_args_list[0].args[0]
        assert result.mtlx_path is not None and result.mtlx_path.exists()

    def test_png_404_falls_back_to_jpg(self, tmp_path):
        """PNG 404 → JPG retried and succeeds."""
        zip_bytes = _make_mtlx_zip()
        with patch("threejs_materials.sources.ambientcg.requests.get") as mock_get:
            mock_get.side_effect = [_resp(404), _resp(200, zip_bytes)]
            result = ambientcg.fetch("Onyx015", "1K", tmp_path)

        assert mock_get.call_count == 2
        assert "PNG" in mock_get.call_args_list[0].args[0]
        assert "JPG" in mock_get.call_args_list[1].args[0]
        assert result.mtlx_path is not None and result.mtlx_path.exists()

    def test_both_404_raises(self, tmp_path):
        """Both variants 404 → RuntimeError mentioning both attempts."""
        with patch("threejs_materials.sources.ambientcg.requests.get") as mock_get:
            mock_get.side_effect = [_resp(404), _resp(404)]
            with pytest.raises(RuntimeError, match="no package found"):
                ambientcg.fetch("Missing", "1K", tmp_path)

        assert mock_get.call_count == 2

    def test_non_404_http_error_propagates(self, tmp_path):
        """A 500 on the PNG URL is a real error — don't silently fall back."""
        import requests
        with patch("threejs_materials.sources.ambientcg.requests.get") as mock_get:
            mock_get.return_value = _resp(500)
            with pytest.raises(requests.HTTPError):
                ambientcg.fetch("Onyx015", "1K", tmp_path)

        assert mock_get.call_count == 1

    def test_invalid_resolution_raises(self, tmp_path):
        with pytest.raises(ValueError, match="not available"):
            ambientcg.fetch("Onyx015", "16K", tmp_path)
