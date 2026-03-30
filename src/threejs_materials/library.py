"""Public API: load materials on demand with local JSON caching."""

import base64
import copy
import hashlib
import io
import json
import logging
import mimetypes
import shutil
import tempfile
import warnings
from pathlib import Path
from PIL import Image as PILImage
from PIL import ImageColor

from pygltflib import (
    GLTF2,
    ImageFormat,
    Image as GltfImage,
    NormalMaterialTexture,
    OcclusionTextureInfo,
    PbrMetallicRoughness,
    Sampler,
    Texture as GltfTexture,
    TextureInfo,
)
from pygltflib import Material as GltfMaterial

from threejs_materials.utils import ensure_materialx

log = logging.getLogger(__name__)

CACHE_DIR = Path.home() / ".materialx-cache"


def _is_data_uri(s: str) -> bool:
    """Return True if *s* is a base64 data URI."""
    return s.startswith("data:")


def _resolve_to_data_uri(texture_ref: str, texture_dir: Path) -> str:
    """Resolve a texture reference to a base64 data URI.

    If *texture_ref* is already a data URI it is returned unchanged.
    Otherwise it is treated as a filename relative to *texture_dir*
    and the file is read and base64-encoded.
    """
    if _is_data_uri(texture_ref):
        return texture_ref
    file_path = texture_dir / texture_ref
    mime, _ = mimetypes.guess_type(str(file_path))
    if mime is None:
        mime = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
        }.get(file_path.suffix.lower(), "application/octet-stream")
    b64 = base64.b64encode(file_path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _abbreviate_textures(obj):
    """Deep-copy a dict, replacing base64 data URIs with a short placeholder."""
    if isinstance(obj, dict):
        return {k: _abbreviate_textures(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_abbreviate_textures(v) for v in obj]
    if isinstance(obj, str) and obj.startswith("data:"):
        return "data:image/png;base64,..."
    return obj


def _cache_path(source: str, name: str, resolution: str | None) -> Path:
    """Build the cache file path for a material."""
    safe_name = name.lower().replace(" ", "_")
    if resolution:
        safe_res = resolution.lower().replace(" ", "_")
        filename = f"{source}_{safe_name}_{safe_res}.json"
    else:
        filename = f"{source}_{safe_name}.json"
    return CACHE_DIR / filename


def _collect_textures(
    properties: dict, tex_dir: Path | None, cache_tex_dir: Path
) -> None:
    """Copy texture files into *cache_tex_dir* and rewrite paths in *properties*.

    Texture references in *properties* are either file paths relative to
    *tex_dir* (new format from ``to_threejs_physical``) or base64 data URIs
    (old format / non-baked sources).  File-path textures are copied to
    *cache_tex_dir* and the references updated to just the filename.
    Data-URI textures are decoded and written as files.
    """
    has_textures = False
    for prop_name, prop in properties.items():
        if isinstance(prop, dict) and "texture" in prop:
            tex_ref = prop["texture"]
            if _is_data_uri(tex_ref):
                # Decode data URI and write to file
                has_textures = True
                if not cache_tex_dir.exists():
                    cache_tex_dir.mkdir(parents=True)
                # Determine extension from MIME type
                header, b64 = tex_ref.split(",", 1)
                mime = header.split(":")[1].split(";")[0]
                ext = {
                    "image/png": ".png",
                    "image/jpeg": ".jpg",
                }.get(mime, ".png")
                fname = prop_name + ext
                dst = cache_tex_dir / fname
                dst.write_bytes(base64.b64decode(b64))
                prop["texture"] = fname
            elif tex_dir is not None:
                # File path relative to tex_dir — copy to cache
                src = tex_dir / tex_ref
                if src.exists():
                    has_textures = True
                    if not cache_tex_dir.exists():
                        cache_tex_dir.mkdir(parents=True)
                    fname = prop_name + src.suffix
                    dst = cache_tex_dir / fname
                    shutil.copy2(src, dst)
                    prop["texture"] = fname

    if not has_textures and cache_tex_dir.exists():
        # Clean up empty directory
        shutil.rmtree(cache_tex_dir, ignore_errors=True)


def _linear_to_srgb(c: float) -> float:
    """Convert a single linear RGB component to sRGB (0-1)."""
    c = max(0.0, min(1.0, c))
    if c <= 0.0031308:
        return c * 12.92
    return 1.055 * (c ** (1.0 / 2.4)) - 0.055


def _srgb_to_linear(c: float) -> float:
    """Convert a single sRGB component to linear RGB (0-1)."""
    if c <= 0.04045:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


def _open_texture_image(ref: str, texture_dir: Path | None = None):
    """Open a texture as a PIL Image from a data URI or file path."""
    if _is_data_uri(ref):
        _, b64 = ref.split(",", 1)
        return PILImage.open(io.BytesIO(base64.b64decode(b64)))
    if texture_dir is not None:
        return PILImage.open(texture_dir / ref)
    return PILImage.open(ref)


def _has_real_alpha(ref: str, texture_dir: Path | None = None) -> bool:
    """Check if a texture has any non-opaque alpha pixels."""
    img = _open_texture_image(ref, texture_dir)
    if img.mode != "RGBA":
        return False
    alpha_min, _ = img.getchannel("A").getextrema()
    return alpha_min < 255


def _merge_opacity_into_color(
    color_ref: str | None,
    opacity_ref: str,
    texture_dir: Path | None = None,
) -> str:
    """Merge an RGB color texture and a grayscale opacity texture into RGBA PNG.

    If *color_ref* is ``None`` a white RGB image at the opacity texture's
    resolution is used instead.  Returns a ``data:image/png;base64,...`` URI.
    """
    opacity_img = _open_texture_image(opacity_ref, texture_dir).convert("L")

    if color_ref:
        color_img = _open_texture_image(color_ref, texture_dir).convert("RGB")
        if color_img.size != opacity_img.size:
            opacity_img = opacity_img.resize(color_img.size, PILImage.LANCZOS)
    else:
        color_img = PILImage.new("RGB", opacity_img.size, (255, 255, 255))

    rgba = color_img.copy()
    rgba.putalpha(opacity_img)

    buf = io.BytesIO()
    rgba.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def _average_texture_linear(
    ref: str, texture_dir: Path | None = None
) -> tuple[float, float, float]:
    """Return the average color of a texture in linear RGB."""
    img = _open_texture_image(ref, texture_dir).convert("RGB")
    avg = img.resize((1, 1), PILImage.LANCZOS).getpixel((0, 0))
    r, g, b = (_srgb_to_linear(c / 255.0) for c in avg[:3])
    return (r, g, b)


def _parse_color_string(color: str) -> tuple[float, float, float]:
    """Parse a CSS color name or hex string to linear RGB (0-1).

    Supports ``#rgb``, ``#rrggbb``, and CSS named colors (same set as Three.js).
    """
    r, g, b = ImageColor.getrgb(color)
    return (
        _srgb_to_linear(r / 255.0),
        _srgb_to_linear(g / 255.0),
        _srgb_to_linear(b / 255.0),
    )


# ---------------------------------------------------------------------------
# glTF helpers (pygltflib-based)
# ---------------------------------------------------------------------------

# WebGL / glTF sampler constants
_GL_LINEAR = 9729
_GL_LINEAR_MIPMAP_LINEAR = 9987
_GL_REPEAT = 10497

_DEFAULT_SAMPLER = Sampler(
    magFilter=_GL_LINEAR,
    minFilter=_GL_LINEAR_MIPMAP_LINEAR,
    wrapS=_GL_REPEAT,
    wrapT=_GL_REPEAT,
)


class _GltfBuilder:
    """Builds a self-contained ``pygltflib.GLTF2`` from Materials.

    File-path textures are resolved to base64 data URIs.  The resulting
    object can be saved directly as ``.glb`` or converted to external
    files via ``convert_images(ImageFormat.FILE)`` before saving as
    ``.gltf``.
    """

    def __init__(self) -> None:
        self.gltf = GLTF2(samplers=[copy.copy(_DEFAULT_SAMPLER)])
        self._uri_to_index: dict[str, int] = {}
        self._extensions_used: set[str] = set()

    def _register_image(self, uri: str, name: str | None = None) -> int:
        """Add a data-URI image (deduplicated) and return its texture index."""
        h = hashlib.sha256(uri.encode("ascii", errors="replace")).hexdigest()
        if h not in self._uri_to_index:
            self._uri_to_index[h] = len(self.gltf.images)
            mime = "image/png"
            if _is_data_uri(uri):
                mime = uri.split(":")[1].split(";")[0]
            ext = {
                "image/png": ".png",
                "image/jpeg": ".jpg",
            }.get(mime, ".png")
            img_name = (name or f"texture_{len(self.gltf.images)}") + ext
            self.gltf.images.append(GltfImage(uri=uri, mimeType=mime, name=img_name))
            self.gltf.textures.append(
                GltfTexture(source=len(self.gltf.images) - 1, sampler=0)
            )
        return self._uri_to_index[h]

    def _resolve_tex(self, ref: str | None, texture_dir: Path | None) -> str | None:
        """Resolve a texture reference to a data URI."""
        if ref is None:
            return None
        if _is_data_uri(ref):
            return ref
        if texture_dir is not None:
            return _resolve_to_data_uri(ref, texture_dir)
        return None

    def _tex_ref(self, ti: TextureInfo | None) -> dict | None:
        """Convert a TextureInfo to an extension-safe ``{"index": N, ...}`` dict."""
        if ti is None:
            return None
        ref: dict = {"index": ti.index}
        if ti.extensions:
            ref["extensions"] = ti.extensions
        return ref

    def add_material(self, material: "Material", name: str | None = None) -> None:
        """Convert a Material and append it to the GLTF2 document."""
        props = material.properties
        texture_dir = material._texture_dir
        tex_repeat = material.texture_repeat

        def val(prop_name: str):
            return props.get(prop_name, {}).get("value")

        def tex_uri(prop_name: str) -> str | None:
            return self._resolve_tex(
                props.get(prop_name, {}).get("texture"), texture_dir
            )

        def tex_info(prop_name: str) -> TextureInfo | None:
            uri = tex_uri(prop_name)
            if uri is None:
                return None
            ti = TextureInfo(index=self._register_image(uri, prop_name))
            if tex_repeat is not None:
                ti.extensions["KHR_texture_transform"] = {
                    "scale": list(tex_repeat),
                }
                self._extensions_used.add("KHR_texture_transform")
            return ti

        pbr = self._build_pbr(val, tex_uri, tex_info, tex_repeat)
        extensions = self._build_extensions(val, tex_uri, tex_info)

        # Alpha mode
        alpha_mode = "OPAQUE"
        alpha_cutoff = None
        alpha_test = val("alphaTest")
        if alpha_test is not None:
            alpha_mode = "MASK"
            alpha_cutoff = alpha_test
        elif val("transparent") is True:
            alpha_mode = "BLEND"
        elif props.get("opacity", {}).get("texture"):
            alpha_mode = "MASK"
            alpha_cutoff = 0.5

        # Emissive
        emissive_val = val("emissive")
        emissive_factor = (
            (emissive_val[:3] if isinstance(emissive_val, list) else [0.0, 0.0, 0.0])
            if emissive_val is not None
            else [0.0, 0.0, 0.0]
        )

        gmat = GltfMaterial(
            name=name or material.name,
            pbrMetallicRoughness=pbr,
            normalTexture=self._build_normal(val, tex_uri, tex_repeat),
            occlusionTexture=self._build_occlusion(tex_uri, tex_repeat),
            emissiveFactor=emissive_factor,
            emissiveTexture=tex_info("emissive"),
            alphaMode=alpha_mode,
            alphaCutoff=alpha_cutoff,
            doubleSided=val("side") == 2,
        )
        if extensions:
            gmat.extensions = extensions
            self._extensions_used.update(extensions.keys())

        self.gltf.materials.append(gmat)

    def _build_pbr(self, val, tex_uri, tex_info, tex_repeat):
        """Build PbrMetallicRoughness from internal properties."""
        color_val = val("color")
        opacity_val = val("opacity")
        alpha = float(opacity_val) if isinstance(opacity_val, (int, float)) else 1.0

        if color_val is not None and isinstance(color_val, list):
            base_color_factor = color_val[:3] + [alpha]
        elif color_val is not None or alpha < 1.0:
            base_color_factor = [1.0, 1.0, 1.0, alpha]
        else:
            base_color_factor = None

        # Base color texture (may need opacity merge)
        color_tex_uri = tex_uri("color")
        opacity_tex_uri = tex_uri("opacity")

        if color_tex_uri and opacity_tex_uri:
            merged = _merge_opacity_into_color(color_tex_uri, opacity_tex_uri)
            base_color_texture = self._make_tex_info(merged, "color", tex_repeat)
        elif opacity_tex_uri:
            merged = _merge_opacity_into_color(None, opacity_tex_uri)
            base_color_texture = self._make_tex_info(merged, "color", tex_repeat)
        else:
            base_color_texture = tex_info("color")

        # Metallic-roughness texture
        mr_ti = tex_info("metallicRoughness")
        if not mr_ti:
            mr_ti = tex_info("metalness") or tex_info("roughness")

        return PbrMetallicRoughness(
            baseColorFactor=base_color_factor or [1.0, 1.0, 1.0, 1.0],
            baseColorTexture=base_color_texture,
            metallicFactor=val("metalness") if val("metalness") is not None else 1.0,
            roughnessFactor=val("roughness") if val("roughness") is not None else 1.0,
            metallicRoughnessTexture=mr_ti,
        )

    def _make_tex_info(self, uri: str, name: str, tex_repeat) -> TextureInfo:
        """Create a TextureInfo from a data URI, with optional texture transform."""
        ti = TextureInfo(index=self._register_image(uri, name))
        if tex_repeat is not None:
            ti.extensions["KHR_texture_transform"] = {
                "scale": list(tex_repeat),
            }
            self._extensions_used.add("KHR_texture_transform")
        return ti

    def _build_normal(self, val, tex_uri, tex_repeat):
        """Build NormalMaterialTexture or return None."""
        uri = tex_uri("normal")
        if uri is None:
            return None
        scale = val("normalScale")
        if isinstance(scale, list):
            scale = scale[0]
        nmt = NormalMaterialTexture(
            index=self._register_image(uri, "normal"),
            scale=scale if scale is not None else 1.0,
        )
        if tex_repeat is not None:
            nmt.extensions["KHR_texture_transform"] = {
                "scale": list(tex_repeat),
            }
            self._extensions_used.add("KHR_texture_transform")
        return nmt

    def _build_occlusion(self, tex_uri, tex_repeat):
        """Build OcclusionTextureInfo or return None."""
        uri = tex_uri("ao")
        if uri is None:
            return None
        oti = OcclusionTextureInfo(index=self._register_image(uri, "ao"))
        if tex_repeat is not None:
            oti.extensions["KHR_texture_transform"] = {
                "scale": list(tex_repeat),
            }
            self._extensions_used.add("KHR_texture_transform")
        return oti

    def _build_extensions(self, val, tex_uri, tex_info) -> dict:
        """Build the KHR material extensions dict."""
        extensions: dict = {}

        ior = val("ior")
        if ior is not None:
            extensions["KHR_materials_ior"] = {"ior": ior}

        transmission = val("transmission")
        if transmission is not None and transmission > 0:
            ext: dict = {"transmissionFactor": transmission}
            if ref := self._tex_ref(tex_info("transmission")):
                ext["transmissionTexture"] = ref
            extensions["KHR_materials_transmission"] = ext

        # Volume
        volume: dict = {}
        thickness = val("thickness")
        if thickness is not None and thickness > 0:
            volume["thicknessFactor"] = thickness
            if ref := self._tex_ref(tex_info("thickness")):
                volume["thicknessTexture"] = ref
        att_color = val("attenuationColor")
        if att_color is not None:
            volume["attenuationColor"] = (
                att_color[:3] if isinstance(att_color, list) else att_color
            )
        att_dist = val("attenuationDistance")
        if att_dist is not None:
            volume["attenuationDistance"] = att_dist
        if volume:
            extensions["KHR_materials_volume"] = volume

        # Clearcoat
        clearcoat = val("clearcoat")
        if clearcoat is not None and clearcoat > 0:
            ext = {"clearcoatFactor": clearcoat}
            if ref := self._tex_ref(tex_info("clearcoat")):
                ext["clearcoatTexture"] = ref
            cc_rough = val("clearcoatRoughness")
            if cc_rough is not None:
                ext["clearcoatRoughnessFactor"] = cc_rough
            cc_uri = tex_uri("clearcoatNormal")
            if cc_uri is not None:
                ext["clearcoatNormalTexture"] = {
                    "index": self._register_image(cc_uri, "clearcoatNormal")
                }
            extensions["KHR_materials_clearcoat"] = ext

        # Sheen
        sheen = val("sheen")
        if sheen is not None and sheen > 0:
            ext = {}
            sheen_color = val("sheenColor")
            if sheen_color is not None:
                ext["sheenColorFactor"] = (
                    sheen_color[:3] if isinstance(sheen_color, list) else sheen_color
                )
            if ref := self._tex_ref(tex_info("sheenColor")):
                ext["sheenColorTexture"] = ref
            sheen_rough = val("sheenRoughness")
            if sheen_rough is not None:
                ext["sheenRoughnessFactor"] = sheen_rough
            extensions["KHR_materials_sheen"] = ext

        # Iridescence
        iridescence = val("iridescence")
        if iridescence is not None and iridescence > 0:
            ext = {"iridescenceFactor": iridescence}
            if ref := self._tex_ref(tex_info("iridescence")):
                ext["iridescenceTexture"] = ref
            iri_ior = val("iridescenceIOR")
            if iri_ior is not None:
                ext["iridescenceIor"] = iri_ior
            iri_range = val("iridescenceThicknessRange")
            if isinstance(iri_range, list) and len(iri_range) == 2:
                ext["iridescenceThicknessMinimum"] = iri_range[0]
                ext["iridescenceThicknessMaximum"] = iri_range[1]
            extensions["KHR_materials_iridescence"] = ext

        # Anisotropy
        anisotropy = val("anisotropy")
        if anisotropy is not None and anisotropy > 0:
            ext = {"anisotropyStrength": anisotropy}
            aniso_rot = val("anisotropyRotation")
            if aniso_rot is not None:
                ext["anisotropyRotation"] = aniso_rot
            extensions["KHR_materials_anisotropy"] = ext

        # Specular
        spec_intensity = val("specularIntensity")
        spec_color = val("specularColor")
        if spec_intensity is not None or spec_color is not None:
            ext = {}
            if spec_intensity is not None:
                ext["specularFactor"] = spec_intensity
            if ref := self._tex_ref(tex_info("specularIntensity")):
                ext["specularTexture"] = ref
            if spec_color is not None:
                ext["specularColorFactor"] = (
                    spec_color[:3] if isinstance(spec_color, list) else spec_color
                )
            if ref := self._tex_ref(tex_info("specularColor")):
                ext["specularColorTexture"] = ref
            extensions["KHR_materials_specular"] = ext

        # Emissive strength
        emissive_intensity = val("emissiveIntensity")
        if emissive_intensity is not None and emissive_intensity != 1.0:
            extensions["KHR_materials_emissive_strength"] = {
                "emissiveStrength": emissive_intensity,
            }

        # Dispersion
        dispersion = val("dispersion")
        if dispersion is not None and dispersion > 0:
            extensions["KHR_materials_dispersion"] = {"dispersion": dispersion}

        return extensions

    def build(self) -> GLTF2:
        """Finalize and return the GLTF2 document."""
        self.gltf.extensionsUsed = sorted(self._extensions_used)
        return self.gltf


def _build_gltf(
    materials: list["Material"],
    names: list[str] | None = None,
) -> GLTF2:
    """Build a self-contained ``pygltflib.GLTF2`` from one or more Materials."""
    builder = _GltfBuilder()
    for idx, material in enumerate(materials):
        mat_name = names[idx] if names else None
        builder.add_material(material, mat_name)
    return builder.build()


def collect_gltf_textures(materials: dict[str, "Material"]) -> GLTF2:
    """Convert multiple materials to a ``pygltflib.GLTF2`` with shared textures.

    Parameters
    ----------
    materials : dict[str, Material]
        Mapping of ``{name: Material}``.  The *name* is used as the
        glTF material name (overriding ``material.name``).

    Returns
    -------
    pygltflib.GLTF2
        A glTF 2.0 document with materials, images, textures, and samplers.
        Textures shared across materials are deduplicated.
    """
    mat_list = list(materials.values())
    name_list = list(materials.keys())
    return _build_gltf(mat_list, name_list)


class _SourceLoader:
    """Proxy providing ``.load()`` for a specific material source.

    The module reference is resolved lazily so that MaterialX need not
    be installed unless a source is actually loaded.
    """

    def __init__(self, source_name: str):
        self._source = source_name

    @property
    def _module(self):
        ensure_materialx()
        from threejs_materials.sources import ambientcg, gpuopen, polyhaven, physicallybased
        return {"ambientcg": ambientcg, "gpuopen": gpuopen,
                "polyhaven": polyhaven, "physicallybased": physicallybased}[self._source]

    def load(self, name: str, resolution: str = "1K") -> "Material":
        """Download, convert, and cache a material.

        Parameters
        ----------
        name : str
            Material name/ID as shown on the source website.
        resolution : str
            ``"1K"``, ``"2K"``, ``"4K"``, or ``"8K"`` (case-insensitive).
            Defaults to ``"1K"``. Ignored for PhysicallyBased.
        """
        label = f"{self._source} / {name}"
        res_key = resolution.upper()

        cache_file = _cache_path(self._source, name, res_key)
        if cache_file.exists():
            data = json.loads(cache_file.read_text())
            # Resolve relative _texture_dir against JSON location
            td = data.get("_texture_dir")
            if td is not None:
                data["_texture_dir"] = str((cache_file.parent / td).resolve())
            mat = Material(data)
            print(f"{label}: loading from cache — License: {mat.license}")
            return mat

        print(f"{label}: downloading ...", end=" ", flush=True)
        from threejs_materials.convert import _process_mtlx

        with tempfile.TemporaryDirectory() as tmp:
            result = self._module.fetch(name, res_key, Path(tmp))
            if result.mtlx_path:
                print("baking ...", end=" ", flush=True)
                properties, _, tex_dir = _process_mtlx(result.mtlx_path)
            else:
                properties = result.properties
                tex_dir = None
            for key, v in result.overrides.items():
                if key in properties:
                    properties[key]["value"] = v

            # Copy texture files to persistent cache directory
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_tex_dir = cache_file.with_suffix("")  # strip .json
            _collect_textures(properties, tex_dir, cache_tex_dir)

        output = {
            "id": name,
            "name": name,
            "source": self._source,
            "url": result.url,
            "license": result.license,
            "properties": properties,
        }
        # Store relative path in JSON, absolute in runtime Material
        if cache_tex_dir.exists():
            output["_texture_dir"] = cache_tex_dir.name  # relative for JSON

        cache_file.write_text(json.dumps(output, indent=2))
        print(f"saving ... done — License: {result.license}")

        # Use absolute path for the runtime Material
        if cache_tex_dir.exists():
            output["_texture_dir"] = str(cache_tex_dir)
        return Material(output)

    def __repr__(self):
        return f"Material.{self._source}"


class Material:
    """A loaded PBR material with Three.js MeshPhysicalMaterial properties."""

    __slots__ = (
        "id",
        "name",
        "source",
        "url",
        "license",
        "properties",
        "texture_repeat",
        "_texture_dir",
    )

    ambientcg = _SourceLoader("ambientcg")
    gpuopen = _SourceLoader("gpuopen")
    polyhaven = _SourceLoader("polyhaven")
    physicallybased = _SourceLoader("physicallybased")

    def __init__(self, data: dict):
        self.id: str = data["id"]
        self.name: str = data["name"]
        self.source: str = data["source"]
        self.url: str = data["url"]
        self.license: str = data["license"]
        self.properties: dict = data["properties"]
        self.texture_repeat: tuple | None = data.get("texture_repeat")
        td = data.get("_texture_dir")
        self._texture_dir: Path | None = Path(td) if td is not None else None

    # -----------------------------------------------------------------------
    # Factory methods
    # -----------------------------------------------------------------------

    @classmethod
    def from_gltf(
        cls,
        gltf: GLTF2,
    ) -> dict[str, "Material"]:
        """Import all materials from a ``pygltflib.GLTF2`` object.

        Returns a dict mapping material names to Material objects.
        Accepts both file-referenced and data-URI images.  File
        references are converted to data URIs automatically (requires
        the GLTF2 to have been loaded via ``GLTF2().load()`` so that
        pygltflib knows where the files are).

        Parameters
        ----------
        gltf : pygltflib.GLTF2
            A glTF 2.0 document loaded from disk or built
            programmatically.
        """
        # Convert any file-referenced images to data URIs
        if any(img.uri and not _is_data_uri(img.uri) for img in (gltf.images or [])):
            gltf.convert_images(ImageFormat.DATAURI)
        images = gltf.images or []
        textures_arr = gltf.textures or []

        def _resolve_tex_by_index(tex_idx: int | None) -> str | None:
            """Resolve a texture index to a data URI."""
            if tex_idx is None:
                return None
            if tex_idx >= len(textures_arr):
                return None
            tex_obj = textures_arr[tex_idx]
            src = tex_obj.source
            if src is None:
                src = tex_idx
            if src >= len(images):
                return None
            img = images[src]
            if img.uri and _is_data_uri(img.uri):
                return img.uri
            return None

        def _get_tex_repeat_from_info(ti) -> tuple | None:
            """Extract KHR_texture_transform scale from a TextureInfo."""
            if ti is None:
                return None
            exts = getattr(ti, "extensions", None) or {}
            transform = exts.get("KHR_texture_transform")
            if transform and "scale" in transform:
                s = transform["scale"]
                return (s[0], s[1])
            return None

        result: dict[str, "Material"] = {}

        for mat_index, mat in enumerate(gltf.materials):
            props: dict = {}

            def val(name, value):
                props.setdefault(name, {})["value"] = value

            def tex(name, tex_idx):
                uri = _resolve_tex_by_index(tex_idx)
                if uri:
                    props.setdefault(name, {})["texture"] = uri

            def tex_from_ext(name, ext_tex_ref):
                """Resolve a texture from an extension dict entry like {"index": N}."""
                if ext_tex_ref is None:
                    return
                idx = ext_tex_ref.get("index") if isinstance(ext_tex_ref, dict) else None
                if idx is not None:
                    tex(name, idx)

            # --- pbrMetallicRoughness ---
            pbr = mat.pbrMetallicRoughness
            if pbr is None:
                pbr = PbrMetallicRoughness()

            bcf = pbr.baseColorFactor or [1.0, 1.0, 1.0, 1.0]
            val("color", list(bcf[:3]))
            if len(bcf) > 3 and bcf[3] < 1.0:
                val("opacity", bcf[3])
                val("transparent", True)

            if pbr.baseColorTexture is not None:
                tex("color", pbr.baseColorTexture.index)

            val("metalness", pbr.metallicFactor)
            val("roughness", pbr.roughnessFactor)

            if pbr.metallicRoughnessTexture is not None:
                mr_idx = pbr.metallicRoughnessTexture.index
                tex("metalness", mr_idx)
                tex("roughness", mr_idx)

            # --- Top-level ---
            if mat.normalTexture is not None:
                tex("normal", mat.normalTexture.index)
                if mat.normalTexture.scale != 1.0:
                    val("normalScale", [mat.normalTexture.scale, mat.normalTexture.scale])

            if mat.occlusionTexture is not None:
                tex("ao", mat.occlusionTexture.index)

            if mat.emissiveFactor != [0.0, 0.0, 0.0]:
                val("emissive", list(mat.emissiveFactor))
            if mat.emissiveTexture is not None:
                tex("emissive", mat.emissiveTexture.index)

            # --- Alpha mode ---
            if mat.alphaMode == "BLEND":
                actually_transparent = True
                color_uri = props.get("color", {}).get("texture")
                if color_uri:
                    actually_transparent = _has_real_alpha(color_uri)
                if actually_transparent:
                    val("transparent", True)
            elif mat.alphaMode == "MASK":
                val("alphaTest", mat.alphaCutoff if mat.alphaCutoff is not None else 0.5)

            if mat.doubleSided:
                val("side", 2)

            # --- Extensions ---
            exts = mat.extensions or {}

            ext = exts.get("KHR_materials_ior", {})
            if "ior" in ext:
                val("ior", ext["ior"])

            ext = exts.get("KHR_materials_transmission", {})
            if "transmissionFactor" in ext:
                val("transmission", ext["transmissionFactor"])
            tex_from_ext("transmission", ext.get("transmissionTexture"))

            ext = exts.get("KHR_materials_volume", {})
            if "thicknessFactor" in ext:
                val("thickness", ext["thicknessFactor"])
            tex_from_ext("thickness", ext.get("thicknessTexture"))
            if "attenuationColor" in ext:
                val("attenuationColor", ext["attenuationColor"])
            if "attenuationDistance" in ext:
                val("attenuationDistance", ext["attenuationDistance"])

            ext = exts.get("KHR_materials_clearcoat", {})
            if "clearcoatFactor" in ext:
                val("clearcoat", ext["clearcoatFactor"])
            tex_from_ext("clearcoat", ext.get("clearcoatTexture"))
            if "clearcoatRoughnessFactor" in ext:
                val("clearcoatRoughness", ext["clearcoatRoughnessFactor"])
            tex_from_ext("clearcoatNormal", ext.get("clearcoatNormalTexture"))

            ext = exts.get("KHR_materials_sheen", {})
            if "sheenColorFactor" in ext:
                val("sheenColor", ext["sheenColorFactor"])
                val("sheen", 1.0)
            tex_from_ext("sheenColor", ext.get("sheenColorTexture"))
            if "sheenRoughnessFactor" in ext:
                val("sheenRoughness", ext["sheenRoughnessFactor"])

            ext = exts.get("KHR_materials_iridescence", {})
            if "iridescenceFactor" in ext:
                val("iridescence", ext["iridescenceFactor"])
            tex_from_ext("iridescence", ext.get("iridescenceTexture"))
            if "iridescenceIor" in ext:
                val("iridescenceIOR", ext["iridescenceIor"])
            iri_min = ext.get("iridescenceThicknessMinimum")
            iri_max = ext.get("iridescenceThicknessMaximum")
            if iri_min is not None and iri_max is not None:
                val("iridescenceThicknessRange", [iri_min, iri_max])

            ext = exts.get("KHR_materials_anisotropy", {})
            if "anisotropyStrength" in ext:
                val("anisotropy", ext["anisotropyStrength"])
            if "anisotropyRotation" in ext:
                val("anisotropyRotation", ext["anisotropyRotation"])

            ext = exts.get("KHR_materials_specular", {})
            if "specularFactor" in ext:
                val("specularIntensity", ext["specularFactor"])
            tex_from_ext("specularIntensity", ext.get("specularTexture"))
            if "specularColorFactor" in ext:
                val("specularColor", ext["specularColorFactor"])
            tex_from_ext("specularColor", ext.get("specularColorTexture"))

            ext = exts.get("KHR_materials_emissive_strength", {})
            if "emissiveStrength" in ext:
                val("emissiveIntensity", ext["emissiveStrength"])

            ext = exts.get("KHR_materials_dispersion", {})
            if "dispersion" in ext:
                val("dispersion", ext["dispersion"])

            # --- Texture repeat from KHR_texture_transform ---
            texture_repeat = None
            for ti in [
                pbr.baseColorTexture if pbr else None,
                pbr.metallicRoughnessTexture if pbr else None,
                mat.normalTexture,
                mat.occlusionTexture,
                mat.emissiveTexture,
            ]:
                tr = _get_tex_repeat_from_info(ti)
                if tr is not None:
                    texture_repeat = tr
                    break

            name = mat.name or f"material_{mat_index}"
            data = {
                "id": name,
                "name": name,
                "source": "gltf",
                "url": "",
                "license": "",
                "properties": props,
            }
            if texture_repeat is not None:
                data["texture_repeat"] = texture_repeat
            result[name] = cls(data)

        return result

    @classmethod
    def load_gltf(cls, gltf_file: str) -> dict[str, "Material"]:
        """Import all materials from a ``.gltf`` or ``.glb`` file on disk.

        Returns a dict mapping material names to Material objects.

        Parameters
        ----------
        gltf_file : str
            Path to a ``.gltf`` or ``.glb`` file.
        """
        gltf_path = Path(gltf_file).resolve()
        if not gltf_path.exists():
            raise FileNotFoundError(f"File not found: {gltf_path}")
        return cls.from_gltf(GLTF2().load(str(gltf_path)))

    @classmethod
    def from_mtlx(cls, mtlx_file: str) -> "Material":
        """Convert a local .mtlx file to a Material.

        Texture paths in the .mtlx are resolved relative to the file's location.
        If the material references textures that don't exist on disk, a
        ``FileNotFoundError`` is raised.
        """
        ensure_materialx()
        from threejs_materials.convert import _process_mtlx, extract_materials, load_document_with_stdlib

        mtlx_path = Path(mtlx_file).resolve()
        if not mtlx_path.exists():
            raise FileNotFoundError(f"File not found: {mtlx_path}")

        # Validate that referenced texture files exist
        doc, _ = load_document_with_stdlib(mtlx_path)
        orig_mats = extract_materials(doc)
        if orig_mats:
            base_dir = mtlx_path.parent
            missing = [
                tex_info["file"]
                for mat in orig_mats
                for tex_info in mat["textures"].values()
                if tex_info.get("file") and not (base_dir / tex_info["file"]).exists()
            ]
            if missing:
                raise FileNotFoundError(
                    f"Textures not found (relative to {base_dir}): {', '.join(missing)}"
                )

        baked_mtlx = mtlx_path.parent / "material.baked.mtlx"
        try:
            properties, _, tex_dir = _process_mtlx(mtlx_path)
        finally:
            baked_mtlx.unlink(missing_ok=True)

        name = mtlx_path.stem
        return cls(
            {
                "id": name,
                "name": name,
                "source": "local",
                "url": "",
                "license": "",
                "properties": properties,
                "_texture_dir": str(tex_dir),
            }
        )

    @classmethod
    def create(
        cls,
        id: str,
        *,
        # --- Scalar values (reasonable defaults) ---
        color=(0.8, 0.8, 0.8),
        metalness: float = 0.0,
        roughness: float = 0.5,
        ior: float = 1.5,
        transmission: float = 0.0,
        opacity: float = 1.0,
        transparent: bool = False,
        alphaTest: float | None = None,
        emissive: tuple | list | None = None,
        emissiveIntensity: float | None = None,
        clearcoat: float = 0.0,
        clearcoatRoughness: float = 0.0,
        sheen: float = 0.0,
        sheenColor: tuple | list | None = None,
        sheenRoughness: float = 0.0,
        anisotropy: float = 0.0,
        anisotropyRotation: float = 0.0,
        specularIntensity: float = 1.0,
        specularColor: tuple | list | None = None,
        attenuationColor: tuple | list | None = None,
        attenuationDistance: float | None = None,
        thickness: float = 0.0,
        iridescence: float = 0.0,
        iridescenceIOR: float = 1.3,
        iridescenceThicknessRange: tuple | list | None = None,
        dispersion: float = 0.0,
        normalScale: tuple | list | None = None,
        displacementScale: float | None = None,
        side: int | None = None,
        # --- Texture maps (data URI or file path, None = no texture) ---
        color_map: str | None = None,
        metalness_map: str | None = None,
        roughness_map: str | None = None,
        normal_map: str | None = None,
        emissive_map: str | None = None,
        ao_map: str | None = None,
        opacity_map: str | None = None,
        clearcoat_map: str | None = None,
        clearcoatRoughness_map: str | None = None,
        clearcoatNormal_map: str | None = None,
        transmission_map: str | None = None,
        sheenColor_map: str | None = None,
        sheenRoughness_map: str | None = None,
        anisotropy_map: str | None = None,
        iridescence_map: str | None = None,
        specularIntensity_map: str | None = None,
        specularColor_map: str | None = None,
        thickness_map: str | None = None,
        displacement_map: str | None = None,
    ) -> "Material":
        """Create a Material from explicit PBR values and texture paths.

        Parameters
        ----------
        id : str
            Material identifier (also used as name).

        Scalar parameters use Three.js ``MeshPhysicalMaterial`` defaults.
        Texture parameters accept a ``data:`` URI or a local file path
        (which will be read and base64-encoded automatically).

        Example::

            mat = Material.create(
                "walnut",
                color=(0.4, 0.2, 0.1),
                roughness=0.8,
                normal_map="bakes/Cube_Normal.png",
                color_map="bakes/Cube_Diffuse.png",
                roughness_map="bakes/Cube_Roughness.png",
            )
        """
        texture_dirs: list[Path] = []

        def _resolve_texture(tex: str | None) -> str | None:
            if tex is None:
                return None
            if tex.startswith("data:"):
                return tex
            p = Path(tex).resolve()
            if p.exists():
                texture_dirs.append(p.parent)
                return p.name  # store just the filename
            raise FileNotFoundError(f"Texture file not found: {tex}")

        props: dict = {}

        # --- Build properties with values ---
        if isinstance(color, str):
            props["color"] = {"value": list(_parse_color_string(color))}
        else:
            props["color"] = {"value": list(color)[:3]}
        props["metalness"] = {"value": metalness}
        props["roughness"] = {"value": roughness}
        props["ior"] = {"value": ior}

        if transmission > 0:
            props["transmission"] = {"value": transmission}
        if opacity < 1.0:
            props["opacity"] = {"value": opacity}
        if transparent:
            props["transparent"] = {"value": True}
        if alphaTest is not None:
            props["alphaTest"] = {"value": alphaTest}
        if emissive is not None:
            props["emissive"] = {"value": list(emissive[:3])}
        if emissiveIntensity is not None:
            props["emissiveIntensity"] = {"value": emissiveIntensity}
        if clearcoat > 0:
            props["clearcoat"] = {"value": clearcoat}
            props["clearcoatRoughness"] = {"value": clearcoatRoughness}
        if sheen > 0:
            props["sheen"] = {"value": sheen}
            if sheenColor is not None:
                props["sheenColor"] = {"value": list(sheenColor[:3])}
            props["sheenRoughness"] = {"value": sheenRoughness}
        if anisotropy > 0:
            props["anisotropy"] = {"value": anisotropy}
            props["anisotropyRotation"] = {"value": anisotropyRotation}
        if specularIntensity != 1.0:
            props["specularIntensity"] = {"value": specularIntensity}
        if specularColor is not None:
            props["specularColor"] = {"value": list(specularColor[:3])}
        if attenuationColor is not None:
            props["attenuationColor"] = {"value": list(attenuationColor[:3])}
        if attenuationDistance is not None:
            props["attenuationDistance"] = {"value": attenuationDistance}
        if thickness > 0:
            props["thickness"] = {"value": thickness}
        if iridescence > 0:
            props["iridescence"] = {"value": iridescence}
            props["iridescenceIOR"] = {"value": iridescenceIOR}
            if iridescenceThicknessRange is not None:
                props["iridescenceThicknessRange"] = {
                    "value": list(iridescenceThicknessRange)
                }
        if dispersion > 0:
            props["dispersion"] = {"value": dispersion}
        if normalScale is not None:
            props["normalScale"] = {"value": list(normalScale)}
        if displacementScale is not None:
            props["displacementScale"] = {"value": displacementScale}
        if side is not None:
            props["side"] = {"value": side}

        # --- Resolve and attach textures ---
        tex_map = {
            "color": color_map,
            "metalness": metalness_map,
            "roughness": roughness_map,
            "normal": normal_map,
            "emissive": emissive_map,
            "ao": ao_map,
            "opacity": opacity_map,
            "clearcoat": clearcoat_map,
            "clearcoatRoughness": clearcoatRoughness_map,
            "clearcoatNormal": clearcoatNormal_map,
            "transmission": transmission_map,
            "sheenColor": sheenColor_map,
            "sheenRoughness": sheenRoughness_map,
            "anisotropy": anisotropy_map,
            "iridescence": iridescence_map,
            "specularIntensity": specularIntensity_map,
            "specularColor": specularColor_map,
            "thickness": thickness_map,
            "displacement": displacement_map,
        }
        for prop_name, tex_path in tex_map.items():
            uri = _resolve_texture(tex_path)
            if uri:
                props.setdefault(prop_name, {})["texture"] = uri
                # Set neutral scalar when texture is present
                if prop_name == "color" and "value" in props.get("color", {}):
                    props["color"]["value"] = [1.0, 1.0, 1.0]
                elif prop_name in ("metalness", "roughness") and prop_name in props:
                    props[prop_name]["value"] = 1.0

        data = {
            "id": id,
            "name": id,
            "source": "custom",
            "url": "",
            "license": "",
            "properties": props,
        }
        if texture_dirs:
            # All texture files must be in the same directory
            common = texture_dirs[0]
            if not all(d == common for d in texture_dirs):
                raise ValueError("All texture files must be in the same directory")
            data["_texture_dir"] = str(common)
        return cls(data)

    # -----------------------------------------------------------------------
    # Transforms
    # -----------------------------------------------------------------------

    def override(
        self,
        *,
        color=None,
        roughness=None,
        metalness=None,
        ior=None,
        transmission=None,
        opacity=None,
        clearcoat=None,
        clearcoatRoughness=None,
        sheen=None,
        sheenColor=None,
        sheenRoughness=None,
        anisotropy=None,
        anisotropyRotation=None,
        specularIntensity=None,
        emissionColor=None,
        emissionIntensity=None,
        attenuationColor=None,
        attenuationDistance=None,
        thickness=None,
        thinFilmThickness=None,
    ) -> "Material":
        """Return a new Material with property overrides.

        Each parameter sets the ``value`` of the corresponding property,
        creating it if absent.

        For ``color``, if a texture exists it is removed and replaced by
        the solid color value.  A warning is logged so the caller knows
        the texture was dropped.
        """
        props = {
            k: v
            for k, v in {
                "color": color,
                "roughness": roughness,
                "metalness": metalness,
                "ior": ior,
                "transmission": transmission,
                "opacity": opacity,
                "clearcoat": clearcoat,
                "clearcoatRoughness": clearcoatRoughness,
                "sheen": sheen,
                "sheenColor": sheenColor,
                "sheenRoughness": sheenRoughness,
                "anisotropy": anisotropy,
                "anisotropyRotation": anisotropyRotation,
                "specularIntensity": specularIntensity,
                "emissionColor": emissionColor,
                "emissionIntensity": emissionIntensity,
                "attenuationColor": attenuationColor,
                "attenuationDistance": attenuationDistance,
                "thickness": thickness,
                "thinFilmThickness": thinFilmThickness,
            }.items()
            if v is not None
        }
        new_props = copy.deepcopy(self.properties)
        for key, value in props.items():
            if isinstance(value, tuple):
                value = list(value)
            if key == "color" and "texture" in new_props.get("color", {}):
                del new_props["color"]["texture"]
                warnings.warn(
                    "color override: existing color texture removed and "
                    "replaced by solid color value",
                    stacklevel=2,
                )
            new_props.setdefault(key, {})["value"] = value
        data = self._raw_data()
        data["properties"] = new_props
        return Material(data)

    def scale(self, u: float, v: float) -> "Material":
        """Return a new Material with texture scale applied.

        ``scale(2, 2)`` makes the texture appear 2x larger, which
        corresponds to ``textureRepeat = (0.5, 0.5)`` in Three.js.

        Parameters
        ----------
        u, v : float
            Scale factors for the U and V axes.
        """
        data = self._raw_data()
        data["texture_repeat"] = (1.0 / u, 1.0 / v)
        return Material(data)

    def _raw_data(self) -> dict:
        """Return a raw data dict preserving file-path texture references."""
        d = {
            "id": self.id,
            "name": self.name,
            "source": self.source,
            "url": self.url,
            "license": self.license,
            "properties": copy.deepcopy(self.properties),
        }
        if self.texture_repeat is not None:
            d["texture_repeat"] = self.texture_repeat
        if self._texture_dir is not None:
            d["_texture_dir"] = str(self._texture_dir)
        return d

    # -----------------------------------------------------------------------
    # Serialization: Three.js output
    # -----------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Return the full material as a plain dict with base64 data-URI textures.

        File-path texture references are resolved to base64 data URIs
        so the result is self-contained and ready for the viewer.
        """
        d = self._raw_data()
        if self._texture_dir:
            for prop in d["properties"].values():
                if isinstance(prop, dict) and "texture" in prop:
                    tex = prop["texture"]
                    if not _is_data_uri(tex):
                        prop["texture"] = _resolve_to_data_uri(tex, self._texture_dir)
        # Use camelCase key for external consumers
        tr = d.pop("texture_repeat", None)
        if tr is not None:
            d["textureRepeat"] = list(tr)
        d.pop("_texture_dir", None)
        return d

    def to_json(self, **kwargs) -> str:
        """Serialize to JSON string. Keyword args are passed to ``json.dumps``."""
        kwargs.setdefault("indent", 2)
        return json.dumps(self.to_dict(), **kwargs)

    # -----------------------------------------------------------------------
    # Serialization: glTF I/O
    # -----------------------------------------------------------------------

    def to_gltf(self) -> GLTF2:
        """Convert to a ``pygltflib.GLTF2`` document.

        Returns a self-contained glTF 2.0 document with materials, images,
        textures, and samplers.  Properties with no glTF equivalent
        (``displacement``, ``displacementScale``) are silently dropped.
        """
        return _build_gltf([self])

    def save_gltf(self, path: str | Path, *, overwrite: bool = False) -> None:
        """Save the material as a ``.gltf`` or ``.glb`` file.

        The format is chosen automatically from the file extension.
        For ``.gltf``, textures are written as separate files in a
        companion directory (e.g. ``wood.gltf`` + ``wood/color.png``).
        For ``.glb``, textures are embedded in the binary file.

        Parameters
        ----------
        path : str or Path
            Output file path (``.gltf`` or ``.glb``).
        overwrite : bool
            If ``False`` (default), raise ``FileExistsError`` when *path*
            or its companion texture directory already exist.  If ``True``,
            overwrite the file and texture files in the directory.
        """
        path = Path(path)
        is_gltf = path.suffix.lower() == ".gltf"

        if not overwrite and path.exists():
            raise FileExistsError(f"File already exists: {path}")

        gltf = self.to_gltf()  # always data URIs

        if is_gltf and gltf.images:
            tex_dir = path.with_suffix("")  # e.g. wood.gltf → wood/
            if not overwrite and tex_dir.exists():
                raise FileExistsError(f"Companion path already exists: {tex_dir}")
            if tex_dir.exists() and not tex_dir.is_dir():
                raise FileExistsError(
                    f"Cannot overwrite: {tex_dir} exists and is not a directory"
                )
            # Let pygltflib extract data URIs to external files
            tex_dir.mkdir(parents=True, exist_ok=True)
            gltf.convert_images(ImageFormat.FILE, path=str(tex_dir), override=overwrite)
            # Make URIs relative to the .gltf file
            for img in gltf.images:
                if img.uri and not _is_data_uri(img.uri):
                    img.uri = path.stem + "/" + img.uri

        gltf.save(str(path))

    # -----------------------------------------------------------------------
    # Display
    # -----------------------------------------------------------------------

    def dump(self, gltf: bool = False, json_format: bool = False) -> str:
        """Return a human-readable summary of the material properties.

        When *gltf* is ``True`` the glTF property structure is shown
        instead of the Three.js layout.  When *json_format* is ``True``
        the output is valid JSON with textures abbreviated.
        """
        if json_format:
            if gltf:
                data = json.loads(self.to_gltf().to_json())
            else:
                data = self.to_dict()
            return json.dumps(_abbreviate_textures(data), indent=2)

        lines = [
            f"Material(name={self.name!r}, source={self.source!r}, "
            f"license={self.license!r})"
        ]
        if self._texture_dir is not None:
            lines.append(f"  _texture_dir: {self._texture_dir.name}")
        if gltf:
            data = _abbreviate_textures(json.loads(self.to_gltf().to_json()))
            self._dump_nested(data, lines, indent=2)
        else:
            for key, prop in self.properties.items():
                parts = []
                if "value" in prop:
                    parts.append(f"value={prop['value']}")
                if "texture" in prop:
                    tex = prop["texture"]
                    if _is_data_uri(tex):
                        parts.append("texture='data:image/...;base64,...'")
                    else:
                        parts.append(f"texture='{tex}'")
                lines.append(f"  {key}: {', '.join(parts)}")
        return "\n".join(lines)

    @staticmethod
    def _dump_nested(obj, lines, indent=2):
        """Recursively format a nested dict/list for dump output."""
        prefix = " " * indent
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, str) and v.startswith("data:"):
                    lines.append(f"{prefix}{k}: 'data:image/png;base64,...'")
                elif isinstance(v, dict):
                    lines.append(f"{prefix}{k}:")
                    Material._dump_nested(v, lines, indent + 2)
                elif isinstance(v, list) and v and isinstance(v[0], dict):
                    lines.append(f"{prefix}{k}:")
                    for i, item in enumerate(v):
                        lines.append(f"{prefix}  [{i}]:")
                        Material._dump_nested(item, lines, indent + 4)
                else:
                    lines.append(f"{prefix}{k}: {v}")

    def __repr__(self) -> str:
        return self.dump()

    # -----------------------------------------------------------------------
    # Utilities
    # -----------------------------------------------------------------------

    @classmethod
    def list_sources(cls) -> None:
        """Print available material sources with clickable URLs."""
        loaders = [cls.ambientcg, cls.gpuopen, cls.polyhaven, cls.physicallybased]
        width = max(len(l._source) for l in loaders)
        print("Material sources:")
        for loader in loaders:
            label = f"Material.{loader._source}"
            url = loader._module.BROWSE_URL
            print(f"  {label:<{width + 10}}  {url}")

    def interpolate_color(self) -> tuple[float, float, float, float]:
        """Estimate a representative sRGB color + alpha for CAD mode display.

        Returns an ``(r, g, b, a)`` tuple with each component in 0-1 (sRGB).
        When the material has a color texture, the texture is averaged.
        When the color is a scalar (linear RGB), it is converted to sRGB.

        Transmission is mapped to partial transparency so glass-like
        materials look semi-transparent in CAD mode.

        Usage::

            wood = Material.gpuopen.load("Ivory Walnut Solid Wood")
            obj.material = "wood"
            obj.color = wood.interpolate_color()  # (0.53, 0.31, 0.18, 1.0)
        """
        props = self.properties
        color_prop = props.get("color", {})

        # --- Color ---
        # Three.js multiplies color × map texture, so when both exist we
        # multiply the scalar value by the average texture color.
        color_val = color_prop.get("value")
        if isinstance(color_val, str):
            r, g, b = _parse_color_string(color_val)
        elif "texture" in color_prop:
            tr, tg, tb = _average_texture_linear(
                color_prop["texture"], self._texture_dir
            )
            if isinstance(color_val, list):
                r, g, b = color_val[0] * tr, color_val[1] * tg, color_val[2] * tb
            else:
                r, g, b = tr, tg, tb
        elif isinstance(color_val, list):
            r, g, b = color_val[:3]
        else:
            r, g, b = 0.5, 0.5, 0.5

        # Linear → sRGB
        sr, sg, sb = _linear_to_srgb(r), _linear_to_srgb(g), _linear_to_srgb(b)

        # --- Alpha ---
        alpha = 1.0
        opacity_val = props.get("opacity", {}).get("value")
        if isinstance(opacity_val, (int, float)) and opacity_val < 1.0:
            alpha = float(opacity_val)
        else:
            transmission_val = props.get("transmission", {}).get("value")
            if isinstance(transmission_val, (int, float)) and transmission_val > 0:
                alpha = max(0.15, 1.0 - transmission_val * 0.7)

        return (round(sr, 4), round(sg, 4), round(sb, 4), round(alpha, 4))

    def __getitem__(self, key: str):
        return self.to_dict()[key]

    def __contains__(self, key: str) -> bool:
        return key in self.to_dict()

    # -----------------------------------------------------------------------
    # Cache management
    # -----------------------------------------------------------------------

    @classmethod
    def list_cache(cls) -> list[tuple[str, str]]:
        """List cached materials.

        Returns a sorted list of ``(source, name)`` tuples.
        Use ``Material.{source}.from_cache(name)`` to load.

        Example::

            Material.list_cache()
            # [('ambientcg', 'Metal 009'), ('gpuopen', 'Car Paint'), ...]

            # Load one:
            mat = Material.gpuopen.from_cache("Car Paint")
        """
        if not CACHE_DIR.exists():
            return []
        result = []
        for f in sorted(CACHE_DIR.iterdir()):
            if not f.is_file() or f.suffix != ".json":
                continue
            data = json.loads(f.read_text())
            source = data.get("source", "?")
            name = data.get("name", f.stem)
            result.append((source, name))
        return result

    @classmethod
    def clear_cache(cls, name: str | None = None, source: str | None = None) -> int:
        """Delete cached material files.

        Parameters
        ----------
        name : str, optional
            Only clear caches whose filename contains this name (case-insensitive).
        source : str, optional
            Only clear caches whose filename starts with this source prefix.

        Returns
        -------
        int
            Number of files deleted.
        """
        if not CACHE_DIR.exists():
            return 0
        if name is None and source is None:
            count = sum(1 for f in CACHE_DIR.iterdir() if f.is_file())
            shutil.rmtree(CACHE_DIR)
            return count
        count = 0
        for f in list(CACHE_DIR.iterdir()):
            if not f.is_file() or f.suffix != ".json":
                continue
            fname = f.name.lower()
            if source and not fname.startswith(source.lower() + "_"):
                continue
            if name and name.lower().replace(" ", "_") not in fname:
                continue
            f.unlink()
            # Also remove companion texture directory
            tex_dir = f.with_suffix("")
            if tex_dir.is_dir():
                shutil.rmtree(tex_dir)
            count += 1
        return count
