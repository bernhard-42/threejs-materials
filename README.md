# materialx-db

A Python library that downloads PBR materials on demand from four open sources and converts them into [Three.js `MeshPhysicalMaterial`](https://threejs.org/docs/#api/en/materials/MeshPhysicalMaterial)-compatible JSON with base64-encoded textures.

## Sources

| Source | Type | Shader model |
|---|---|---|
| [ambientCG](https://ambientcg.com/) | Texture-based | `open_pbr_surface` |
| [GPUOpen MaterialX Library](https://matlib.gpuopen.com/) | Procedural (baked) | `standard_surface` |
| [PolyHaven](https://polyhaven.com/) | Texture-based | `standard_surface` |
| [PhysicallyBased](https://physicallybased.info/) | Parametric (no textures) | `open_pbr_surface` |

Browse materials on the source websites, then load them by name.

## Installation

```bash
cd materialx-db
uv sync        # or: pip install -e .
```

### Dependencies

- `materialx >= 1.39.4` — MaterialX SDK with TextureBaker
- `requests >= 2.31.0` — HTTP downloads
- `openexr >= 3.3` — EXR to PNG conversion

## API

### `load_material(source, name, resolution=None) -> dict`

Download, convert, and cache a material.

```python
from materialx_db import load_material

mat = load_material(source="gpuopen", name="Car Paint", resolution="1K")
mat = load_material(source="ambientcg", name="Onyx015", resolution="1K")
mat = load_material(source="polyhaven", name="plank_flooring_04", resolution="1K")
mat = load_material(source="physicallybased", name="Titanium")
```

The first call downloads and converts the material (takes a few seconds). Subsequent calls return the cached JSON instantly from `~/.materialx-cache/`.

#### Resolution

Pass a normalized resolution (`1K`, `2K`, `4K`, `8K` — case-insensitive). Each source maps it to its native format:

| Input | GPUOpen | ambientCG | PolyHaven | PhysicallyBased |
|---|---|---|---|---|
| 1K | 1k 8b | 1K-PNG | 1k | n/a |
| 2K | 2k 8b | 2K-PNG | 2k | n/a |
| 4K | 4k 8b | 4K-PNG | 4k | n/a |
| 8K | — | 8K-PNG | 8k | n/a |

PhysicallyBased materials are parametric — no resolution needed (and not accepted).

### `list_sources() -> dict`

Print available sources with clickable URLs.

```python
from materialx_db import list_sources

list_sources()
# Material sources:
#   ambientcg: https://ambientcg.com/list?type=material
#   gpuopen: https://matlib.gpuopen.com/main/materials/all
#   polyhaven: https://polyhaven.com/textures
#   physicallybased: https://physicallybased.info/
```

### `convert_local_mtlx(mtlx_file) -> dict`

Convert a local `.mtlx` file without downloading anything.

```python
from materialx_db import convert_local_mtlx

mat = convert_local_mtlx("examples/gpuo-car-paint.mtlx")
```

Texture paths in the `.mtlx` are resolved relative to the file's location.

### `encode_texture_base64(file_path) -> str`

Encode an image file as a base64 data URI. Automatically converts EXR to PNG.

```python
from materialx_db import encode_texture_base64

data_uri = encode_texture_base64("textures/normal.png")
# -> 'data:image/png;base64,iVBORw0KGgo...'
```

## Output format

Each property carries a `value`, a base64-encoded `texture`, or both:

```json
{
    "id": "Car Paint",
    "name": "Car Paint",
    "source": "gpuopen",
    "properties": {
        "color": {
            "value": [0.944, 0.776, 0.373],
            "texture": "data:image/png;base64,..."
        },
        "metalness": { "value": 1.0 },
        "roughness": { "value": 0.5, "texture": "data:image/png;base64,..." },
        "normal": { "texture": "data:image/png;base64,..." },
        "ior": { "value": 1.5 }
    }
}
```

Parametric materials (PhysicallyBased) have values only:

```json
{
    "id": "Gold",
    "name": "Gold",
    "source": "physicallybased",
    "properties": {
        "color": { "value": [1.059, 0.773, 0.307] },
        "metalness": { "value": 1.0 },
        "roughness": { "value": 0.0 },
        "ior": { "value": 1.5 }
    }
}
```

## Cache

Converted materials are cached as flat JSON files in `~/.materialx-cache/`:

```
~/.materialx-cache/
    gpuopen_car_paint_1k_8b.json
    ambientcg_onyx015_1k-png.json
    polyhaven_plank_flooring_04_1k.json
    physicallybased_titanium.json
```

To force re-conversion, delete the cached file and call `load_material()` again.

## Conversion pipeline

When `load_material()` or `convert_local_mtlx()` is called:

1. **Download** — source-specific: fetch ZIP (ambientCG, GPUOpen), individual files (PolyHaven), or generate from parameters (PhysicallyBased)
2. **Bake** — run MaterialX `TextureBaker` (GLSL preferred, MSL fallback on macOS) to flatten procedural graphs into texture images
3. **Fallback merge** — if the baker can't handle certain textures, merge from the original document
4. **EXR to PNG** — convert any EXR textures to 8-bit PNG
5. **Extract** — map shader inputs to `MeshPhysicalMaterial` properties with base64-encoded textures
6. **Cache** — write JSON to `~/.materialx-cache/`

## Three.js usage

```javascript
const data = JSON.parse(jsonStr);
const material = new THREE.MeshPhysicalMaterial();

for (const [key, prop] of Object.entries(data.properties)) {
    if (prop.texture) {
        material[key] = new THREE.TextureLoader().load(prop.texture);
    }
    if (prop.value !== undefined) {
        if (Array.isArray(prop.value) && prop.value.length === 3) {
            material[key] = new THREE.Color(...prop.value);
        } else {
            material[key] = prop.value;
        }
    }
}
```
