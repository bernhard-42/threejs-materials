# v1.0.5

## Fixes

- **Three glTF textures now round-trip losslessly** — `clearcoatRoughnessTexture`, `sheenRoughnessTexture`, and `anisotropyTexture` are now written to `KHR_materials_clearcoat`, `KHR_materials_sheen`, and `KHR_materials_anisotropy` on export and restored on import. Previously the corresponding `PbrMaps` fields (`clearcoat_roughness`, `sheen_roughness`, `anisotropy`) were accepted by `library.py` but silently dropped by `to_gltf()` / `from_gltf()`.
- 3 new round-trip regression tests, one per texture slot.

## Docs

- README Three.js output table now lists `clearcoatRoughnessMap`, `sheenRoughnessMap`, and `anisotropyMap` (previously shown as unsupported).

# v1.0.4

## Fixes

- **`inject_materials` crash on `.gltf` files** — `.gltf` files store binary data in a separate `.bin` file, so `binary_blob()` returns `None`. Now loads the external buffer into memory for processing, writes modified data back to `.bin`, and restores the buffer URI before saving.
- 5 new regression tests: `.gltf` ASCII injection + 4 round-trip tests (gltf→gltf, gltf→glb, glb→glb, glb→gltf) verifying values, extensions, and texture image hashes (SHA-256) survive all format combinations.

# v1.0.3

## Fixes

- **Never cache empty materials** — when baking or conversion produces empty values/textures, the result is no longer written to `~/.materialx-cache/`. Previously, a failed bake on Windows would cache an empty material permanently.
- **Logging for silent failures** — all silent fallback paths in the conversion pipeline now log warnings:
  - `extract_materials()` warns when a material has no shader nodes
  - `_process_mtlx()` warns when baked output is empty and on fallback to original
  - `to_threejs_physical()` warns when no PBR properties are produced
- 4 new regression tests for silent failure detection.

# v1.0.2

## Fixes

- **Always bake MaterialX materials** — procedural materials without textures (e.g. GPUOpen "Brass") had their colors lost because baking was skipped. The baker is now always invoked, resolving procedural node graphs to scalar values.
- **`extensionsUsed` set to `[]` instead of `None`** — fixes a pygltflib compatibility issue when saving GLB files without extensions.
- 15 regression tests covering all prior fixes (1-bit textures, transmissive materials, name collisions, array padding, no-op texture transform, procedural baking).

# v1.0.1

## Fixes

- **1-bit boolean textures** (e.g. ambientCG metalness maps) now correctly convert to 8-bit. Previously, boolean `True` became `1` instead of `255` in the packed metallicRoughness texture, making metallic materials appear non-metallic in glTF viewers.
- **`inject_materials` API change** — now accepts node indices (instead of material indices), handles deduplication and primitive-to-material assignment internally. This moves the logic from the build123d exporter into threejs-materials where it belongs.
- Added `py.typed` marker for PEP 561 type checking support and fixed all mypy errors.

# v1.0.0

## Features

- **`PbrProperties` dataclass** replaces the `Material` class with typed fields and full IDE tab completion
  - `PbrValues` dataclass for scalar PBR values (29 typed fields: `color`, `metalness`, `roughness`, `ior`, etc.)
  - `PbrMaps` dataclass for texture map references (20 typed fields: `color`, `normal`, `roughness`, etc.)
  - Snake_case field names (`normal_scale`, `sheen_color`, `specular_intensity`) with automatic camelCase mapping for Three.js/glTF output
  - Compact `__repr__` showing only non-None fields
- **Source classmethods** with IDE tab completion:
  - `PbrProperties.from_gpuopen(name, resolution)`
  - `PbrProperties.from_ambientcg(name, resolution)`
  - `PbrProperties.from_polyhaven(name, resolution)`
  - `PbrProperties.from_physicallybased(name, resolution)`
- **`normalize_uvs` flag** for UV mode control
  - `scale(u, v, fixed=True)` (default): texture density independent of object size (CAD-appropriate)
  - `scale(u, v, fixed=False)`: raw parametric UVs matching standard glTF viewer behavior
  - Materials imported from glTF default to `normalize_uvs=False`
- **Module restructuring** for clean separation of concerns:
  - `models.py` — `PbrValues` and `PbrMaps` dataclasses with name mapping
  - `library.py` — `PbrProperties` dataclass with all methods
  - `gltf.py` — glTF I/O (builder, import, export, inject)
  - `convert.py` — MaterialX baking and conversion
  - `sources/` — source loaders, cache management, `SourceResult`
  - `utils.py` — shared helpers (data URIs, image, color-space, MaterialX/OpenEXR loading)
- **`list_cache()`** prints grouped summary by default, `list_cache(as_json=True)` for tuples
- **`clear_cache()`** prints success messages
- **`requests`** moved from optional to core dependency
- **Separate `ensure_openexr()`** — MaterialX materials without EXR textures no longer require OpenEXR
- **`PbrProperties.create()`** for building materials from explicit values and texture paths
- **Cache format** uses `"values"` + `"textures"` keys (flat dicts, no nested `{"value":..., "texture":...}`)

## Fixes

- **Transmissive materials appearing opaque in glTF viewers** — PhysicallyBased source now always emits `color`, `metalness`, and `roughness` values (missing metalness defaulted to 1.0 in glTF, making dielectrics render as mirrors)
- **`KHR_materials_dispersion` without volume** — automatically add minimal `KHR_materials_volume` when dispersion is present (required by glTF spec)
- **`inject_materials` collapsing same-name materials** — materials with the same name but different values (e.g. color overrides of the same base) are no longer deduplicated into a single glTF material
- **`inject_materials` index out of range** — target materials array is padded when requested indices exceed current length
- **No-op `KHR_texture_transform`** — `scale(1, 1)` no longer adds a redundant `{scale: [1, 1]}` extension to the glTF output
- **Anisotropy tests** — fixed tests that expected `standard_surface` and `open_pbr_surface` anisotropy to be mapped (intentionally not mapped due to structural incompatibility)

# v0.5.0

## Features

- **MaterialX material conversion** — download PBR materials from four open sources, bake procedural graphs with MaterialX TextureBaker, and convert to Three.js `MeshPhysicalMaterial` JSON
- **Four material sources**:
  - [GPUOpen MaterialX Library](https://matlib.gpuopen.com/) — procedural materials, baked to textures
  - [ambientCG](https://ambientcg.com/) — texture-based materials
  - [PolyHaven](https://polyhaven.com/) — texture-based materials
  - [PhysicallyBased](https://physicallybased.info/) — parametric materials (no textures)
- **Three shader model support**: `standard_surface`, `gltf_pbr`, `open_pbr_surface`
- **Full PBR property coverage**: color, metalness, roughness, normal, specular, transmission, clearcoat, sheen, iridescence, emission, opacity, displacement, dispersion
- **glTF I/O** via pygltflib:
  - `Material.from_gltf()` / `Material.load_gltf()` — import from glTF/GLB files or GLTF2 objects
  - `material.to_gltf()` / `material.save_gltf()` — export to glTF/GLB
  - `collect_gltf_textures()` — multi-material export with shared, deduplicated textures
  - `inject_materials()` — replace materials in existing glTF/GLB files
  - KHR extensions: ior, transmission, volume, clearcoat, sheen, iridescence, anisotropy, specular, emissive strength, dispersion, texture transform
- **Blender glTF import** — load materials from Blender glTF/GLB exports with automatic texture resolution
- **Local MaterialX conversion** — `Material.from_mtlx()` for local `.mtlx` files
- **`Material.override()`** — create color and property variants without re-downloading
- **`Material.scale()`** — texture tiling via `KHR_texture_transform`
- **`Material.create()`** — build materials from explicit PBR values and texture paths
- **`interpolate_color()`** — estimate a representative sRGB color for CAD mode display
- **Persistent caching** — downloaded materials cached in `~/.materialx-cache/` as JSON + texture files
- **`Material.list_cache()`** / **`Material.clear_cache()`** — cache management
- **EXR to PNG conversion** — automatic conversion of EXR textures from MaterialX baking
- **Thread-safe baking** — serialized via `threading.Lock` for concurrent use
- **CSS color strings** — `override(color="#ff0000")` and named colors supported
- **`encode_texture_base64()`** — utility for base64 data URI encoding
