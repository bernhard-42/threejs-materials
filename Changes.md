# v1.2.3

## Breaking changes

- **`plastic.plastic_clean()` renamed to `plastic.plastic()`** — plastic now follows the same base/finish shape as metal (`gold` / `gold_brushed` / `gold_matte`), so the untextured base carries the bare category name and the textured variants suffix it. The old name still resolves as a deprecated alias that emits a `DeprecationWarning` and forwards to `plastic()`; it is kept out of `__all__` and will be removed at the next major.

## Features

- **`plastic.plastic_fdm()` — 3D-printed FDM layer lines.** A procedurally generated layer-line surface for printed parts, colorable to any filament (`plastic_fdm(color="#e04020")`). The texture set is produced by the new `scripts/fdm_layers.py`: each extruded layer is modelled as a horizontal tube of radius `k·p/2` stacked with vertical period `p`, so the exposed profile is the arc between two overlapping beads and the normal map is derived from a real millimetre height field rather than filtered out of an image. Ships color + normal + roughness at 1K (0.85 MB), with per-layer over/under-extrusion banding, centreline wobble, drag marks along the bead, and isotropic micro-texture. All noise lattices wrap and the layer count divides the tile, so the set tiles seamlessly in both axes. Retraction zits are modelled (`BLOBS`) but off by default: modern pressure advance has largely removed them, and being the only feature big enough to recognize they made the 6.4 mm tile repeat visible.

  Authored at a real-world scale — one tile is 6.4 mm holding 32 layers at 0.2 mm — so unlike every other bundled material it bakes a **millimetre-true UV transform** (`texture_repeat = 1/6.4` with `normalize_uvs=False`) instead of an object-relative one. The printed pitch therefore holds on a part of any size; `scale=(3.2, 3.2)` gives a 0.1 mm layer height. Two caveats, both from layer lines being a build-axis phenomenon rather than a surface property: triplanar mapping ignores the baked transform and normalizes by the bounding box (pass `scale=(6.4/d, 6.4/d)`), and only triplanar guarantees the lines stack along the build axis — with parametric CAD UVs the direction and phase follow each face's own parameterization.

- **`plastic.plastic_fdm_skin()` — the top/bottom solid-infill surface.** The companion to `plastic_fdm` for flat top and bottom faces: 0.4 mm line pitch (the extrusion width, giving exactly 16 lines per 6.4 mm tile), the bead squashed flat against the substrate (`k=8` → 12.5 µm relief against the wall's 53.7 µm), and glossier to match — roughness 0.35–0.63 where the wall is 0.47–0.82. Its tint map is held at the same mean as the wall's (0.878 vs 0.877) so both read as the same colour under one tint when used together on a part. Same generator, different parameters — `generate()` gained `k`, `grain_mm`, `rough` and `tint` keyword arguments. 0.74 MB.

  The 45° is a **baked UV rotation, not baked pixels**: rotating a square tile's contents by 45° would break the tiling, whereas rotating in texture space preserves both the wrap and the 0.4 mm pitch. `rotation=-45` gives the bottom face, the way slicers alternate. Unlike the wall texture, the skin has no build-axis constraint, so both UV modes place it correctly on a flat face.

- **Plastic restructured to the metal base/finish pattern** — `plastic()` is the scalar base; `plastic_rough()` and `plastic_fdm()` graft shared finish map sets onto it, the same way `gold_brushed` / `gold_matte` share `_brush` / `_matte`. `plastic_rough`'s textures moved unchanged from `_assets/plastic_rough/` to the shared `_assets/_rough/`. Adding filament bases (PLA, ABS, …) later costs no new texture bytes.

## Internals

- **`scripts/build_bundle.py` can bake a non-identity UV transform into a payload** — `_payload(baked=...)` writes `texture_repeat` / `normalize_uvs` into the `from_dict` dict rather than calling `.scale()` at construction, so a default factory call keeps `id == name` instead of picking up a hashed variant id. The emitted signature's `scale` default and its `fixed=` argument follow from the same value.
- **`_payload(prov=...)`** overrides source/url/license per material, for entries whose maps do not come from the material's own source.
- **`DEPRECATED_ALIASES`** in the generator emits `functools.wraps`-preserving deprecation shims after `__all__`, so renamed factories keep working with an introspectable signature.

## Tests

- `tests/test_fdm_generator.py` (5 tests) covers the procedural generator: seamless wrap in both axes (compared against interior steps at the same bead phase, since the V wrap lands mid-flank where steps are steepest anyway), integer layer period, rejection of a layer height that doesn't divide the tile, and a neutral-grayscale color map.
- 5 new `test_bundled.py` tests for the plastic base/finish split, the baked millimetre-true transform, scale overrides not silently re-enabling UV normalization, FDM provenance and tinting, and the deprecated alias. Full suite: **390 passing**.

## Docs

- README: plastic section documents the base/finish split, the deprecated alias, and a `plastic_fdm` subsection covering the millimetre scale rule and the two mapping caveats.
- README wood table was missing `cherry`.

# v1.2.2

## Materials

- **`wood.cherry()`** — ingested from a local glTF (ambientCG Wood093), then replaced with "Fine Cherry Wood Veneer": 1024×936 at the source's native aspect rather than a 2048×1024 embed stretched to square, with finer straight grain and no knots. Rotated 90° to run horizontally like oak/maple/walnut, using a plain transpose on all three maps with no normal channel-swap (the channel-swap darkens the render).

# v1.2.1

## Breaking changes

- **`coats.colored_coat_matte/gloss` renamed to `coats.coat_matte/gloss`** — "coat" already implies painted/dielectric as the counterpart to `metallic_coat_*`, so the "colored" prefix was redundant.
- **`textile.fabric_knit` removed** (ambientCG Fabric 019).

## Materials

- **`metal.steel`, `metal.titanium`, `metal.zinc`, `metal.tin`, `metal.nickel`** — each with the usual `_brushed` / `_matte` finishes. `tin` has no source-DB entry and is hand-authored (sRGB color, linear specularColor).
- **`coats.metallic_coat_matte/gloss`** — plated/converted metallic scalars (metalness 1), the mirror of the dielectric `coat_*` pair: black oxide, PVD colours, nickel/tin/zinc plating.
- **`plastic.carbon_fiber`** (ambientCG Fabric 004) and **`wood.pine`**; `wood.spruce` re-textured.

## Fixes

- **PhysicallyBased colours were double-linearized** — the database publishes "sRGB (Linear)" F0 values, which were stored verbatim as `values.color`; but the pipeline treats `values.color` as sRGB and re-linearizes it at the glTF boundary and in the viewer. Every bare metal, plus glass and acrylic, therefore rendered too dark. `physicallybased.py` now converts the base colour to sRGB before storing (`specularColor` / `attenuationColor` stay linear per the field convention). Verified by round-tripping zinc's `baseColorFactor` back to its F0 `[0.808, 0.844, 0.865]`.
- **Bundle generator ordering** — `_copy_maps` could read a source's cache directory before `bake()` had populated it, producing a material with empty maps for a brand-new uncached source.

## Docs

- README documents that `override()` composes an arbitrary colour/metalness/roughness over a bundled material's existing relief (`gold_brushed().override(...)`), so texture ⊗ response composition needs no dedicated feature.
- `pyproject.toml` gains project URLs.

# v1.2.0

## Breaking changes

- **`openexr` is no longer a dependency, and `materialx` moved to an optional extra** (`pip install "threejs-materials[materialx]"`). `import threejs_materials`, the bundled materials, and glTF import/export all work without either. MaterialX is imported lazily inside the conversion functions, which raise a clear `ImportError` pointing at the extra. Install `openexr` yourself if you want Polyhaven's full-precision EXR route.

## Features

- **Bundled PBR material library** — ready-to-use, pre-converted materials grouped into category modules (`wood`, `metal`, `coats`, `plastic`, `glass`, `paper`, `textile`) under the `pbr_properties/` subpackage, lazily re-exported from the package root so `from threejs_materials import metal; metal.gold_brushed()` works without `pbr_properties` in the path and without loading anything on a bare import. Each material is a factory returning a fresh `PbrProperties`, and its signature exposes only the overrides that make sense: `color` (colorable materials; raw metals omit it — their colour is intrinsic), `roughness`, `scale` + `rotation` (textured), `thickness` (transmissive). Textures are held as file references, so importing a module reads no bytes; they ship in the wheel. Needs no MaterialX and no network. The authoring generator (`scripts/build_bundle.py`) is included and re-runnable.
- **Polyhaven glTF fallback** — `SourceResult.gltf_path` plus a glTF branch in the loader let Polyhaven materials be ingested from their glTF download instead of baking `.mtlx`, which is what makes dropping `openexr` possible: Polyhaven is the only source that ships EXR, and glTF 2.0 permits only JPEG/PNG. Trade-off: 8-bit lossy maps instead of float EXR, and no displacement channel.
- **`load_gltf()` / `from_gltf()` accept a `GLTF2` object and an optional `index`** — with `index` omitted they return `dict[str, PbrProperties]`; with it, a single material. Overloads are typed so the return type narrows correctly.
- **`download_gpuopen()` / `download_ambientcg()` / `download_polyhaven()`** — fetch and unzip a source's raw MaterialX package (`.mtlx` + textures, hierarchy preserved) into `<dest>/<normalized_name>/`, for inspecting or hand-editing a source before conversion.
- **`with_maps()` and `strip_maps()`** — graft another material's named texture maps onto a scalar base (`only=("normal", "roughness")`), or drop all maps while keeping scalars. These are what the bundle's shiny/brushed/matte triples are built from.
- **Shared `normalize_name()`** for folder-safe source names, adopted across ambientCG / GPUOpen / Polyhaven.
- **Type stubs for MaterialX / OpenEXR / Imath** — minimal `.pyi` files so the `ty` type checker resolves the untyped C-extension imports, mirroring the existing mypy `ignore_missing_imports`.

## Fixes

- **`from_gltf()` no longer drops textures stored in bufferViews**, and imported materials default to `normalize_uvs=True`.
- **glTF texture-transform application deduplicated** — the four texture-info builders shared inline code that assigned into `extensions` without guarding `None`, which would crash. Extracted into `_apply_tex_transform()` with the guard, plus `bufferView` / `emissiveFactor` None-guards.
- **`interpolate_color()` accepts a data-URI string for `texture=`** — the branch previously required `bytes` unless the string started with `data:`, so a bare data URI fell through to a `TypeError`.
- Removed an incorrect MaterialX guard and a `has_textures` filter that excluded valid scalar-only materials.

## Tests

- `tests/test_polyhaven_gltf_fallback.py` and `tests/test_sources_download.py`; existing suites updated for the sources changes, the `to_dict` colour-space convention, and typing cleanups.

# v1.1.1

## Features

- **Texture rotation support** — `mat.scale(rotation=deg)` and the new `PbrProperties.texture_rotation` field carry a counterclockwise rotation in degrees. Round-trips through `KHR_texture_transform.rotation` (radians on the wire) on every textured slot (`baseColor`, `metallicRoughness`, `normal`, `occlusion`). `to_dict()` emits `textureRotation` in radians for direct Three.js `texture.rotation` consumption. `TextureTransform` gains a matching `rotation` field.

## Fixes

- **`override()` now preserves `texture_rotation`** — chained `mat.scale(rotation=90).override(color="red")` was silently dropping the rotation because the `override()` constructor call didn't propagate the new field.
- **`interpolate_color()` respects override-applied color when a color texture is present** — `mat.override(color="red").interpolate_color()` previously ignored the override and returned the unmodified texture average, because the texture branch fired before the list-color branch. `values.color` (string or list) now tints the texture identically to passing `override_color=` directly: the result equals `mat.interpolate_color(override_color="red")` for the same input. The explicit `override_color=` argument still wins when both are set.
- Fix handling 16bit texture inputs in metallicRoughness packing for glTF
- MaterialX file from polyhaven contain exr files. Fix that exr textures got dropped by the baking process.
- Ensure that polyhaven AO textures get exported by the baking process

# v1.1.0

## Breaking changes

- **`PbrProperties.values.color` is now sRGB-stored** (was linear). Matches three-cad-viewer's `setRGB(r, g, b, SRGBColorSpace)` consumption — the viewer linearizes internally, so storing sRGB byte ratios lets a numeric input like `(0.5, 0.5, 0.5)` mean perceptual midgray. glTF spec compliance is preserved by an `_srgb_to_linear` conversion at the `to_gltf()`/`from_gltf()` boundary (`baseColorFactor` remains linear on the wire). The `emissive`, `sheen_color`, `specular_color`, `attenuation_color` fields **remain linear** (matching glTF \*Factor spec and Three.js's bare `new THREE.Color(r, g, b)` constructor convention).

  User impact: `mat.override(color=(0.2, 0.4, 0.6))` will render brighter than before with the same numeric input — same input now interpreted as sRGB. Hex strings like `"#ff8000"` are unaffected (sRGB at source).

- **Cache migration required.** Two reasons: (a) the cache directory moved during v1.1 development from `~/.materialx-cache/` to a `platformdirs`-based location (exposed as `threejs_materials.CACHE_DIR`), and (b) the sRGB-color convention above invalidates any cached materials produced by an earlier v1.1 prerelease.

  To migrate:
  1. Delete the legacy cache: `rm -rf ~/.materialx-cache/` (no longer used).
  2. If you've used a v1.1 prerelease, also clear the new location:
     ```python
     from threejs_materials import clear_cache
     clear_cache()
     ```
     (or `rm -rf "$(python -c 'from threejs_materials import CACHE_DIR; print(CACHE_DIR)')"`).

  The cache repopulates on next access.

## Features

- **`PbrProperties.from_pymat()`** — new factory that converts a Three.js-style PBR dict (with keys like `map`, `normalMap`, `roughnessMap`, `color_hex`) into a `PbrProperties`. Numeric color tuples are interpreted as sRGB byte ratios (matches build123d's `Color` class output and three-cad-viewer's downstream consumption). Complements the existing `from_gltf`, `from_mtlx`, and `from_dict` factories.
- **Full `override()` kwargs parity with `PbrValues`** — `override()` now accepts every scalar/color field on `PbrValues`. Previously missing: `transparent`, `alpha_test`, `specular_color`, `iridescence_ior`, `iridescence_thickness_range`, `dispersion`, `normal_scale`, `displacement_scale`, `side`.
- **`_parse_color_string(color, as_linear=True)`** — utility gained an `as_linear` flag. Default `True` keeps the sRGB→linear decoding used for legacy/internal flows; `False` returns raw `byte/255` ratios for display-space uses.
- **Typed config dataclasses for clients: `PbrOverrides`, `TextureTransform`** — frozen dataclasses that mirror `override()` / `scale()` kwargs, exposed at the package root. Clients construct them once and pass via `mat.override(**ov.as_kwargs())` / `mat.scale(**t.as_kwargs())`. Hashable and shareable. `__post_init__` normalizes per-field convention (sRGB for `color`, linear for the others).
- **Permissive `Color` type with per-field normalization** — `Color = str | tuple[float, ...] | list[float]` is the new public alias accepted by `override`, `create`, `from_pymat`, `PbrOverrides`. Accepts `"#rrggbb"`, `"#rrggbbaa"`, CSS names, 3-tuples, and 4-tuples. **String inputs are always sRGB**; numeric-tuple convention is field-dependent (sRGB for `color`, linear for `emissive`/`sheen_color`/`specular_color`/`attenuation_color`). The 4th element / `aa` byte on `color` is lifted into the separate `opacity` field (explicit `opacity=` wins). Backed by two helpers: `_normalize_color` (linear output) and `_normalize_srgb_color` (sRGB output).
- **`PbrOverrides` extensibility hook + int color support** — `PbrOverrides._color_to_tuple(c)` is a new static method that subclasses can override to bridge custom color types (e.g. build123d's `Color`) into the standard `Color` form before normalization. Default is passthrough. `__post_init__` also handles `int` inputs (e.g. `PbrOverrides(color=0xff0000)`) by converting to `"#rrggbb"` before the hook runs — subclasses inherit this for free without re-implementing the int check. `bool` is correctly excluded (Python's `bool` is a subclass of `int`).
- **`PbrOverrides` color field type hints widened to `Color | None`** — the 5 color fields (`color`, `emissive`, `sheen_color`, `specular_color`, `attenuation_color`) now annotate the input type the constructor actually accepts, not the post-normalization 3-tuple storage form. Type checkers no longer reject `PbrOverrides(color="#ff0000")`. The class docstring documents the input-type-vs-stored-form gap.
- **`interpolate_color(override_color=None)` parameter** — when set, replaces `values.color` for the preview; matches what `mat.override(color=override_color)` would render at runtime, without materializing a new `PbrProperties`. With a color texture present, the multiplied result (`texture × override` in linear space) is rescaled to the texture's Rec.709 luminance so the preview brightness tracks the rendered preview rather than collapsing to near-black for non-neutral tints. Pure-black overrides stay black; the rescale is clamped (8×) to keep extreme cases bounded. Useful for CAD-viewer preview color computation when a tint will be applied to a textured material.

## Behavior changes

- **`override()` and `scale()` produce hashed variant ids** — the returned material carries `id = f"{name}_{hash}"` where `hash = blake2b((parent_id, kwargs))[:8]`. Same kwargs on the same parent → same id (deterministic across Python sessions); chained calls cascade the parent id into the hash so different chains stay distinguishable. Fixes the silent-collision bug where `{m.id: m for m in [mat, mat.override(color=red)]}` collapsed to one entry, losing the variant. The `name` (display) field is preserved unchanged.
- **`PbrProperties.create()` rewritten as pure passthrough** — every kwarg now defaults to `None` and is emitted to `PbrValues` / `PbrMaps` only when the caller explicitly provides it. No auto-defaults, no map-aware neutral multipliers, no silent `values.color = [0.8, 0.8, 0.8]` style substitutions. Fields left unset get Three.js/glTF's own defaults at render time.
- **`interpolate_color()` ignores `values.color` when a color texture is present** — the scalar is physically correct for Three.js rendering (multiplies into the texture), but including it in the preview produces a pre-tone-mapping color visibly darker than the on-screen render. The texture's linear-space average is now used directly.
- **Opacity textures now auto-enable masking** — whenever an opacity texture survives conversion, `alphaTest=0.5` is emitted so Three.js actually respects the `alphaMap` (otherwise the material silently renders fully opaque). Chose pure MASK over BLEND to avoid depth-sort bleed-through on closed shapes like spheres.
- **`iridescenceThicknessRange` emitted as `[X, X]`** (was `[0, X]`) across all three shader models. Matches MaterialX's single-scalar `thin_film_thickness` / `iridescence_thickness` semantics; the former `0` minimum was an invented value with no spec grounding.

## Fixes

- **Three glTF textures now round-trip losslessly** — `clearcoatRoughnessTexture`, `sheenRoughnessTexture`, and `anisotropyTexture` are now written to `KHR_materials_clearcoat`, `KHR_materials_sheen`, and `KHR_materials_anisotropy` on export and restored on import. Previously the corresponding `PbrMaps` fields (`clearcoat_roughness`, `sheen_roughness`, `anisotropy`) were accepted by `library.py` but silently dropped by `to_gltf()` / `from_gltf()`.
- **ambientCG PNG → JPG fallback** — when `{asset}_{res}-PNG.zip` returns 404, the loader now automatically falls back to `{asset}_{res}-JPG.zip`. ambientCG has begun shipping JPG-only packages for some materials.
- **Procedural feature inputs no longer silently dropped** — 11 sites across `standard_surface`, `gltf_pbr`, and `open_pbr_surface` where a feature (transmission, clearcoat, sheen, emission, iridescence, thickness, …) was gated on `scalar > 0`. When the author wired the scalar through a procedural graph, the baker put the result in `textures` (leaving `params[scalar] == 0`), the gate failed, and both the scalar and the texture were silently dropped. Each gate now also checks `has_tex(primary_texture)` and promotes the scalar to the neutral multiplier.
- **MaterialX TextureBaker IOR recovery** — MaterialX 1.39.x TextureBaker does not preserve graph-connected scalar shader inputs; it rewrites them to a placeholder `value="1"`. This silently broke 6 GPUOpen materials (Old Paint + the Marble family) whose `specular_IOR` is wired through a constant-valued nodegraph, producing `ior=1.0` in the cache instead of the authored 1.39–1.80. A narrowly-scoped pre-bake walker now evaluates `constant | dot | convert` chains for `specular_IOR` / `coat_IOR` / `thin_film_IOR` and restores the author's value over the baker's clobbered one.
- **`open_pbr_surface.geometry_opacity` texture** — ambientCG Smear 005 (and similar decals) wired their opacity mask to `geometry_opacity`, but `convert.py` only read the scalar and silently dropped the texture. Now emitted as `maps.opacity` with `alphaTest=0.5`.
- **`gltf_pbr.sheen_roughness` texture** — baked sheen_roughness graphs were dropped on the gltf_pbr path. Now wired through with the scalar promoted to `1.0` when the texture is present.
- **`from_pymat` color handling consistent with `create`/`override`** — hex/named/numeric color inputs are now normalized through the same per-field path. Stored sRGB on `values.color` (matches three-cad-viewer's `setRGB(SRGBColorSpace)` consumption); see Breaking changes for the convention shift.
- **`create(color=4-tuple)` no longer drops alpha** — previously the 4th element was silently truncated. Now lifted into `opacity` (explicit `opacity=` still wins). Hex with alpha (`"#rrggbbaa"`) lifts the same way.
- **String colors no longer silently dropped on glTF export** — `values.color = "#ff8000"` previously fell through `_build_pbr`'s `isinstance(color, list)` check and exported as `[1.0, 1.0, 1.0]` (color lost). Now normalized before the linear conversion at the boundary, so all input forms (hex, name, list, tuple) survive export.
- **glTF round-trip for color is lossless within float precision** — `to_gltf()` does sRGB→linear at `_build_pbr`; `from_gltf()` does linear→sRGB on read. Round-trip preserves the input value.
- **`from_gltf()` no longer drops textures stored in bufferViews** — `.glb` files (and any glTF that stores images inline as bufferViews instead of as `uri` fields) had their textures silently dropped on import: `PbrMaps()` came back empty even though the file's `images` array was populated. The pygltflib `convert_images(ImageFormat.DATAURI)` call now also fires for bufferView-stored images, not only file-URI references. pygltflib's bufferView-extraction path is also noisy (prints internal state to stdout, emits over-paranoid "may corrupt the GLTF" warnings) — both are now suppressed at the call site since the resulting data URIs are correct.

## Internals / refactor

- **`gltf_pbr` sheen block** — stylistic rewrite for pattern consistency with clearcoat/transmission/iridescence blocks. Same behavior, explicit "texture → neutral multiplier" written the same way throughout. Comment clarifies why `gltf_pbr` hardcodes `sheen=1.0` (no independent weight input in the glTF shader).

## Tests

- ~60 new tests across `test_convert.py`, `test_library.py`, `test_gltf.py`, and `tests/test_sources_ambientcg.py` covering every fix and behavior change above. Includes dedicated `TestNormalizeColor` (linear path) and `TestNormalizeSrgbColor` (sRGB path) classes, normalization tests on `PbrOverrides`/`create`/`override`/`from_pymat` covering each input form (hex 6/8, named, 3-tuple, 4-tuple) for both paths, explicit-opacity-wins precedence, glTF wire-format linearity assertion, midgray round-trip (catches sign/factor errors that pass on extremes like (1,0,0)), and `TestStringColorExport` for the string-color silent-drop regression. Full suite: **322 passing**.

## Docs

- README Three.js output table now lists `clearcoatRoughnessMap`, `sheenRoughnessMap`, and `anisotropyMap` (previously shown as unsupported).
- README Customization section gains a **Variant ids** subsection documenting the `{name}_{hash}` pattern on `override()` / `scale()`.
- README `interpolate_color()` entry explains the new texture-only behavior (scalar ignored when texture is present, with reason).
- README "New in v1.0.0" `create()` bullet expanded to note the pure-passthrough semantics.
- README §"Consumer notes" gains a **Color space convention** subsection covering the asymmetric per-field convention (sRGB-stored `color`, linear-stored `emissive`/`sheen_color`/`specular_color`/`attenuation_color`), the `setRGB(SRGBColorSpace)` rationale, glTF boundary conversion in both directions, and the matching client-API contract for `override()` / `create()` / `from_pymat()` / `PbrOverrides` numeric-tuple inputs.
- Public-API docstring contracts on `PbrValues` (field-level color-space note), `PbrProperties.from_dict` / `from_pymat` / `create` / `override` (per-method per-field convention), `PbrOverrides` class docstring (asymmetric normalization), and the `Color` type alias.

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
