"""
Download + bake + extract → MeshPhysicalMaterial JSON with base64-encoded textures.

Reuses proven logic from the experimental convert_mtlx.py.
"""

import base64
import io
import json
import logging
import mimetypes
import os
import shutil
import sqlite3
import zipfile
from pathlib import Path
from sys import platform

import requests
import MaterialX as mx
from MaterialX import PyMaterialXRender as mx_render
from MaterialX import PyMaterialXRenderGlsl as mx_render_glsl
if platform == "darwin":
    from MaterialX import PyMaterialXRenderMsl as mx_render_msl

from materialx_lib.db import DB_DIR

log = logging.getLogger(__name__)

BAKED_DIR = DB_DIR / "baked"


# ---------------------------------------------------------------------------
# MaterialX helpers (from convert_mtlx.py)
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
        log.warning("Validation warnings: %s", msg)
    return doc, search_path


def bake_materials(doc, search_path, baked_mtlx_path: Path, tex_dir: Path,
                   mtlx_dir: Path | None = None, width=1024, height=1024):
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


def to_threejs_physical(mat: dict, textures_root_url: str = "textures") -> dict:
    """Convert extracted MaterialX material to MeshPhysicalMaterial-style dict."""
    p = mat["params"]
    t = mat["textures"]
    model = mat["shader_model"]
    result = {}

    def get_param(name, default=None):
        return p.get(name, default)

    def get_tex(name):
        if name not in t:
            return None
        fname = t[name]["file"]
        return f"{textures_root_url}/{os.path.basename(fname)}"

    if model == "standard_surface":
        base = get_param("base", 1.0)
        base_color = get_param("base_color", [0.8, 0.8, 0.8])
        if get_tex("base_color"):
            result["map"] = get_tex("base_color")
        else:
            result["color"] = [c * base for c in base_color]

        result["metalness"] = get_param("metalness", 0.0)
        if get_tex("metalness"):
            result["metalnessMap"] = get_tex("metalness")

        result["roughness"] = get_param("specular_roughness", 0.5)
        if get_tex("specular_roughness"):
            result["roughnessMap"] = get_tex("specular_roughness")

        if get_tex("normal"):
            result["normalMap"] = get_tex("normal")

        result["specularIntensity"] = get_param("specular", 1.0)
        result["specularColor"] = get_param("specular_color", [1.0, 1.0, 1.0])
        result["ior"] = get_param("specular_IOR", 1.5)

        transmission = get_param("transmission", 0.0)
        if transmission > 0.0:
            result["transmission"] = transmission
            result["transparent"] = True

        coat = get_param("coat", 0.0)
        if coat > 0.0:
            result["clearcoat"] = coat
            result["clearcoatRoughness"] = get_param("coat_roughness", 0.1)

        sheen = get_param("sheen", 0.0)
        if sheen > 0.0:
            result["sheen"] = sheen
            result["sheenColor"] = get_param("sheen_color", [1.0, 1.0, 1.0])
            result["sheenRoughness"] = get_param("sheen_roughness", 0.3)

        emission = get_param("emission", 0.0)
        if emission > 0.0:
            em_color = get_param("emission_color", [1.0, 1.0, 1.0])
            result["emissive"] = [c * emission for c in em_color]
            result["emissiveIntensity"] = 1.0
            if get_tex("emission_color"):
                result["emissiveMap"] = get_tex("emission_color")

        tf_thickness = get_param("thin_film_thickness", 0.0)
        if tf_thickness > 0.0:
            result["iridescence"] = 1.0
            result["iridescenceIOR"] = get_param("thin_film_IOR", 1.5)
            result["iridescenceThicknessRange"] = [0.0, tf_thickness]

        opacity = get_param("opacity", 1.0)
        if isinstance(opacity, list):
            avg_opacity = sum(opacity) / len(opacity)
        else:
            avg_opacity = opacity
        if avg_opacity < 1.0:
            result["opacity"] = avg_opacity
            result["transparent"] = True

    elif model == "gltf_pbr":
        if get_tex("base_color"):
            result["map"] = get_tex("base_color")
        else:
            result["color"] = get_param("base_color", [1.0, 1.0, 1.0])

        result["metalness"] = get_param("metallic", 0.0)
        result["roughness"] = get_param("roughness", 1.0)
        result["ior"] = get_param("ior", 1.5)
        result["transmission"] = get_param("transmission", 0.0)

        if get_tex("metallic_roughness"):
            mr = get_tex("metallic_roughness")
            result["metalnessMap"] = mr
            result["roughnessMap"] = mr

        if get_tex("normal"):
            result["normalMap"] = get_tex("normal")

        clearcoat = get_param("clearcoat", 0.0)
        if clearcoat > 0.0:
            result["clearcoat"] = clearcoat
            result["clearcoatRoughness"] = get_param("clearcoat_roughness", 0.0)

        sheen_color = get_param("sheen_color")
        if sheen_color:
            result["sheenColor"] = sheen_color
            result["sheenRoughness"] = get_param("sheen_roughness", 0.0)
            result["sheen"] = 1.0

        emissive = get_param("emissive", [0.0, 0.0, 0.0])
        if any(c > 0.0 for c in emissive):
            result["emissive"] = emissive
            result["emissiveIntensity"] = get_param("emissive_strength", 1.0)
            if get_tex("emissive"):
                result["emissiveMap"] = get_tex("emissive")

    elif model == "open_pbr_surface":
        base_weight = get_param("base_weight", 1.0)
        base_color = get_param("base_color", [0.8, 0.8, 0.8])
        if get_tex("base_color"):
            result["map"] = get_tex("base_color")
        else:
            result["color"] = [c * base_weight for c in base_color]

        result["metalness"] = get_param("base_metalness", 0.0)
        if get_tex("base_metalness"):
            result["metalnessMap"] = get_tex("base_metalness")

        result["roughness"] = get_param("specular_roughness", 0.5)
        if get_tex("specular_roughness"):
            result["roughnessMap"] = get_tex("specular_roughness")

        if get_tex("geometry_normal"):
            result["normalMap"] = get_tex("geometry_normal")

        result["ior"] = get_param("specular_ior", 1.5)

        transmission = get_param("transmission_weight", 0.0)
        if transmission > 0.0:
            result["transmission"] = transmission
            result["transparent"] = True

        coat = get_param("coat_weight", 0.0)
        if coat > 0.0:
            result["clearcoat"] = coat
            result["clearcoatRoughness"] = get_param("coat_roughness", 0.0)

        emission_lum = get_param("emission_luminance", 0.0)
        if emission_lum > 0.0:
            em_color = get_param("emission_color", [1.0, 1.0, 1.0])
            result["emissive"] = em_color
            result["emissiveIntensity"] = emission_lum / 1000.0
            if get_tex("emission_color"):
                result["emissiveMap"] = get_tex("emission_color")

    return result


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

    # Determine channels (typically R, G, B — may also have A)
    channel_names = list(header["channels"].keys())
    rgb = [ch for ch in ("R", "G", "B") if ch in channel_names]
    if not rgb:
        rgb = sorted(channel_names)[:3]

    # Read channel data as 32-bit float
    pt = Imath.PixelType(Imath.PixelType.FLOAT)
    raw = exr_file.channels(rgb, pt)

    # Convert float channel buffers → 8-bit pixels in RGB interleaved order
    num_pixels = width * height
    pixels = bytearray(num_pixels * len(rgb))
    for ch_idx, ch_data in enumerate(raw):
        floats = array.array("f", ch_data)
        for i, val in enumerate(floats):
            clamped = max(0.0, min(1.0, val))
            pixels[i * len(rgb) + ch_idx] = int(clamped * 255 + 0.5)

    from PIL import Image

    mode = "RGB" if len(rgb) == 3 else "RGBA"
    img = Image.frombytes(mode, (width, height), bytes(pixels))
    png_path = exr_path.with_suffix(".png")
    img.save(png_path)
    log.info("Converted EXR → PNG: %s", png_path.name)
    return png_path


# ---------------------------------------------------------------------------
# Base64 encoding helper
# ---------------------------------------------------------------------------

def _encode_texture_base64(file_path: Path) -> str:
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
# Download helpers (source-specific)
# ---------------------------------------------------------------------------

def _download_ambientcg(download_url: str, out_dir: Path) -> Path | None:
    """Download ambientCG zip, extract .mtlx + images."""
    log.info("Downloading ambientCG zip: %s", download_url)
    resp = requests.get(download_url, timeout=120)
    resp.raise_for_status()

    tex_dir = out_dir / "textures"
    tex_dir.mkdir(parents=True, exist_ok=True)

    mtlx_path = None
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        for name in zf.namelist():
            if name.endswith(".mtlx"):
                mtlx_path = out_dir / "material.mtlx"
                mtlx_path.write_bytes(zf.read(name))
            elif any(name.lower().endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".exr")):
                dst = tex_dir / Path(name).name
                dst.write_bytes(zf.read(name))

    return mtlx_path


def _download_gpuopen(download_url: str, out_dir: Path) -> Path | None:
    """Download GPUOpen package zip, extract .mtlx + images."""
    log.info("Downloading GPUOpen package: %s", download_url)
    resp = requests.get(download_url, timeout=120)
    resp.raise_for_status()

    tex_dir = out_dir / "textures"
    tex_dir.mkdir(parents=True, exist_ok=True)

    mtlx_path = None
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        for name in zf.namelist():
            if name.endswith(".mtlx"):
                mtlx_path = out_dir / "material.mtlx"
                mtlx_path.write_bytes(zf.read(name))
            elif any(name.lower().endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".exr")):
                dst = tex_dir / Path(name).name
                dst.write_bytes(zf.read(name))

    return mtlx_path


def _download_polyhaven(download_meta: dict, out_dir: Path) -> Path | None:
    """Download PolyHaven .mtlx and individual texture files."""
    mtlx_url = download_meta.get("mtlx_url")
    if not mtlx_url:
        log.error("No mtlx_url in PolyHaven download_meta")
        return None

    headers = {"User-Agent": "MTLX_Polyaven_Loader/1.0"}

    log.info("Downloading PolyHaven mtlx: %s", mtlx_url)
    resp = requests.get(mtlx_url, headers=headers, timeout=60)
    resp.raise_for_status()
    mtlx_path = out_dir / "material.mtlx"
    mtlx_path.write_text(resp.text)

    tex_dir = out_dir / "textures"
    tex_dir.mkdir(parents=True, exist_ok=True)

    texture_urls = download_meta.get("texture_urls", {})
    for tex_path, tex_url in texture_urls.items():
        log.info("Downloading texture: %s", tex_url)
        tex_resp = requests.get(tex_url, headers=headers, timeout=120)
        tex_resp.raise_for_status()
        dst = tex_dir / Path(tex_path).name
        dst.write_bytes(tex_resp.content)

    return mtlx_path


def _generate_physicallybased(download_meta: dict, out_dir: Path) -> Path | None:
    """Generate .mtlx from PhysicallyBased parametric data (no download needed)."""
    import MaterialX as mx_mod
    from materialxMaterials.physicallyBasedMaterialX import PhysicallyBasedMaterialLoader

    name = download_meta.get("name", "Material")

    loader = PhysicallyBasedMaterialLoader(mx_mod, None)
    loader.materials = [download_meta]
    loader.materialNames = [name]

    loader.convertToMaterialX([name], "open_pbr_surface", {}, "OpenPBR")
    mtlx_string = loader.convertToMaterialXString()

    if not mtlx_string:
        log.error("Failed to generate MaterialX for %s", name)
        return None

    out_dir.mkdir(parents=True, exist_ok=True)
    mtlx_path = out_dir / "material.mtlx"
    mtlx_path.write_text(mtlx_string)
    return mtlx_path


# ---------------------------------------------------------------------------
# Main conversion pipeline
# ---------------------------------------------------------------------------

def convert_material(
    material_id: str,
    resolution: str | None,
    conn: sqlite3.Connection,
) -> Path:
    """
    Convert a material to Three.js MeshPhysicalMaterial JSON with base64 textures.

    Returns path to the material.json file.
    """
    # Sanitize material_id for filesystem (replace : with /)
    out_dir = BAKED_DIR / material_id.replace(":", "/")

    # Lazy check
    json_path = out_dir / "material.json"
    if json_path.exists():
        return json_path

    out_dir.mkdir(parents=True, exist_ok=True)

    # Fetch material + variant info from DB
    mat_row = conn.execute(
        "SELECT * FROM materials WHERE id = ?", (material_id,)
    ).fetchone()
    if not mat_row:
        raise ValueError(f"Material not found: {material_id}")

    source = mat_row["source"]
    has_textures = bool(mat_row["has_textures"])

    # Pick variant
    if resolution:
        var_row = conn.execute(
            "SELECT * FROM material_variants WHERE material_id = ? AND resolution = ?",
            (material_id, resolution),
        ).fetchone()
    else:
        var_row = conn.execute(
            "SELECT * FROM material_variants WHERE material_id = ? ORDER BY rowid LIMIT 1",
            (material_id,),
        ).fetchone()

    if not var_row:
        raise ValueError(
            f"No variant found for {material_id} resolution={resolution}"
        )

    download_url = var_row["download_url"]
    download_meta = json.loads(var_row["download_meta"]) if var_row["download_meta"] else {}

    # --- Download phase ---
    mtlx_path = None
    if source == "ambientcg":
        mtlx_path = _download_ambientcg(download_url, out_dir)
    elif source == "gpuopen":
        mtlx_path = _download_gpuopen(download_url, out_dir)
    elif source == "polyhaven":
        mtlx_path = _download_polyhaven(download_meta, out_dir)
    elif source == "physicallybased":
        mtlx_path = _generate_physicallybased(download_meta, out_dir)

    if not mtlx_path or not mtlx_path.exists():
        raise RuntimeError(f"Failed to obtain .mtlx for {material_id}")

    # --- Bake phase (only for texture-based materials) ---
    tex_dir = out_dir / "textures"
    if has_textures:
        baked_mtlx = out_dir / "material.baked.mtlx"
        try:
            bake_materials(
                *load_document_with_stdlib(mtlx_path),
                baked_mtlx,
                tex_dir,
                mtlx_dir=mtlx_path.parent.resolve(),
            )
            doc, _ = load_document_with_stdlib(baked_mtlx)
            mats = extract_materials(doc)
        except Exception as e:
            log.warning("Baking failed for %s: %s — using original doc", material_id, e)
            mats = []

        # Also extract from original doc to fill in any textures the baker missed
        # (e.g. EXR files the baker can't handle)
        orig_doc, _ = load_document_with_stdlib(mtlx_path)
        orig_mats = extract_materials(orig_doc)

        if not any(m["textures"] for m in mats):
            # Full fallback: baker produced nothing usable
            log.info("Fallback: using original document for %s", material_id)
            mats = orig_mats

        # Merge: for each texture in original that's missing from baked,
        # copy the source file and add the texture reference
        if mats and orig_mats:
            baked_tex = mats[0].get("textures", {})
            orig_tex = orig_mats[0].get("textures", {})
            for inp_name, tex_info in orig_tex.items():
                if inp_name not in baked_tex:
                    src_file = tex_info.get("file")
                    if not src_file:
                        continue
                    src_path = (mtlx_path.parent / src_file).resolve()
                    if src_path.exists():
                        dst = tex_dir / src_path.name
                        if not dst.exists():
                            tex_dir.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(src_path, dst)
                        log.info("Merged missing texture %s from original: %s",
                                 inp_name, src_path.name)
                        mats[0]["textures"][inp_name] = tex_info
                    else:
                        # Try alternative extensions (EXR→JPG/PNG)
                        for alt_ext in (".jpg", ".png", ".jpeg"):
                            alt_path = src_path.with_suffix(alt_ext)
                            if alt_path.exists():
                                dst = tex_dir / alt_path.name
                                if not dst.exists():
                                    tex_dir.mkdir(parents=True, exist_ok=True)
                                    shutil.copy2(alt_path, dst)
                                alt_info = dict(tex_info, file=str(
                                    Path(src_file).with_suffix(alt_ext)))
                                mats[0]["textures"][inp_name] = alt_info
                                log.info("Substituted %s → %s for %s",
                                         src_path.name, alt_path.name, inp_name)
                                break
    else:
        # Parametric (PhysicallyBased) — no baking
        doc, _ = load_document_with_stdlib(mtlx_path)
        mats = extract_materials(doc)

    if not mats:
        raise RuntimeError(f"No materials extracted from {material_id}")

    # Use first material
    mat = mats[0]

    # --- Extract phase → Three.js JSON ---
    threejs_params = to_threejs_physical(mat, textures_root_url="textures")

    # Detect shader_model and update DB if needed
    shader_model = mat.get("shader_model")
    if shader_model and not mat_row["shader_model"]:
        conn.execute(
            "UPDATE materials SET shader_model = ? WHERE id = ?",
            (shader_model, material_id),
        )
        conn.commit()

    # --- Build base64 texture dict (EXR files are converted to PNG) ---
    textures_b64 = {}
    for key, val in list(threejs_params.items()):
        if isinstance(val, str) and val.startswith("textures/"):
            tex_file = tex_dir / Path(val).name
            if tex_file.exists():
                b64_data = _encode_texture_base64(tex_file)
                # If EXR was converted to PNG, update the key and param
                if tex_file.suffix.lower() == ".exr":
                    png_name = tex_file.with_suffix(".png").name
                    new_val = f"textures/{png_name}"
                    threejs_params[key] = new_val
                    textures_b64[new_val] = b64_data
                else:
                    textures_b64[val] = b64_data

    # --- Write output JSON ---
    output = {
        "id": material_id,
        "name": mat_row["name"],
        "source": source,
        "category": mat_row["category"],
        "params": threejs_params,
        "textures": textures_b64,
    }

    json_path.write_text(json.dumps(output, indent=2))
    log.info("Wrote %s", json_path)
    return json_path
