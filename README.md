# materialx-db

A Python library that catalogs 3,200+ PBR materials from four open sources into a local SQLite database and converts them on demand into [Three.js `MeshPhysicalMaterial`](https://threejs.org/docs/#api/en/materials/MeshPhysicalMaterial)-compatible JSON with base64-encoded textures.

## Goal

Provide a single, unified API to **browse, search, and retrieve** production-ready PBR materials from:

| Source                                                   | Materials | Type                     | Shader model       |
| -------------------------------------------------------- | --------- | ------------------------ | ------------------ |
| [ambientCG](https://ambientcg.com/)                      | ~1,960    | Texture-based            | `open_pbr_surface` |
| [GPUOpen MaterialX Library](https://matlib.gpuopen.com/) | ~454      | Procedural (baked)       | `standard_surface` |
| [PolyHaven](https://polyhaven.com/)                      | ~742      | Texture-based            | `standard_surface` |
| [PhysicallyBased](https://physicallybased.info/)         | ~86       | Parametric (no textures) | `open_pbr_surface` |

The output JSON is self-contained and ready for direct consumption by a Three.js viewer or any renderer that understands `MeshPhysicalMaterial` properties.

## Design

### Principles

- **Catalog first, download later.** Building the DB only fetches metadata (names, categories, download URLs, resolutions) via source APIs. No material files are downloaded until you actually request one.
- **Lazy loading with caching.** The first call to `get_material()` downloads, bakes, and converts. Subsequent calls return the cached result instantly.
- **Minimize API calls.** GPUOpen resolution labels are fetched on demand per material (typically 6 API calls), not during the catalog build (which would require ~2,700 calls). Labels are cached in the DB after the first fetch.
- **EXR to PNG conversion.** PolyHaven ships some textures as EXR (HDR float). Since browsers and Three.js prefer standard formats, EXR files are automatically converted to 8-bit PNG using OpenEXR.

### Architecture

```
~/.materialx/
    materials.db                    # SQLite catalog (~3,200 materials, ~20,800 variants)
    baked/
        acg/Fabric038/              # ambientCG material
            material.mtlx           # source MaterialX document
            material.baked.mtlx     # baked MaterialX document (if baker succeeded)
            material.json           # Three.js output (params + base64 textures)
            textures/               # texture images
        gpuo/Copper_Brushed/        # GPUOpen material
            ...
        ph/rusty_metal/             # PolyHaven material
            ...
        pb/Gold/                    # PhysicallyBased material (no textures/)
            ...
```

### Project structure

```
materialx-db/
    pyproject.toml
    scripts/
        build_db.py                 # CLI: rebuild the catalog DB
        example_usage.py            # Tutorial script
    src/materialx_db/
        __init__.py                 # Exports MaterialLibrary
        db.py                       # SQLite schema + insert helpers
        categories.py               # Canonical category mapping
        convert.py                  # Download + bake + extract pipeline
        library.py                  # Public API: MaterialLibrary class
        sources/
            ambientcg.py            # ambientCG catalog fetcher
            gpuopen.py              # GPUOpen catalog fetcher
            polyhaven.py            # PolyHaven catalog fetcher
            physicallybased.py      # PhysicallyBased catalog fetcher
```

### SQLite schema

**`materials`** table — one row per material:

| Column          | Type    | Description                                                                      |
| --------------- | ------- | -------------------------------------------------------------------------------- |
| `id`            | TEXT PK | Prefixed ID: `acg:Fabric038`, `gpuo:Copper_Brushed`, `ph:rusty_metal`, `pb:Gold` |
| `source`        | TEXT    | `ambientcg`, `gpuopen`, `polyhaven`, `physicallybased`                           |
| `name`          | TEXT    | Human-readable name                                                              |
| `category`      | TEXT    | Canonical group (see below)                                                      |
| `has_textures`  | INTEGER | 1 = image-based, 0 = parametric only                                             |
| `shader_model`  | TEXT    | `standard_surface`, `open_pbr_surface`, etc. (set after first conversion)        |
| `thumbnail_url` | TEXT    | Preview image URL from source                                                    |
| `tags`          | TEXT    | JSON array of source tags                                                        |

**`material_variants`** table — one row per resolution/package:

| Column          | Type    | Description                                                 |
| --------------- | ------- | ----------------------------------------------------------- |
| `material_id`   | TEXT FK | References `materials.id`                                   |
| `resolution`    | TEXT    | `1K-JPG`, `2K-PNG`, `1k`, `4k`, `1k 8b`, `parametric`, etc. |
| `download_url`  | TEXT    | Direct download URL                                         |
| `download_meta` | TEXT    | JSON with source-specific download info                     |
| `file_size`     | INTEGER | Size in bytes (when known)                                  |

### Categories

15 canonical groups, mapped from each source's taxonomy:

`metal` `wood` `plastic` `brick` `stone` `concrete` `fabric` `leather` `glass` `ceramic` `organic` `terrain` `asphalt` `plaster` `other`

- **PhysicallyBased** — mapped from its `category` field
- **PolyHaven** — mapped from its `categories` list (first match)
- **ambientCG / GPUOpen** — inferred from material name via keyword matching

### Conversion pipeline

When `get_material()` is called for the first time on a material:

1. **Download** — source-specific: fetch zip (ambientCG, GPUOpen), individual files (PolyHaven), or generate from parameters (PhysicallyBased)
2. **Bake** — for texture-based materials, run MaterialX `TextureBaker` (GLSL preferred, MSL fallback on macOS) to flatten procedural graphs into texture images
3. **Fallback merge** — if the baker can't handle certain textures (e.g. EXR inputs, `open_pbr_surface` shader), merge missing textures from the original document with automatic format substitution (EXR to JPG/PNG)
4. **EXR to PNG** — convert any remaining EXR textures to 8-bit PNG
5. **Extract** — walk the MaterialX document to map shader inputs to `MeshPhysicalMaterial` properties
6. **Encode** — base64-encode all texture images as data URIs
7. **Cache** — write `material.json` to disk; subsequent calls return it instantly

### Output format

```json
{
    "id": "gpuo:Copper_Brushed",
    "name": "Copper Brushed",
    "source": "gpuopen",
    "category": "metal",
    "params": {
        "map": "textures/Copper_Brushed_standard_surface_base_color.png",
        "metalness": 1.0,
        "roughness": 0.5,
        "roughnessMap": "textures/Copper_Brushed_standard_surface_specular_roughness.png",
        "normalMap": "textures/Copper_Brushed_standard_surface_normal.png",
        "specularIntensity": 1.0,
        "specularColor": [1.0, 1.0, 1.0],
        "ior": 1.5
    },
    "textures": {
        "textures/Copper_Brushed_standard_surface_base_color.png": "data:image/png;base64,...",
        "textures/Copper_Brushed_standard_surface_specular_roughness.png": "data:image/png;base64,...",
        "textures/Copper_Brushed_standard_surface_normal.png": "data:image/png;base64,..."
    }
}
```

The `params` keys are Three.js `MeshPhysicalMaterial` property names. The `textures` dict maps each texture path referenced in `params` to its base64-encoded data URI.

Parametric materials (PhysicallyBased) have scalar `params` and an empty `textures` dict:

```json
{
    "id": "pb:Gold",
    "name": "Gold",
    "source": "physicallybased",
    "category": "metal",
    "params": {
        "color": [1.059, 0.773, 0.307],
        "metalness": 1.0,
        "roughness": 0.0,
        "ior": 1.5
    },
    "textures": {}
}
```

---

## API

### `MaterialLibrary`

```python
from materialx_db import MaterialLibrary

lib = MaterialLibrary()          # uses ~/.materialx/materials.db
lib = MaterialLibrary("my.db")   # custom DB path
```

#### `list_categories() -> list[str]`

Return sorted list of all categories present in the DB.

```python
lib.list_categories()
# ['asphalt', 'brick', 'ceramic', 'concrete', 'fabric', 'glass',
#  'leather', 'metal', 'organic', 'other', 'plaster', 'plastic',
#  'stone', 'terrain', 'wood']
```

#### `list_materials(*, category, has_textures, source, name) -> list[MaterialInfo]`

Query materials with optional filters. All parameters are keyword-only and optional. `name` does case-insensitive substring matching.

```python
lib.list_materials(category="metal")                        # all metals
lib.list_materials(source="gpuopen")                        # all GPUOpen materials
lib.list_materials(source="polyhaven", category="stone")    # PolyHaven stones
lib.list_materials(has_textures=False)                      # parametric only
lib.list_materials(name="copper")                           # name contains "copper"
```

Returns a list of `MaterialInfo` dataclasses:

```python
@dataclass
class MaterialInfo:
    id: str                        # "gpuo:Copper_Brushed"
    source: str                    # "gpuopen"
    name: str                      # "Copper Brushed"
    category: str                  # "metal"
    has_textures: bool             # True
    resolutions: list[str]         # ["pkg_0", "pkg_1", ...] or ["1K-JPG", "2K-JPG", ...]
    thumbnail_url: str | None      # preview image URL
```

#### `search(query) -> list[MaterialInfo]`

Search materials by name or tags (case-insensitive substring).

```python
lib.search("brass")    # matches name or tags containing "brass"
```

#### `get_material(material_id, resolution=None) -> dict | list[str]`

Get a material as a Three.js-compatible dict. Downloads, bakes, and converts on first call; returns cached result on subsequent calls.

**Return type depends on context:**

- `dict` — material JSON with `id`, `name`, `source`, `category`, `params`, `textures`
- `list[str]` — available resolution labels (when `resolution` is omitted and multiple variants exist)

**Single-variant materials** (PhysicallyBased) don't need a resolution:

```python
mat = lib.get_material("pb:Gold")
# -> {"id": "pb:Gold", "params": {"color": [...], "metalness": 1.0, ...}, "textures": {}}
```

**Multi-variant materials** require a resolution:

```python
lib.get_material("acg:Fabric038")
# -> ["1K-JPG", "1K-PNG", "2K-JPG", "2K-PNG", "4K-JPG", ...]

mat = lib.get_material("acg:Fabric038", resolution="1K-JPG")
# -> {"id": "acg:Fabric038", "params": {...}, "textures": {...}}
```

**GPUOpen staged resolution flow:**

GPUOpen packages are stored with opaque IDs (`pkg_0`, `pkg_1`, ...) during the catalog build. Human-readable labels like `"1k 8b"` or `"4k 16b"` are fetched on demand from the GPUOpen API when you first request a material — only for that material's packages (typically 6 API calls), not all 454 materials.

```python
# Step 1: fetch labels (triggers ~6 API calls, cached in DB)
resolutions = lib.get_material("gpuo:Copper_Brushed")
# -> ["1k 8b", "1k 16b", "2k 8b", "2k 16b", "4k 8b", "4k 16b"]

# Step 2: download and convert at chosen resolution
mat = lib.get_material("gpuo:Copper_Brushed", resolution="1k 8b")

# Shortcut: if you know the label, skip step 1
mat = lib.get_material("gpuo:Copper_Brushed", resolution="2k 8b")
```

#### `rebuild(sources=None) -> dict[str, int]`

Rebuild the catalog DB from scratch. Drops all tables and re-fetches metadata from each source's API. No material files are downloaded.

```python
lib.rebuild()
# -> {"ambientcg": 1962, "gpuopen": 454, "polyhaven": 742, "physicallybased": 86}

lib.rebuild(sources=["ambientcg", "polyhaven"])  # rebuild specific sources only
```

**Warning:** `rebuild()` drops ALL tables before recreating. If you pass `sources=["gpuopen"]`, you will lose the other sources. Always rebuild all sources together, or use the build script.

#### `close()`

Close the database connection.

---

## Installation

```bash
# Clone and install with uv
cd materialx-db
uv sync

# Or pip
pip install -e .
```

### Dependencies

- `materialx >= 1.39.4` — MaterialX SDK with TextureBaker
- `materialxmaterials >= 1.39.4` — loader classes for all four sources
- `requests >= 2.31.0` — HTTP downloads
- `openexr >= 3.3` — EXR to PNG conversion

### Build the catalog

```bash
uv run python scripts/build_db.py
```

This fetches metadata from all four source APIs and populates `~/.materialx/materials.db`. Takes about 2-3 minutes. No material files are downloaded.

```
$ uv run python scripts/build_db.py
12:00:01 INFO build_db: Dropping and recreating tables...
12:00:01 INFO build_db: Fetching ambientcg...
12:00:15 INFO build_db:   ambientcg: 1962 materials (14.2s)
12:00:15 INFO build_db: Fetching gpuopen...
12:01:02 INFO build_db:   gpuopen: 454 materials (46.8s)
12:01:02 INFO build_db: Fetching polyhaven...
12:01:30 INFO build_db:   polyhaven: 742 materials (28.1s)
12:01:30 INFO build_db: Fetching physicallybased...
12:01:31 INFO build_db:   physicallybased: 86 materials (0.8s)
12:01:31 INFO build_db: Total: 3244 materials
12:01:31 INFO build_db:   Total variants: 20876
```

---

## Tutorial

Full working example in `scripts/example_usage.py`.

### Browse the catalog

```python
from materialx_db import MaterialLibrary

lib = MaterialLibrary()

# List all categories
categories = lib.list_categories()
# ['asphalt', 'brick', 'ceramic', 'concrete', 'fabric', ...]

# List metals from all sources
metals = lib.list_materials(category="metal")
print(f"{len(metals)} metals")  # 289 metals

# List GPUOpen materials
gpuopen = lib.list_materials(source="gpuopen")
print(f"{len(gpuopen)} GPUOpen materials")  # 454

# Combine filters: PolyHaven stones
ph_stone = lib.list_materials(source="polyhaven", category="stone")
print(f"{len(ph_stone)} PolyHaven stone materials")  # 105

# Search by name
results = lib.search("copper")
for m in results:
    print(f"  {m.id}: {m.name} ({m.source})")
```

### Load a parametric material (instant)

PhysicallyBased materials are parametric — no download or baking needed:

```python
mat = lib.get_material("pb:Gold")
print(mat["params"])
# {'color': [1.059, 0.773, 0.307], 'metalness': 1.0, 'roughness': 0.0, 'ior': 1.5}
print(mat["textures"])
# {}
```

### Load a texture-based material

Materials with textures are downloaded and converted on first access:

```python
# Check available resolutions
resolutions = lib.get_material("acg:Fabric038")
print(resolutions)
# ['1K-JPG', '1K-PNG', '2K-JPG', '2K-PNG', '4K-JPG', '4K-PNG', '8K-JPG', '8K-PNG']

# Download and convert at 1K-JPG (first call takes a few seconds)
mat = lib.get_material("acg:Fabric038", resolution="1K-JPG")
print(mat["params"])
# {'map': 'textures/Fabric038_1K-JPG_Color.jpg', 'metalness': 0.0, 'roughness': 0.5,
#  'roughnessMap': 'textures/Fabric038_1K-JPG_Roughness.jpg',
#  'normalMap': 'textures/Fabric038_1K-JPG_NormalGL.jpg', 'ior': 1.5}
print(list(mat["textures"].keys()))
# ['textures/Fabric038_1K-JPG_Color.jpg', 'textures/Fabric038_1K-JPG_Roughness.jpg',
#  'textures/Fabric038_1K-JPG_NormalGL.jpg']

# Second call returns cached result instantly
mat = lib.get_material("acg:Fabric038", resolution="1K-JPG")
```

### Load a GPUOpen procedural material

GPUOpen materials are procedural — the TextureBaker renders them into texture images:

```python
# Step 1: fetch resolution labels (API call, cached in DB)
resolutions = lib.get_material("gpuo:Copper_Brushed")
print(resolutions)
# ['1k 8b', '1k 16b', '2k 8b', '2k 16b', '4k 8b', '4k 16b']

# Step 2: download and bake
mat = lib.get_material("gpuo:Copper_Brushed", resolution="1k 8b")
print(mat["params"]["metalness"])  # 1.0
print(list(mat["textures"].keys()))
# ['textures/Copper_Brushed_standard_surface_base_color.png',
#  'textures/Copper_Brushed_standard_surface_specular_roughness.png',
#  'textures/Copper_Brushed_standard_surface_normal.png']
```

### Load a PolyHaven material

PolyHaven materials may include EXR textures, which are automatically converted to PNG:

```python
mat = lib.get_material("ph:rusty_metal", resolution="1k")
print(mat["params"])
# {'map': 'textures/rusty_metal_standard_surface_base_color.png', 'metalness': 0.0,
#  'roughness': 0.301961, 'roughnessMap': 'textures/rusty_metal_rough_1k.jpg',
#  'normalMap': 'textures/rusty_metal_nor_gl_1k.png', ...}
```

### Send to a Three.js viewer

The output JSON is designed for direct use with Three.js:

```python
import json

mat = lib.get_material("gpuo:Copper_Brushed", resolution="1k 8b")

# The params dict maps directly to MeshPhysicalMaterial properties
# The textures dict contains base64 data URIs ready for TextureLoader
json_str = json.dumps(mat)

# Send json_str to your Three.js frontend via WebSocket, REST API, etc.
```

On the JavaScript side:

```javascript
const data = JSON.parse(jsonStr);
const material = new THREE.MeshPhysicalMaterial(data.params);

// Load base64 textures
for (const [key, param] of Object.entries(data.params)) {
    if (typeof param === "string" && param.startsWith("textures/")) {
        const dataUri = data.textures[param];
        const texture = new THREE.TextureLoader().load(dataUri);
        material[key] = texture;
    }
}
```

### Rebuild the database

From Python:

```python
lib = MaterialLibrary()
counts = lib.rebuild()
print(counts)
# {'ambientcg': 1962, 'gpuopen': 454, 'polyhaven': 742, 'physicallybased': 86}
```

From the command line:

```bash
uv run python scripts/build_db.py
uv run python scripts/build_db.py --sources ambientcg,polyhaven
```

### Force re-conversion of a material

Delete the cached output directory and call `get_material()` again:

```python
import shutil
shutil.rmtree("~/.materialx/baked/gpuo/Copper_Brushed/")
mat = lib.get_material("gpuo:Copper_Brushed", resolution="1k 8b")
```
