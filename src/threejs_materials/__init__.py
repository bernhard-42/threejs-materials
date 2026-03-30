from threejs_materials.library import Material, collect_gltf_textures
from threejs_materials.convert import encode_texture_base64

__all__ = [
    "Material",
    "encode_texture_base64",
    "collect_gltf_textures",
    "gpuopen_pbr",
    "ambientcg_pbr",
    "polyhaven_pbr",
    "physicallybased_pbr",
    "gpuopen_gltf",
    "ambientcg_gltf",
    "polyhaven_gltf",
    "physicallybased_gltf",
]


def _load_pbr(
    loader,
    name: str,
    color: tuple[float, float, float] = None,
    scale: tuple[float, float] = None,
) -> Material:
    material = loader.load(name)
    if color:
        material.override(color=color)
    if scale:
        material = material.scale(*scale)
    return material


def gpuopen_pbr(
    name: str,
    color: tuple[float, float, float] = None,
    scale: tuple[float, float] = None,
) -> Material:
    return _load_pbr(Material.gpuopen, name, color, scale)


def ambientcg_pbr(
    name: str,
    color: tuple[float, float, float] = None,
    scale: tuple[float, float] = None,
) -> Material:
    return _load_pbr(Material.ambientcg, name, color, scale)


def polyhaven_pbr(
    name: str,
    color: tuple[float, float, float] = None,
    scale: tuple[float, float] = None,
) -> Material:
    return _load_pbr(Material.polyhaven, name, color, scale)


def physicallybased_pbr(
    name: str,
    color: tuple[float, float, float] = None,
    scale: tuple[float, float] = None,
) -> Material:
    return _load_pbr(Material.physicallybased, name, color, scale)


def gpuopen_gltf(
    name: str,
    color: tuple[float, float, float] = None,
    scale: tuple[float, float] = None,
) -> Material:
    return _load_pbr(Material.gpuopen, name, color, scale).to_gltf()


def ambientcg_gltf(
    name: str,
    color: tuple[float, float, float] = None,
    scale: tuple[float, float] = None,
) -> Material:
    return _load_pbr(Material.ambientcg, name, color, scale).to_gltf()


def polyhaven_gltf(
    name: str,
    color: tuple[float, float, float] = None,
    scale: tuple[float, float] = None,
) -> Material:
    return _load_pbr(Material.polyhaven, name, color, scale).to_gltf()


def physicallybased_gltf(
    name: str,
    color: tuple[float, float, float] = None,
    scale: tuple[float, float] = None,
) -> Material:
    return _load_pbr(Material.physicallybased, name, color, scale).to_gltf()
