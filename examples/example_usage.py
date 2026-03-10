#!/usr/bin/env python3
"""Example usage of the MaterialX material library."""

import logging

from threejs_materials import Material

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ── 1) List available sources ────────────────────────────────────────────

print("=" * 60)
print("1) Available material sources")
print("=" * 60)
Material.list_sources()

# ── 2) Load materials from each source ───────────────────────────────────

print()
print("=" * 60)
print("2) Load a material from each source")
print("=" * 60)

examples = [
    # (loader, name, resolution, description)
    (
        Material.physicallybased,
        "Gold",
        None,
        "PhysicallyBased — parametric, no textures",
    ),
    (
        Material.ambientcg,
        "Fabric038",
        "1K",
        "ambientCG — open_pbr_surface with textures",
    ),
    (
        Material.polyhaven,
        "rusty_metal",
        "1k",
        "PolyHaven — standard_surface with textures",
    ),
    (
        Material.gpuopen,
        "Copper Brushed",
        "1K",
        "GPUOpen — standard_surface, baked procedural",
    ),
]

for loader, name, resolution, description in examples:
    print(f"\n   --- {description} ---")
    res_str = f" at {resolution}" if resolution else ""
    print(f"   Loading {loader}/{name}{res_str} ...")

    kwargs = {"resolution": resolution} if resolution else {}
    mat = loader.load(name, **kwargs)

    print(f"   id:       {mat.id}")
    print(f"   name:     {mat.name}")
    print(f"   source:   {mat.source}")
    for key, prop in mat.properties.items():
        has_val = "value" in prop
        has_tex = "texture" in prop
        parts = []
        if has_val:
            parts.append(f"value={prop['value']}")
        if has_tex:
            parts.append(f"texture=({len(prop['texture'])} chars)")
        print(f"     {key}: {', '.join(parts)}")

# ── 3) Load a local .mtlx file ──────────────────────────────────────────

print()
print("=" * 60)
print("3) Load a local .mtlx file")
print("=" * 60)

mat = Material.from_mtlx("examples/gpuo-car-paint.mtlx")
print(f"   name:     {mat.name}")
for key, prop in mat.properties.items():
    has_val = "value" in prop
    has_tex = "texture" in prop
    parts = []
    if has_val:
        parts.append(f"value={prop['value']}")
    if has_tex:
        parts.append(f"texture=({len(prop['texture'])} chars)")
    print(f"     {key}: {', '.join(parts)}")

print("\nDone.")
