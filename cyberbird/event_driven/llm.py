"""The provider seam. The only module that names a provider or loads its key."""
from __future__ import annotations

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from pydantic import BaseModel

from cyberbird.event_driven.config import CONFIG, Config

# OPENROUTER_API_KEY lives in the repo-root .env; find_dotenv searches upward.
load_dotenv()


def build_model(config: Config = CONFIG):
    extra = {}
    if config.model.startswith("openrouter:") and config.reasoning:
        extra["reasoning"] = config.reasoning
    return init_chat_model(config.model, temperature=config.temperature,
                           seed=config.seed, max_tokens=config.max_tokens, **extra)


def json_schema_format(schema: type[BaseModel]) -> dict:
    """A `response_format` that makes the model reply with JSON matching `schema`.

    What `with_structured_output(method="json_schema")` binds, without the parser
    it adds: the reply stays a message, so it can be streamed like any other and
    parsed once it is complete. (Not function_calling: Qwen refuses a forced tool
    call while it is reasoning.)
    """
    return {"type": "json_schema",
            "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()}}


def is_rate_limited(exc: Exception) -> bool:
    """True for the provider's "slow down" (HTTP 429), worth retrying.

    The OpenRouter SDK retries 5xx itself but raises 429 at once.
    """
    from openrouter.errors import TooManyRequestsResponseError
    return isinstance(exc, TooManyRequestsResponseError)
