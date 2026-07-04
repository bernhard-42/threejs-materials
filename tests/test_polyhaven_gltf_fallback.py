"""Polyhaven glTF fallback when openexr is absent.

When openexr is not installed, a Polyhaven material whose .mtlx references EXR
maps is loaded from Polyhaven's glTF instead, and cached in the same internal
format as the .mtlx route.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pygltflib import (
    GLTF2,
    Image,
    Material,
    PbrMetallicRoughness,
    Sampler,
    Texture,
    TextureInfo,
)

from conftest import _make_1x1_png
from threejs_materials.sources import _gltf_to_properties, polyhaven
from threejs_materials.sources.common import SourceResult


def _build_gltf(dir_: Path) -> Path:
    """Write a minimal glTF (one textured material) with an external PNG."""
    (dir_ / "textures").mkdir(parents=True, exist_ok=True)
    (dir_ / "textures" / "color.png").write_bytes(_make_1x1_png(128, 100, 80))

    g = GLTF2()
    g.images = [Image(uri="textures/color.png")]
    g.samplers = [Sampler()]
    g.textures = [Texture(source=0, sampler=0)]
    g.materials = [
        Material(
            name="DarkBricks",
            pbrMetallicRoughness=PbrMetallicRoughness(
                baseColorFactor=[0.5, 0.4, 0.3, 1.0],
                baseColorTexture=TextureInfo(index=0),
                metallicFactor=0.0,
                roughnessFactor=0.7,
            ),
        )
    ]
    gltf_path = dir_ / "dark_bricks_1k.gltf"
    g.save(str(gltf_path))
    return gltf_path


class TestGltfToProperties:
    def test_parses_values_and_texture_datauri(self, tmp_path):
        gltf_path = _build_gltf(tmp_path)
        props = _gltf_to_properties(gltf_path)

        # external texture resolved to a data URI (relative to the glTF dir)
        assert props["color"]["texture"].startswith("data:image/png;base64,")
        assert props["metalness"]["value"] == 0.0
        assert props["roughness"]["value"] == 0.7
        assert "value" in props["color"]  # baseColorFactor → color value too


class TestPolyhavenRouting:
    def _listing(self):
        """A _resolve() return whose .mtlx includes an EXR map."""
        data = {"gltf": {"1k": {"gltf": {"url": "https://x/m.gltf", "include": {}}}}}
        mtlx_info = {
            "url": "https://x/m.mtlx",
            "include": {
                "textures/diff.png": {"url": "https://x/diff.png"},
                "textures/nor.exr": {"url": "https://x/nor.exr"},
            },
        }
        return data, mtlx_info, "1k", "dark_bricks"

    def test_no_openexr_and_exr_present_routes_to_gltf(self, tmp_path):
        with patch.object(polyhaven, "_resolve", return_value=self._listing()), \
             patch.object(polyhaven, "OpenEXR", None), \
             patch.object(polyhaven, "_fetch_gltf", return_value=tmp_path / "m.gltf") as fg:
            result = polyhaven.fetch("Dark Bricks", "1K", tmp_path)

        fg.assert_called_once()
        assert result.gltf_path == tmp_path / "m.gltf"
        assert result.mtlx_path is None

    def test_openexr_present_routes_to_mtlx(self, tmp_path):
        def fake_get(url, **kw):
            r = MagicMock()
            r.status_code = 200
            r.text = "<materialx/>"
            r.content = b"BYTES"
            r.raise_for_status.return_value = None
            return r

        with patch.object(polyhaven, "_resolve", return_value=self._listing()), \
             patch.object(polyhaven, "OpenEXR", object()), \
             patch.object(polyhaven, "_fetch_gltf") as fg, \
             patch.object(polyhaven.requests, "get", side_effect=fake_get):
            result = polyhaven.fetch("Dark Bricks", "1K", tmp_path)

        fg.assert_not_called()
        assert result.mtlx_path is not None
        assert result.gltf_path is None

    def test_gltf_needed_but_missing_raises(self, tmp_path):
        data, mtlx_info, res, name = self._listing()
        data["gltf"] = {}  # no glTF available
        with patch.object(polyhaven, "_resolve", return_value=(data, mtlx_info, res, name)), \
             patch.object(polyhaven, "OpenEXR", None):
            with pytest.raises(RuntimeError, match="no glTF available"):
                polyhaven.fetch("Dark Bricks", "1K", tmp_path)


class TestLoaderGltfBranch:
    def test_load_caches_gltf_in_internal_format(self, tmp_path, monkeypatch):
        import threejs_materials.sources as S

        cache = tmp_path / "cache"
        monkeypatch.setattr("threejs_materials.sources.CACHE_DIR", cache)

        gltf_dir = tmp_path / "dl"
        gltf_dir.mkdir()
        gltf_path = _build_gltf(gltf_dir)

        def fake_fetch(name, res, out_dir):
            return SourceResult(
                gltf_path=gltf_path, license="CC0 1.0", url="https://x"
            )

        monkeypatch.setattr(S.polyhaven, "fetch", fake_fetch)

        out = S.polyhaven_loader.load("Dark Bricks", "1K")

        # same internal format as the .mtlx route
        assert out["source"] == "polyhaven"
        assert out["name"] == "Dark Bricks"
        assert out["values"]["roughness"] == 0.7
        assert "color" in out["textures"]
        # texture was collected to the cache dir, referenced by filename
        assert (cache / out["maps_dir"]).is_dir()
        assert out["textures"]["color"] == "color.png"
