"""
Bake + extract → MeshPhysicalMaterial JSON with base64-encoded textures.
"""

import base64
import logging
import mimetypes
import os
import shutil
from pathlib import Path
from sys import platform

import MaterialX as mx
from MaterialX import PyMaterialXRender as mx_render
from MaterialX import PyMaterialXRenderGlsl as mx_render_glsl

if platform == "darwin":
    from MaterialX import PyMaterialXRenderMsl as mx_render_msl

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# MaterialX helpers
# ---------------------------------------------------------------------------


def load_document_with_stdlib(mtlx_path: Path):
    """Load a MaterialX document with standard library."""
    doc = mx.createDocument()
    stdlib = mx.createDocument()
    search_path = mx.getDefaultDataSearchPath()
    search_path.append(str(mtlx_path.parent))

    library_folders = list(mx.getDefaultDataLibraryFolders())
    mx.loadLibraries(library_folders, search_path, stdlib)

    mx.readFromXmlFile(doc, str(mtlx_path), search_path)
    doc.setDataLibrary(stdlib)

    valid, msg = doc.validate()
    if not valid:
        log.debug("Validation warnings: %s", msg)
    return doc, search_path


def bake_materials(
    doc,
    search_path,
    baked_mtlx_path: Path,
    tex_dir: Path,
    mtlx_dir: Path | None = None,
    width=1024,
    height=1024,
):
    """Bake all materials using TextureBaker (GLSL preferred, MSL fallback)."""
    tex_dir.mkdir(parents=True, exist_ok=True)

    base_type = mx_render.BaseType.UINT8
    try:
        baker = mx_render_glsl.TextureBaker.create(width, height, base_type)
    except Exception:
        if platform == "darwin":
            baker = mx_render_msl.TextureBaker.create(width, height, base_type)
        else:
            raise

    baker.writeDocumentPerMaterial(False)

    baked_mtlx_path = baked_mtlx_path.resolve()
    tex_dir = tex_dir.resolve()
    baker_out = tex_dir / baked_mtlx_path.name

    orig_dir = os.getcwd()
    if mtlx_dir:
        os.chdir(mtlx_dir)
    try:
        # Suppress C++ stdout/stderr from TextureBaker
        with open(os.devnull, "w") as devnull:
            old_stdout, old_stderr = os.dup(1), os.dup(2)
            os.dup2(devnull.fileno(), 1)
            os.dup2(devnull.fileno(), 2)
            try:
                baker.bakeAllMaterials(doc, search_path, str(baker_out))
            finally:
                os.dup2(old_stdout, 1)
                os.dup2(old_stderr, 2)
                os.close(old_stdout)
                os.close(old_stderr)
    finally:
        os.chdir(orig_dir)

    if baked_mtlx_path != baker_out:
        baked_mtlx_path.write_text(baker_out.read_text())
        baker_out.unlink(missing_ok=True)

    return baked_mtlx_path


def parse_value(value_str: str, type_str: str):
    if not value_str:
        return None
    if type_str == "float":
        return float(value_str)
    if type_str in ("color3", "vector3"):
        return [float(x.strip()) for x in value_str.split(",")]
    if type_str in ("color4", "vector4"):
        return [float(x.strip()) for x in value_str.split(",")]
    if type_str == "integer":
        return int(value_str)
    if type_str == "boolean":
        return value_str.lower() in ("true", "1")
    return value_str


def find_upstream_image(inp) -> dict | None:
    """Walk upstream from an input to find an image/tiledimage node."""
    connected = inp.getConnectedNode()
    doc = inp.getDocument()

    if connected is None and inp.hasNodeGraphString():
        ng_name = inp.getNodeGraphString()
        ng = doc.getNodeGraph(ng_name)
        if ng:
            out_name = (
                inp.getAttribute(mx.Output.OUTPUT_ATTRIBUTE)
                if inp.hasAttribute(mx.Output.OUTPUT_ATTRIBUTE)
                else ""
            )
            if out_name:
                out_port = ng.getOutput(out_name)
            else:
                outputs = ng.getOutputs()
                out_port = outputs[0] if outputs else None
            if out_port:
                node_name = out_port.getNodeName()
                if node_name:
                    connected = ng.getNode(node_name)

    return _extract_image_info(connected)


def _extract_image_info(node) -> dict | None:
    if node is None:
        return None

    category = node.getCategory()
    if category in ("image", "tiledimage"):
        result = {"node": node.getName()}
        file_input = node.getInput("file")
        if file_input:
            resolved = file_input.getResolvedValueString()
            if not resolved:
                resolved = file_input.getValueString()
            result["file"] = resolved

        if file_input:
            if file_input.hasColorSpace():
                result["colorspace"] = file_input.getColorSpace()
            elif file_input.hasAttribute(mx.Element.COLOR_SPACE_ATTRIBUTE):
                result["colorspace"] = file_input.getAttribute(
                    mx.Element.COLOR_SPACE_ATTRIBUTE
                )

        for addr in ("uaddressmode", "vaddressmode"):
            addr_input = node.getInput(addr)
            if addr_input:
                result[addr] = addr_input.getValueString()

        return result

    # Recurse upstream (handles normalmap nodes etc.)
    for upstream_inp in node.getInputs():
        upstream_node = upstream_inp.getConnectedNode()
        if upstream_node:
            img = _extract_image_info(upstream_node)
            if img:
                return img

    return None


def extract_materials(doc) -> list[dict]:
    """Extract all materials from a MaterialX document."""
    materials = []
    for mat_node in doc.getMaterialNodes():
        mat_info = {
            "name": mat_node.getName(),
            "shader_model": None,
            "params": {},
            "textures": {},
        }
        shader_nodes = mx.getShaderNodes(mat_node)
        if not shader_nodes:
            continue
        shader = shader_nodes[0]
        mat_info["shader_model"] = shader.getCategory()

        for inp in shader.getInputs():
            inp_name = inp.getName()
            inp_type = inp.getType()
            img_info = find_upstream_image(inp)
            if img_info and "file" in img_info:
                mat_info["textures"][inp_name] = img_info
            else:
                val_str = inp.getValueString()
                if val_str:
                    mat_info["params"][inp_name] = parse_value(val_str, inp_type)

        materials.append(mat_info)
    return materials


def to_threejs_physical(mat: dict, base_dir: Path) -> dict:
    """Convert extracted MaterialX material to MeshPhysicalMaterial properties.

    Returns ``{property: {value: ..., texture: data_uri}}``  where each
    property carries a *value*, a base64-encoded *texture*, or both.
    """
    p = mat["params"]
    t = mat["textures"]
    model = mat["shader_model"]
    props: dict[str, dict] = {}

    def val(name, value):
        props.setdefault(name, {})["value"] = value

    def has_tex(mtlx_input):
        """Check if a MaterialX input has a valid texture file."""
        return mtlx_input in t and (base_dir / t[mtlx_input]["file"]).exists()

    def tex(name, mtlx_input):
        if mtlx_input not in t:
            return
        tex_path = base_dir / t[mtlx_input]["file"]
        if tex_path.exists():
            props.setdefault(name, {})["texture"] = encode_texture_base64(tex_path)

    if model == "standard_surface":
        # Three.js multiplies scalar × texture for all map properties.
        # When a texture exists, set scalar to neutral so texture controls fully.
        # The baker's output already reflects the intended diffuse brightness;
        # applying `base` again would double-darken the result.
        base = p.get("base", 1.0)
        base_color = p.get("base_color", [0.8, 0.8, 0.8])
        if has_tex("base_color"):
            val("color", [1.0, 1.0, 1.0])
        else:
            val("color", [c * base for c in base_color])
        tex("color", "base_color")

        val("metalness", 1.0 if has_tex("metalness") else p.get("metalness", 0.0))
        tex("metalness", "metalness")

        val("roughness", 1.0 if has_tex("specular_roughness") else p.get("specular_roughness", 0.5))
        tex("roughness", "specular_roughness")

        tex("normal", "normal")

        val("specularIntensity", p.get("specular", 1.0))
        val("specularColor", p.get("specular_color", [1.0, 1.0, 1.0]))
        val("ior", p.get("specular_IOR", 1.5))

        transmission = p.get("transmission", 0.0)
        if transmission > 0.0:
            val("transmission", transmission)
            val("transparent", True)

        coat = p.get("coat", 0.0)
        if coat > 0.0:
            val("clearcoat", coat)
            val("clearcoatRoughness", p.get("coat_roughness", 0.1))

        sheen = p.get("sheen", 0.0)
        if sheen > 0.0:
            val("sheen", sheen)
            val("sheenColor", p.get("sheen_color", [1.0, 1.0, 1.0]))
            val("sheenRoughness", p.get("sheen_roughness", 0.3))

        emission = p.get("emission", 0.0)
        if emission > 0.0:
            em_color = p.get("emission_color", [1.0, 1.0, 1.0])
            val("emissive", [c * emission for c in em_color])
            val("emissiveIntensity", 1.0)
            tex("emissive", "emission_color")

        tf_thickness = p.get("thin_film_thickness", 0.0)
        if tf_thickness > 0.0:
            val("iridescence", 1.0)
            val("iridescenceIOR", p.get("thin_film_IOR", 1.5))
            val("iridescenceThicknessRange", [0.0, tf_thickness])

        opacity = p.get("opacity", 1.0)
        if isinstance(opacity, list):
            avg_opacity = sum(opacity) / len(opacity)
        else:
            avg_opacity = opacity
        if avg_opacity < 1.0:
            val("opacity", avg_opacity)
            val("transparent", True)
        tex("opacity", "opacity")

    elif model == "gltf_pbr":
        # Three.js multiplies scalar × texture — set scalar to neutral when texture exists.
        val("color", [1.0, 1.0, 1.0] if has_tex("base_color") else p.get("base_color", [1.0, 1.0, 1.0]))
        tex("color", "base_color")

        has_mr_tex = has_tex("metallic_roughness")
        val("metalness", 1.0 if has_mr_tex else p.get("metallic", 0.0))
        val("roughness", 1.0 if has_mr_tex else p.get("roughness", 1.0))
        val("ior", p.get("ior", 1.5))
        val("transmission", p.get("transmission", 0.0))

        tex("metalness", "metallic_roughness")
        tex("roughness", "metallic_roughness")

        tex("normal", "normal")

        clearcoat = p.get("clearcoat", 0.0)
        if clearcoat > 0.0:
            val("clearcoat", clearcoat)
            val("clearcoatRoughness", p.get("clearcoat_roughness", 0.0))

        sheen_color = p.get("sheen_color")
        if sheen_color:
            val("sheenColor", sheen_color)
            val("sheenRoughness", p.get("sheen_roughness", 0.0))
            val("sheen", 1.0)

        emissive = p.get("emissive", [0.0, 0.0, 0.0])
        if any(c > 0.0 for c in emissive):
            val("emissive", emissive)
            val("emissiveIntensity", p.get("emissive_strength", 1.0))
            tex("emissive", "emissive")

    elif model == "open_pbr_surface":
        # Three.js multiplies scalar × texture — set to neutral when texture exists.
        base_weight = p.get("base_weight", 1.0)
        base_color = p.get("base_color", [0.8, 0.8, 0.8])
        if has_tex("base_color"):
            val("color", [1.0, 1.0, 1.0])
        else:
            val("color", [c * base_weight for c in base_color])
        tex("color", "base_color")

        val("metalness", 1.0 if has_tex("base_metalness") else p.get("base_metalness", 0.0))
        tex("metalness", "base_metalness")

        val("roughness", 1.0 if has_tex("specular_roughness") else p.get("specular_roughness", 0.3))
        tex("roughness", "specular_roughness")

        spec_color = p.get("specular_color")
        if spec_color:
            val("specularIntensity", 1.0)
            val("specularColor", spec_color)

        tex("normal", "geometry_normal")

        val("ior", p.get("specular_ior", 1.5))

        transmission = p.get("transmission_weight", 0.0)
        if transmission > 0.0:
            val("transmission", transmission)
            val("transparent", True)
            tx_color = p.get("transmission_color")
            if tx_color:
                val("attenuationColor", tx_color)
            tx_depth = p.get("transmission_depth")
            if tx_depth and tx_depth > 0.0:
                val("attenuationDistance", tx_depth)

        # Dispersion: Abbe number → Three.js dispersion (= 20 / V_d)
        abbe = p.get("transmission_dispersion_abbe_number")
        if abbe and abbe > 0:
            val("dispersion", 20.0 / abbe)

        coat = p.get("coat_weight", 0.0)
        if coat > 0.0:
            val("clearcoat", coat)
            val("clearcoatRoughness", p.get("coat_roughness", 0.0))

        emission_lum = p.get("emission_luminance", 0.0)
        if emission_lum > 0.0:
            em_color = p.get("emission_color", [1.0, 1.0, 1.0])
            val("emissive", em_color)
            val("emissiveIntensity", emission_lum / 1000.0)
            tex("emissive", "emission_color")

        tf_weight = p.get("thin_film_weight", 0.0)
        if tf_weight > 0.0:
            val("iridescence", tf_weight)
            val("iridescenceIOR", p.get("thin_film_ior", 1.5))
            # thin_film_thickness is in μm; Three.js expects nm
            tf_thickness_um = p.get("thin_film_thickness", 0.5)
            val("iridescenceThicknessRange", [0.0, tf_thickness_um * 1000.0])

    return props


# ---------------------------------------------------------------------------
# EXR → PNG conversion
# ---------------------------------------------------------------------------


def _convert_exr_to_png(exr_path: Path) -> Path:
    """Convert an EXR image to 8-bit PNG. Returns path to the new PNG file."""
    import array

    import Imath
    import OpenEXR

    exr_file = OpenEXR.InputFile(str(exr_path))
    header = exr_file.header()
    dw = header["dataWindow"]
    width = dw.max.x - dw.min.x + 1
    height = dw.max.y - dw.min.y + 1

    # Determine channels
    channel_names = list(header["channels"].keys())
    rgb = [ch for ch in ("R", "G", "B") if ch in channel_names]
    if not rgb:
        rgb = sorted(channel_names)[:3]

    # Read channel data as 32-bit float
    pt = Imath.PixelType(Imath.PixelType.FLOAT)
    raw = exr_file.channels(rgb, pt)

    num_pixels = width * height

    if len(rgb) == 1:
        # Single-channel (e.g. roughness, displacement) → grayscale
        floats = array.array("f", raw[0])
        pixels = bytearray(num_pixels)
        for i, val in enumerate(floats):
            pixels[i] = int(max(0.0, min(1.0, val)) * 255 + 0.5)
        mode = "L"
    else:
        # Multi-channel → RGB interleaved
        pixels = bytearray(num_pixels * len(rgb))
        for ch_idx, ch_data in enumerate(raw):
            floats = array.array("f", ch_data)
            for i, val in enumerate(floats):
                clamped = max(0.0, min(1.0, val))
                pixels[i * len(rgb) + ch_idx] = int(clamped * 255 + 0.5)
        mode = "RGB" if len(rgb) == 3 else "RGBA"

    from PIL import Image
    img = Image.frombytes(mode, (width, height), bytes(pixels))
    png_path = exr_path.with_suffix(".png")
    img.save(png_path)
    log.info("Converted EXR → PNG: %s", png_path.name)
    return png_path


# ---------------------------------------------------------------------------
# Base64 encoding helper
# ---------------------------------------------------------------------------


def encode_texture_base64(file_path: Path) -> str:
    """Read image file and return data-URI string with base64 content.
    Automatically converts EXR to PNG first."""
    # Convert EXR to PNG before encoding
    if file_path.suffix.lower() == ".exr":
        file_path = _convert_exr_to_png(file_path)

    mime, _ = mimetypes.guess_type(str(file_path))
    if mime is None:
        suffix = file_path.suffix.lower()
        mime = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
        }.get(suffix, "application/octet-stream")

    data = file_path.read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"


# ---------------------------------------------------------------------------
# Shared conversion pipeline
# ---------------------------------------------------------------------------


def _process_mtlx(mtlx_path: Path) -> tuple[dict, str | None]:
    """Core pipeline: load → bake → extract → merge → properties.

    Returns ``(properties_dict, shader_model)``.
    """
    base_dir = mtlx_path.parent
    tex_dir = base_dir / "textures"

    doc, search_path = load_document_with_stdlib(mtlx_path)
    orig_mats = extract_materials(doc)

    if not orig_mats:
        raise RuntimeError(f"No materials found in {mtlx_path}")

    has_textures = any(m["textures"] for m in orig_mats)

    used_baker = False
    if has_textures:
        baked_mtlx = base_dir / "material.baked.mtlx"
        try:
            bake_materials(
                doc, search_path, baked_mtlx, tex_dir, mtlx_dir=base_dir,
            )
            baked_doc, _ = load_document_with_stdlib(baked_mtlx)
            mats = extract_materials(baked_doc)
            used_baker = bool(mats)
        except Exception as e:
            log.warning("Baking failed for %s: %s — using original doc", mtlx_path, e)
            mats = []

        if not mats:
            log.info("Fallback: using original document for %s", mtlx_path.name)
            mats = orig_mats

        # Merge textures the baker missed from the original.
        # Skip if the baker intentionally collapsed the texture to a scalar
        # (present in baked params) — the raw original texture may have been
        # processed through MaterialX nodes and shouldn't be used directly.
        if mats and orig_mats and mats is not orig_mats:
            baked_tex = mats[0].get("textures", {})
            baked_params = mats[0].get("params", {})
            orig_tex = orig_mats[0].get("textures", {})
            for inp_name, tex_info in orig_tex.items():
                if inp_name not in baked_tex and inp_name not in baked_params:
                    src_file = tex_info.get("file")
                    if not src_file:
                        continue
                    src_path = (base_dir / src_file).resolve()
                    if src_path.exists():
                        dst = tex_dir / src_path.name
                        if not dst.exists():
                            tex_dir.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(src_path, dst)
                        mats[0]["textures"][inp_name] = tex_info
                    else:
                        for alt_ext in (".jpg", ".png", ".jpeg"):
                            alt_path = src_path.with_suffix(alt_ext)
                            if alt_path.exists():
                                dst = tex_dir / alt_path.name
                                if not dst.exists():
                                    tex_dir.mkdir(parents=True, exist_ok=True)
                                    shutil.copy2(alt_path, dst)
                                mats[0]["textures"][inp_name] = dict(
                                    tex_info,
                                    file=str(Path(src_file).with_suffix(alt_ext)),
                                )
                                break
    else:
        mats = orig_mats

    mat = mats[0]
    properties = to_threejs_physical(mat, base_dir)
    return properties, mat.get("shader_model")
