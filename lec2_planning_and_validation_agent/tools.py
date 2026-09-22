"""The three tools the model may request, and the scripted fault hook.

Each tool takes the workspace root first and resolves every path through
`lec2_planning_and_validation_agent.workspace.resolve` before touching disk. That call is not optional: it is
the boundary that keeps a run inside its own copy of the fixture.

Tools raise on failure. Turning a failure into an observation the model can read
is the dispatcher's job (Task 4), because only the dispatcher knows how to
record it in the trace.
"""
from __future__ import annotations

import re
from enum import Enum
from pathlib import Path

from lec2_planning_and_validation_agent.config import CONFIG
from lec2_planning_and_validation_agent.exceptions import ToolFault
from lec2_planning_and_validation_agent.workspace import resolve

# Tools read CONFIG directly rather than taking it as a parameter. A tool's
# signature is its model-facing contract: the dispatcher validates the model's
# arguments by binding against it, so every parameter here is something the
# model is allowed to supply. A `config` parameter would become a bindable
# argument the model could pass, which it must never be able to do.


def read_file(ws: Path, path: str, start: int | None = None,
              end: int | None = None) -> str:
    """Return the file's text, or lines `start`..`end` inclusive (1-based).

    Returns plain text with no line numbers. That is deliberate: `edit` matches
    on an exact string, and a model that copied a numbered line back into an
    edit would never match. Line numbers reach the model through the alert and
    through `search` instead.
    """
    target = resolve(ws, path)
    if not target.is_file():
        raise ToolFault(f"no such file: {path}")

    text = target.read_text(encoding="utf-8", errors="replace")

    if start is None and end is None:
        if len(text) > CONFIG.read_max_bytes:
            return text[:CONFIG.read_max_bytes] + f"\n... truncated at {CONFIG.read_max_bytes} bytes"
        return text

    lines = text.splitlines()
    first = max(1, start or 1)
    last = min(len(lines), end or len(lines))
    if first > len(lines):
        raise ToolFault(f"{path} has {len(lines)} lines; line {first} was requested")
    return "\n".join(lines[first - 1:last])


def search(ws: Path, pattern: str, glob: str | None = None) -> list[dict]:
    """Every line matching `pattern`, as {file, line, text}.

    Capped at CONFIG.search_max_hits. A list of exactly that length may have been
    truncated; the dispatcher says so in the observation.
    """
    try:
        rx = re.compile(pattern)
    except re.error as exc:
        raise ToolFault(f"bad pattern {pattern!r}: {exc}") from exc

    root = Path(ws).resolve()
    hits: list[dict] = []
    for candidate in sorted(root.glob(glob or CONFIG.default_glob)):
        if not candidate.is_file():
            continue
        try:
            body = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for number, line in enumerate(body.splitlines(), start=1):
            if rx.search(line):
                hits.append({"file": str(candidate.relative_to(root)),
                             "line": number,
                             "text": line.strip()})
                if len(hits) >= CONFIG.search_max_hits:
                    return hits
    return hits


def edit(ws: Path, path: str, old: str, new: str) -> dict:
    """Replace one exact occurrence of `old` with `new`.

    Refuses both a missing target and an ambiguous one. A silent multi-replace
    produces a patch nobody can explain, and an edit the model cannot predict is
    worse than an edit that fails loudly.
    """
    target = resolve(ws, path)
    if not target.is_file():
        raise ToolFault(f"no such file: {path}")

    body = target.read_text(encoding="utf-8")
    occurrences = body.count(old)
    if occurrences == 0:
        raise ValueError(f"{old!r} does not appear in {path}")
    if occurrences > 1:
        raise ValueError(
            f"{old!r} appears {occurrences} times in {path}; "
            "include surrounding context to make it unique")

    target.write_text(body.replace(old, new, 1), encoding="utf-8")
    return {"path": path, "replaced": 1}


# The dispatcher's registry imports this. A tool absent here cannot be called,
# which is what makes an unregistered-tool request a refusal rather than a crash.
class TOOLS(Enum):
    # Wrap functions so Enum treats them as members, not methods.
    read_file = (read_file,)
    search = (search,)
    edit = (edit,)

    def __call__(self, *args, **kwargs):
        return self.value[0](*args, **kwargs)


# What the model is shown.
#
# These carry the names, arguments and descriptions the model binds against.
# Their bodies never run: the dispatcher executes the real functions above, so
# that every call passes through validation and lands in the trace. Declared
# here, beside the implementations, so a description cannot drift from what the
# tool actually does.
from langchain_core.tools import tool  # noqa: E402


def _dispatcher_executes(name: str):
    raise RuntimeError(
        f"{name} was called directly; tool execution belongs to the dispatcher")


@tool("read_file")
def read_file_schema(path: str, start: int | None = None, end: int | None = None) -> str:
    """Read a source file from the repository.

    Give `start` and `end` to read a range of lines (1-based, inclusive);
    omit both to read the whole file. Paths are relative to the repository
    root, e.g. "testcode/BenchmarkTest00283.py".
    """
    _dispatcher_executes("read_file")


@tool("search")
def search_schema(pattern: str, glob: str | None = None) -> str:
    """Search the repository for a Python regular expression.

    Returns matching lines as "file:line: text". Use this to follow a value
    into a helper the alert does not name. `glob` narrows the files searched
    and defaults to every .py file.
    """
    _dispatcher_executes("search")


@tool("edit")
def edit_schema(path: str, old: str, new: str) -> str:
    """Replace an exact string in a file.

    `old` must appear exactly once in the file, so include enough surrounding
    text to make it unique. Whitespace must match the file exactly.
    """
    _dispatcher_executes("edit")


# Structured replies, not actions.
#
# `propose_plan` and `decide` are shaped like tools because tool calling is how
# a model returns a structured value reliably, but nothing executes them. The
# node reads the arguments off the reply and throws the call away.
#
# They are deliberately absent from TOOLS. The dispatcher's registry is what a
# model is permitted to *do*, and neither of these does anything — a `decide`
# reaching the dispatcher would be an unregistered tool, which is a refusal. It
# also keeps the roles apart: the controller is bound to the three real tools
# and cannot decide its own acceptance, and the validator is bound to `decide`
# and cannot edit the tree it is judging.
def _structured_reply(name: str):
    raise RuntimeError(
        f"{name} carries a structured reply; the node reads its arguments "
        "and never executes it")


@tool("propose_plan")
def propose_plan_schema(steps: list[str]) -> str:
    """Record the investigation plan.

    Each step is one string formatted "goal :: evidence": what to do, then the
    observation that would show it is done. Three to five steps.
    """
    _structured_reply("propose_plan")


@tool("decide")
def decide_schema(accepted: bool, reasons: list[str]) -> str:
    """Record the acceptance decision for a candidate patch.

    `accepted` releases the patch; false refuses it. `reasons` gives the
    specific grounds, one per entry, in both cases.
    """
    _structured_reply("decide")


class ToolBundle:
    """A collection of all available tools."""

    READ_FILE = read_file_schema
    SEARCH = search_schema
    EDIT = edit_schema
    PROPOSE_PLAN = propose_plan_schema
    DECIDE = decide_schema

    @classmethod
    def get_all_tools(cls) -> list:
        """What the controller may call. The three that touch the repository."""
        return [cls.READ_FILE, cls.SEARCH, cls.EDIT]

    @classmethod
    def planner_tools(cls) -> list:
        return [cls.PROPOSE_PLAN]

    @classmethod
    def validator_tools(cls) -> list:
        return [cls.DECIDE]
