from threejs_materials.convert import encode_texture_base64
from threejs_materials.gltf import collect_gltf_textures, inject_materials
from threejs_materials.library import Material
from threejs_materials.sources import (
    _load_ambientcg,
    _load_gpuopen,
    _load_physicallybased,
    _load_polyhaven,
    clear_cache,
    list_cache,
)

__all__ = [
    "Material",
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


def load_gpuopen(name: str, resolution: str = "1K") -> Material:
    return Material(_load_gpuopen(name, resolution))


def load_ambientcg(name: str, resolution: str = "1K") -> Material:
    return Material(_load_ambientcg(name, resolution))


def load_polyhaven(name: str, resolution: str = "1K") -> Material:
    return Material(_load_polyhaven(name, resolution))


def load_physicallybased(name: str, resolution: str = "1K") -> Material:
    return Material(_load_physicallybased(name, resolution))
