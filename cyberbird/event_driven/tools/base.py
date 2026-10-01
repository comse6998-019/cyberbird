"""A tool: its arguments, its work, and what a model is shown.

Every tool call, whoever asked for it, goes through `Tool.__call__`. That is the
one place a failure becomes an observation instead of a crash, so a subclass
only writes `run` (and `render`, if its value is too large to show a model).
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel


@dataclass
class Observation:
    """What the runtime saw.

    `result` is a string, because that is what a model is shown. `value` is the
    tool's own return, for runtime callers; no model sees it.
    """
    tool: str
    args: dict = field(default_factory=dict)
    ok: bool = True
    result: str = ""
    value: Any = None 
    # Value is the tool call result as python object. This needn't be persisted 
    # outside of whoever made the tool call at that time. So we drop it from the 
    # dictionary representation to reduce state bloat.
    def as_dict(self) -> dict:
        return {
            "tool": self.tool,
            "args": self.args,
            "ok": self.ok,
            "result": self.result,
        }


class Tool(ABC):
    name: ClassVar[str]
    Args: ClassVar[type[BaseModel]]

    @abstractmethod
    def run(self, args: BaseModel) -> Any:
        """The work. Raise on failure; __call__ turns it into an observation."""

    def render(self, value: Any) -> str:
        """What a model is shown. Override to summarise."""
        return value if isinstance(value, str) else json.dumps(value)

    def __call__(self, raw: dict) -> Observation:
        try:
            # Bad arguments fail here, as an observation a model can correct.
            value = self.run(self.Args(**raw))
        except Exception as exc:
            return Observation(self.name, raw, ok=False,
                               result=f"{type(exc).__name__}: {exc}")
        return Observation(self.name, raw, result=self.render(value), value=value)

    @classmethod
    def schema(cls) -> dict:
        """For bind_tools: the same class that runs the tool describes it."""
        return {"type": "function", "function": {
            "name": cls.name, "description": cls.__doc__,
            "parameters": cls.Args.model_json_schema()}}

class WorkspaceTool(Tool):
    """A tool bound to one alert's workspace. It never touches anything outside it."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    def resolve(self, path: str) -> Path:
        """`path` inside the workspace, or PermissionError.

        Both sides are resolved first, so `..` and symlinks cannot step out.
        Comparing strings with startswith() would not be safe: "/tmp/ws-evil"
        starts with "/tmp/ws".
        """
        candidate = (self.root / path).resolve()
        if not candidate.is_relative_to(self.root):
            raise PermissionError(f"{path!r} is outside the workspace")
        return candidate
