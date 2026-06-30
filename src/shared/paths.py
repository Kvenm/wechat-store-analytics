from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_DB_PATH = DATA_DIR / "processed" / "wechat_store.sqlite"
DEFAULT_REPORTS_DIR = DATA_DIR / "reports"
DEFAULT_STANDARD_DIR = DATA_DIR / "processed" / "standard_tables"


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path
