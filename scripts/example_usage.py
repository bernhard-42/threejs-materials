#!/usr/bin/env python3
"""Example usage of the MaterialX material library."""

import json
import logging

from materialx_lib import MaterialLibrary

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

lib = MaterialLibrary()

# ── 1) List materials by category ─────────────────────────────────────────

print("=" * 60)
print("1) Materials in category 'metal'")
print("=" * 60)
metals = lib.list_materials(category="metal")
print(f"   Found {len(metals)} metals")
for m in metals[:8]:
    print(f"   {m.id:40s} {m.name}")
if len(metals) > 8:
    print(f"   ... and {len(metals) - 8} more")

# ── 2) List materials by source ───────────────────────────────────────────

print()
print("=" * 60)
print("2) Materials from GPUOpen")
print("=" * 60)
gpuopen = lib.list_materials(source="gpuopen")
print(f"   Found {len(gpuopen)} GPUOpen materials")
for m in gpuopen[:8]:
    print(f"   {m.id:40s} {m.name}")
if len(gpuopen) > 8:
    print(f"   ... and {len(gpuopen) - 8} more")

# ── 3) List materials by source + category ────────────────────────────────

print()
print("=" * 60)
print("3) PolyHaven materials in category 'stone'")
print("=" * 60)
ph_stone = lib.list_materials(source="polyhaven", category="stone")
print(f"   Found {len(ph_stone)} PolyHaven stone materials")
for m in ph_stone[:8]:
    print(f"   {m.id:40s} {m.name}")
if len(ph_stone) > 8:
    print(f"   ... and {len(ph_stone) - 8} more")

# ── 4) Load one material from each source ─────────────────────────────────

print()
print("=" * 60)
print("4) Load a material from each source")
print("=" * 60)

examples = [
    # (material_id, resolution, description)
    ("pb:Gold", None, "PhysicallyBased — parametric, no textures"),
    ("acg:Fabric038", "1K-JPG", "ambientCG — open_pbr_surface with textures"),
    ("ph:rusty_metal", "1k", "PolyHaven — standard_surface with textures"),
    ("gpuo:Copper_Brushed", "1k 8b", "GPUOpen — standard_surface, baked procedural"),
]

for mat_id, resolution, description in examples:
    print(f"\n   --- {description} ---")
    print(f"   Loading {mat_id}" + (f" at {resolution}" if resolution else "") + " ...")

    mat = lib.get_material(mat_id, resolution=resolution)

    # GPUOpen staged flow: first call without resolution returns label list
    if isinstance(mat, list):
        print(f"   Available resolutions: {mat}")
        mat = lib.get_material(mat_id, resolution=resolution)

    print(f"   id:       {mat['id']}")
    print(f"   name:     {mat['name']}")
    print(f"   source:   {mat['source']}")
    print(f"   category: {mat['category']}")
    print(f"   params:   {mat['params']}")
    if mat["textures"]:
        print(f"   textures: {len(mat['textures'])} files")
        for key in mat["textures"]:
            b64 = mat["textures"][key]
            print(f"             {key} ({len(b64)} chars)")
    else:
        print(f"   textures: none (parametric)")

    # Dump full JSON to see the structure (truncate base64 for readability)
    compact = {**mat, "textures": {k: v[:60] + "..." for k, v in mat["textures"].items()}}
    print(f"   JSON preview: {json.dumps(compact, indent=2)[:300]}...")

lib.close()
print("\nDone.")
