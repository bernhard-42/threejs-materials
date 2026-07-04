"""Tests for raw MaterialX download (download_* / source.download)."""

import io
import zipfile
from unittest.mock import MagicMock, patch

import pytest

from threejs_materials import PbrProperties
from threejs_materials.sources import ambientcg
from threejs_materials.sources.common import normalize_name


def _resp(status_code=200, content=b"", text="", json_data=None):
    r = MagicMock()
    r.status_code = status_code
    r.content = content
    r.text = text
    r.json.return_value = json_data
    if status_code >= 400:
        import requests
        r.raise_for_status.side_effect = requests.HTTPError(f"{status_code}")
    else:
        r.raise_for_status.return_value = None
    return r


def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


class TestNormalizeName:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Dark Bricks", "dark_bricks"),
            ("Metal:009", "metal_009"),
            ("  Onyx015  ", "onyx015"),
            ("Car - Paint!!", "car_paint"),
        ],
    )
    def test_normalize(self, raw, expected):
        assert normalize_name(raw) == expected


class TestAmbientCgDownload:
    def test_extracts_zip_unchanged(self, tmp_path):
        zip_bytes = _zip({
            "Bricks075A.mtlx": b"<materialx/>",
            "Bricks075A_Color.png": b"PNG",
            "Bricks075A_Normal.jpg": b"JPG",
        })
        with patch("threejs_materials.sources.ambientcg.requests.get") as g:
            g.return_value = _resp(200, content=zip_bytes)
            PbrProperties.download_ambientcg("Bricks075A", dest=str(tmp_path))

        folder = tmp_path / "bricks075a"
        assert folder.is_dir()
        assert (folder / "Bricks075A.mtlx").read_bytes() == b"<materialx/>"
        assert (folder / "Bricks075A_Color.png").exists()
        assert (folder / "Bricks075A_Normal.jpg").exists()

    def test_preserves_subdir_hierarchy(self, tmp_path):
        zip_bytes = _zip({
            "mat.mtlx": b"<materialx/>",
            "tex/color.png": b"PNG",
        })
        with patch("threejs_materials.sources.ambientcg.requests.get") as g:
            g.return_value = _resp(200, content=zip_bytes)
            ambientcg.download("Onyx015", "1K", tmp_path)

        folder = tmp_path / "onyx015"
        assert (folder / "tex" / "color.png").read_bytes() == b"PNG"


class TestGpuOpenDownload:
    def test_extracts_with_normalized_title(self, tmp_path):
        search_json = {
            "results": [
                {
                    "title": "Dark Bricks",
                    "id": "abc",
                    "license": "CC0",
                    "packages": ["pkg-uuid"],
                }
            ]
        }
        pkg_json = {"label": "1k 8b"}
        zip_bytes = _zip({"material.mtlx": b"<materialx/>", "color.png": b"PNG"})

        with patch("threejs_materials.sources.gpuopen.requests.get") as g:
            g.side_effect = [
                _resp(200, json_data=search_json),  # search
                _resp(200, json_data=pkg_json),     # package label lookup
                _resp(200, content=zip_bytes),      # zip download
            ]
            PbrProperties.download_gpuopen("Dark Bricks", dest=str(tmp_path))

        folder = tmp_path / "dark_bricks"
        assert folder.is_dir()
        assert (folder / "material.mtlx").read_bytes() == b"<materialx/>"
        assert (folder / "color.png").exists()


class TestPolyhavenDownload:
    def test_writes_mtlx_textures_and_ao(self, tmp_path):
        listing = {
            "mtlx": {
                "1k": {
                    "mtlx": {
                        "url": "https://x/dark_bricks_1k.mtlx",
                        "include": {
                            "textures/dark_bricks_diff_1k.png": {"url": "https://x/diff.png"},
                            "textures/dark_bricks_nor_1k.exr": {"url": "https://x/nor.exr"},
                        },
                    }
                }
            },
            "AO": {"1k": {"png": {"url": "https://x/dark_bricks_ao_1k.png"}}},
        }

        def fake_get(url, **kwargs):
            if url.endswith("/files/dark_bricks"):
                return _resp(200, json_data=listing)
            if url.endswith(".mtlx"):
                return _resp(200, text="<materialx/>")
            return _resp(200, content=b"BYTES")

        with patch("threejs_materials.sources.polyhaven.requests.get", side_effect=fake_get):
            PbrProperties.download_polyhaven("Dark Bricks", dest=str(tmp_path))

        folder = tmp_path / "dark_bricks"
        assert (folder / "dark_bricks_1k.mtlx").read_text() == "<materialx/>"
        # include paths preserved so the .mtlx references still resolve
        assert (folder / "textures" / "dark_bricks_diff_1k.png").read_bytes() == b"BYTES"
        assert (folder / "textures" / "dark_bricks_nor_1k.exr").exists()
        # side-loaded AO written alongside
        assert (folder / "dark_bricks_ao_1k.png").exists()
