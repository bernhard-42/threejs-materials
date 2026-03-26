"""Public API: load materials on demand with local JSON caching."""

import copy
import json
import logging
import re
import shutil
import tempfile
from pathlib import Path

from threejs_materials.convert import (
    _process_mtlx,
    extract_materials,
    load_document_with_stdlib,
)
from threejs_materials.sources import ambientcg, gpuopen, polyhaven, physicallybased

log = logging.getLogger(__name__)

CACHE_DIR = Path.home() / ".materialx-cache"

_B64_RE = re.compile(r"(data:[^;]+;base64,).{30,}")


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


def _has_real_alpha(data_uri: str) -> bool:
    """Check if a base64-encoded texture has any non-opaque alpha pixels."""
    import base64
    import io

    from PIL import Image

    _, b64 = data_uri.split(",", 1)
    img = Image.open(io.BytesIO(base64.b64decode(b64)))
    if img.mode != "RGBA":
        return False
    alpha_min, _ = img.getchannel("A").getextrema()
    return alpha_min < 255


def _merge_opacity_into_color(color_uri: str | None, opacity_uri: str) -> str:
    """Merge an RGB color texture and a grayscale opacity texture into RGBA PNG.

    If *color_uri* is ``None`` a white RGB image at the opacity texture's
    resolution is used instead.  Returns a ``data:image/png;base64,...`` URI.
    """
    import base64
    import io

    from PIL import Image

    _, op_b64 = opacity_uri.split(",", 1)
    opacity_img = Image.open(io.BytesIO(base64.b64decode(op_b64))).convert("L")

    if color_uri:
        _, col_b64 = color_uri.split(",", 1)
        color_img = Image.open(io.BytesIO(base64.b64decode(col_b64))).convert("RGB")
        # Resize opacity to match color if dimensions differ
        if color_img.size != opacity_img.size:
            opacity_img = opacity_img.resize(color_img.size, Image.LANCZOS)
    else:
        color_img = Image.new("RGB", opacity_img.size, (255, 255, 255))

    rgba = color_img.copy()
    rgba.putalpha(opacity_img)

    buf = io.BytesIO()
    rgba.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def _average_texture_linear(data_uri: str) -> tuple[float, float, float]:
    """Decode a base64 texture and return its average color in linear RGB."""
    import base64
    import io

    from PIL import Image

    _, b64 = data_uri.split(",", 1)
    img = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
    avg = img.resize((1, 1), Image.LANCZOS).getpixel((0, 0))
    # Pixel values are sRGB 0-255; convert to linear 0-1
    r, g, b = (_srgb_to_linear(c / 255.0) for c in avg[:3])
    return (r, g, b)


def _parse_color_string(color: str) -> tuple[float, float, float]:
    """Parse a CSS color name or hex string to linear RGB (0-1).

    Supports ``#rgb``, ``#rrggbb``, and CSS named colors (same set as Three.js).
    """
    from PIL import ImageColor

    r, g, b = ImageColor.getrgb(color)
    return (
        _srgb_to_linear(r / 255.0),
        _srgb_to_linear(g / 255.0),
        _srgb_to_linear(b / 255.0),
    )


# ---------------------------------------------------------------------------
# glTF helpers
# ---------------------------------------------------------------------------

# Default sampler: LINEAR mag, LINEAR_MIPMAP_LINEAR min, REPEAT wrap
_DEFAULT_SAMPLER = {
    "magFilter": 9729,
    "minFilter": 9987,
    "wrapS": 10497,
    "wrapT": 10497,
}


def _material_to_gltf(material: "Material") -> dict:
    """Convert a single Material to a glTF material dict with inline URIs.

    This is an internal helper — the URIs are later replaced with index
    references by :func:`_finalize_gltf`.
    """
    props = material.properties
    result: dict = {"name": material.name}

    def _get(name: str) -> dict:
        return props.get(name, {})

    def _val(name: str):
        return _get(name).get("value")

    def _tex(name: str) -> str | None:
        return _get(name).get("texture")

    def _tex_obj(name: str) -> dict | None:
        uri = _tex(name)
        if uri:
            return {"uri": uri}
        return None

    # --- pbrMetallicRoughness ---
    pbr: dict = {}

    color_val = _val("color")
    opacity_val = _val("opacity")
    alpha = float(opacity_val) if isinstance(opacity_val, (int, float)) else 1.0
    if color_val is not None:
        if isinstance(color_val, list):
            pbr["baseColorFactor"] = color_val[:3] + [alpha]
        else:
            pbr["baseColorFactor"] = [1.0, 1.0, 1.0, alpha]
    elif alpha < 1.0:
        pbr["baseColorFactor"] = [1.0, 1.0, 1.0, alpha]

    color_tex = _tex("color")
    opacity_tex = _tex("opacity")
    if color_tex and opacity_tex:
        pbr["baseColorTexture"] = {
            "uri": _merge_opacity_into_color(color_tex, opacity_tex)
        }
    elif opacity_tex:
        pbr["baseColorTexture"] = {"uri": _merge_opacity_into_color(None, opacity_tex)}
    elif color_tex:
        pbr["baseColorTexture"] = {"uri": color_tex}

    metalness_val = _val("metalness")
    if metalness_val is not None:
        pbr["metallicFactor"] = metalness_val
    roughness_val = _val("roughness")
    if roughness_val is not None:
        pbr["roughnessFactor"] = roughness_val

    mr_tex = _tex_obj("metallicRoughness")
    if mr_tex:
        pbr["metallicRoughnessTexture"] = mr_tex
    else:
        m_tex = _tex_obj("metalness")
        r_tex = _tex_obj("roughness")
        if m_tex:
            pbr["metallicRoughnessTexture"] = m_tex
        elif r_tex:
            pbr["metallicRoughnessTexture"] = r_tex

    if pbr:
        result["pbrMetallicRoughness"] = pbr

    # --- Top-level texture fields ---
    normal_tex = _tex_obj("normal")
    if normal_tex:
        normal_scale = _val("normalScale")
        if normal_scale is not None:
            if isinstance(normal_scale, list):
                normal_tex["scale"] = normal_scale[0]
            else:
                normal_tex["scale"] = normal_scale
        result["normalTexture"] = normal_tex

    ao_tex = _tex_obj("ao")
    if ao_tex:
        result["occlusionTexture"] = ao_tex

    emissive_val = _val("emissive")
    if emissive_val is not None:
        result["emissiveFactor"] = (
            emissive_val[:3] if isinstance(emissive_val, list) else emissive_val
        )
    emissive_tex = _tex_obj("emissive")
    if emissive_tex:
        result["emissiveTexture"] = emissive_tex

    # --- Alpha mode ---
    alpha_test = _val("alphaTest")
    transparent = _val("transparent")
    if alpha_test is not None:
        result["alphaMode"] = "MASK"
        result["alphaCutoff"] = alpha_test
    elif transparent is True:
        result["alphaMode"] = "BLEND"
    elif opacity_tex:
        result["alphaMode"] = "MASK"
        result["alphaCutoff"] = 0.5

    # --- doubleSided ---
    side = _val("side")
    if side == 2:
        result["doubleSided"] = True

    # --- Extensions ---
    extensions: dict = {}

    ior = _val("ior")
    if ior is not None:
        extensions["KHR_materials_ior"] = {"ior": ior}

    transmission = _val("transmission")
    if transmission is not None and transmission > 0:
        ext: dict = {"transmissionFactor": transmission}
        t_tex = _tex_obj("transmission")
        if t_tex:
            ext["transmissionTexture"] = t_tex
        extensions["KHR_materials_transmission"] = ext

    volume: dict = {}
    thickness = _val("thickness")
    if thickness is not None and thickness > 0:
        volume["thicknessFactor"] = thickness
        t_tex = _tex_obj("thickness")
        if t_tex:
            volume["thicknessTexture"] = t_tex
    att_color = _val("attenuationColor")
    if att_color is not None:
        volume["attenuationColor"] = (
            att_color[:3] if isinstance(att_color, list) else att_color
        )
    att_dist = _val("attenuationDistance")
    if att_dist is not None:
        volume["attenuationDistance"] = att_dist
    if volume:
        extensions["KHR_materials_volume"] = volume

    clearcoat = _val("clearcoat")
    if clearcoat is not None and clearcoat > 0:
        ext = {"clearcoatFactor": clearcoat}
        cc_tex = _tex_obj("clearcoat")
        if cc_tex:
            ext["clearcoatTexture"] = cc_tex
        cc_rough = _val("clearcoatRoughness")
        if cc_rough is not None:
            ext["clearcoatRoughnessFactor"] = cc_rough
        cc_normal = _tex_obj("clearcoatNormal")
        if cc_normal:
            ext["clearcoatNormalTexture"] = cc_normal
        extensions["KHR_materials_clearcoat"] = ext

    sheen = _val("sheen")
    if sheen is not None and sheen > 0:
        ext = {}
        sheen_color = _val("sheenColor")
        if sheen_color is not None:
            ext["sheenColorFactor"] = (
                sheen_color[:3] if isinstance(sheen_color, list) else sheen_color
            )
        sc_tex = _tex_obj("sheenColor")
        if sc_tex:
            ext["sheenColorTexture"] = sc_tex
        sheen_rough = _val("sheenRoughness")
        if sheen_rough is not None:
            ext["sheenRoughnessFactor"] = sheen_rough
        extensions["KHR_materials_sheen"] = ext

    iridescence = _val("iridescence")
    if iridescence is not None and iridescence > 0:
        ext = {"iridescenceFactor": iridescence}
        iri_tex = _tex_obj("iridescence")
        if iri_tex:
            ext["iridescenceTexture"] = iri_tex
        iri_ior = _val("iridescenceIOR")
        if iri_ior is not None:
            ext["iridescenceIor"] = iri_ior
        iri_range = _val("iridescenceThicknessRange")
        if (
            iri_range is not None
            and isinstance(iri_range, list)
            and len(iri_range) == 2
        ):
            ext["iridescenceThicknessMinimum"] = iri_range[0]
            ext["iridescenceThicknessMaximum"] = iri_range[1]
        extensions["KHR_materials_iridescence"] = ext

    anisotropy = _val("anisotropy")
    if anisotropy is not None and anisotropy > 0:
        ext = {"anisotropyStrength": anisotropy}
        aniso_rot = _val("anisotropyRotation")
        if aniso_rot is not None:
            ext["anisotropyRotation"] = aniso_rot
        extensions["KHR_materials_anisotropy"] = ext

    spec_intensity = _val("specularIntensity")
    spec_color = _val("specularColor")
    if spec_intensity is not None or spec_color is not None:
        ext = {}
        if spec_intensity is not None:
            ext["specularFactor"] = spec_intensity
        si_tex = _tex_obj("specularIntensity")
        if si_tex:
            ext["specularTexture"] = si_tex
        if spec_color is not None:
            ext["specularColorFactor"] = (
                spec_color[:3] if isinstance(spec_color, list) else spec_color
            )
        sc_tex = _tex_obj("specularColor")
        if sc_tex:
            ext["specularColorTexture"] = sc_tex
        extensions["KHR_materials_specular"] = ext

    emissive_intensity = _val("emissiveIntensity")
    if emissive_intensity is not None and emissive_intensity != 1.0:
        extensions["KHR_materials_emissive_strength"] = {
            "emissiveStrength": emissive_intensity,
        }

    dispersion = _val("dispersion")
    if dispersion is not None and dispersion > 0:
        extensions["KHR_materials_dispersion"] = {"dispersion": dispersion}

    if extensions:
        result["extensions"] = extensions

    return result


def _finalize_gltf(
    mat_dicts: list[dict],
    texture_repeats: list[tuple | None],
) -> dict:
    """Convert a list of glTF material dicts with inline URIs into the
    full glTF schema with shared ``images``/``textures``/``samplers`` arrays.

    *texture_repeats* is a parallel list of ``(u, v)`` tuples (or ``None``)
    corresponding to each material's texture repeat setting.
    """
    import hashlib

    uri_to_index: dict[str, int] = {}
    images: list[dict] = []

    def _register_uri(uri: str) -> int:
        h = hashlib.sha256(uri.encode("ascii", errors="replace")).hexdigest()
        if h not in uri_to_index:
            uri_to_index[h] = len(images)
            images.append({"uri": uri})
        return uri_to_index[h]

    def _replace_uris(obj, tex_repeat):
        """Recursively replace ``{"uri": "data:..."}`` with ``{"index": N}``."""
        if isinstance(obj, dict):
            if (
                "uri" in obj
                and isinstance(obj["uri"], str)
                and obj["uri"].startswith("data:")
            ):
                uri = obj.pop("uri")
                obj["index"] = _register_uri(uri)
                # Apply KHR_texture_transform if texture_repeat is set
                if tex_repeat is not None:
                    obj.setdefault("extensions", {})["KHR_texture_transform"] = {
                        "scale": list(tex_repeat),
                    }
                return
            for v in obj.values():
                _replace_uris(v, tex_repeat)
        elif isinstance(obj, list):
            for item in obj:
                _replace_uris(item, tex_repeat)

    # Collect extensions used across all materials
    all_extensions: set[str] = set()
    for mat_dict, tex_repeat in zip(mat_dicts, texture_repeats):
        _replace_uris(mat_dict, tex_repeat)
        if tex_repeat is not None:
            all_extensions.add("KHR_texture_transform")
        for ext_name in mat_dict.get("extensions", {}):
            all_extensions.add(ext_name)

    result: dict = {
        "asset": {"version": "2.0", "generator": "threejs-materials"},
        "materials": mat_dicts,
    }

    if images:
        result["images"] = images
        result["samplers"] = [dict(_DEFAULT_SAMPLER)]
        result["textures"] = [{"source": i, "sampler": 0} for i in range(len(images))]

    if all_extensions:
        result["extensionsUsed"] = sorted(all_extensions)

    return result


def collect_gltf_textures(materials: dict[str, "Material"]) -> dict:
    """Convert multiple materials to a glTF structure with shared textures.

    Parameters
    ----------
    materials : dict[str, Material]
        Mapping of ``{name: Material}``.  The *name* is used as the
        glTF material name (overriding ``material.name``).

    Returns
    -------
    dict
        Same schema as :meth:`Material.to_gltf` — ``images``,
        ``samplers``, ``textures``, ``materials``, ``extensionsUsed``.
        Textures shared across materials are deduplicated.
    """
    mat_dicts = []
    tex_repeats = []
    for name, mat in materials.items():
        d = _material_to_gltf(mat)
        d["name"] = name
        mat_dicts.append(d)
        tex_repeats.append(mat.texture_repeat)
    return _finalize_gltf(mat_dicts, tex_repeats)


class _SourceLoader:
    """Proxy providing ``.load()`` for a specific material source."""

    def __init__(self, module, source_name: str):
        self._module = module
        self._source = source_name

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
            mat = Material(json.loads(cache_file.read_text()))
            print(f"{label}: loading from cache — License: {mat.license}")
            return mat

        print(f"{label}: downloading ...", end=" ", flush=True)
        with tempfile.TemporaryDirectory() as tmp:
            result = self._module.fetch(name, res_key, Path(tmp))
            if result.mtlx_path:
                print("baking ...", end=" ", flush=True)
                properties, _ = _process_mtlx(result.mtlx_path)
            else:
                properties = result.properties
            for key, v in result.overrides.items():
                if key in properties:
                    properties[key]["value"] = v

        output = {
            "id": name,
            "name": name,
            "source": self._source,
            "url": result.url,
            "license": result.license,
            "properties": properties,
        }

        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(output, indent=2))
        print(f"saving ... done — License: {result.license}")

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
    )

    ambientcg = _SourceLoader(ambientcg, "ambientcg")
    gpuopen = _SourceLoader(gpuopen, "gpuopen")
    polyhaven = _SourceLoader(polyhaven, "polyhaven")
    physicallybased = _SourceLoader(physicallybased, "physicallybased")

    def __init__(self, data: dict):
        self.id: str = data["id"]
        self.name: str = data["name"]
        self.source: str = data["source"]
        self.url: str = data["url"]
        self.license: str = data["license"]
        self.properties: dict = data["properties"]
        self.texture_repeat: tuple | None = data.get("texture_repeat")

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
        for f in CACHE_DIR.iterdir():
            if not f.is_file() or f.suffix != ".json":
                continue
            fname = f.name.lower()
            if source and not fname.startswith(source.lower() + "_"):
                continue
            if name and name.lower().replace(" ", "_") not in fname:
                continue
            f.unlink()
            count += 1
        return count

    @classmethod
    def from_mtlx(cls, mtlx_file: str) -> "Material":
        """Convert a local .mtlx file to a Material.

        Texture paths in the .mtlx are resolved relative to the file's location.
        If the material references textures that don't exist on disk, a
        ``FileNotFoundError`` is raised.
        """
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
            properties, _ = _process_mtlx(mtlx_path)
        finally:
            baked_mtlx.unlink(missing_ok=True)

        name = mtlx_path.stem
        return cls({
            "id": name,
            "name": name,
            "source": "local",
            "url": "",
            "license": "",
            "properties": properties,
        })

    @classmethod
    def from_gltf_file(cls, gltf_file: str, index: int = 0) -> "Material":
        """Import a material from a ``.gltf`` file on disk.

        Texture file paths in the glTF are resolved relative to the
        file's directory and encoded as base64 data URIs.

        Parameters
        ----------
        gltf_file : str
            Path to a ``.gltf`` JSON file.
        index : int
            Index into the ``materials`` array (default ``0``).
        """
        gltf_path = Path(gltf_file).resolve()
        if not gltf_path.exists():
            raise FileNotFoundError(f"File not found: {gltf_path}")
        gltf_data = json.loads(gltf_path.read_text())
        return cls.from_gltf(gltf_data, index=index, base_dir=gltf_path.parent)

    @classmethod
    def from_gltf(
        cls,
        gltf_data: dict,
        index: int = 0,
        base_dir: Path | str | None = None,
    ) -> "Material":
        """Import a material from a glTF structure.

        Parameters
        ----------
        gltf_data : dict
            A glTF dict with ``images``, ``textures``, and ``materials``
            arrays — the same schema returned by :meth:`to_gltf` and
            :func:`collect_gltf_textures`, or parsed from a ``.gltf`` file.
        index : int
            Index into the ``materials`` array (default ``0``).
        base_dir : Path or str, optional
            Directory for resolving relative texture file paths.  Required
            when importing a glTF file that references textures by filename
            (e.g. Blender exports).  Not needed when textures are inline
            base64 data URIs.
        """
        from threejs_materials.convert import encode_texture_base64

        if base_dir is not None:
            base_dir = Path(base_dir)

        images = gltf_data.get("images", [])
        textures_arr = gltf_data.get("textures", [])
        mat = gltf_data["materials"][index]

        def _resolve_image_uri(uri: str) -> str | None:
            """Resolve an image URI to a base64 data URI."""
            from urllib.parse import unquote

            if uri.startswith("data:"):
                return uri
            # File path — URL-decode and resolve relative to base_dir
            if base_dir is not None:
                file_path = base_dir / unquote(uri)
                if file_path.exists():
                    return encode_texture_base64(file_path)
            return None

        def _resolve_tex(tex_ref: dict | None) -> str | None:
            """Resolve a texture reference to a data URI."""
            if tex_ref is None:
                return None
            if "uri" in tex_ref:
                return _resolve_image_uri(tex_ref["uri"])
            idx = tex_ref.get("index")
            if idx is not None and idx < len(textures_arr):
                src = textures_arr[idx].get("source", idx)
                if src < len(images):
                    uri = images[src].get("uri")
                    if uri:
                        return _resolve_image_uri(uri)
            return None

        def _get_tex_repeat(tex_ref: dict | None) -> tuple | None:
            """Extract KHR_texture_transform scale from a texture ref."""
            if tex_ref is None:
                return None
            transform = (tex_ref.get("extensions") or {}).get("KHR_texture_transform")
            if transform and "scale" in transform:
                s = transform["scale"]
                return (s[0], s[1])
            return None

        props: dict = {}

        def val(name, value):
            props.setdefault(name, {})["value"] = value

        def tex(name, tex_ref):
            uri = _resolve_tex(tex_ref)
            if uri:
                props.setdefault(name, {})["texture"] = uri

        # --- pbrMetallicRoughness ---
        # glTF defaults: baseColorFactor=[1,1,1,1], metallicFactor=1, roughnessFactor=1
        pbr = mat.get("pbrMetallicRoughness", {})

        bcf = pbr.get("baseColorFactor", [1.0, 1.0, 1.0, 1.0])
        val("color", bcf[:3])
        if len(bcf) > 3 and bcf[3] < 1.0:
            val("opacity", bcf[3])
            val("transparent", True)

        tex("color", pbr.get("baseColorTexture"))

        val("metalness", pbr.get("metallicFactor", 1.0))
        val("roughness", pbr.get("roughnessFactor", 1.0))

        mr_tex_ref = pbr.get("metallicRoughnessTexture")
        tex("metalness", mr_tex_ref)
        tex("roughness", mr_tex_ref)

        # --- Top-level ---
        normal_ref = mat.get("normalTexture")
        tex("normal", normal_ref)
        if normal_ref and "scale" in normal_ref:
            val("normalScale", [normal_ref["scale"], normal_ref["scale"]])

        tex("ao", mat.get("occlusionTexture"))

        if "emissiveFactor" in mat:
            val("emissive", mat["emissiveFactor"])
        tex("emissive", mat.get("emissiveTexture"))

        # --- Alpha mode ---
        alpha_mode = mat.get("alphaMode")
        if alpha_mode == "BLEND":
            # Check if the baseColor texture alpha is actually non-opaque.
            # Blender sometimes exports alphaMode=BLEND even when the alpha
            # channel is fully opaque (255 everywhere) — skip in that case.
            actually_transparent = True
            color_uri = props.get("color", {}).get("texture")
            if color_uri and color_uri.startswith("data:"):
                actually_transparent = _has_real_alpha(color_uri)
            if actually_transparent:
                val("transparent", True)
        elif alpha_mode == "MASK":
            val("alphaTest", mat.get("alphaCutoff", 0.5))

        if mat.get("doubleSided"):
            val("side", 2)

        # --- Extensions ---
        exts = mat.get("extensions", {})

        ext = exts.get("KHR_materials_ior", {})
        if "ior" in ext:
            val("ior", ext["ior"])

        ext = exts.get("KHR_materials_transmission", {})
        if "transmissionFactor" in ext:
            val("transmission", ext["transmissionFactor"])
        tex("transmission", ext.get("transmissionTexture"))

        ext = exts.get("KHR_materials_volume", {})
        if "thicknessFactor" in ext:
            val("thickness", ext["thicknessFactor"])
        tex("thickness", ext.get("thicknessTexture"))
        if "attenuationColor" in ext:
            val("attenuationColor", ext["attenuationColor"])
        if "attenuationDistance" in ext:
            val("attenuationDistance", ext["attenuationDistance"])

        ext = exts.get("KHR_materials_clearcoat", {})
        if "clearcoatFactor" in ext:
            val("clearcoat", ext["clearcoatFactor"])
        tex("clearcoat", ext.get("clearcoatTexture"))
        if "clearcoatRoughnessFactor" in ext:
            val("clearcoatRoughness", ext["clearcoatRoughnessFactor"])
        tex("clearcoatNormal", ext.get("clearcoatNormalTexture"))

        ext = exts.get("KHR_materials_sheen", {})
        if "sheenColorFactor" in ext:
            val("sheenColor", ext["sheenColorFactor"])
            val("sheen", 1.0)
        tex("sheenColor", ext.get("sheenColorTexture"))
        if "sheenRoughnessFactor" in ext:
            val("sheenRoughness", ext["sheenRoughnessFactor"])

        ext = exts.get("KHR_materials_iridescence", {})
        if "iridescenceFactor" in ext:
            val("iridescence", ext["iridescenceFactor"])
        tex("iridescence", ext.get("iridescenceTexture"))
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
        tex("specularIntensity", ext.get("specularTexture"))
        if "specularColorFactor" in ext:
            val("specularColor", ext["specularColorFactor"])
        tex("specularColor", ext.get("specularColorTexture"))

        ext = exts.get("KHR_materials_emissive_strength", {})
        if "emissiveStrength" in ext:
            val("emissiveIntensity", ext["emissiveStrength"])

        ext = exts.get("KHR_materials_dispersion", {})
        if "dispersion" in ext:
            val("dispersion", ext["dispersion"])

        # --- Texture repeat from KHR_texture_transform ---
        texture_repeat = None
        for tex_ref in [
            pbr.get("baseColorTexture"),
            pbr.get("metallicRoughnessTexture"),
            mat.get("normalTexture"),
            mat.get("occlusionTexture"),
            mat.get("emissiveTexture"),
        ]:
            tr = _get_tex_repeat(tex_ref)
            if tr is not None:
                texture_repeat = tr
                break

        name = mat.get("name", f"material_{index}")
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
        return cls(data)

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
        from threejs_materials.convert import encode_texture_base64

        def _resolve_texture(tex: str | None) -> str | None:
            if tex is None:
                return None
            if tex.startswith("data:"):
                return tex
            p = Path(tex)
            if p.exists():
                return encode_texture_base64(p)
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

        return cls({
            "id": id,
            "name": id,
            "source": "custom",
            "url": "",
            "license": "",
            "properties": props,
        })

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
        import warnings

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
        data = self.to_dict()
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
        data = self.to_dict()
        data["texture_repeat"] = (1.0 / u, 1.0 / v)
        return Material(data)

    def to_dict(self) -> dict:
        """Return the full material as a plain dict (including complete base64 textures)."""
        d = {
            "id": self.id,
            "name": self.name,
            "source": self.source,
            "url": self.url,
            "license": self.license,
            "properties": self.properties,
        }
        if self.texture_repeat is not None:
            d["textureRepeat"] = list(self.texture_repeat)
        return d

    def to_json(self, **kwargs) -> str:
        """Serialize to JSON string. Keyword args are passed to ``json.dumps``."""
        kwargs.setdefault("indent", 2)
        return json.dumps(self.to_dict(), **kwargs)

    def to_gltf(self) -> dict:
        """Convert to a glTF 2.0 material structure.

        Returns a dict with ``images``, ``samplers``, ``textures``,
        ``materials``, and ``extensionsUsed`` arrays — the same schema
        produced by :func:`collect_gltf_textures` for multiple materials.

        Properties with no glTF equivalent (``displacement``,
        ``displacementScale``) are silently dropped.
        """
        return _finalize_gltf(
            [_material_to_gltf(self)],
            [self.texture_repeat],
        )

    def dump(self, gltf: bool = False, json_format: bool = False) -> str:
        """Return a human-readable summary of the material properties.

        When *gltf* is ``True`` the glTF property structure is shown
        instead of the Three.js layout.  When *json_format* is ``True``
        the output is valid JSON with textures abbreviated.
        """
        if json_format:
            data = self.to_gltf() if gltf else self.to_dict()
            return json.dumps(_abbreviate_textures(data), indent=2)

        lines = [
            f"Material(name={self.name!r}, source={self.source!r}, "
            f"license={self.license!r})"
        ]
        if gltf:
            data = _abbreviate_textures(self.to_gltf())
            self._dump_nested(data, lines, indent=2)
        else:
            for key, prop in self.properties.items():
                parts = []
                if "value" in prop:
                    parts.append(f"value={prop['value']}")
                if "texture" in prop:
                    parts.append("texture='data:image/png;base64,...'")
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
            tr, tg, tb = _average_texture_linear(color_prop["texture"])
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
