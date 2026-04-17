from __future__ import annotations

from functools import lru_cache
from importlib.resources import files


@lru_cache(maxsize=None)
def load_prompt_asset(*parts: str) -> str:
    resource = files("jobflow_desktop_app.resources.prompts")
    for part in parts:
        resource = resource.joinpath(part)
    return resource.read_text(encoding="utf-8").strip()


__all__ = ["load_prompt_asset"]
