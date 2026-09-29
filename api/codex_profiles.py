"""Explicit Hermes CLI profiles, independent of the desktop/global Codex model."""
from __future__ import annotations


def profile(*, chief: bool = False) -> dict[str, str]:
    # Verified with the Hermes CLI ChatGPT login; GPT-6 Luna is rejected there.
    # Check app-server model/list AND a real CLI probe before changing this.
    return {"model": "gpt-6-astra" if chief else "gpt-5.6-luna",
            "reasoning_effort": "high" if chief else "medium"}


def cli_args(*, chief: bool = False) -> list[str]:
    selected = profile(chief=chief)
    return ["--model", selected["model"], "-c",
            'model_reasoning_effort="' + selected["reasoning_effort"] + '"']


def label(*, chief: bool = False) -> str:
    selected = profile(chief=chief)
    return selected["model"] + " / " + selected["reasoning_effort"]
