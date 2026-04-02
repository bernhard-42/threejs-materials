from threejs_materials.convert import encode_texture_base64
from threejs_materials.gltf import collect_gltf_textures, inject_materials
from threejs_materials.library import PbrProperties
from threejs_materials.models import PbrMaps, PbrValues
from threejs_materials.sources import (
    _load_ambientcg,
    _load_gpuopen,
    _load_physicallybased,
    _load_polyhaven,
    clear_cache,
    list_cache,
)

__all__ = [
    "PbrProperties",
    "PbrValues",
    "PbrMaps",
    "encode_texture_base64",
    "collect_gltf_textures",
    "inject_materials",
    "load_ambientcg",
    "load_gpuopen",
    "load_physicallybased",
    "load_polyhaven",
    "list_cache",
    "clear_cache",
]


def load_gpuopen(name: str, resolution: str = "1K") -> PbrProperties:
    return PbrProperties.from_dict(_load_gpuopen(name, resolution))


def load_ambientcg(name: str, resolution: str = "1K") -> PbrProperties:
    return PbrProperties.from_dict(_load_ambientcg(name, resolution))


def load_polyhaven(name: str, resolution: str = "1K") -> PbrProperties:
    return PbrProperties.from_dict(_load_polyhaven(name, resolution))


def load_physicallybased(name: str, resolution: str = "1K") -> PbrProperties:
    return PbrProperties.from_dict(_load_physicallybased(name, resolution))
