# materialx-db

A Python library that downloads PBR materials on demand from four open sources and converts them into [Three.js `MeshPhysicalMaterial`](https://threejs.org/docs/#api/en/materials/MeshPhysicalMaterial)-compatible JSON with base64-encoded textures.

## Sources

| Source                                                   | Type                     | Shader model       |
| -------------------------------------------------------- | ------------------------ | ------------------ |
| [ambientCG](https://ambientcg.com/)                      | Texture-based            | `open_pbr_surface` |
| [GPUOpen MaterialX Library](https://matlib.gpuopen.com/) | Procedural (baked)       | `standard_surface` |
| [PolyHaven](https://polyhaven.com/)                      | Texture-based            | `standard_surface` |
| [PhysicallyBased](https://physicallybased.info/)         | Parametric (no textures) | `open_pbr_surface` |

Browse materials on the source websites, then load them by name.

<table>
<tr>
<td align="center"><strong>CAD mode</strong> (<code>interpolate_color()</code>)</td>
<td align="center"><strong>Studio mode</strong> (full PBR)</td>
</tr>
<tr>
<td><img src="screenshots/CAD mode.png" width="400"></td>
<td><img src="screenshots/Studio mode.png" width="400"></td>
</tr>
</table>

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

### `Material.{source}.load(name, resolution="1K") -> Material`

Download, convert, and cache a material.

```python
from materialx_db import Material

mat = Material.gpuopen.load("Car Paint", resolution="1K")
mat = Material.ambientcg.load("Onyx015", resolution="1K")
mat = Material.polyhaven.load("plank_flooring_04", resolution="1K")
mat = Material.physicallybased.load("Titanium")
```

The first call downloads and converts the material (takes a few seconds). Subsequent calls return the cached JSON instantly from `~/.materialx-cache/`.

#### Resolution

Pass a normalized resolution (`1K`, `2K`, `4K`, `8K` — case-insensitive). Each source maps it to its native format:

| Input | GPUOpen | ambientCG | PolyHaven | PhysicallyBased |
| ----- | ------- | --------- | --------- | --------------- |
| 1K    | 1k 8b   | 1K-PNG    | 1k        | n/a             |
| 2K    | 2k 8b   | 2K-PNG    | 2k        | n/a             |
| 4K    | 4k 8b   | 4K-PNG    | 4k        | n/a             |
| 8K    | —       | 8K-PNG    | 8k        | n/a             |

PhysicallyBased materials are parametric — no resolution needed (and not accepted).

### `Material.list_sources()`

Print available sources with clickable URLs.

```python
from materialx_db import Material

Material.list_sources()
# Material sources:
#   Material.ambientCG        https://ambientcg.com/list?type=material
#   Material.GPUOpen          https://matlib.gpuopen.com/main/materials/all
#   Material.PolyHaven        https://polyhaven.com/textures
#   Material.PhysicallyBased  https://physicallybased.info/
```

### `Material.from_mtlx(mtlx_file) -> Material`

Convert a local `.mtlx` file without downloading anything.

```python
from materialx_db import Material

mat = Material.from_mtlx("examples/gpuo-car-paint.mtlx")
```

Texture paths in the `.mtlx` are resolved relative to the file's location.

### `encode_texture_base64(file_path) -> str`

Encode an image file as a base64 data URI. Automatically converts EXR to PNG.

```python
from materialx_db import encode_texture_base64

data_uri = encode_texture_base64("textures/normal.png")
# -> 'data:image/png;base64,iVBORw0KGgo...'
```

### `material.interpolate_color() -> (r, g, b, a)`

Estimate a single representative sRGB color from a material — useful for CAD viewers that need a flat color per object while keeping a material dictionary for full PBR rendering.

```python
from materialx_db import Material

wood = Material.gpuopen.load("Ivory Walnut Solid Wood")

materials = {"wood": wood}      # keep for full PBR rendering
object.material = "wood"
object.color = wood.interpolate_color()   # (0.53, 0.31, 0.18, 1.0)
```

When the material has a color texture, the texture is decoded and averaged (requires `Pillow`). Scalar colors (linear RGB) are converted to sRGB. Transmission and opacity are mapped to the alpha channel so glass-like materials appear semi-transparent.

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

### Property name mapping

Each output property maps to Three.js `MeshPhysicalMaterial` fields:

| Output property | `value` → | `texture` → |
| --- | --- | --- |
| `color` | `color` | `map` |
| `metalness` | `metalness` | `metalnessMap` |
| `roughness` | `roughness` | `roughnessMap` |
| `normal` | — | `normalMap` |
| `normalScale` | `normalScale` | — |
| `ao` | — | `aoMap` |
| `emissive` | `emissive` | `emissiveMap` |
| `emissiveIntensity` | `emissiveIntensity` | — |
| `ior` | `ior` | — |
| `transmission` | `transmission` | `transmissionMap` |
| `thickness` | `thickness` | `thicknessMap` |
| `attenuationColor` | `attenuationColor` | — |
| `attenuationDistance` | `attenuationDistance` | — |
| `clearcoat` | `clearcoat` | `clearcoatMap` |
| `clearcoatRoughness` | `clearcoatRoughness` | — |
| `clearcoatNormal` | — | `clearcoatNormalMap` |
| `sheen` | `sheen` | — |
| `sheenColor` | `sheenColor` | `sheenColorMap` |
| `sheenRoughness` | `sheenRoughness` | — |
| `iridescence` | `iridescence` | `iridescenceMap` |
| `iridescenceIOR` | `iridescenceIOR` | — |
| `iridescenceThicknessRange` | `iridescenceThicknessRange` | — |
| `anisotropy` | `anisotropy` | — |
| `anisotropyRotation` | `anisotropyRotation` | — |
| `specularIntensity` | `specularIntensity` | `specularIntensityMap` |
| `specularColor` | `specularColor` | `specularColorMap` |
| `opacity` | `opacity` | `alphaMap` |
| `transparent` | `transparent` | — |
| `alphaTest` | `alphaTest` | — |
| `dispersion` | `dispersion` | — |
| `displacement` | — | `displacementMap` |
| `displacementScale` | `displacementScale` | — |
| `side` | `side` | — |
| `metallicRoughness` | — | `metalnessMap` + `roughnessMap` (G=roughness, B=metalness) |

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

To force re-conversion, delete the cached file and call `.load()` again.

## Conversion pipeline

When a material is loaded:

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

### Consumer notes

- **Color space**: Textures include a `colorSpace` field when available. Three.js expects color textures (baseColor, emissive, sheenColor, specularColor) in **sRGB** and data textures (roughness, metalness, normal, AO, displacement) in **linear**. Set `texture.colorSpace` accordingly.
- **Normal maps**: Baked using the OpenGL convention (Y-up), matching Three.js and glTF expectations.
- **Scalar x texture**: When both `value` and `texture` are present, Three.js multiplies them. The library sets scalars to `1.0` (neutral) when a texture is present so the texture controls fully.
- **glTF packed metallicRoughness**: When present, the `metallicRoughness` property carries a single packed texture (G=roughness, B=metalness). The consumer must assign it to both `metalnessMap` and `roughnessMap`.
- **Emission intensity**: For `open_pbr_surface`, `emission_luminance` (in nits) is scaled by `/1000` for `emissiveIntensity` to produce reasonable brightness in typical non-HDR Three.js scenes. This is a pragmatic normalization, not physically exact.

## Shader model coverage

Supported models: `standard_surface`, `gltf_pbr`, `open_pbr_surface`. Other models produce empty output with a logged warning.

| Feature | standard_surface | gltf_pbr | open_pbr_surface |
| --- | --- | --- | --- |
| Base color | Yes | Yes | Yes |
| Metalness | Yes | Yes | Yes |
| Roughness | Yes | Yes | Yes |
| Normal map | Yes | Yes | Yes |
| Specular | Yes (weight, color, IOR) | Yes (weight, color, IOR) | Yes (weight, color, IOR) |
| Transmission | Yes | Yes (+ attenuation) | Yes (+ attenuation) |
| Emission | Yes | Yes | Yes |
| Clearcoat | Yes | Yes | Yes |
| Clearcoat normal | Yes | Yes | Yes |
| Sheen | Yes | Yes | Yes (fuzz) |
| Iridescence | Yes | Yes | Yes |
| Anisotropy | Yes (scalar — no Three.js strength map) | Yes | Yes |
| Opacity | Yes | Yes (alpha/alpha_mode) | Yes (geometry_opacity) |
| Displacement | Yes (model-independent) | Yes | Yes |
| Dispersion | No | Yes | Yes |
| Normal scale | No (baked into texture) | Yes | No (baked into texture) |
| Thin-walled | No | No | Yes (→ DoubleSide) |
| Subsurface | No | No | No |

Subsurface scattering is not mapped — Three.js `MeshPhysicalMaterial` has no SSS support.

## Limitations

### Materials

- **Single material per document** — only the first material is used when a `.mtlx` file contains multiple materials. A warning is logged.
- **First shader node** — if a material has multiple shader nodes (e.g. surface + volume), only the first surface shader is extracted.

### Baking

- **8-bit textures** — the TextureBaker uses `UINT8` output. HDR information (emissive, HDR environment lighting baked into textures) is clamped to [0,1]. This is acceptable for web preview but lossy for physically accurate emissive maps.
- **Global bake lock** — baking operations are serialized via a `threading.Lock` because the MaterialX baker requires `os.chdir`. This is thread-safe but becomes a bottleneck under concurrent load. The lock is per-process only (`threading.Lock`, not `multiprocessing.Lock`).
- **Geometry-dependent nodes** — procedurals driven by `<position>`, `<normal>`, or `<tangent>` cannot be baked (the baker renders on a flat UV quad with no 3D geometry).

### Image tracing

- **Single upstream image** — `find_upstream_image` returns the first image node found when walking upstream. Complex graphs with multiple images (layered blends, channel packing before baking) will only capture one image. After baking, this is fine since the baker flattens everything to single `<image>` nodes.
- **No channel extraction tracking** — when an image passes through `extract` or `swizzle` nodes, the specific channel being used is not recorded. The consumer must know glTF metallicRoughness packing conventions (G=roughness, B=metalness).

### EXR conversion

- **LDR clamp** — EXR textures are clamped to [0,1] and converted to 8-bit PNG. Dynamic range beyond 1.0 is lost.
- **Channel naming** — EXR files with non-standard channel names (not R/G/B/A) fall back to source-order channel selection, which may produce incorrect color mappings for unusual EXR layouts.

### Network

- **No retry logic** — a single network failure raises an exception. The caller is responsible for retries.
- **GPUOpen pagination** — the material search assumes all results fit in one API page. Materials not in the first page may not be found.
- **GPUOpen sequential package lookup** — each package UUID is queried individually; materials with many packages may be slow to resolve.

### Caching

- **No cache invalidation** — cached materials are never automatically refreshed. Delete the cache file manually to force re-conversion.
