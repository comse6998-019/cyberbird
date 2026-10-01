"""The model's tools: read, search and edit, inside one alert's workspace."""
from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel

from cyberbird.event_driven.tools.base import WorkspaceTool

MAX_LINES = 400   # per read: protects the context window, not the disk
MAX_HITS = 50     # per search


class ReadFile(WorkspaceTool):
    """Read a workspace file with line numbers, optionally only lines start..end."""
    name = "read_file"

    class Args(BaseModel):
        path: str
        start: int | None = None
        end: int | None = None

    def run(self, args: Args) -> str:
        lines = self.resolve(args.path).read_text().splitlines()
        start = max(args.start or 1, 1)
        end = min(args.end or len(lines), len(lines), start + MAX_LINES - 1)
        return "\n".join(f"{n:>5}  {lines[n - 1]}" for n in range(start, end + 1))


class Search(WorkspaceTool):
    """Search workspace files for a regular expression; returns file:line: text."""
    name = "search"

    class Args(BaseModel):
        pattern: str
        glob: str = "**/*.py"

    def run(self, args: Args) -> list[dict]:
        # Refuse before walking: glob("../**") would traverse everything above
        # the workspace even if every match were filtered out afterwards.
        glob = Path(args.glob)
        if glob.is_absolute() or ".." in glob.parts:
            raise PermissionError(f"glob {args.glob!r} reaches outside the workspace")
        regex = re.compile(args.pattern)
        hits = []
        for path in sorted(self.root.glob(args.glob)):
            # A glob like "../**" would walk out of the workspace; skip anything outside.
            if not path.is_file() or not path.resolve().is_relative_to(self.root):
                continue
            for n, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
                if regex.search(line):
                    hits.append({"file": str(path.relative_to(self.root)),
                                 "line": n, "text": line.strip()})
                    if len(hits) == MAX_HITS:
                        return hits
        return hits

    def render(self, value: list[dict]) -> str:
        if not value:
            return "no matches"
        return "\n".join(f"{h['file']}:{h['line']}: {h['text']}" for h in value)


class Edit(WorkspaceTool):
    """Replace exactly one occurrence of `old` with `new` in a workspace file."""
    name = "edit"

    class Args(BaseModel):
        path: str
        old: str
        new: str

    def run(self, args: Args) -> dict:
        path = self.resolve(args.path)
        text = path.read_text()
        count = text.count(args.old)
        if count != 1:
            # Ambiguous or missing: refuse rather than guess which one was meant.
            raise ValueError(f"`old` must match exactly once; it matched {count} times")
        path.write_text(text.replace(args.old, args.new, 1))
        return {"path": args.path, "replaced": 1}

    def render(self, value: dict) -> str:
        return f"edited {value['path']}"
