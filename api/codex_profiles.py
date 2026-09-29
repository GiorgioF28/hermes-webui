"""Explicit Hermes CLI profiles, independent of the desktop/global Codex model."""
from __future__ import annotations


def profile(*, chief: bool = False) -> dict[str, str]:
    # Verified with Hermes CLI 0.159.0 and ChatGPT login; 0.153.4 rejected Luna 6.
    # Check app-server model/list AND a real CLI probe before changing this.
    return {"model": "gpt-6-astra" if chief else "gpt-6-luna",
            "reasoning_effort": "high" if chief else "medium"}


def cli_args(*, chief: bool = False) -> list[str]:
    selected = profile(chief=chief)
    return ["--model", selected["model"], "-c",
            'model_reasoning_effort="' + selected["reasoning_effort"] + '"']


def label(*, chief: bool = False) -> str:
    selected = profile(chief=chief)
    return selected["model"] + " / " + selected["reasoning_effort"]
