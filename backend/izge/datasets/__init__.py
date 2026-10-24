"""Local dataset preparation. No upload or training side effects."""
from .manifest import check_leakage, load_manifest

__all__ = ["check_leakage", "load_manifest"]
