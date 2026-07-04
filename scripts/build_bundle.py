"""Authoring generator for the bundled material library.

Runs the curated source recipes (needs `pip install threejs-materials[materialx]`
+ network), bakes/transforms them, writes the trimmed texture assets under
`src/threejs_materials/pbr_properties/_assets/`, and emits one module per
category (`wood.py`, `metal.py`, `coats.py`, ...) inside `pbr_properties/`.
`threejs_materials/__init__.py` re-exports them so users do
`from threejs_materials import wood; wood.ash()`.

Re-runnable / idempotent. Edit the RECIPE tunables below and re-run to curate.
Bundled materials load with NO materialx; this generator is the only part that
needs it.
"""

from __future__ import annotations

import pprint
import shutil
from pathlib import Path

import numpy as np
from PIL import Image

from threejs_materials import PbrProperties
from threejs_materials.sources import CACHE_DIR
from threejs_materials.utils import _normalize_srgb_color

RES = "1K"
PKG = Path(__file__).resolve().parent.parent / "src" / "threejs_materials"
BUNDLED = PKG / "bundled"          # legacy flat module/dir — cleaned up on run
SUB = PKG / "pbr_properties"       # category modules + shared _assets live here
ASSETS = SUB / "_assets"           # shared textures, referenced by every category module
# Category → module name. Each becomes threejs_materials/pbr_properties/<module>.py,
# re-exported from threejs_materials/__init__.py so `from threejs_materials import metal` works.
CATEGORY_MODULES = ("wood", "paper", "metal", "coats", "plastic", "glass", "textile")
# Pre-optimized wood assets (color/normal/roughness PNGs per subdir), committed
# to the repo because wood is ingested from local glTF, not a download source.
WOOD_SRC = PKG.parent.parent / "wood_src"

# --- Curation tunables (edit + re-run) -------------------------------------
LEATHER_DEFAULT_COLOR = "#7a5230"  # baked default (medium brown); leather(color=...) overrides
LEATHER_MAP_MEAN = 0.85            # brightness target for the desaturated map
PLASTIC_CLEAN_ROUGHNESS = 0.15     # roughness of texture-less "clean" plastic
COAT_MATTE_ROUGHNESS = 0.5         # colored_coat_matte (satin)
COAT_GLOSS_ROUGHNESS = 0.12        # colored_coat_gloss
TRANSMISSIVE_THICKNESS = 1.0       # baked default thickness (object units) for glass/acrylic
                                   #   drives volumetric refraction; overridable per call

# Metals that get a shiny/brushed/matte triple (scalar base + shared surface map)
METAL_FINISH_BASES: dict[str, tuple[str, str]] = {
    "aluminum": ("physicallybased", "Aluminum"),
    "brass": ("physicallybased", "Brass"),
    "bronze": ("gpuopen", "Bronze"),
    "copper": ("physicallybased", "Copper"),
    "gold": ("physicallybased", "Gold"),
    "silver": ("physicallybased", "Silver"),
    "stainless": ("physicallybased", "Stainless Steel"),
}
# Standalone scalar metals (no finish triple)
METAL_STANDALONE: dict[str, tuple[str, str]] = {
    "aluminum_anodized": ("physicallybased", "Aluminum (Anodized Red)"),
    "chrome": ("physicallybased", "Chromium"),
}
# Shared metal surface map sets: (asset dir, source, name)
BRUSH = ("_brush", "ambientcg", "Metal 009")
MATTE = ("_matte", "ambientcg", "Metal 032")

# Own-dir textured materials: name -> (source, source_name)  [keep color+normal+roughness]
TEXTURED: dict[str, tuple[str, str]] = {
    "plastic_rough": ("ambientcg", "Plastic 013 A"),
    "corrugated_cardboard": ("ambientcg", "Cardboard 002"),
    "paper": ("ambientcg", "Paper 001"),
    "foamboard": ("ambientcg", "Styrofoam 004"),
    "fabric_weave": ("ambientcg", "Fabric 060"),
    "fabric_knit": ("ambientcg", "Fabric 019"),
    "felt": ("ambientcg", "Fabric 034"),
}
# Scalar (texture-less) materials: name -> (source, source_name)
SCALARS: dict[str, tuple[str, str]] = {
    "acrylic": ("physicallybased", "Plastic (Acrylic)"),
    "glass": ("physicallybased", "Glass (Soda-lime)"),
}

TEX_CAT = {
    "plastic_rough": "plastic",
    "corrugated_cardboard": "paper",
    "paper": "paper",
    "foamboard": "paper",
    "fabric_weave": "textile",
    "fabric_knit": "textile",
    "felt": "textile",
}

SURFACE_MAPS = ("normal", "roughness")
FULL_MAPS = ("color", "normal", "roughness")

_FN = {
    "physicallybased": PbrProperties.from_physicallybased,
    "ambientcg": PbrProperties.from_ambientcg,
    "gpuopen": PbrProperties.from_gpuopen,
}


def _cache_dir(source: str, name: str) -> Path:
    safe = name.lower().replace(" ", "_")
    return CACHE_DIR / f"{source}_{safe}_{RES.lower()}"


def bake(source: str, name: str) -> PbrProperties:
    return _FN[source](name, RES)


def _copy_maps(src_dir: Path, dst_dir: Path, keep: tuple[str, ...]) -> dict[str, str]:
    """Copy the kept map PNGs into dst_dir; return {field: filename}."""
    dst_dir.mkdir(parents=True, exist_ok=True)
    out = {}
    for field in keep:
        f = src_dir / f"{field}.png"
        if f.exists():
            shutil.copy2(f, dst_dir / f.name)
            out[field] = f.name
    return out


def _desaturate_normalize(src: Path, dst: Path, mean: float) -> None:
    lum = Image.open(src).convert("L")
    arr = np.asarray(lum, dtype=np.float32) / 255.0
    scaled = np.clip(arr * (mean / (arr.mean() + 1e-6)), 0, 1)
    Image.fromarray((scaled * 255).astype("uint8"), mode="L").save(dst)


def _drop_stale(values: dict, kept: tuple[str, ...]) -> dict:
    """Drop scalars whose paired map was trimmed away."""
    if "displacement" not in kept:
        values.pop("displacementScale", None)
    return values


# (name, from_dict payload sans maps_dir, asset_dir|None, accepts_color)
Entry = tuple[str, dict, str | None, bool]


def _payload(name: str, mat: PbrProperties, tex: dict[str, str]) -> dict:
    return {
        "id": name,
        "name": name,
        "source": mat.source,
        "url": mat.url,
        "license": mat.license,
        "values": _drop_stale(mat.values.to_dict(), tuple(tex)),
        "textures": tex,
    }


def build() -> tuple[list[Entry], dict[str, str]]:
    out: list[Entry] = []
    cat: dict[str, str] = {}

    def add(category, name, mat, tex=None, adir=None, color=False):
        out.append((name, _payload(name, mat, tex or {}), adir, color))
        cat[name] = category

    # plastics + glass scalars (colorable dielectrics). Transmissive ones get a
    # baked thickness so volumetric refraction works out of the box.
    for category, name in (("plastic", "acrylic"), ("glass", "glass")):
        m = bake(*SCALARS[name])
        if m.values.transmission:
            m.values.thickness = TRANSMISSIVE_THICKNESS
        add(category, name, m, color=True)

    clean = bake(*TEXTURED["plastic_rough"]).strip_maps()
    clean.values.roughness = PLASTIC_CLEAN_ROUGHNESS
    add("plastic", "plastic_clean", clean, color=True)

    # own-dir textured (colorable dielectrics; color+normal+roughness)
    for name, (src, sname) in TEXTURED.items():
        tex = _copy_maps(_cache_dir(src, sname), ASSETS / name, FULL_MAPS)
        add(TEX_CAT[name], name, bake(src, sname), tex, name, color=True)

    # metals — NO color (intrinsic reflectance). shiny = base name;
    # brushed/matte share the two surface-map sets.
    for adir, src, sname in (BRUSH, MATTE):
        _copy_maps(_cache_dir(src, sname), ASSETS / adir, SURFACE_MAPS)
    brush, matte = bake(*BRUSH[1:]), bake(*MATTE[1:])
    stex = {"normal": "normal.png", "roughness": "roughness.png"}
    for base_name, (src, sname) in METAL_FINISH_BASES.items():
        add("metal", base_name, bake(src, sname))
        base = bake(src, sname)
        for finish, other, adir in (("brushed", brush, "_brush"), ("matte", matte, "_matte")):
            m = base.with_maps(other, only=SURFACE_MAPS)
            m.values.roughness = 1.0  # let the surface map drive roughness
            add("metal", f"{base_name}_{finish}", m, dict(stex), adir)
    # anodized aluminum is dyed → colorable (metal); chrome is a mirror finish (coats)
    add("metal", "aluminum_anodized",
        bake(*METAL_STANDALONE["aluminum_anodized"]), color=True)
    add("coats", "chrome", bake(*METAL_STANDALONE["chrome"]))

    # colored coats — hand-authored dielectric scalars, colorable (default white)
    for name, rough in (("colored_coat_matte", COAT_MATTE_ROUGHNESS),
                        ("colored_coat_gloss", COAT_GLOSS_ROUGHNESS)):
        m = PbrProperties.create(id=name, color="#ffffff", metalness=0.0, roughness=rough)
        add("coats", name, m, color=True)

    # leather — one desaturated+normalized shared texture, default brown baked
    lsrc, lname = "ambientcg", "Leather 028"
    ldir = ASSETS / "leather"
    _copy_maps(_cache_dir(lsrc, lname), ldir, SURFACE_MAPS)
    _desaturate_normalize(_cache_dir(lsrc, lname) / "color.png", ldir / "color.png", LEATHER_MAP_MEAN)
    lbase = bake(lsrc, lname)
    lbase.values.color = list(_normalize_srgb_color(LEATHER_DEFAULT_COLOR)[0])
    ltex = {"color": "color.png", "normal": "normal.png", "roughness": "roughness.png"}
    add("textile", "leather", lbase, ltex, "leather", color=True)

    # wood — pre-optimized local assets, colorable (tint/stain) + textured
    if WOOD_SRC.exists():
        for wd in sorted(p for p in WOOD_SRC.iterdir() if p.is_dir()):
            slug = wd.name
            tex = {}
            for field in ("color", "normal", "roughness"):
                src = wd / f"{field}.png"
                if src.exists():
                    (ASSETS / slug).mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, ASSETS / slug / src.name)
                    tex[field] = src.name
            # neutral scalars → color map shows natural grain, roughness map drives;
            # color override tints, roughness override multiplies.
            mat = PbrProperties.create(id=slug, color="#ffffff", metalness=0.0, roughness=1.0)
            add("wood", slug, mat, tex, slug, color=True)

    return out, cat


def _ctor(payload: dict, adir: str | None) -> str:
    body = pprint.pformat(payload, indent=4, width=84, sort_dicts=False)
    if adir is None:
        return f"PbrProperties.from_dict({body})"
    return (
        f"PbrProperties.from_dict({{\n"
        f"        **{body},\n"
        f'        "maps_dir": str(_ASSETS / {adir!r}),\n'
        f"    }})"
    )


def _material_lines(name: str, payload: dict, adir: str | None, color: bool) -> list[str]:
    """Code lines for one factory function."""
    ctor = _ctor(payload, adir)
    has_rough = "roughness" in payload["values"]
    transmissive = payload["values"].get("transmission", 0) > 0
    params = (
        (["color=None"] if color else [])
        + (["roughness=None"] if has_rough else [])
        + (["scale=(1, 1)"] if adir else [])
        + (["rotation=0.0"] if adir else [])
        + (["thickness=None"] if transmissive else [])
    )
    lines = [f"def {name}({', '.join(params)}):"]
    if not params:
        lines.append(f"    return {ctor}")
        lines.append("")
        return lines
    lines.append(f"    m = {ctor}")
    if color:
        lines += ["    if color is not None:", "        m = m.override(color=color)"]
    if has_rough:
        lines += ["    if roughness is not None:", "        m = m.override(roughness=roughness)"]
    if adir:
        lines += ["    if scale != (1, 1) or rotation:",
                  "        m = m.scale(scale[0], scale[1], rotation=rotation)"]
    if transmissive:
        lines += ["    if thickness is not None:", "        m = m.override(thickness=thickness)"]
    lines += ["    return m", ""]
    return lines


def emit(entries: list[Entry], cat: dict[str, str]) -> None:
    """Write one module per category into the package."""
    by_cat: dict[str, list[Entry]] = {}
    for e in entries:
        by_cat.setdefault(cat[e[0]], []).append(e)

    for category, ents in by_cat.items():
        first = ents[0][0]
        lines = [
            f'"""Bundled {category} materials — GENERATED by scripts/build_bundle.py.',
            "Do not edit by hand.",
            "",
            f"    from threejs_materials import {category}",
            f"    {category}.{first}()",
            "",
            "Each material is a factory returning a fresh PbrProperties; the signature",
            "exposes only the overrides that make sense: `color` (colorable; raw metals",
            "omit it), `roughness`, `scale=(u, v)` + `rotation` degrees (textured),",
            "`thickness` (transmissive).",
            '"""',
            "from pathlib import Path",
            "",
            "from threejs_materials.library import PbrProperties",
            "",
            '_ASSETS = Path(__file__).parent / "_assets"',
            "",
        ]
        for name, payload, adir, color in ents:
            lines += _material_lines(name, payload, adir, color)
        lines.append(f"__all__ = {pprint.pformat(sorted(e[0] for e in ents))}")
        lines.append("")
        (SUB / f"{category}.py").write_text("\n".join(lines))

    mods = sorted(by_cat)
    init = [
        '"""Bundled PBR material factories, grouped by category — GENERATED.',
        "",
        "    from threejs_materials import metal   # re-exported; no `pbr_properties` needed",
        "    metal.gold_brushed()",
        '"""',
        f"from threejs_materials.pbr_properties import ({', '.join(mods)})",
        "",
        f"__all__ = {pprint.pformat(mods)}",
        "",
    ]
    (SUB / "__init__.py").write_text("\n".join(init))


def main() -> None:
    # clean legacy flat bundle, pre-subfolder top-level modules, and the subpackage
    if BUNDLED.exists():
        shutil.rmtree(BUNDLED)
    for c in CATEGORY_MODULES:
        (PKG / f"{c}.py").unlink(missing_ok=True)
    if (PKG / "_assets").exists():
        shutil.rmtree(PKG / "_assets")
    if SUB.exists():
        shutil.rmtree(SUB)
    SUB.mkdir(parents=True)
    ASSETS.mkdir(parents=True)

    entries, cat = build()
    emit(entries, cat)
    total = sum(f.stat().st_size for f in ASSETS.rglob("*") if f.is_file())
    cats = sorted(set(cat.values()))
    print(f"\nGenerated {len(entries)} materials into {len(cats)} modules: {', '.join(cats)}")
    print(f"assets total: {total / 1e6:.2f} MB @ {RES}")


if __name__ == "__main__":
    main()
