from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class ModelProfile:
    name: str
    env_file: Path
    base_url: str
    api_key: str
    model: str


def read_env_values(env_file: Path) -> dict[str, str]:
    load_dotenv(env_file, override=True)
    result: dict[str, str] = {}
    for line in env_file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def load_model_profiles(env_files: list[Path]) -> list[ModelProfile]:
    profiles: list[ModelProfile] = []
    for env_file in env_files:
        if not env_file.exists():
            continue
        values = read_env_values(env_file)
        model = values.get("LOCAL_LLM_MODEL", "").strip()
        if not model:
            continue
        profiles.append(
            ModelProfile(
                name=model,
                env_file=env_file,
                base_url=values.get("LOCAL_LLM_BASE_URL", "").strip(),
                api_key=values.get("LOCAL_LLM_API_KEY", "").strip(),
                model=model,
            )
        )
    return profiles
