from __future__ import annotations

from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[4]
ARTIFACT_ROOT = ROOT_DIR / "artifacts" / "stage2"
DEFAULT_CDP_URL = "http://localhost:9222"
ONLINE_RECORD_URL = "https://www.zbsykj.com:19096/record/online"
DEFAULT_ENV_FILES = [
    ROOT_DIR / "demo" / ".env",
    ROOT_DIR / "demo" / "local_qwen.env",
]
