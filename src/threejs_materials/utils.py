"""Shared utilities for lazy dependency loading."""

_MATERIALX_INSTALL_MSG = """\
materialx and openexr are not installed. Use either:
- "uv add --extra materialx threejs-materials"
- "uv pip install threejs-materials[materialx]"
- "pip install threejs-materials[materialx]"
or install them directly:
- "uv add materialx openexr"
- "uv pip install materialx openexr"
- "pip install materialx openexr"
Note: For the latest Python, the installer tries to compile these packages. \
This might not be possible under Windows if no compiler is installed."""


def ensure_materialx():
    """Import and return the MaterialX module, raising ImportError with install instructions."""
    try:
        import MaterialX
    except ImportError as e:
        raise ImportError(_MATERIALX_INSTALL_MSG) from e
    return MaterialX
