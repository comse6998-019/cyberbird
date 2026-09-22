"""The provider seam.

The only module that names a model provider. Every node calls `build_model`;
nothing else knows whether the run is on Ollama, Anthropic or anything else, so
swapping provider is one config field.

`--model anthropic:claude-opus-5` or `--model openai:gpt-...` works unchanged.
"""
from __future__ import annotations

from langchain.chat_models import init_chat_model

from lec2_planning_and_validation_agent.config import CONFIG, Config


def build_model(config: Config = CONFIG):
    """A chat model bound to nothing yet.

    Temperature 0 and a fixed seed, so a run on the same model and machine
    replays identically. That reproducibility is the reason the seed is config
    rather than left to the provider's default.
    """
    extra = {}
    # `reasoning` is an Ollama parameter. Sending it to another provider is an
    # error, so it is only added when the provider is Ollama.
    if config.model.startswith("ollama:") and config.reasoning:
        extra["reasoning"] = True
    return init_chat_model(config.model,
                           temperature=config.temperature,
                           seed=config.seed,
                           **extra)
