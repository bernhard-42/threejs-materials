"""
Bake + extract → MeshPhysicalMaterial JSON with base64-encoded textures.
"""

import array
import base64
import logging
import mimetypes
import os
import shutil
import threading
from pathlib import Path
from sys import platform

import numpy as np
from PIL import Image

from threejs_materials.utils import OpenEXR, Imath, ensure_materialx, _linear_to_srgb

log = logging.getLogger(__name__)

_bake_lock = threading.Lock()


# ---------------------------------------------------------------------------
# MaterialX helpers
# ---------------------------------------------------------------------------


def load_document_with_stdlib(mtlx_path: Path):
    """Load a MaterialX document with standard library."""
    mx = ensure_materialx()
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


def _iter_image_nodes(doc):
    """Yield every <image>/<tiledimage> node in the document, both at the
    top level and inside any nodegraph."""
    for node in doc.getNodes():
        if node.getCategory() in ("image", "tiledimage"):
            yield node
    for ng in doc.getNodeGraphs():
        for node in ng.getNodes():
            if node.getCategory() in ("image", "tiledimage"):
                yield node


def _exr_to_png(exr_path: Path, png_path: Path) -> None:
    """Read an EXR file and write an 8-bit linear PNG with the same pixel
    values (clipped to [0, 1], no gamma correction).

    Polyhaven EXRs come in two layouts: single-channel ``Y`` (grayscale —
    roughness, displacement) or ``RGBA`` (normals). Other layouts fall back
    to per-channel R/G/B reads.
    """
    if OpenEXR is None:
        raise ImportError("openexr is required to read EXR files: pip install openexr")

    with OpenEXR.File(str(exr_path)) as f:
        channels = f.parts[0].channels
        if "Y" in channels:
            arr = channels["Y"].pixels
            arr8 = (np.clip(arr, 0, 1) * 255 + 0.5).astype(np.uint8)
            img = Image.fromarray(arr8, mode="L")
        elif "RGBA" in channels:
            arr = channels["RGBA"].pixels[..., :3]
            arr8 = (np.clip(arr, 0, 1) * 255 + 0.5).astype(np.uint8)
            img = Image.fromarray(arr8, mode="RGB")
        elif "RGB" in channels:
            arr = channels["RGB"].pixels[..., :3]
            arr8 = (np.clip(arr, 0, 1) * 255 + 0.5).astype(np.uint8)
            img = Image.fromarray(arr8, mode="RGB")
        elif {"R", "G", "B"}.issubset(channels):
            arr = np.stack(
                [channels["R"].pixels, channels["G"].pixels, channels["B"].pixels],
                axis=-1,
            )
            arr8 = (np.clip(arr, 0, 1) * 255 + 0.5).astype(np.uint8)
            img = Image.fromarray(arr8, mode="RGB")
        else:
            raise NotImplementedError(
                f"EXR has unrecognized channel layout: {list(channels.keys())}"
            )

    img.save(str(png_path), format="PNG")


def _transcode_exr_inputs(doc, base_dir: Path) -> None:
    """Rewrite any ``<image>`` node referencing a ``.exr`` file to use a
    sibling ``.png`` instead, transcoding the file on disk if needed.

    MaterialX 1.39's TextureBaker can't read EXR — when an image-node's
    file points at an .exr, the baker silently falls back to the node's
    ``<input name="default">`` value, turning a real texture into a flat
    scalar. Pre-transcoding to 8-bit linear PNG keeps the bake honest.

    A ``colorspace="lin_rec709"`` attribute is set on the rewritten file
    input when none was already declared, so downstream tools don't gamma-
    decode the linear data as if the PNG were sRGB.
    """

    for node in _iter_image_nodes(doc):
        file_input = node.getInput("file")
        if file_input is None:
            continue
        value = file_input.getValueString()
        if not value or not value.lower().endswith(".exr"):
            continue
        mx = ensure_materialx()
        exr_path = (base_dir / value).resolve()
        if not exr_path.exists():
            log.debug("EXR not on disk, skipping transcode: %s", exr_path)
            continue
        png_path = exr_path.with_suffix(".png")
        if not png_path.exists():
            _exr_to_png(exr_path, png_path)
        new_value = value[: -len(".exr")] + ".png"
        file_input.setValueString(new_value)
        cs_attr = mx.Element.COLOR_SPACE_ATTRIBUTE
        if not file_input.hasAttribute(cs_attr):
            file_input.setColorSpace("lin_rec709")


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
    mx = ensure_materialx()
    tex_dir.mkdir(parents=True, exist_ok=True)

    base_type = mx.PyMaterialXRender.BaseType.UINT8
    try:
        baker = mx.PyMaterialXRenderGlsl.TextureBaker.create(width, height, base_type)
    except Exception:
        if platform == "darwin":
            baker = mx.PyMaterialXRenderMsl.TextureBaker.create(width, height, base_type)
        else:
            raise

    baker.writeDocumentPerMaterial(False)

    baked_mtlx_path = baked_mtlx_path.resolve()
    tex_dir = tex_dir.resolve()
    baker_out = tex_dir / baked_mtlx_path.name

    with _bake_lock:
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
    if type_str == "vector2":
        return [float(x.strip()) for x in value_str.split(",")]
    if type_str in ("matrix33", "matrix44"):
        return [float(x.strip()) for x in value_str.split(",")]
    if type_str == "integer":
        return int(value_str)
    if type_str == "boolean":
        return value_str.lower() in ("true", "1")
    return value_str


def find_upstream_image(inp) -> dict | None:
    """Walk upstream from an input to find an image/tiledimage node."""
    mx = ensure_materialx()
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

    mx = ensure_materialx()
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


# ---------------------------------------------------------------------------
# MaterialX TextureBaker workaround — recover scalar IORs from constant graphs
# ---------------------------------------------------------------------------
# MaterialX 1.39.x TextureBaker does not preserve the values of scalar shader
# inputs that are wired through a nodegraph to a constant node — it rewrites
# the baked shader input to a placeholder ``value="1"`` instead of the
# graph's evaluated constant. This silently breaks ~6 GPUOpen materials
# (Old Paint + the Marble family) that expose ``specular_IOR`` through a
# graph rather than as a direct value, producing ``ior=1.0``
#
# Workaround: before we trust the baker's params, walk each IOR-family input
# on the ORIGINAL doc. If it reduces to a trivial constant (through the
# node categories ``constant``, ``dot``, and ``convert``), we restore that
# value over whatever the baker wrote. Scope is deliberately narrow — only
# the three IOR inputs whose default semantics (refractive index ≥ 1) are
# universal across MaterialX shader models.
#
# If this ever needs to cover more scalars or more complex graphs, graduate
# to a general graph-walker; for today, the narrow patch closes every known
# real failure and introduces no new policy for inputs that aren't affected.

_IOR_INPUTS_TO_RECOVER: tuple[str, ...] = (
    "specular_IOR", "coat_IOR", "thin_film_IOR",
)


def _evaluate_constant_scalar_graph(inp) -> float | None:
    """Walk an input's upstream graph and return the constant scalar it
    reduces to, or ``None`` if the graph isn't a trivial constant.

    Handles the narrow pattern ``constant | dot | convert`` chains
    terminating at a ``constant`` node — which is what GPUOpen's affected
    materials use. Anything more elaborate returns ``None`` so the baker's
    value is left in place.
    """
    mx = ensure_materialx()
    connected = inp.getConnectedNode()
    doc = inp.getDocument()

    if connected is None and inp.hasNodeGraphString():
        ng_name = inp.getNodeGraphString()
        ng = doc.getNodeGraph(ng_name)
        if ng:
            out_attr = mx.Output.OUTPUT_ATTRIBUTE
            out_name = (
                inp.getAttribute(out_attr) if inp.hasAttribute(out_attr) else ""
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

    visited: set[str] = set()
    while connected is not None and connected.getName() not in visited:
        visited.add(connected.getName())
        cat = connected.getCategory()
        if cat == "constant":
            value_inp = connected.getInput("value")
            if value_inp is None:
                return None
            val_str = value_inp.getValueString()
            try:
                return float(val_str) if val_str else None
            except ValueError:
                return None
        if cat in ("dot", "convert"):
            in_inp = connected.getInput("in")
            if in_inp is None:
                return None
            val_str = in_inp.getValueString()
            if val_str:
                try:
                    return float(val_str)
                except ValueError:
                    return None
            connected = in_inp.getConnectedNode()
            continue
        return None
    return None


def _recover_baker_clobbered_iors(orig_doc, mats: list[dict]) -> None:
    """MaterialX TextureBaker workaround — see module-level comment above.

    For each material, walks ``specular_IOR`` / ``coat_IOR`` /
    ``thin_film_IOR`` on the ORIGINAL doc. If the input reduces to a
    constant via ``_evaluate_constant_scalar_graph``, overwrites the
    corresponding entry in ``mats[i]['params']`` so the author's graph-wired
    value survives baking.
    """
    mx = ensure_materialx()
    mats_by_name = {m["name"]: m for m in mats}
    for mat_node in orig_doc.getMaterialNodes():
        target = mats_by_name.get(mat_node.getName())
        if target is None:
            continue
        shader_nodes = mx.getShaderNodes(mat_node)
        if not shader_nodes:
            continue
        shader = shader_nodes[0]
        for inp in shader.getInputs():
            inp_name = inp.getName()
            if inp_name not in _IOR_INPUTS_TO_RECOVER:
                continue
            value = _evaluate_constant_scalar_graph(inp)
            if value is not None:
                target.setdefault("params", {})[inp_name] = value

    return None


def extract_materials(doc) -> list[dict]:
    """Extract all materials from a MaterialX document."""
    mx = ensure_materialx()
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
            log.warning("Material '%s' has no shader nodes — skipping", mat_node.getName())
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

        # Check for displacement shader on the material node
        disp_input = mat_node.getInput("displacementshader")
        if disp_input:
            disp_node = disp_input.getConnectedNode()
            if disp_node and disp_node.getCategory() == "displacement":
                disp_inp = disp_node.getInput("displacement")
                if disp_inp:
                    img_info = find_upstream_image(disp_inp)
                    if img_info and "file" in img_info:
                        mat_info["textures"]["displacement"] = img_info
                scale_inp = disp_node.getInput("scale")
                if scale_inp:
                    scale_str = scale_inp.getValueString()
                    if scale_str:
                        mat_info["params"]["displacement_scale"] = float(scale_str)

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
        tex_path = (base_dir / t[mtlx_input]["file"]).resolve()
        if tex_path.exists():
            entry = props.setdefault(name, {})
            entry["texture"] = tex_path.relative_to(base_dir.resolve()).as_posix()
            cs = t[mtlx_input].get("colorspace")
            if cs:
                entry["colorSpace"] = cs

    if model == "standard_surface":
        # Three.js multiplies scalar × texture for all map properties.
        # When a texture exists, set scalar to neutral so texture controls fully.
        # The baker's output already reflects the intended diffuse brightness;
        # applying `base` again would double-darken the result.
        # MaterialX scalar values are linear; values.color is sRGB-stored
        # (matches three-cad-viewer's setRGB(SRGBColorSpace)).
        base = p.get("base", 1.0)
        base_color = p.get("base_color", [0.8, 0.8, 0.8])
        metalness_val = p.get("metalness", 0.0)
        if has_tex("base_color"):
            # Three.js multiplies color × map texture.
            # For metallic materials (metalness≥1), the baked texture IS the
            # F0 reflectance.  The baker does not include the `base` weight,
            # but applying it here darkens metals too much — the
            # standard_surface specular layer compensates in ways that glTF
            # pbrMetallicRoughness cannot replicate.  Use neutral [1,1,1].
            # [1,1,1] is invariant under linear↔sRGB.
            if metalness_val >= 1.0 or has_tex("metalness"):
                val("color", [1.0, 1.0, 1.0])
            else:
                val("color", [_linear_to_srgb(base)] * 3)
        else:
            val("color", [_linear_to_srgb(c * base) for c in base_color])
        tex("color", "base_color")

        val("metalness", 1.0 if has_tex("metalness") else p.get("metalness", 0.0))
        tex("metalness", "metalness")

        val("roughness", 1.0 if has_tex("specular_roughness") else p.get("specular_roughness", 0.5))
        tex("roughness", "specular_roughness")

        tex("normal", "normal")

        val("specularIntensity", p.get("specular", 1.0))
        tex("specularIntensity", "specular")
        val("specularColor", p.get("specular_color", [1.0, 1.0, 1.0]))
        tex("specularColor", "specular_color")
        val("ior", p.get("specular_IOR", 1.5))

        transmission = p.get("transmission", 0.0)
        if has_tex("transmission"):
            # Procedural transmission: baker bakes the scalar into the
            # texture, so the effective scalar is the neutral multiplier 1.0.
            transmission = 1.0
        if transmission > 0.0:
            val("transmission", transmission)
            tex("transmission", "transmission")
            # Do NOT set transparent=True here.  Three.js renders transmissive
            # objects in a dedicated pass; setting transparent moves them to the
            # wrong (transparent) pass and breaks physically-correct refraction.

        # standard_surface specular_anisotropy is not mapped.
        # It splits roughness into directional axes (α·(1±a)), while glTF
        # boosts one axis from base roughness — the models are structurally
        # incompatible and no scalar remap produces correct results.

        coat = p.get("coat", 0.0)
        if has_tex("coat"):
            coat = 1.0
        if coat > 0.0:
            val("clearcoat", coat)
            tex("clearcoat", "coat")
            val("clearcoatRoughness", p.get("coat_roughness", 0.1))
            tex("clearcoatNormal", "coat_normal")

        sheen = p.get("sheen", 0.0)
        # Only infer "sheen on" from a procedural sheen_color when no
        # explicit weight was authored — preserve literal sheen < 1.0.
        if sheen == 0.0 and has_tex("sheen_color"):
            sheen = 1.0
        if sheen > 0.0:
            val("sheen", sheen)
            val("sheenColor", p.get("sheen_color", [1.0, 1.0, 1.0]))
            tex("sheenColor", "sheen_color")
            val("sheenRoughness", p.get("sheen_roughness", 0.3))

        emission = p.get("emission", 0.0)
        if emission > 0.0:
            em_color = p.get("emission_color", [1.0, 1.0, 1.0])
            if has_tex("emission_color"):
                # Baked texture already includes emission color; use neutral scalar
                val("emissive", [1.0, 1.0, 1.0])
            else:
                val("emissive", [c * emission for c in em_color])
            val("emissiveIntensity", emission)
            tex("emissive", "emission_color")

        tf_thickness = p.get("thin_film_thickness", 0.0)
        if tf_thickness > 0.0:
            val("iridescence", 1.0)
            tex("iridescence", "thin_film_weight")
            val("iridescenceIOR", p.get("thin_film_IOR", 1.5))
            # MaterialX exposes a single `thin_film_thickness` scalar (nm);
            # Three.js uses a [min, max] range. Emit the scalar as uniform
            # thickness so no fictional min value is introduced.
            val("iridescenceThicknessRange", [tf_thickness, tf_thickness])

        # Only apply opacity when transmission is not active
        # (transmission subsumes opacity; combining them causes double attenuation)
        if transmission <= 0.0:
            opacity = p.get("opacity", 1.0)
            if isinstance(opacity, list):
                avg_opacity = sum(opacity) / len(opacity)
            else:
                avg_opacity = opacity
            if avg_opacity < 1.0:
                val("opacity", avg_opacity)
                val("transparent", True)
            if has_tex("opacity"):
                # Pure MASK mode for opacity textures: alphaTest alone gives
                # crisp cutoff AND depth writes. Adding transparent=True here
                # would disable depth writes and cause back-face bleed-through
                # on closed shapes (spheres, etc.).
                val("alphaTest", 0.5)
            tex("opacity", "opacity")

    elif model == "gltf_pbr":
        # Three.js multiplies scalar × texture — set scalar to neutral when texture exists.
        # MaterialX base_color is linear; values.color is sRGB-stored.
        # [1,1,1] is invariant under linear↔sRGB conversion.
        if has_tex("base_color"):
            val("color", [1.0, 1.0, 1.0])
        else:
            base_color = p.get("base_color", [1.0, 1.0, 1.0])
            val("color", [_linear_to_srgb(c) for c in base_color])
        tex("color", "base_color")

        has_mr_tex = has_tex("metallic_roughness")
        has_separate_m = has_tex("metallic")
        has_separate_r = has_tex("roughness")
        val("metalness", 1.0 if (has_mr_tex or has_separate_m) else p.get("metallic", 0.0))
        val("roughness", 1.0 if (has_mr_tex or has_separate_r) else p.get("roughness", 1.0))
        val("ior", p.get("ior", 1.5))

        transmission = p.get("transmission", 0.0)
        if has_tex("transmission"):
            transmission = 1.0
        val("transmission", transmission)
        tex("transmission", "transmission")
        if transmission > 0.0:
            att_color = p.get("attenuation_color")
            if att_color:
                val("attenuationColor", att_color)
            att_dist = p.get("attenuation_distance")
            if att_dist and att_dist > 0.0:
                val("attenuationDistance", att_dist)
            thickness = p.get("thickness")
            if has_tex("thickness"):
                thickness = 1.0
            if thickness and thickness > 0.0:
                val("thickness", thickness)
            tex("thickness", "thickness")

        # glTF metallic-roughness is a packed texture (G=roughness, B=metalness).
        # Encode once under a dedicated key; consumer assigns to both maps.
        if has_mr_tex:
            tex("metallicRoughness", "metallic_roughness")
            if "metallicRoughness" in props:
                props["metallicRoughness"]["channelMapping"] = {
                    "roughness": "g",
                    "metalness": "b",
                }

        # The baker may output separate textures per input instead of a
        # packed metallic_roughness texture.  Map them individually.
        if not has_mr_tex:
            tex("metalness", "metallic")
            tex("roughness", "roughness")

        tex("normal", "normal")
        normal_scale = p.get("normal_scale", 1.0)
        if normal_scale != 1.0:
            val("normalScale", [normal_scale, normal_scale])

        tex("ao", "occlusion")

        aniso = p.get("anisotropy_strength", 0.0)
        if aniso > 0.0:
            val("anisotropy", aniso)
            val("anisotropyRotation", p.get("anisotropy_rotation", 0.0))

        clearcoat = p.get("clearcoat", 0.0)
        if has_tex("clearcoat"):
            clearcoat = 1.0
        if clearcoat > 0.0:
            val("clearcoat", clearcoat)
            tex("clearcoat", "clearcoat")
            val("clearcoatRoughness", p.get("clearcoat_roughness", 0.0))
            tex("clearcoatNormal", "clearcoat_normal")

        # gltf_pbr has no separate `sheen` weight input; KHR_materials_sheen
        # activates the layer whenever sheenColorFactor != [0,0,0]. We infer
        # sheen=1.0 and gate the block on any sheen-related signal.
        sheen_color = p.get("sheen_color")
        has_sheen_color_tex = has_tex("sheen_color")
        has_sheen_roughness_tex = has_tex("sheen_roughness")
        if sheen_color or has_sheen_color_tex or has_sheen_roughness_tex:
            val(
                "sheenColor",
                [1.0, 1.0, 1.0] if has_sheen_color_tex else (sheen_color or [1.0, 1.0, 1.0]),
            )
            tex("sheenColor", "sheen_color")
            val(
                "sheenRoughness",
                1.0 if has_sheen_roughness_tex else p.get("sheen_roughness", 0.0),
            )
            tex("sheenRoughness", "sheen_roughness")
            val("sheen", 1.0)

        emissive = p.get("emissive", [0.0, 0.0, 0.0])
        if any(c > 0.0 for c in emissive):
            val("emissive", emissive)
            val("emissiveIntensity", p.get("emissive_strength", 1.0))
            tex("emissive", "emissive")

        # glTF alpha / opacity
        alpha = p.get("alpha", 1.0)
        alpha_mode = p.get("alpha_mode", 0)  # 0=OPAQUE, 1=MASK, 2=BLEND
        if alpha_mode == 2:
            # BLEND mode → standard opacity
            if alpha < 1.0:
                val("opacity", alpha)
                val("transparent", True)
            if has_tex("alpha"):
                # Alpha texture under BLEND only works if transparent=True;
                # without this flag Three.js ignores the alphaMap entirely.
                val("transparent", True)
            tex("opacity", "alpha")
        elif alpha_mode == 1:
            # MASK mode → alphaTest
            val("alphaTest", p.get("alpha_cutoff", 0.5))

        # glTF iridescence (KHR_materials_iridescence)
        iridescence = p.get("iridescence", 0.0)
        if has_tex("iridescence"):
            iridescence = 1.0
        if iridescence > 0.0:
            val("iridescence", iridescence)
            tex("iridescence", "iridescence")
            val("iridescenceIOR", p.get("iridescence_ior", 1.3))
            # MaterialX gltf_pbr exposes a single `iridescence_thickness`
            # scalar in nm (default 100 per the MaterialX shader spec).
            # Three.js uses a [min, max] range; emit the scalar as uniform
            # thickness so no fictional min value is introduced.
            iri_thick = p.get("iridescence_thickness", 100.0)
            val("iridescenceThicknessRange", [iri_thick, iri_thick])

        # glTF dispersion (KHR_materials_dispersion)
        dispersion = p.get("dispersion", 0.0)
        if dispersion > 0.0:
            val("dispersion", dispersion)

    elif model == "open_pbr_surface":
        # Mirrors standard_surface's dielectric handling: the baker leaves
        # `base_weight` as a literal on the shader input and does NOT fold it
        # into the baked `base_color` texture. Emit `base_weight` as the
        # scalar so Three.js reproduces the MaterialX shading math
        # `base_weight × base_color` at render time. See materialx_baker.md.
        # MaterialX values are linear; values.color is sRGB-stored
        # (matches three-cad-viewer's setRGB(SRGBColorSpace)).
        base_weight = p.get("base_weight", 1.0)
        base_color = p.get("base_color", [0.8, 0.8, 0.8])
        if has_tex("base_color"):
            val("color", [_linear_to_srgb(base_weight)] * 3)
        else:
            val("color", [_linear_to_srgb(c * base_weight) for c in base_color])
        tex("color", "base_color")

        val("metalness", 1.0 if has_tex("base_metalness") else p.get("base_metalness", 0.0))
        tex("metalness", "base_metalness")

        val("roughness", 1.0 if has_tex("specular_roughness") else p.get("specular_roughness", 0.3))
        tex("roughness", "specular_roughness")

        spec_weight = p.get("specular_weight", 1.0)
        spec_color = p.get("specular_color")
        if spec_color or spec_weight != 1.0:
            val("specularIntensity", spec_weight)
            tex("specularIntensity", "specular_weight")
            if spec_color:
                val("specularColor", spec_color)
            tex("specularColor", "specular_color")

        tex("normal", "geometry_normal")

        val("ior", p.get("specular_ior", 1.5))

        transmission = p.get("transmission_weight", 0.0)
        if has_tex("transmission_weight"):
            transmission = 1.0
        if transmission > 0.0:
            val("transmission", transmission)
            tex("transmission", "transmission_weight")
            # Do NOT set transparent=True — see standard_surface comment above.
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

        # open_pbr_surface specular_roughness_anisotropy is not mapped.
        # Same structural mismatch as standard_surface — see comment above.

        coat = p.get("coat_weight", 0.0)
        if has_tex("coat_weight"):
            coat = 1.0
        if coat > 0.0:
            val("clearcoat", coat)
            tex("clearcoat", "coat_weight")
            val("clearcoatRoughness", p.get("coat_roughness", 0.0))
            tex("clearcoatNormal", "geometry_coat_normal")

        # OpenPBR fuzz → Three.js sheen
        fuzz = p.get("fuzz_weight", 0.0)
        # Only infer "fuzz on" from a procedural fuzz_color when no
        # explicit weight was authored — preserve literal fuzz_weight < 1.0.
        if fuzz == 0.0 and has_tex("fuzz_color"):
            fuzz = 1.0
        if fuzz > 0.0:
            val("sheen", fuzz)
            val("sheenColor", p.get("fuzz_color", [1.0, 1.0, 1.0]))
            tex("sheenColor", "fuzz_color")
            val("sheenRoughness", p.get("fuzz_roughness", 0.5))

        emission_lum = p.get("emission_luminance", 0.0)
        if emission_lum > 0.0:
            em_color = p.get("emission_color", [1.0, 1.0, 1.0])
            val("emissive", em_color)
            # emission_luminance is in nits (cd/m^2).  Dividing by 1000
            # produces reasonable brightness in typical non-HDR Three.js
            # scenes.  This is a pragmatic normalization, not physically exact.
            val("emissiveIntensity", emission_lum / 1000.0)
            tex("emissive", "emission_color")

        # Geometry opacity (only when transmission is not active)
        if transmission <= 0.0:
            geo_opacity = p.get("geometry_opacity", [1.0, 1.0, 1.0])
            if isinstance(geo_opacity, list):
                avg_opacity = sum(geo_opacity) / len(geo_opacity)
            else:
                avg_opacity = float(geo_opacity)
            if avg_opacity < 1.0:
                val("opacity", avg_opacity)
                val("transparent", True)
            if has_tex("geometry_opacity"):
                # Pure MASK mode — see standard_surface comment above.
                val("alphaTest", 0.5)
            tex("opacity", "geometry_opacity")

        # Thin-walled geometry → render both sides
        if p.get("geometry_thin_walled", False):
            val("side", 2)  # THREE.DoubleSide

        tf_weight = p.get("thin_film_weight", 0.0)
        if has_tex("thin_film_weight"):
            tf_weight = 1.0
        if tf_weight > 0.0:
            val("iridescence", tf_weight)
            tex("iridescence", "thin_film_weight")
            val("iridescenceIOR", p.get("thin_film_ior", 1.5))
            # OpenPBR `thin_film_thickness` is in μm (default 0.5 per spec);
            # Three.js expects nm.  Emit the scalar as uniform thickness so
            # no fictional min value is introduced.
            tf_thickness_nm = p.get("thin_film_thickness", 0.5) * 1000.0
            val("iridescenceThicknessRange", [tf_thickness_nm, tf_thickness_nm])

    else:
        log.warning("Unsupported shader model '%s' — only displacement will be mapped", model)

    # Displacement (model-independent — comes from material node, not surface shader)
    tex("displacement", "displacement")
    disp_scale = p.get("displacement_scale")
    if disp_scale is not None:
        val("displacementScale", disp_scale)

    # Warn if no meaningful properties were extracted
    non_disp = {k: v for k, v in props.items() if k not in ("displacement", "displacementScale")}
    if not non_disp:
        log.warning(
            "Material '%s' (model=%s) produced no PBR properties",
            mat.get("name", "?"), model,
        )

    return props


# ---------------------------------------------------------------------------
# EXR → PNG conversion
# ---------------------------------------------------------------------------


def _convert_exr_to_png(exr_path: Path) -> Path:
    """Convert an EXR image to 8-bit PNG. Returns path to the new PNG file."""
    if OpenEXR is None or Imath is None:
        raise ImportError("openexr is required to read EXR files: pip install openexr")

    exr_file = OpenEXR.InputFile(str(exr_path))
    header = exr_file.header()
    dw = header["dataWindow"]
    width = dw.max.x - dw.min.x + 1
    height = dw.max.y - dw.min.y + 1

    # Determine channels
    channel_names = list(header["channels"].keys())
    rgb = [ch for ch in ("R", "G", "B") if ch in channel_names]
    if not rgb:
        # Try case-insensitive match, preserving R,G,B order
        lower_map = {ch.lower(): ch for ch in channel_names}
        rgb = [lower_map[c] for c in ("r", "g", "b") if c in lower_map]
    if not rgb:
        # Last resort: take channels in source order (don't sort alphabetically)
        rgb = channel_names[:3]

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
    elif len(rgb) == 2:
        # Two-channel EXR — treat as grayscale + alpha (LA).
        pixels = bytearray(num_pixels * 2)
        for ch_idx, ch_data in enumerate(raw):
            floats = array.array("f", ch_data)
            for i, val in enumerate(floats):
                clamped = max(0.0, min(1.0, val))
                pixels[i * 2 + ch_idx] = int(clamped * 255 + 0.5)
        mode = "LA"
    else:
        # 3 or 4 channel → RGB or RGBA interleaved
        pixels = bytearray(num_pixels * len(rgb))
        for ch_idx, ch_data in enumerate(raw):
            floats = array.array("f", ch_data)
            for i, val in enumerate(floats):
                clamped = max(0.0, min(1.0, val))
                pixels[i * len(rgb) + ch_idx] = int(clamped * 255 + 0.5)
        mode = "RGB" if len(rgb) == 3 else "RGBA"

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


def _safe_copy(src: Path, dst_dir: Path) -> Path:
    """Copy src into dst_dir, avoiding overwrites from different source files."""
    dst = dst_dir / src.name
    if dst.exists():
        if dst.read_bytes() == src.read_bytes():
            return dst
        # Collision: different file with same name. Add numeric suffix.
        stem, suffix = src.stem, src.suffix
        counter = 1
        while True:
            dst = dst_dir / f"{stem}_{counter}{suffix}"
            if not dst.exists():
                break
            counter += 1
    dst_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return dst


_RESOLUTION_PIXELS: dict[str, int] = {
    "1K": 1024,
    "2K": 2048,
    "4K": 4096,
    "8K": 8192,
}


def _resolution_to_pixels(resolution: str) -> int:
    """Parse a resolution token (``"1K"``, ``"2K"``, ``"4K"``, ``"8K"``) into
    a pixel dimension. Unknown strings fall back to 1024 to preserve the
    historical default without raising."""
    return _RESOLUTION_PIXELS.get(resolution.upper(), 1024)


def _process_mtlx(
    mtlx_path: Path, resolution: str = "1K"
) -> tuple[dict, str | None, Path]:
    """Core pipeline: load → bake → extract → merge → properties.

    Returns ``(properties_dict, shader_model, tex_dir)`` where *tex_dir*
    is the directory containing baked texture files.

    ``resolution`` controls the baker's texture dimensions. Default ``"1K"``
    (1024×1024). Higher resolutions give sharper baked output at the cost
    of bake time and cache size (a 4K texture is 16× the memory of a 1K).
    """
    px = _resolution_to_pixels(resolution)
    base_dir = mtlx_path.parent
    tex_dir = base_dir / "textures"

    doc, search_path = load_document_with_stdlib(mtlx_path)
    _transcode_exr_inputs(doc, base_dir)
    orig_mats = extract_materials(doc)

    if not orig_mats:
        raise RuntimeError(f"No materials found in {mtlx_path}")

    if len(orig_mats) > 1:
        log.warning(
            "Document contains %d materials, using only the first ('%s')",
            len(orig_mats),
            orig_mats[0]["name"],
        )

    # Always bake: even materials without textures may have procedural
    # node graphs (e.g. GPUOpen "Brass") whose colors are only resolved
    # by the TextureBaker.
    baked_mtlx = base_dir / "material.baked.mtlx"
    try:
        bake_materials(
            doc, search_path, baked_mtlx, tex_dir, mtlx_dir=base_dir,
            width=px, height=px,
        )
        baked_doc, _ = load_document_with_stdlib(baked_mtlx)
        mats = extract_materials(baked_doc)
    except Exception as e:
        log.warning("Baking failed for %s: %s — using original doc", mtlx_path, e)
        mats = []

    if not mats:
        log.warning("Baking produced no materials for %s — falling back to original", mtlx_path.name)
        mats = orig_mats

    # MaterialX TextureBaker workaround: restore scalar IORs the baker
    # clobbers when they're wired through a constant-valued nodegraph.
    # See _recover_baker_clobbered_iors for the full context.
    if mats and mats is not orig_mats:
        _recover_baker_clobbered_iors(doc, mats)

    # Merge textures the baker missed from the original.
    # The baker sometimes collapses a texture to a single sampled
    # scalar (e.g. normal → [0.5, 0.5, 1.0], roughness → 0.3).
    # Only merge back an original texture if:
    # 1. The baker didn't produce a baked texture for this input, AND
    # 2. The baker also didn't produce a scalar value for it
    #    (a scalar means the baker intentionally resolved the
    #    procedural graph, e.g. channel extraction from a packed
    #    texture — merging back the raw packed texture would be wrong)
    if mats and orig_mats and mats is not orig_mats:
        baked_tex = mats[0].get("textures", {})
        baked_params = mats[0].get("params", {})
        orig_tex = orig_mats[0].get("textures", {})
        for inp_name, tex_info in orig_tex.items():
            if inp_name in baked_tex:
                continue
            if inp_name in baked_params:
                # Baker resolved this to a scalar — trust it
                continue
            src_file = tex_info.get("file")
            if not src_file:
                continue
            src_path = (base_dir / src_file).resolve()
            if src_path.exists():
                dst = _safe_copy(src_path, tex_dir)
                mats[0]["textures"][inp_name] = dict(
                    tex_info, file=dst.relative_to(base_dir).as_posix(),
                )
            else:
                for alt_ext in (".jpg", ".png", ".jpeg"):
                    alt_path = src_path.with_suffix(alt_ext)
                    if alt_path.exists():
                        dst = _safe_copy(alt_path, tex_dir)
                        mats[0]["textures"][inp_name] = dict(
                            tex_info, file=dst.relative_to(base_dir).as_posix(),
                        )
                        break

        # Merge displacement params the baker dropped (displacement lives
        # on the material node, not the surface shader, so the baker
        # never sees it).
        orig_params = orig_mats[0].get("params", {})
        if "displacement_scale" in orig_params and "displacement_scale" not in baked_params:
            mats[0]["params"]["displacement_scale"] = orig_params["displacement_scale"]

    mat = mats[0]
    properties = to_threejs_physical(mat, base_dir)

    if not properties:
        log.warning(
            "Conversion of '%s' produced empty properties — "
            "material may appear white or missing",
            mat.get("name", mtlx_path.name),
        )

    return properties, mat.get("shader_model"), base_dir
