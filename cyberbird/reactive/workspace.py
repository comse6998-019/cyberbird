"""Disposable fixture workspaces with benchmark answers temporarily withheld.

When case isolation is enabled, sibling cases and templates are moved aside to
prevent copying solutions; helpers remain available. Files are restored on exit
before the workspace is kept or removed.

All agent tools use `resolve` to restrict file access to the workspace root.
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
