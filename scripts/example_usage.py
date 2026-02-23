#!/usr/bin/env python3
"""Example usage of the MaterialX material library."""

import json
import logging

from materialx_db import list_sources, load_material, convert_local_mtlx

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ── 1) List available sources ────────────────────────────────────────────

print("=" * 60)
print("1) Available material sources")
print("=" * 60)
sources = list_sources()

# ── 2) Load materials from each source ───────────────────────────────────

print()
print("=" * 60)
print("2) Load a material from each source")
print("=" * 60)

examples = [
    # (source, name, resolution, description)
    ("physicallybased", "Gold", None, "PhysicallyBased — parametric, no textures"),
    ("ambientcg", "Fabric038", "1K", "ambientCG — open_pbr_surface with textures"),
    ("polyhaven", "rusty_metal", "1k", "PolyHaven — standard_surface with textures"),
    ("gpuopen", "Copper Brushed", "1K", "GPUOpen — standard_surface, baked procedural"),
]

for source, name, resolution, description in examples:
    print(f"\n   --- {description} ---")
    res_str = f" at {resolution}" if resolution else ""
    print(f"   Loading {source}/{name}{res_str} ...")

    mat = load_material(source=source, name=name, resolution=resolution)

    print(f"   id:       {mat['id']}")
    print(f"   name:     {mat['name']}")
    print(f"   source:   {mat['source']}")
    props = mat["properties"]
    for key, prop in props.items():
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

mat = convert_local_mtlx("examples/gpuo-car-paint.mtlx")
print(f"   name:     {mat['name']}")
for key, prop in mat["properties"].items():
    has_val = "value" in prop
    has_tex = "texture" in prop
    parts = []
    if has_val:
        parts.append(f"value={prop['value']}")
    if has_tex:
        parts.append(f"texture=({len(prop['texture'])} chars)")
    print(f"     {key}: {', '.join(parts)}")

print("\nDone.")
