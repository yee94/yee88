from importlib.metadata import version as _pkg_version

try:
    __version__ = _pkg_version("yee88")
except Exception:
    __version__ = "0.10.5"  # fallback when not installed as package
