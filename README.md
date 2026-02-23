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
            material.json           # Three.js output (properties + base64 textures)
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
        __init__.py                 # Exports MaterialLibrary, convert_local_mtlx, encode_texture_base64
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

When `get_material()` or `convert_local_mtlx()` is called:

1. **Download** (DB-backed only) — source-specific: fetch zip (ambientCG, GPUOpen), individual files (PolyHaven), or generate from parameters (PhysicallyBased)
2. **Bake** — for texture-based materials, run MaterialX `TextureBaker` (GLSL preferred, MSL fallback on macOS) to flatten procedural graphs into texture images
3. **Fallback merge** — if the baker can't handle certain textures (e.g. EXR inputs, `open_pbr_surface` shader), merge missing textures from the original document with automatic format substitution (EXR to JPG/PNG)
4. **EXR to PNG** — convert any remaining EXR textures to 8-bit PNG
5. **Extract** — walk the MaterialX document to map shader inputs to `MeshPhysicalMaterial` properties, base64-encoding textures inline
6. **Cache** (DB-backed only) — write `material.json` to disk; subsequent calls return it instantly

### Output format

Each property in the output carries a `value`, a base64-encoded `texture`, or both:

```json
{
    "id": "gpuo:Copper_Brushed",
    "name": "Copper Brushed",
    "source": "gpuopen",
    "category": "metal",
    "properties": {
        "color": {
            "value": [0.944, 0.776, 0.373],
            "texture": "data:image/png;base64,..."
        },
        "metalness": {
            "value": 1.0
        },
        "roughness": {
            "value": 0.5,
            "texture": "data:image/png;base64,..."
        },
        "normal": {
            "texture": "data:image/png;base64,..."
        },
        "specularIntensity": {
            "value": 1.0
        },
        "specularColor": {
            "value": [1.0, 1.0, 1.0]
        },
        "ior": {
            "value": 1.5
        }
    }
}
```

- `value` — scalar or array, maps directly to a `MeshPhysicalMaterial` property
- `texture` — base64 data URI, ready to load as a Three.js `Texture`
- Properties with only `texture` (e.g. `normal`) have no meaningful scalar fallback
- Properties with only `value` (e.g. `ior`) are purely parametric

Parametric materials (PhysicallyBased) have values only:

```json
{
    "id": "pb:Gold",
    "name": "Gold",
    "source": "physicallybased",
    "category": "metal",
    "properties": {
        "color": {"value": [1.059, 0.773, 0.307]},
        "metalness": {"value": 1.0},
        "roughness": {"value": 0.0},
        "ior": {"value": 1.5}
    }
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

- `dict` — material with `id`, `name`, `source`, `category`, `properties`
- `list[str]` — available resolution labels (when `resolution` is omitted and multiple variants exist)

**Single-variant materials** (PhysicallyBased) don't need a resolution:

```python
mat = lib.get_material("pb:Gold")
print(mat["properties"]["color"])
# {'value': [1.059, 0.773, 0.307]}
```

**Multi-variant materials** require a resolution:

```python
lib.get_material("acg:Fabric038")
# -> ["1K-JPG", "1K-PNG", "2K-JPG", "2K-PNG", "4K-JPG", ...]

mat = lib.get_material("acg:Fabric038", resolution="1K-JPG")
print(mat["properties"]["roughness"])
# {'value': 0.5, 'texture': 'data:image/png;base64,...'}
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

#### `load_local_material(mtlx_file) -> dict`

Convert a local `.mtlx` file to Three.js JSON. Does not use the database. See `convert_local_mtlx()` below.

```python
mat = lib.load_local_material("path/to/material.mtlx")
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

### `convert_local_mtlx(mtlx_file) -> dict`

Convert a local `.mtlx` file to Three.js `MeshPhysicalMaterial` JSON without needing the database. Texture paths in the `.mtlx` are resolved relative to the file's location. Raises `FileNotFoundError` if referenced textures are missing.

```python
from materialx_db import convert_local_mtlx

# Textured material (textures/ folder must exist next to the .mtlx)
mat = convert_local_mtlx("path/to/car_paint.mtlx")
print(mat["properties"]["roughness"])
# {'value': 0.5, 'texture': 'data:image/png;base64,...'}

# Parametric material (no textures needed)
mat = convert_local_mtlx("path/to/gold.mtlx")
print(mat["properties"]["color"])
# {'value': [1.059, 0.773, 0.307]}
```

### `encode_texture_base64(file_path) -> str`

Encode an image file as a base64 data URI. Automatically converts EXR to PNG. Useful for building material JSON from custom parameters and images.

```python
from materialx_db import encode_texture_base64

# Encode a texture file
data_uri = encode_texture_base64("textures/my_normal.png")
# -> 'data:image/png;base64,iVBORw0KGgo...'

# Build a custom material dict
material = {
    "id": "custom-material",
    "name": "My Material",
    "source": "local",
    "category": "metal",
    "properties": {
        "color": {"value": [0.8, 0.2, 0.1]},
        "metalness": {"value": 1.0},
        "roughness": {"value": 0.3},
        "normal": {"texture": encode_texture_base64("textures/normal.png")},
    }
}
```

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
print(mat["properties"]["color"])
# {'value': [1.059, 0.773, 0.307]}
print(mat["properties"]["metalness"])
# {'value': 1.0}
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
print(mat["properties"]["roughness"])
# {'value': 0.5, 'texture': 'data:image/png;base64,...'}
print(mat["properties"]["normal"])
# {'texture': 'data:image/png;base64,...'}

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
print(mat["properties"]["metalness"])
# {'value': 1.0}
print(mat["properties"]["color"])
# {'value': [0.944, 0.776, 0.373], 'texture': 'data:image/png;base64,...'}
```

### Load a local .mtlx file

Convert a local MaterialX file without using the database:

```python
from materialx_db import convert_local_mtlx

mat = convert_local_mtlx("examples/gpuo-car-paint.mtlx")
print(mat["properties"]["clearcoat"])
# {'value': 1.0}
print(mat["properties"]["roughness"])
# {'value': 0.5, 'texture': 'data:image/png;base64,...'}
```

### Build a custom material from images

Use `encode_texture_base64` to create material JSON from your own textures:

```python
from materialx_db import encode_texture_base64

material = {
    "id": "my-material",
    "name": "My Material",
    "source": "local",
    "category": "metal",
    "properties": {
        "color": {
            "value": [0.9, 0.85, 0.7],
            "texture": encode_texture_base64("textures/diffuse.png"),
        },
        "roughness": {
            "value": 0.4,
            "texture": encode_texture_base64("textures/roughness.png"),
        },
        "normal": {
            "texture": encode_texture_base64("textures/normal.png"),
        },
        "metalness": {"value": 1.0},
        "ior": {"value": 1.5},
    }
}
```

### Send to a Three.js viewer

The output JSON is designed for direct use with Three.js:

```python
import json

mat = lib.get_material("gpuo:Copper_Brushed", resolution="1k 8b")
json_str = json.dumps(mat)

# Send json_str to your Three.js frontend via WebSocket, REST API, etc.
```

On the JavaScript side:

```javascript
const data = JSON.parse(jsonStr);
const material = new THREE.MeshPhysicalMaterial();

for (const [key, prop] of Object.entries(data.properties)) {
    if (prop.texture) {
        material[key] = new THREE.TextureLoader().load(prop.texture);
    }
    if (prop.value !== undefined) {
        // Set scalar/array values (color, roughness, metalness, etc.)
        // For color properties, convert array to THREE.Color
        if (Array.isArray(prop.value) && prop.value.length === 3) {
            material[key] = new THREE.Color(...prop.value);
        } else {
            material[key] = prop.value;
        }
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
