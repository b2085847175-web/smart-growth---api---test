from pathlib import Path


def project_root() -> Path:
    """Return repository root (parent of testcases/)."""
    return Path(__file__).resolve().parent.parent.parent
