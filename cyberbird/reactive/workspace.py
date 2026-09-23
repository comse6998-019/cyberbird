"""A disposable copy of the fixture, with the benchmark's answers withheld.

    with AgentWorkspace(config, case="BenchmarkTest00283") as ws:
        ...

`AgentWorkspace` owns the lifetime. It copies the fixture on entry, moves the
answer files aside, and on exit puts them back before deciding whether to remove
the tree at all.

WHY THE ANSWERS ARE WITHHELD
OWASP Benchmark ships each weakness twice: a vulnerable case and a safe twin
implementing the same endpoint correctly. 758 of its 1,230 cases are safe ones.
So the correct patch for any alert sits in a sibling file, one grep away, and an
agent that finds it has pattern-matched a neighbour rather than reasoned about
the code. It looks like investigation in the trace and is not.

Measured on the worked alert: with siblings present the agent accepted in five
model calls; with them withheld it diagnosed the bug correctly in words and then
spent its whole budget hunting for an example to copy, and produced no patch.

WHY MOVED, NOT DELETED
Withholding is a loan, not a demolition. Files go to a sibling directory and
come back on exit, which means the tree a human opens after a failed run is
complete, the withheld set is inspectable rather than inferred, and nothing here
can destroy anything even if pointed at a tree that is not a copy.

`helpers/` is deliberately never withheld. Following a value into
`helpers/separate_request.py` is the genuine investigation the lecture wants,
and it is not where the answers live.

`resolve` is the agent's security boundary. Every tool calls it before touching
disk. It is module-level and public on purpose: a boundary every caller must
pass through should not look private.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from cyberbird.reactive.config import CONFIG, Config
from cyberbird.reactive.exceptions import PathEscapeException

# Patterns whose non-matching files are withheld from the agent.
WITHHELD_GLOBS = ("testcode/BenchmarkTest*.py", "templates/web/*/BenchmarkTest*.html")

STASH_SUFFIX = "-withheld"


def resolve(root: Path, path: str) -> Path:
    """Return `path` as an absolute path inside `root`, or raise PathEscapeException.

    Both sides are fully resolved before they are compared, which is what makes
    this safe: `..` segments collapse and symlinks are followed, so neither can
    step outside. Resolving `root` matters on macOS, where a temp directory is
    reached through the /var -> /private/var symlink and an unresolved root
    would never match.

    Comparing the resolved paths as strings with startswith() would look
    equivalent and would not be: "/tmp/ws-evil" starts with "/tmp/ws".
    """
    root = Path(root).resolve()
    # `root / path` yields `path` itself when `path` is absolute, so an absolute
    # argument lands outside the root and is refused below rather than honoured.
    candidate = (root / path).resolve()
    if not candidate.is_relative_to(root):
        raise PathEscapeException(f"{path!r} resolves outside the workspace root")
    return candidate


class AgentWorkspace:
    """One disposable fixture copy: created on entry, removed on exit.

    `keep` may be set while the block is open — `ReactiveAgent` sets it once the
    run's terminal status is known, so a failed run leaves its tree behind and a
    successful one does not.
    """

    def __init__(self, config: Config = CONFIG, case: str | None = None,
                 keep: bool = False):
        self.config = config
        self.case = case
        self.keep = keep
        self.root: Path | None = None
        self.stash: Path | None = None

    def __enter__(self) -> Path:
        if self.root is not None:
            raise RuntimeError("workspace is already open")

        source = self.config.fixture.resolve()
        if not source.is_dir():
            raise FileNotFoundError(f"fixture not found: {source}")

        dest = Path(tempfile.mkdtemp(prefix=self.config.workspace_prefix))
        try:
            # dirs_exist_ok: mkdtemp has already created the target directory.
            shutil.copytree(source, dest, dirs_exist_ok=True, symlinks=True)
        except BaseException:
            shutil.rmtree(dest, ignore_errors=True)
            raise

        self.root = dest.resolve()
        if self.case and self.config.isolate_case:
            self.stash = self._withhold()
        return self.root

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if self.root is None:
            return
        self._restore()
        if self.keep or exc_type is not None:
            print(f"workspace kept for inspection: {self.root}")
        else:
            shutil.rmtree(self.root, ignore_errors=True)
        self.root = None

    def withheld(self) -> list[Path]:
        """What is currently being withheld, for inspection or assertion."""
        if self.stash is None or not self.stash.is_dir():
            return []
        return sorted(p for p in self.stash.rglob("*") if p.is_file())

    def _withhold(self) -> Path:
        """Move every benchmark case but `self.case` out of the tree."""
        stash = self.root.parent / f"{self.root.name}{STASH_SUFFIX}"
        for pattern in WITHHELD_GLOBS:
            for path in sorted(self.root.glob(pattern)):
                if path.stem == self.case:
                    continue
                target = stash / path.relative_to(self.root)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), str(target))
        return stash

    def _restore(self) -> None:
        """Put the withheld files back, so a kept tree is complete."""
        if self.stash is None or not self.stash.is_dir():
            return
        for path in sorted(p for p in self.stash.rglob("*") if p.is_file()):
            target = self.root / path.relative_to(self.stash)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(target))
        shutil.rmtree(self.stash, ignore_errors=True)
        self.stash = None
