"""Request -> validate -> execute -> observation.

This is where execution authority lives. The model *proposes* a tool call; the
runtime executes it, or refuses. Every refusal in the system happens here, in
one place, identically for all five versions.

It is also what owns the observation channel. A model can emit a thought, an
action, and a fabricated observation in one response without ever touching the
environment (EnIGMA calls this "soliloquizing"). Because only the dispatcher
writes observations, an agent reporting a file it never opened is structurally
impossible rather than something the prompt has to prevent.

Not a context manager, deliberately: a `with` block manages a resource lifetime,
and the dispatcher owns no resource. The workspace belongs to the run's state
and the trace closes itself. A class here bundles collaborators, nothing more.
"""
from __future__ import annotations

import inspect
import types as pytypes
import typing
from dataclasses import asdict, dataclass, field
from pathlib import Path

from cyberbird.reactive.exceptions import PathEscapeException, ToolFault
from cyberbird.reactive.config import CONFIG, Config
from cyberbird.reactive.tools import TOOLS
from cyberbird.reactive.trace import Trace


def _concrete_types(hint) -> set:
    """The concrete types a hint permits, flattening unions."""
    if hint is None:
        return set()
    if typing.get_origin(hint) in (typing.Union, pytypes.UnionType):
        return {t for arg in typing.get_args(hint) for t in _concrete_types(arg)}
    return {hint} if isinstance(hint, type) else set()


def _coerce(function, args: dict) -> dict:
    """Convert string arguments to the type the tool declares, where that is safe.

    Only strings are touched, and only where the parameter permits a number or
    a boolean but not a string, so nothing ambiguous is converted. A value that
    will not convert is passed through unchanged and refused downstream with a
    message the model can act on.
    """
    try:
        hints = typing.get_type_hints(function)
    except Exception:
        return args

    coerced = {}
    for name, value in args.items():
        permitted = _concrete_types(hints.get(name))
        if isinstance(value, str) and str not in permitted:
            if int in permitted:
                try:
                    value = int(value.strip())
                except ValueError:
                    pass
            elif bool in permitted:
                if value.strip().lower() in ("true", "false"):
                    value = value.strip().lower() == "true"
        coerced[name] = value
    return coerced


def registry_from_enum(tools=TOOLS) -> dict:
    """Unwrap the TOOLS enum into {name: function}.

    The enum's `__call__` forwards to `self.value[0]`, so
    `inspect.signature(TOOLS.read_file)` reports `(*args, **kwargs)` rather than
    the tool's real parameters. Binding arguments against that accepts anything
    and defers the failure into the tool as a raw TypeError — validation that
    looks present and catches nothing. Unwrapping once here keeps the enum as
    the declared set of tools while giving the dispatcher real functions to
    introspect.
    """
    return {member.name: member.value[0] for member in tools}


@dataclass
class Observation:
    """What the runtime saw. The only thing a model is allowed to learn from.

    `result` is always a string: it goes back to the model as a tool message.
    """

    tool: str
    args: dict = field(default_factory=dict)
    ok: bool = True
    result: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


class Dispatcher:
    """Executes one tool request against one workspace, recording both events.

    Callable, so a graph node reads as `observation = dispatch(request)`.
    """

    def __init__(self, ws: Path, trace: Trace, registry: dict | None = None,
                 config: Config = CONFIG):
        self.ws = Path(ws)
        self.trace = trace
        self.config = config
        self.registry = registry_from_enum() if registry is None else registry

    def __call__(self, request: dict) -> Observation:
        return self.dispatch(request)

    def dispatch(self, request: dict) -> Observation:
        """Validate, execute, observe. Never raises for a bad request.

        A refusal is an observation, not an exception. An exception ends the
        run; an observation lets the model read what went wrong and choose
        again — which is precisely what V1's loop is for. When `edit` refuses an
        ambiguous target, that message goes back to the model and it retries
        with more context. Raise instead and V1 dies on its first sloppy edit.

        Exceptions remain for the genuinely unrecoverable, such as an unwritable
        trace. Not for the model being wrong.
        """
        name = request.get("name", "")
        args = dict(request.get("args") or {})
        self.trace.event("tool_request", tool=name, args=args)

        # validate
        # This is the seam that becomes an admission chain in Week 11:
        # permissions and budget would join the checks below as ordered stages.
        function = self.registry.get(name)
        if function is None:
            return self._refuse(name, args,
                                f"unregistered tool {name!r}; "
                                f"available: {', '.join(sorted(self.registry))}")

        # Bind the arguments before calling, so a wrong argument *name* becomes
        # a readable refusal instead of an opaque TypeError from inside a tool.
        try:
            inspect.signature(function).bind(self.ws, **args)
        except TypeError as exc:
            return self._refuse(name, args, f"bad arguments for {name}: {exc}")

        # Binding checks names and arity, not types. Models routinely send
        # "40" where an int is wanted, so coerce what is unambiguous rather
        # than spending a model call to say so.
        args = _coerce(function, args)

        # execute
        try:
            value = function(self.ws, **args)
        except PathEscapeException as exc:
            return self._refuse(name, args, f"refused: {exc}")
        except (ToolFault, ValueError) as exc:
            return self._refuse(name, args, str(exc))
        except TypeError as exc:
            # A wrong argument *type* that coercion could not fix. A refusal
            # the model can read, never a crash that ends the run.
            return self._refuse(name, args, f"bad argument type for {name}: {exc}")

        return self._observe(name, args, self._render(name, value))

    # helpers
    def _render(self, tool: str, value) -> str:
        """Tool return value -> the string the model reads."""
        if tool == "search":
            if not value:
                return "no matches"
            lines = [f"{h['file']}:{h['line']}: {h['text']}" for h in value]
            if len(value) >= self.config.search_max_hits:
                lines.append(f"... stopped at {self.config.search_max_hits} matches; "
                             "narrow the pattern to see the rest")
            return "\n".join(lines)
        if isinstance(value, dict):
            return ", ".join(f"{k}={v}" for k, v in value.items())
        return str(value)

    def _observe(self, tool: str, args: dict, result: str) -> Observation:
        obs = Observation(tool=tool, args=args, ok=True, result=result)
        self.trace.event("tool_result", tool=tool, ok=True, chars=len(result))
        return obs

    def _refuse(self, tool: str, args: dict, reason: str) -> Observation:
        obs = Observation(tool=tool, args=args, ok=False, result=reason)
        self.trace.event("tool_result", tool=tool, ok=False, reason=reason)
        return obs
