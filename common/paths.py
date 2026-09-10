from pathlib import Path


def project_root() -> Path:
    """Return repository root (parent of common/)."""
    return Path(__file__).resolve().parent.parent
