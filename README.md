# threejs-materials

A Python library that converts PBR materials into [Three.js `MeshPhysicalMaterial`](https://threejs.org/docs/#api/en/materials/MeshPhysicalMaterial)-compatible JSON with base64-encoded textures.

The primary input format is [MaterialX](https://materialx.org/) — the library can download materials on demand from four open sources and bake procedural graphs into flat textures. For Blender / USD users, `Material.from_usd()` reads `UsdPreviewSurface` materials directly without baking.

<table>
<tr>
<td align="center"><strong>Studio mode</strong> (full PBR)</td>
<td align="center"><strong>CAD mode</strong> (<code>interpolate_color()</code>)</td>
</tr>
<tr>
<td><img src="https://raw.githubusercontent.com/bernhard-42/threejs-materials/main/screenshots/Studio mode.png" width="400"></td>
<td><img src="https://raw.githubusercontent.com/bernhard-42/threejs-materials/main/screenshots/CAD mode.png" width="400"></td>
</tr>
</table>

## Installation

### MaterialX support

```bash
pip install threejs-materials
# uv pip install threejs-materials
# uv add threejs-materials
```

**Dependencies**

- `materialx >= 1.39.4` — MaterialX SDK with TextureBaker
- `requests >= 2.31.0` — HTTP downloads
- `openexr >= 3.3` — EXR to PNG conversion

### Optional: USD support

```bash
pip install threejs-materials[usd]
# uv pip install --extra usd threejs-materials
# uv add --extra usd threejs-materials
```

**Additional dependencies**

- `usd-core >= 26.3` — MaterialX SDK with TextureBaker

This installs `usd-core` for `Material.from_usd()`. 

**Note** `usd-core` may not support the latest Python version as binary package. It will try to compile it during installation.

## Output Formats

### Three.js

The internal format uses Three.js `MeshPhysicalMaterial` property names. Both MaterialX and USD pipelines produce the same structure. Each property carries a `value`, a base64-encoded `texture`, or both:

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

#### Property name mapping

Each output property maps to Three.js `MeshPhysicalMaterial` fields:

| Output property             | `value` →                   | `texture` →                                                |
| --------------------------- | --------------------------- | ---------------------------------------------------------- |
| `color`                     | `color`                     | `map`                                                      |
| `metalness`                 | `metalness`                 | `metalnessMap`                                             |
| `roughness`                 | `roughness`                 | `roughnessMap`                                             |
| `normal`                    | —                           | `normalMap`                                                |
| `normalScale`               | `normalScale`               | —                                                          |
| `ao`                        | —                           | `aoMap`                                                    |
| `emissive`                  | `emissive`                  | `emissiveMap`                                              |
| `emissiveIntensity`         | `emissiveIntensity`         | —                                                          |
| `ior`                       | `ior`                       | —                                                          |
| `transmission`              | `transmission`              | `transmissionMap`                                          |
| `thickness`                 | `thickness`                 | `thicknessMap`                                             |
| `attenuationColor`          | `attenuationColor`          | —                                                          |
| `attenuationDistance`       | `attenuationDistance`       | —                                                          |
| `clearcoat`                 | `clearcoat`                 | `clearcoatMap`                                             |
| `clearcoatRoughness`        | `clearcoatRoughness`        | —                                                          |
| `clearcoatNormal`           | —                           | `clearcoatNormalMap`                                       |
| `sheen`                     | `sheen`                     | —                                                          |
| `sheenColor`                | `sheenColor`                | `sheenColorMap`                                            |
| `sheenRoughness`            | `sheenRoughness`            | —                                                          |
| `iridescence`               | `iridescence`               | `iridescenceMap`                                           |
| `iridescenceIOR`            | `iridescenceIOR`            | —                                                          |
| `iridescenceThicknessRange` | `iridescenceThicknessRange` | —                                                          |
| `anisotropy`                | `anisotropy`                | —                                                          |
| `anisotropyRotation`        | `anisotropyRotation`        | —                                                          |
| `specularIntensity`         | `specularIntensity`         | `specularIntensityMap`                                     |
| `specularColor`             | `specularColor`             | `specularColorMap`                                         |
| `opacity`                   | `opacity`                   | `alphaMap`                                                 |
| `transparent`               | `transparent`               | —                                                          |
| `alphaTest`                 | `alphaTest`                 | —                                                          |
| `dispersion`                | `dispersion`                | —                                                          |
| `displacement`              | —                           | `displacementMap`                                          |
| `displacementScale`         | `displacementScale`         | —                                                          |
| `side`                      | `side`                      | —                                                          |
| `metallicRoughness`         | —                           | `metalnessMap` + `roughnessMap` (G=roughness, B=metalness) |

### glTF

`to_gltf()` converts a single material to the glTF 2.0 JSON structure. `collect_gltf_textures()` does the same for multiple materials with shared, deduplicated textures. Both return the same schema:

```json
{
    "asset": { "version": "2.0", "generator": "threejs-materials" },
    "images": [
        { "uri": "data:image/png;base64,..." }
    ],
    "samplers": [
        { "magFilter": 9729, "minFilter": 9987, "wrapS": 10497, "wrapT": 10497 }
    ],
    "textures": [
        { "source": 0, "sampler": 0 }
    ],
    "materials": [
        {
            "name": "Car Paint",
            "pbrMetallicRoughness": {
                "baseColorFactor": [0.944, 0.776, 0.373, 1.0],
                "baseColorTexture": { "index": 0 },
                "metallicFactor": 1.0,
                "roughnessFactor": 0.5,
                "metallicRoughnessTexture": { "index": 1 }
            },
            "normalTexture": { "index": 2 },
            "extensions": {
                "KHR_materials_clearcoat": { "clearcoatFactor": 0.8 }
            }
        }
    ],
    "extensionsUsed": ["KHR_materials_clearcoat"]
}
```

#### Single material

```python
mat = Material.gpuopen.load("Car Paint")
gltf = mat.to_gltf()
```

#### Multiple materials with texture deduplication

```python
from threejs_materials import Material, collect_gltf_textures

materials = {
    "body": Material.gpuopen.load("Car Paint"),
    "wood": Material.gpuopen.load("Ivory Walnut Solid Wood"),
    "glass": Material.physicallybased.load("Glass"),
}

gltf = collect_gltf_textures(materials)
# Textures shared across materials are deduplicated in the images array.
```

#### Import from glTF

```python
mat = Material.from_gltf(gltf_data)           # first material
mat = Material.from_gltf(gltf_data, index=1)  # second material
```

#### Texture repeat

`Material.scale()` is exported as the `KHR_texture_transform` extension on each texture reference:

```python
tiled = mat.scale(2, 2)  # texture appears 2x larger
gltf = tiled.to_gltf()
# Each texture ref gets: "extensions": {"KHR_texture_transform": {"scale": [0.5, 0.5]}}
```

#### glTF extensions used

Advanced material features are mapped to standard `KHR_materials_*` extensions:

| Feature | glTF extension |
| --- | --- |
| IOR | `KHR_materials_ior` |
| Transmission | `KHR_materials_transmission` |
| Volume (thickness, attenuation) | `KHR_materials_volume` |
| Clearcoat | `KHR_materials_clearcoat` |
| Sheen | `KHR_materials_sheen` |
| Iridescence | `KHR_materials_iridescence` |
| Anisotropy | `KHR_materials_anisotropy` |
| Specular | `KHR_materials_specular` |
| Emissive strength | `KHR_materials_emissive_strength` |
| Dispersion | `KHR_materials_dispersion` |
| Texture repeat | `KHR_texture_transform` |

### Three.js ↔ glTF conversion

The glTF export is **visually lossless** for all properties except displacement. The round-trip `to_gltf()` → `from_gltf()` preserves material appearance but merges some internal representations:

| Property | Round-trip behavior |
| --- | --- |
| All scalar values | Preserved exactly |
| All textures | Preserved (base64 URIs survive the round-trip) |
| Opacity texture | Merged into `baseColorTexture` alpha channel — cannot be separated back |
| Separate metalness + roughness textures | Packed into one `metallicRoughnessTexture` — comes back packed |
| `displacement` / `displacementScale` | **Lost** — no glTF equivalent (see note below) |
| `texture_repeat` / `scale()` | Preserved via `KHR_texture_transform` |
| Source metadata (`id`, `source`, `url`, `license`) | Not stored in glTF; `from_gltf()` sets `source="gltf"` |

#### Round-trip example

```python
m = Material.gpuopen.load("Perforated Metal")
g = m.to_gltf()
m2 = Material.from_gltf(g)
```

**Original material** (`m`):
```
color: value=[1.0, 1.0, 1.0], texture='data:image/png;base64,...'
metalness: value=1.0, texture='data:image/png;base64,...'
roughness: value=1.0, texture='data:image/png;base64,...'
normal: texture='data:image/png;base64,...'
specularIntensity: value=1.0
specularColor: value=[1.0, 1.0, 1.0]
ior: value=1.5
opacity: texture='data:image/png;base64,...'
```

**After round-trip** (`m2`):
```
color: value=[1.0, 1.0, 1.0], texture='data:image/png;base64,...'
metalness: value=1.0
roughness: value=1.0
metallicRoughness: texture='data:image/png;base64,...'
normal: texture='data:image/png;base64,...'
ior: value=1.5
alphaTest: value=0.5
specularIntensity: value=1.0
specularColor: value=[1.0, 1.0, 1.0]
```

What changed:

- **`opacity` texture disappeared** — it was merged into the `color` texture's alpha channel (glTF has no separate opacity texture). The resulting RGBA `baseColorTexture` is now the `color` texture.
- **`alphaTest: 0.5` appeared** — since the original had an opacity texture, `to_gltf()` sets `alphaMode: "MASK"` with `alphaCutoff: 0.5`. On import this becomes `alphaTest`.
- **Separate `metalness` + `roughness` textures → `metallicRoughness`** — glTF only supports a single packed metallicRoughnessTexture (G=roughness, B=metalness). On import this comes back as the packed `metallicRoughness` property.

The visual result is identical — all changes are representation differences, not data loss.

#### Note on displacement

Displacement mapping is the only property fully lost in the glTF conversion. In practice this is rarely an issue for CAD workflows:

- Displacement is a **vertex-level** effect — it offsets mesh vertices along their normals based on a texture. This requires a sufficiently **dense mesh** to produce visible detail.
- CAD tessellation produces meshes optimized for geometric accuracy, not displacement fidelity. Large flat faces (common in CAD) are tessellated with very few triangles, making displacement ineffective.
- Even in the internal Three.js format, displacement is **optional** and most CAD viewers ignore it.
- For visual surface detail, **normal maps** (which survive the round-trip) are a better fit — they simulate surface relief without requiring extra geometry.

## Common API

### Customization

#### `material.override(**props) -> Material`

Return a new `Material` with property overrides. The original material is not modified.

```python
mat = Material.gpuopen.load("Car Paint")

red_paint = mat.override(color=(0.8, 0.1, 0.1))
rough_red = mat.override(color=(0.8, 0.1, 0.1), roughness=0.9)
```

Overrides set the `value` of the named property, creating it if absent. Existing textures are preserved. Calls can be chained: `mat.override(color=(1,0,0)).override(roughness=0.5)`.

#### `material.scale(u, v) -> Material`

Return a new `Material` with texture scaling applied. The original material is not modified.

```python
tiled = mat.scale(3, 3)      # texture appears 3x larger
small = mat.scale(0.5, 0.5)  # texture tiles 2x in each direction
```

`scale(u, v)` sets `texture_repeat = (1/u, 1/v)` internally. In Three.js this maps to `texture.repeat`, in glTF it is exported as `KHR_texture_transform` with `scale: [1/u, 1/v]`. Can be chained with `override()`: `mat.override(color=(1,0,0)).scale(2, 2)`.

### Import and Export

#### `Material.from_mtlx(mtlx_file) -> Material`

Convert a local `.mtlx` file. Texture paths are resolved relative to the file's location. See [MaterialX](#materialx) for details.

```python
mat = Material.from_mtlx("examples/gpuo-car-paint.mtlx")
```

#### `Material.from_usd(usd_file) -> Material`

Load a USD file (`.usda`, `.usdc`, `.usdz`) with `UsdPreviewSurface` materials. Requires `usd-core`. See [USD](#usd) for details.

```python
mat = Material.from_usd("model.usda")
```

#### `Material.from_gltf(gltf_data, index=0) -> Material`

Import a material from a glTF structure (the same schema returned by `to_gltf()` and `collect_gltf_textures()`). Resolves texture indices back to base64 URIs and maps glTF properties to the internal format. See [Three.js ↔ glTF conversion](#threejs--gltf-conversion) for round-trip behavior.

```python
mat = Material.from_gltf(gltf_data)           # first material
mat = Material.from_gltf(gltf_data, index=1)  # second material
```

#### `material.to_gltf() -> dict`

Convert a single material to the glTF 2.0 JSON structure with `asset`, `images`, `samplers`, `textures`, and `materials` arrays. See [glTF](#gltf) for the full schema.

```python
gltf = mat.to_gltf()
```

#### `collect_gltf_textures(materials) -> dict`

Convert multiple materials to a glTF structure with shared, deduplicated textures. Returns the same schema as `to_gltf()`. See [glTF](#gltf) for details.

```python
from threejs_materials import Material, collect_gltf_textures

gltf = collect_gltf_textures({
    "body": Material.gpuopen.load("Car Paint"),
    "glass": Material.physicallybased.load("Glass"),
})
```

### Utilities

#### `Material.list_sources()`

Print available material sources with clickable URLs.

```python
Material.list_sources()
# Material sources:
#   Material.ambientcg        https://ambientcg.com/list?type=material
#   Material.gpuopen          https://matlib.gpuopen.com/main/materials/all
#   Material.polyhaven        https://polyhaven.com/textures
#   Material.physicallybased  https://physicallybased.info/
```

#### `material.dump(gltf=False, json_format=False) -> str`

Return a human-readable summary of the material. Textures are abbreviated to `'data:image/png;base64,...'`. Also used by `repr(material)`.

```python
print(mat.dump())                          # Three.js properties, text
print(mat.dump(gltf=True))                 # glTF structure, text
print(mat.dump(json_format=True))          # Three.js properties, JSON
print(mat.dump(gltf=True, json_format=True))  # glTF structure, JSON
```

#### `material.interpolate_color() -> (r, g, b, a)`

Estimate a single representative sRGB color from a material — useful for CAD viewers that need a flat color per object while keeping a material dictionary for full PBR rendering.

```python
wood = Material.gpuopen.load("Ivory Walnut Solid Wood")

materials = {"wood": wood}      # keep for full PBR rendering
object.material = "wood"
object.color = wood.interpolate_color()   # (0.53, 0.31, 0.18, 1.0)
```

When the material has a color texture, the texture is decoded and averaged (requires `Pillow`). Scalar colors (linear RGB) are converted to sRGB. Transmission and opacity are mapped to the alpha channel so glass-like materials appear semi-transparent.

#### `encode_texture_base64(file_path) -> str`

Encode an image file as a base64 data URI. Automatically converts EXR to PNG.

```python
from threejs_materials import encode_texture_base64

data_uri = encode_texture_base64("path/to/textures/normal.png")
# -> 'data:image/png;base64,iVBORw0KGgo...'
```

### Three.js usage

#### From internal format (single material)

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

#### From glTF (multi-material)

When using `collect_gltf_textures()` to produce a multi-material glTF JSON, load it with Three.js's `GLTFLoader`:

```javascript
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

// gltfJson is the output of collect_gltf_textures(), serialized as JSON
const blob = new Blob([gltfJson], { type: 'application/json' });
const url = URL.createObjectURL(blob);

const loader = new GLTFLoader();
loader.load(url, (gltf) => {
    // Materials are already created as MeshStandardMaterial / MeshPhysicalMaterial
    const materials = gltf.parser.json.materials;

    // If injected into a geometry glTF, the scene contains the full model
    scene.add(gltf.scene);

    URL.revokeObjectURL(url);
});
```

Alternatively, when injecting materials into an existing glTF file (e.g. from build123d), simply load that file with `GLTFLoader` — Three.js handles the `images`, `textures`, and `materials` arrays automatically, including all `KHR_materials_*` extensions.

### Consumer notes

- **Color space**: Textures include a `colorSpace` field when available. Three.js expects color textures (baseColor, emissive, sheenColor, specularColor) in **sRGB** and data textures (roughness, metalness, normal, AO, displacement) in **linear**. Set `texture.colorSpace` accordingly.
- **Normal maps**: Baked using the OpenGL convention (Y-up), matching Three.js and glTF expectations.
- **Scalar x texture**: When both `value` and `texture` are present, Three.js multiplies them. The library sets scalars to `1.0` (neutral) when a texture is present so the texture controls fully.
- **glTF packed metallicRoughness**: When present, the `metallicRoughness` property carries a single packed texture (G=roughness, B=metalness). The consumer must assign it to both `metalnessMap` and `roughnessMap`.

---

## MaterialX

### Sources

| Source                                                   | Type                     | Shader model       |
| -------------------------------------------------------- | ------------------------ | ------------------ |
| [ambientCG](https://ambientcg.com/)                      | Texture-based            | `open_pbr_surface` |
| [GPUOpen MaterialX Library](https://matlib.gpuopen.com/) | Procedural (baked)       | `standard_surface` |
| [PolyHaven](https://polyhaven.com/)                      | Texture-based            | `standard_surface` |
| [PhysicallyBased](https://physicallybased.info/)         | Parametric (no textures) | `open_pbr_surface` |

Browse materials on the source websites, then load them by name.

### `Material.{source}.load(name, resolution="1K") -> Material`

Download, convert, and cache a material.

```python
from threejs_materials import Material

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
from threejs_materials import Material

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
from threejs_materials import Material

mat = Material.from_mtlx("examples/gpuo-car-paint.mtlx")
```

Texture paths in the `.mtlx` are resolved relative to the file's location.

### Conversion pipeline

When a material is loaded:

1. **Download** — source-specific: fetch ZIP (ambientCG, GPUOpen), individual files (PolyHaven), or generate from parameters (PhysicallyBased)
2. **Bake** — run MaterialX `TextureBaker` (GLSL preferred, MSL fallback on macOS) to flatten procedural graphs into texture images
3. **Fallback merge** — if the baker can't handle certain textures, merge from the original document
4. **EXR to PNG** — convert any EXR textures to 8-bit PNG
5. **Extract** — map shader inputs to `MeshPhysicalMaterial` properties with base64-encoded textures
6. **Cache** — write JSON to `~/.materialx-cache/`

### Cache

Converted materials are cached as flat JSON files in `~/.materialx-cache/`:

```
~/.materialx-cache/
    gpuopen_car_paint_1k_8b.json
    ambientcg_onyx015_1k-png.json
    polyhaven_plank_flooring_04_1k.json
    physicallybased_titanium.json
```

To force re-conversion, delete the cached file and call `.load()` again.

### Shader model coverage

Supported models: `standard_surface`, `gltf_pbr`, `open_pbr_surface`. Other models produce empty output with a logged warning.

| Feature          | standard_surface                        | gltf_pbr                 | open_pbr_surface         |
| ---------------- | --------------------------------------- | ------------------------ | ------------------------ |
| Base color       | Yes                                     | Yes                      | Yes                      |
| Metalness        | Yes                                     | Yes                      | Yes                      |
| Roughness        | Yes                                     | Yes                      | Yes                      |
| Normal map       | Yes                                     | Yes                      | Yes                      |
| Specular         | Yes (weight, color, IOR)                | Yes (weight, color, IOR) | Yes (weight, color, IOR) |
| Transmission     | Yes                                     | Yes (+ attenuation)      | Yes (+ attenuation)      |
| Emission         | Yes                                     | Yes                      | Yes                      |
| Clearcoat        | Yes                                     | Yes                      | Yes                      |
| Clearcoat normal | Yes                                     | Yes                      | Yes                      |
| Sheen            | Yes                                     | Yes                      | Yes (fuzz)               |
| Iridescence      | Yes                                     | Yes                      | Yes                      |
| Anisotropy       | Yes (scalar — no Three.js strength map) | Yes                      | Yes                      |
| Opacity          | Yes                                     | Yes (alpha/alpha_mode)   | Yes (geometry_opacity)   |
| Displacement     | Yes (model-independent)                 | Yes                      | Yes                      |
| Dispersion       | No                                      | Yes                      | Yes                      |
| Normal scale     | No (baked into texture)                 | Yes                      | No (baked into texture)  |
| Thin-walled      | No                                      | No                       | Yes (→ DoubleSide)       |
| Subsurface       | No                                      | No                       | No                       |

Subsurface scattering is not mapped — Three.js `MeshPhysicalMaterial` has no SSS support.

### MaterialX limitations

#### Materials

- **Single material per document** — only the first material is used when a `.mtlx` file contains multiple materials. A warning is logged.
- **First shader node** — if a material has multiple shader nodes (e.g. surface + volume), only the first surface shader is extracted.

#### Baking

- **8-bit textures** — the TextureBaker uses `UINT8` output. HDR information (emissive, HDR environment lighting baked into textures) is clamped to [0,1]. This is acceptable for web preview but lossy for physically accurate emissive maps.
- **Global bake lock** — baking operations are serialized via a `threading.Lock` because the MaterialX baker requires `os.chdir`. This is thread-safe but becomes a bottleneck under concurrent load. The lock is per-process only (`threading.Lock`, not `multiprocessing.Lock`).
- **Geometry-dependent nodes** — procedurals driven by `<position>`, `<normal>`, or `<tangent>` cannot be baked (the baker renders on a flat UV quad with no 3D geometry).

#### Image tracing

- **Single upstream image** — `find_upstream_image` returns the first image node found when walking upstream. Complex graphs with multiple images (layered blends, channel packing before baking) will only capture one image. After baking, this is fine since the baker flattens everything to single `<image>` nodes.
- **No channel extraction tracking** — when an image passes through `extract` or `swizzle` nodes, the specific channel being used is not recorded. The consumer must know glTF metallicRoughness packing conventions (G=roughness, B=metalness).

#### EXR conversion

- **LDR clamp** — EXR textures are clamped to [0,1] and converted to 8-bit PNG. Dynamic range beyond 1.0 is lost.
- **Channel naming** — EXR files with non-standard channel names (not R/G/B/A) fall back to source-order channel selection, which may produce incorrect color mappings for unusual EXR layouts.

#### Network

- **No retry logic** — a single network failure raises an exception. The caller is responsible for retries.
- **GPUOpen pagination** — the material search assumes all results fit in one API page. Materials not in the first page may not be found.
- **GPUOpen sequential package lookup** — each package UUID is queried individually; materials with many packages may be slow to resolve.

#### Caching

- **No cache invalidation** — cached materials are never automatically refreshed. Delete the cache file manually to force re-conversion.

---

## USD

For Blender users and other USD workflows: `Material.from_usd()` reads `UsdPreviewSurface` materials directly from USD files. No MaterialX baking is needed since UsdPreviewSurface is already a flat PBR shader.

### `Material.from_usd(usd_file) -> Material`

Load a USD file (`.usda`, `.usdc`, `.usdz`) with `UsdPreviewSurface` materials.

```python
from threejs_materials import Material

mat = Material.from_usd("model.usda")
```

Textures are resolved relative to the file location. USDZ archives with embedded textures are supported. Both metallic and specular workflows are handled (`useSpecularWorkflow` input).

### UsdPreviewSurface input mapping

| UsdPreviewSurface input | Output property      | Notes                              |
| ----------------------- | -------------------- | ---------------------------------- |
| `diffuseColor`          | `color`              | value + texture                    |
| `metallic`              | `metalness`          | value + texture                    |
| `roughness`             | `roughness`          | value + texture                    |
| `normal`                | `normal`             | texture only                       |
| `emissiveColor`         | `emissive`           | value + texture                    |
| `clearcoat`             | `clearcoat`          | value only                         |
| `clearcoatRoughness`    | `clearcoatRoughness` | value only                         |
| `ior`                   | `ior`                | value only                         |
| `occlusion`             | `ao`                 | texture only                       |
| `displacement`          | `displacement`       | texture only                       |
| `opacity`               | `opacity`            | + `transparent: true` if < 1       |
| `opacityThreshold`      | `alphaTest`          | mask/cutout mode                   |
| `specularColor`         | `specularColor`      | only if `useSpecularWorkflow == 1` |

Inputs at their UsdPreviewSurface default value are omitted from the output.

### USD limitations

- **Single material per file** — only the first `UsdPreviewSurface` material is used when a file contains multiple materials. A warning is logged.
- **No UV transform support** — `UsdTransform2d` nodes are not read; texture coordinates are assumed to be used as-is.
- **Emission intensity** — UsdPreviewSurface has no emission intensity input; `emissiveColor` maps directly to `emissive`.

---

## Clients

### build123d

[build123d](https://github.com/gumyr/build123d) exports glTF geometry via OCCT's `RWGltf_CafWriter`, which handles meshes, nodes, and flat colors. To add PBR materials, post-process the generated glTF file by injecting material data from threejs-materials:

```python
import json
from build123d import export_gltf
from threejs_materials import Material, collect_gltf_textures

# 1. Build your CAD model
# ...

# 2. Export geometry to glTF
export_gltf(my_shape, "model.gltf")

# 3. Load PBR materials
materials = {
    "body":  Material.gpuopen.load("Car Paint").override(color=(0.8, 0.1, 0.1)),
    "wood":  Material.gpuopen.load("Ivory Walnut Solid Wood").scale(2, 2),
    "glass": Material.physicallybased.load("Glass"),
}

# 4. Convert to glTF arrays (shared, deduplicated textures)
mat_data = collect_gltf_textures(materials)

# 5. Inject materials into the geometry glTF
with open("model.gltf") as f:
    gltf = json.load(f)

gltf["images"] = mat_data.get("images", [])
gltf["samplers"] = mat_data.get("samplers", [])
gltf["textures"] = mat_data.get("textures", [])
gltf["materials"] = mat_data["materials"]
if "extensionsUsed" in mat_data:
    gltf.setdefault("extensionsUsed", []).extend(mat_data["extensionsUsed"])

with open("model.gltf", "w") as f:
    json.dump(gltf, f)
```

The material names in the `materials` dict must match the material/color names assigned to shapes in the OCCT export so that mesh primitives reference the correct material index.
