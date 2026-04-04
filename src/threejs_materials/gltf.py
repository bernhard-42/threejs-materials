"""glTF I/O: build, read, and write glTF 2.0 documents from Materials."""

from __future__ import annotations

import base64
import copy
import hashlib
import io
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image as PILImage

from pygltflib import (
    BufferView,
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

from threejs_materials.utils import (
    _is_data_uri,
    _has_real_alpha,
    _open_texture_image,
    _resolve_to_data_uri,
)

if TYPE_CHECKING:
    from threejs_materials.library import PbrProperties as Material

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Texture packing helpers
# ---------------------------------------------------------------------------


def _pack_metallic_roughness(
    metalness_ref: str | None,
    roughness_ref: str | None,
    metalness_scalar: float,
    roughness_scalar: float,
    texture_dir: Path | None = None,
) -> str:
    """Pack separate metalness and roughness into a glTF metallicRoughness texture.

    glTF stores metalness in B and roughness in G.  When only one texture
    exists, the missing channel is filled with the scalar value.
    Returns a ``data:image/png;base64,...`` URI.
    """
    if metalness_ref:
        m_img = _open_texture_image(metalness_ref, texture_dir)
        size = m_img.size
    else:
        m_img = None

    if roughness_ref:
        r_img = _open_texture_image(roughness_ref, texture_dir)
        size = r_img.size
    else:
        r_img = None

    if m_img and r_img and m_img.size != r_img.size:
        r_img = r_img.resize(m_img.size, PILImage.Resampling.LANCZOS)
        size = m_img.size

    # R channel = 0 (unused), G = roughness, B = metalness
    # Three.js reads metalness from B channel and roughness from G channel
    # of separate textures, so extract those channels (not grayscale).
    h, w = size[1], size[0]
    r_chan = np.zeros((h, w), dtype=np.uint8)

    if r_img:
        r_arr = np.array(r_img)
        # Three.js reads roughnessMap from the G channel
        g_chan = r_arr[:, :, 1] if r_arr.ndim == 3 else r_arr
    else:
        g_chan = np.full((h, w), int(roughness_scalar * 255 + 0.5), dtype=np.uint8)

    if m_img:
        m_arr = np.array(m_img)
        # Three.js reads metalnessMap from the B channel
        b_chan = m_arr[:, :, 2] if m_arr.ndim == 3 else m_arr
    else:
        b_chan = np.full((h, w), int(metalness_scalar * 255 + 0.5), dtype=np.uint8)

    packed = np.stack([r_chan, g_chan, b_chan], axis=-1)
    img = PILImage.fromarray(packed, "RGB")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


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
            opacity_img = opacity_img.resize(color_img.size, PILImage.Resampling.LANCZOS)
    else:
        color_img = PILImage.new("RGB", opacity_img.size, (255, 255, 255))

    rgba = color_img.copy()
    rgba.putalpha(opacity_img)

    buf = io.BytesIO()
    rgba.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


# ---------------------------------------------------------------------------
# glTF builder
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

    def add_material(self, material, name: str | None = None) -> None:
        """Convert a Material/PbrProperties and append it to the GLTF2 document."""
        vals = material.values
        texs = material.maps
        texture_dir = material.maps_dir
        tex_repeat = material.texture_repeat
        # Skip no-op texture transform
        if tex_repeat is not None and tex_repeat == (1.0, 1.0):
            tex_repeat = None

        # vals/texs may be dataclasses or plain dicts (legacy)
        _vals = vals.to_dict() if hasattr(vals, "to_dict") else vals
        _texs = texs.to_dict() if hasattr(texs, "to_dict") else texs

        def val(prop_name: str):
            return _vals.get(prop_name)

        def tex_uri(prop_name: str) -> str | None:
            return self._resolve_tex(_texs.get(prop_name), texture_dir)

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

        pbr = self._build_pbr(val, tex_uri, tex_info, tex_repeat, texture_dir)
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
        elif _texs.get("opacity"):
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

    def _build_pbr(self, val, tex_uri, tex_info, tex_repeat, texture_dir):
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
        # glTF packs metalness (B) and roughness (G) into one texture.
        # When we have separate textures (or only one), we must pack them
        # correctly — using a single-channel texture as metallicRoughness
        # would put the same data in both G and B, corrupting the result.
        mr_ti = tex_info("metallicRoughness")
        if not mr_ti:
            m_uri = tex_uri("metalness")
            r_uri = tex_uri("roughness")
            if m_uri or r_uri:
                packed_uri = _pack_metallic_roughness(
                    m_uri, r_uri,
                    val("metalness") if val("metalness") is not None else 1.0,
                    val("roughness") if val("roughness") is not None else 1.0,
                    texture_dir,
                )
                mr_ti = self._make_tex_info(packed_uri, "metallicRoughness", tex_repeat)

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

        # Dispersion (requires KHR_materials_volume per glTF spec)
        dispersion = val("dispersion")
        if dispersion is not None and dispersion > 0:
            extensions["KHR_materials_dispersion"] = {"dispersion": dispersion}
            if "KHR_materials_volume" not in extensions:
                extensions["KHR_materials_volume"] = {"thicknessFactor": 0}

        return extensions

    def build(self) -> GLTF2:
        """Finalize and return the GLTF2 document."""
        self.gltf.extensionsUsed = sorted(self._extensions_used)
        return self.gltf


def _embed_data_uri_images(gltf: GLTF2) -> None:
    """Convert data URI images to binary buffer views inside the GLTF2.

    pygltflib's ``convert_images(ImageFormat.BUFFERVIEW)`` cannot do this
    (it warns and no-ops), so we decode the base64 data, append the bytes
    to the binary blob, create buffer views, and update image references.
    """
    blob = gltf.binary_blob() or b""
    buf_index = 0  # GLB uses a single buffer at index 0

    for image in gltf.images or []:
        if image.uri and _is_data_uri(image.uri):
            header, b64_data = image.uri.split(",", 1)
            raw = base64.b64decode(b64_data)

            # Align to 4-byte boundary (glTF spec requirement)
            padding = (4 - len(blob) % 4) % 4
            blob += b"\x00" * padding

            byte_offset = len(blob)
            blob += raw

            bv_index = len(gltf.bufferViews)
            gltf.bufferViews.append(
                BufferView(
                    buffer=buf_index,
                    byteOffset=byte_offset,
                    byteLength=len(raw),
                )
            )

            image.bufferView = bv_index
            image.uri = None

    gltf.set_binary_blob(blob)
    if gltf.buffers:
        gltf.buffers[0].byteLength = len(blob)


def _build_gltf(
    materials: list,
    names: list[str] | None = None,
    *,
    binary: bool = False,
) -> GLTF2:
    """Build a self-contained ``pygltflib.GLTF2`` from one or more Materials.

    Parameters
    ----------
    binary : bool
        If ``True``, convert images from data URIs to buffer views suitable
        for embedding in a GLB container. Defaults to ``False``.
    """
    builder = _GltfBuilder()
    for idx, material in enumerate(materials):
        mat_name = names[idx] if names else None
        builder.add_material(material, mat_name)
    gltf = builder.build()
    if binary and gltf.images:
        _embed_data_uri_images(gltf)
    return gltf


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def collect_gltf_textures(
    materials: dict,
    *,
    binary: bool = False,
) -> GLTF2:
    """Convert multiple materials to a ``pygltflib.GLTF2`` with shared textures.

    Parameters
    ----------
    materials : dict[str, Material]
        Mapping of ``{name: Material}``.  The *name* is used as the
        glTF material name (overriding ``material.name``).
    binary : bool
        If ``True``, convert images from data URIs to buffer views suitable
        for embedding in a GLB container. Defaults to ``False``.

    Returns
    -------
    pygltflib.GLTF2
        A glTF 2.0 document with materials, images, textures, and samplers.
        Textures shared across materials are deduplicated.
    """
    mat_list = list(materials.values())
    name_list = list(materials.keys())
    return _build_gltf(mat_list, name_list, binary=binary)


# ---------------------------------------------------------------------------
# glTF accessor helpers
# ---------------------------------------------------------------------------

_COMPONENT_DTYPES = {
    5120: np.int8,
    5121: np.uint8,
    5122: np.int16,
    5123: np.uint16,
    5125: np.uint32,
    5126: np.float32,
}
_TYPE_COUNTS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


def _read_accessor(gltf: GLTF2, accessor_idx: int) -> np.ndarray:
    """Read a glTF accessor into a numpy array."""
    acc = gltf.accessors[accessor_idx]
    bv = gltf.bufferViews[acc.bufferView]
    blob = gltf.binary_blob()
    dtype = _COMPONENT_DTYPES[acc.componentType]
    n_components = _TYPE_COUNTS[acc.type]
    offset = (bv.byteOffset or 0) + (acc.byteOffset or 0)
    byte_stride = bv.byteStride
    elem_size = np.dtype(dtype).itemsize * n_components

    if byte_stride and byte_stride != elem_size:
        # Strided: pick elements one by one
        data = np.empty((acc.count, n_components), dtype=dtype)
        for i in range(acc.count):
            start = offset + i * byte_stride
            data[i] = np.frombuffer(blob, dtype=dtype, count=n_components, offset=start)
    else:
        data = np.frombuffer(blob, dtype=dtype, count=acc.count * n_components, offset=offset)
        if n_components > 1:
            data = data.reshape(-1, n_components)

    return data.copy()


def _write_accessor(gltf: GLTF2, accessor_idx: int, data: np.ndarray) -> None:
    """Write a numpy array back into a glTF accessor's buffer."""
    acc = gltf.accessors[accessor_idx]
    bv = gltf.bufferViews[acc.bufferView]
    dtype = _COMPONENT_DTYPES[acc.componentType]
    n_components = _TYPE_COUNTS[acc.type]
    offset = (bv.byteOffset or 0) + (acc.byteOffset or 0)
    byte_stride = bv.byteStride
    elem_size = np.dtype(dtype).itemsize * n_components

    blob = bytearray(gltf.binary_blob())
    flat = data.astype(dtype).ravel()

    if byte_stride and byte_stride != elem_size:
        for i in range(acc.count):
            start = offset + i * byte_stride
            blob[start:start + elem_size] = flat[i * n_components:(i + 1) * n_components].tobytes()
    else:
        raw = flat.tobytes()
        blob[offset:offset + len(raw)] = raw

    # Update accessor min/max
    if data.ndim == 1:
        acc.min = [float(data.min())]
        acc.max = [float(data.max())]
    else:
        acc.min = [float(x) for x in data.min(axis=0)]
        acc.max = [float(x) for x in data.max(axis=0)]

    gltf.set_binary_blob(bytes(blob))
    if gltf.buffers:
        gltf.buffers[0].byteLength = len(blob)


def _normalize_primitive_uvs(gltf: GLTF2, mesh_idx: int, prim_idx: int, bbox_max_dim: float) -> None:
    """Normalize UVs of a primitive using the middle-triangle approach.

    Computes the surface partial derivatives (∂P/∂u, ∂P/∂v) from a triangle
    near the centroid, then scales all UVs so that 1 UV unit ≈ bbox_max_dim
    in physical space — matching ocp-tessellate's normalization.
    """
    prim = gltf.meshes[mesh_idx].primitives[prim_idx]

    texcoord_idx = prim.attributes.TEXCOORD_0
    position_idx = prim.attributes.POSITION
    if texcoord_idx is None or position_idx is None:
        return

    positions = _read_accessor(gltf, position_idx).astype(np.float64)
    uvs = _read_accessor(gltf, texcoord_idx).astype(np.float64)

    if prim.indices is not None:
        indices = _read_accessor(gltf, prim.indices).ravel().astype(np.int32)
    else:
        indices = np.arange(len(positions), dtype=np.int32)

    num_triangles = len(indices) // 3
    if num_triangles == 0:
        return

    tri_indices = indices.reshape(-1, 3)

    # Find triangle closest to the centroid
    centroid = positions.mean(axis=0)
    tri_centroids = (
        positions[tri_indices[:, 0]]
        + positions[tri_indices[:, 1]]
        + positions[tri_indices[:, 2]]
    ) / 3.0
    mid_tri = np.argmin(np.linalg.norm(tri_centroids - centroid, axis=1))

    # Middle triangle vertices
    i0, i1, i2 = tri_indices[mid_tri]
    p0, p1, p2 = positions[i0], positions[i1], positions[i2]
    uv0, uv1, uv2 = uvs[i0], uvs[i1], uvs[i2]

    # Compute ∂P/∂u and ∂P/∂v from the triangle's Jacobian
    dp1 = p1 - p0
    dp2 = p2 - p0
    duv1 = uv1 - uv0
    duv2 = uv2 - uv0

    det = duv1[0] * duv2[1] - duv2[0] * duv1[1]
    if abs(det) < 1e-20:
        return  # degenerate triangle in UV space

    dPdu = (duv2[1] * dp1 - duv1[1] * dp2) / det
    dPdv = (-duv2[0] * dp1 + duv1[0] * dp2) / det

    scale_u = np.linalg.norm(dPdu)
    scale_v = np.linalg.norm(dPdv)

    uv_min = uvs.min(axis=0)

    new_uvs = np.empty_like(uvs)
    new_uvs[:, 0] = (
        (uvs[:, 0] - uv_min[0]) * scale_u / bbox_max_dim
        if scale_u > 1e-10
        else 0.5
    )
    new_uvs[:, 1] = (
        (uvs[:, 1] - uv_min[1]) * scale_v / bbox_max_dim
        if scale_v > 1e-10
        else 0.5
    )

    _write_accessor(gltf, texcoord_idx, new_uvs.astype(np.float32))


# ---------------------------------------------------------------------------
# inject_materials
# ---------------------------------------------------------------------------


def inject_materials(
    target_path: str,
    node_materials: dict,
) -> None:
    """Inject full PBR materials into an existing glTF/GLB file.

    Takes a mapping of glTF node indices to PbrProperties (or GLTF2)
    objects, deduplicates them, assigns material indices to mesh
    primitives, injects full PBR data (textures, KHR extensions), and
    optionally normalizes UVs for materials with ``normalize_uvs=True``.

    Parameters
    ----------
    target_path : str
        Path to an existing ``.gltf`` or ``.glb`` file.  The file is
        overwritten in place.
    node_materials : dict[int, PbrProperties | GLTF2]
        Mapping of ``{node_index: PbrProperties_or_GLTF2}``.
    """
    if not node_materials:
        return

    is_binary = target_path.endswith(".glb")
    gltf = GLTF2.load(target_path)

    # Normalize values, converting GLTF2 objects on the fly.
    _gltf_cache: dict[int, Material] = {}
    resolved: dict[int, Material] = {}
    for node_idx, value in node_materials.items():
        if isinstance(value, GLTF2):
            obj_id = id(value)
            if obj_id not in _gltf_cache:
                from threejs_materials.library import PbrProperties
                _gltf_cache[obj_id] = PbrProperties.from_dict(_from_gltf(value, index=0))
            resolved[node_idx] = _gltf_cache[obj_id]
        else:
            resolved[node_idx] = value

    # Deduplicate PbrProperties by identity — one material index per unique object
    pbr_id_to_idx: dict[int, int] = {}
    mat_map: dict[int, Material] = {}

    for node_idx, gltf_node in enumerate(gltf.nodes):
        if node_idx not in resolved or gltf_node.mesh is None:
            continue
        pbr = resolved[node_idx]
        obj_id = id(pbr)
        if obj_id not in pbr_id_to_idx:
            mat_idx = len(pbr_id_to_idx)
            pbr_id_to_idx[obj_id] = mat_idx
            mat_map[mat_idx] = pbr
        for prim in gltf.meshes[gltf_node.mesh].primitives:
            prim.material = pbr_id_to_idx[obj_id]

    if not mat_map:
        return

    # Build merged GLTF2 with deduplicated textures via collect_gltf_textures.
    # Use unique keys — materials with the same name but different content
    # (e.g. color overrides) must not be collapsed.
    tm_materials: dict[str, Material] = {}
    idx_to_name: dict[int, str] = {}
    for mat_idx, material in mat_map.items():
        name = material.name or f"mat_{mat_idx}"
        if name in tm_materials:
            name = f"{name}_{mat_idx}"
        tm_materials[name] = material
        idx_to_name[mat_idx] = name

    merged = collect_gltf_textures(tm_materials, binary=is_binary)

    # Name → merged material index lookup
    merged_name_to_idx: dict[str, int] = {
        mat.name: i for i, mat in enumerate(merged.materials) if mat.name
    }

    # Compute offsets for appending merged assets into the target
    sampler_offset = len(gltf.samplers or [])
    image_offset = len(gltf.images or [])
    texture_offset = len(gltf.textures or [])
    buffer_view_offset = len(gltf.bufferViews or [])

    # Merge binary blob data (GLB mode)
    merged_blob = merged.binary_blob()
    if merged_blob:
        existing_blob = gltf.binary_blob() or b""
        padding = (4 - len(existing_blob) % 4) % 4
        existing_blob += b"\x00" * padding
        blob_offset = len(existing_blob)
        existing_blob += merged_blob
        gltf.set_binary_blob(existing_blob)
        if gltf.buffers:
            gltf.buffers[0].byteLength = len(existing_blob)

        for bv in merged.bufferViews or []:
            new_bv = copy.deepcopy(bv)
            new_bv.buffer = 0
            if new_bv.byteOffset is not None:
                new_bv.byteOffset += blob_offset
            gltf.bufferViews.append(new_bv)

    # For .gltf: write textures as external files instead of data URIs
    if not is_binary and merged.images:
        tex_dir = Path(target_path).with_suffix("")
        tex_dir.mkdir(parents=True, exist_ok=True)
        merged.convert_images(ImageFormat.FILE, path=str(tex_dir), override=True)
        rel_prefix = tex_dir.name + "/"
        for img in merged.images:
            if img.uri and not _is_data_uri(img.uri):
                img.uri = rel_prefix + img.uri

    # Append samplers, images, textures
    for sampler in merged.samplers or []:
        gltf.samplers.append(copy.deepcopy(sampler))

    for image in merged.images or []:
        img = copy.deepcopy(image)
        if img.bufferView is not None:
            img.bufferView += buffer_view_offset
        gltf.images.append(img)

    for texture in merged.textures or []:
        tex = copy.deepcopy(texture)
        if tex.source is not None:
            tex.source += image_offset
        if tex.sampler is not None:
            tex.sampler += sampler_offset
        gltf.textures.append(tex)

    # Replace target materials with remapped merged materials
    def _remap_tex_info(tex_info):
        if tex_info is None:
            return
        if hasattr(tex_info, "index") and tex_info.index is not None:
            tex_info.index += texture_offset

    def _remap_extensions(extensions):
        if not extensions:
            return
        for ext_data in extensions.values():
            if not isinstance(ext_data, dict):
                continue
            for val in ext_data.values():
                if isinstance(val, dict) and "index" in val:
                    val["index"] += texture_offset

    # Ensure target materials array is large enough for all requested indices
    max_idx = max(idx_to_name.keys(), default=-1)
    while len(gltf.materials) <= max_idx:
        gltf.materials.append(GltfMaterial())

    for mat_idx, name in idx_to_name.items():
        merged_idx = merged_name_to_idx.get(name)
        if merged_idx is None:
            continue
        mat = copy.deepcopy(merged.materials[merged_idx])
        if mat.pbrMetallicRoughness is not None:
            _remap_tex_info(mat.pbrMetallicRoughness.baseColorTexture)
            _remap_tex_info(mat.pbrMetallicRoughness.metallicRoughnessTexture)
        _remap_tex_info(mat.normalTexture)
        _remap_tex_info(mat.occlusionTexture)
        _remap_tex_info(mat.emissiveTexture)
        _remap_extensions(mat.extensions)
        gltf.materials[mat_idx] = mat

    # Normalize UVs for materials with normalize_uvs=True
    normalize_mat_indices = {
        mat_idx
        for mat_idx, pbr in mat_map.items()
        if getattr(pbr, "normalize_uvs", True)
    }
    if normalize_mat_indices:
        # Compute bbox_max_dim from all positions across the file
        pos_min = np.full(3, np.inf)
        pos_max = np.full(3, -np.inf)
        for mesh in gltf.meshes:
            for prim in mesh.primitives:
                if prim.attributes.POSITION is not None:
                    acc = gltf.accessors[prim.attributes.POSITION]
                    if acc.min and acc.max:
                        pos_min = np.minimum(pos_min, acc.min)
                        pos_max = np.maximum(pos_max, acc.max)
        dims = pos_max - pos_min
        bbox_max_dim = max(float(dims.sum()) / 3.0, 1e-10)

        for mesh_idx, mesh in enumerate(gltf.meshes):
            for prim_idx, prim in enumerate(mesh.primitives):
                if prim.material in normalize_mat_indices:
                    _normalize_primitive_uvs(gltf, mesh_idx, prim_idx, bbox_max_dim)

    # Merge extensionsUsed
    extensions_used = set(gltf.extensionsUsed or [])
    extensions_used.update(merged.extensionsUsed or [])
    gltf.extensionsUsed = sorted(extensions_used) if extensions_used else []

    # Save back
    if is_binary:
        gltf.save_binary(target_path)
    else:
        gltf.save(target_path)


# ---------------------------------------------------------------------------
# Material ↔ glTF conversion functions
# ---------------------------------------------------------------------------


def to_gltf(material) -> GLTF2:
    """Convert a Material to a ``pygltflib.GLTF2`` document."""
    return _build_gltf([material])


def save_gltf(material, path: str | Path, *, overwrite: bool = False) -> None:
    """Save a Material as a ``.gltf`` or ``.glb`` file.

    The format is chosen automatically from the file extension.
    For ``.gltf``, textures are written as separate files in a
    companion directory (e.g. ``wood.gltf`` + ``wood/color.png``).
    For ``.glb``, textures are embedded in the binary file.

    Parameters
    ----------
    material : Material
        The material to save.
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

    gltf = to_gltf(material)  # always data URIs

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


def _from_gltf(
    gltf: GLTF2,
    index: int | None = None,
) -> dict[str, dict] | dict:
    """Extract material data dicts from a ``pygltflib.GLTF2`` object.

    Returns ``{name: data_dict}`` or a single ``data_dict`` when *index*
    is given.  The caller wraps these in ``Material`` objects.
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

    result: dict[str, dict] = {}

    for mat_index, mat in enumerate(gltf.materials):
        values: dict = {}
        textures: dict = {}

        def val(name, value):
            values[name] = value

        def tex(name, tex_idx):
            uri = _resolve_tex_by_index(tex_idx)
            if uri:
                textures[name] = uri

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
            color_uri = textures.get("color")
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
            "values": values,
            "textures": textures,
            "normalize_uvs": False,
        }
        if texture_repeat is not None:
            data["texture_repeat"] = texture_repeat
        result[name] = data

    if index is not None:
        return list(result.values())[index]
    return result


