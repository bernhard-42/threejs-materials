"""Bundled materials (per-category factory modules) + optional-MaterialX."""

import inspect
import subprocess
import sys
import textwrap

import pytest

from threejs_materials import (
    PbrProperties,
    coats,
    glass,
    metal,
    paper,
    plastic,
    textile,
    wood,
)
from threejs_materials.utils import ensure_materialx

MODULES = {
    "wood": wood, "paper": paper, "metal": metal, "coats": coats,
    "plastic": plastic, "glass": glass, "textile": textile,
}

# 1x1 PNG data URI — dir-independent texture reference for synthetic materials.
PNG = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0"
    "lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _params(fn):
    return set(inspect.signature(fn).parameters)


def test_all_factories_produce_materials():
    for modname, mod in MODULES.items():
        assert mod.__all__, modname
        for name in mod.__all__:
            mat = getattr(mod, name)()
            assert isinstance(mat, PbrProperties)
            assert mat.id == name and mat.source


def test_factory_returns_fresh_instance_each_call():
    assert wood.oak() is not wood.oak()


def test_import_is_byte_free_and_needs_no_materialx():
    """Importing category modules must not read texture bytes or import MaterialX."""
    code = textwrap.dedent(
        """
        import sys, PIL.Image as I
        opens = []
        _o = I.open
        I.open = lambda fp, *a, **k: (opens.append(str(fp)), _o(fp, *a, **k))[1]
        from threejs_materials import wood, paper, metal, coats, plastic, glass, textile
        assert not opens, f"texture files opened at import: {opens}"
        assert "MaterialX" not in sys.modules, "MaterialX imported at module import"
        assert wood.__all__ and metal.__all__
        """
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_category_membership():
    assert "chrome" in coats.__all__ and not hasattr(metal, "chrome")
    assert "gold" in metal.__all__ and "gold_brushed" in metal.__all__
    assert "leather" in textile.__all__            # Fabric → textile
    assert set(wood.__all__) >= {"ash", "oak", "osb"} and "plywood" not in wood.__all__


def test_textured_roundtrips_to_gltf():
    g = metal.gold_brushed().to_gltf()
    assert len(g.materials) == 1 and len(g.images) == 2  # normal + roughness


def test_scalar_roundtrips_to_gltf():
    g = metal.gold().to_gltf()
    assert len(g.materials) == 1 and len(g.images) == 0


def test_shared_metal_textures_are_deduped():
    brushed = [getattr(metal, n)() for n in metal.__all__ if n.endswith("_brushed")]
    assert len(brushed) > 1
    dirs = {m.maps_dir for m in brushed}
    assert len(dirs) == 1 and dirs.pop().name == "_brush"


# --- per-material signature contracts --------------------------------------

def test_raw_metals_have_no_color_param():
    assert _params(metal.gold) == {"roughness"}
    assert _params(metal.gold_brushed) == {"roughness", "scale", "rotation"}
    assert _params(coats.chrome) == {"roughness"}


def test_colorable_and_textured_params():
    assert _params(metal.aluminum_anodized) == {"color", "roughness"}
    assert _params(textile.fabric_weave) == {"color", "roughness", "scale", "rotation"}
    assert _params(wood.oak) == {"color", "roughness", "scale", "rotation"}
    assert _params(coats.colored_coat_matte) == {"color", "roughness"}


def test_transmissive_materials_expose_thickness():
    assert _params(glass.glass) == {"color", "roughness", "thickness"}
    assert _params(plastic.acrylic) == {"color", "roughness", "thickness"}
    assert "thickness" not in _params(plastic.plastic_clean)


def test_thickness_baked_default_and_override():
    assert glass.glass().values.thickness == 1.0
    assert glass.glass(thickness=5.0).values.thickness == 5.0


def test_roughness_override_applies():
    assert metal.gold(roughness=0.3).values.roughness == 0.3
    assert glass.glass(roughness=0.2).values.roughness == 0.2


def test_rotation_only_on_textured():
    import math
    assert "rotation" in _params(wood.oak)
    assert "rotation" not in _params(metal.gold)
    assert "rotation" not in _params(glass.glass)
    assert wood.oak(rotation=90).to_dict()["textureRotation"] == pytest.approx(math.pi / 2)
    assert wood.oak().id == "oak"


def test_physicallybased_metal_color_is_srgb_roundtrip():
    """PhysicallyBased metals store `color` as sRGB, so glTF baseColorFactor
    (linear) recovers the DB's linear F0. Guards against the double-linearization
    bug that rendered these metals too dark (zinc F0 = [0.808, 0.844, 0.865])."""
    bcf = metal.zinc().to_gltf().materials[0].pbrMetallicRoughness.baseColorFactor
    assert list(bcf[:3]) == pytest.approx([0.808, 0.844, 0.865], abs=2e-3)


def test_wood_is_colorable_textured():
    g = wood.oak().to_gltf()
    assert len(g.images) == 3  # color + normal + roughness (metalness dropped)
    tinted = wood.oak(color="#3a1f10").values.color  # stain
    assert tinted != [1.0, 1.0, 1.0] and tinted[0] > tinted[2]


# --- transforms + optional MaterialX ---------------------------------------

def test_with_maps_grafts_surface_onto_scalar():
    base = PbrProperties.create(id="base", metalness=1.0, roughness=0.1)
    other = PbrProperties.create(id="other", normal_map=PNG, roughness_map=PNG)
    m = base.with_maps(other, only=("normal", "roughness"))
    assert m.maps.normal == PNG and m.maps.roughness == PNG
    assert m.values.metalness == 1.0 and m.values.roughness == 0.1


def test_with_maps_rejects_missing_map():
    base = PbrProperties.create(id="base", roughness=0.1)
    other = PbrProperties.create(id="other", normal_map=PNG)
    with pytest.raises(ValueError, match="roughness"):
        base.with_maps(other, only=("normal", "roughness"))


def test_strip_maps_keeps_scalars_drops_maps():
    mat = PbrProperties.create(id="m", roughness=0.4, color_map=PNG)
    s = mat.strip_maps()
    assert s.maps.to_dict() == {} and s.maps_dir is None and s.values.roughness == 0.4


def test_ensure_materialx_absent_raises_with_install_hint(monkeypatch):
    monkeypatch.setitem(sys.modules, "MaterialX", None)
    with pytest.raises(ImportError, match=r"threejs-materials\[materialx\]"):
        ensure_materialx()
