"""Public API: load materials on demand with local JSON caching."""

import base64
import copy
import json
import logging
import warnings
from pathlib import Path

from pygltflib import GLTF2

from threejs_materials.convert import (
    _process_mtlx,
    extract_materials,
    load_document_with_stdlib,
)
from threejs_materials.gltf import (
    _from_gltf,
    to_gltf as _to_gltf,
    save_gltf as _save_gltf,
)
from threejs_materials.utils import (
    ensure_materialx,
    _abbreviate_textures,
    _is_data_uri,
    _resolve_to_data_uri,
    _linear_to_srgb,
    _average_texture_linear,
    _parse_color_string,
)

log = logging.getLogger(__name__)


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
        index: int | None = None,
    ) -> "dict[str, Material] | Material":
        """Import materials from a ``pygltflib.GLTF2`` object.

        When *index* is ``None`` (default), returns a dict mapping
        material names to Material objects.  When *index* is given,
        returns a single Material directly.

        Parameters
        ----------
        gltf : pygltflib.GLTF2
            A glTF 2.0 document loaded from disk or built
            programmatically.
        index : int, optional
            If given, return only the material at this index.
        """
        result = _from_gltf(gltf, index=index)
        if isinstance(result, dict) and not any(k in result for k in ("id", "name")):
            # dict of {name: data_dict} — wrap each in Material
            return {name: cls(data) for name, data in result.items()}
        return cls(result)

    @classmethod
    def load_gltf(
        cls, gltf_file: str, index: int | None = None
    ) -> "dict[str, Material] | Material":
        """Import materials from a ``.gltf`` or ``.glb`` file on disk.

        When *index* is ``None`` (default), returns a dict mapping
        material names to Material objects.  When *index* is given,
        returns a single Material directly.

        Parameters
        ----------
        gltf_file : str
            Path to a ``.gltf`` or ``.glb`` file.
        index : int, optional
            If given, return only the material at this index.
        """
        gltf_path = Path(gltf_file).resolve()
        if not gltf_path.exists():
            raise FileNotFoundError(f"File not found: {gltf_path}")
        return cls.from_gltf(GLTF2.load(str(gltf_path)), index=index)

    @classmethod
    def from_mtlx(cls, mtlx_file: str) -> "Material":
        """Convert a local .mtlx file to a Material.

        Texture paths in the .mtlx are resolved relative to the file's location.
        If the material references textures that don't exist on disk, a
        ``FileNotFoundError`` is raised.
        """
        ensure_materialx()
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
        return cls({
            "id": name,
            "name": name,
            "source": "local",
            "url": "",
            "license": "",
            "properties": properties,
            "_texture_dir": str(tex_dir),
        })

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
        return _to_gltf(self)

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
        _save_gltf(self, path, overwrite=overwrite)

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

    def interpolate_color(self) -> tuple[float, float, float, float]:
        """Estimate a representative sRGB color + alpha for CAD mode display.

        Returns an ``(r, g, b, a)`` tuple with each component in 0-1 (sRGB).
        When the material has a color texture, the texture is averaged.
        When the color is a scalar (linear RGB), it is converted to sRGB.

        Transmission is mapped to partial transparency so glass-like
        materials look semi-transparent in CAD mode.

        Usage::

            from threejs_materials import load_gpuopen
            wood = load_gpuopen("Ivory Walnut Solid Wood")
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
