"""One disposable copy of the fixture per alert, and the pool that owns them all.

The benchmark ships each weakness twice, a vulnerable case and a safe twin, so
the fix for any alert sits in a sibling file. A workspace copies only the
alert's own case: the agent has to reason instead of copying a neighbour.
"""
from __future__ import annotations

import shutil
import tempfile
from contextlib import ExitStack
from pathlib import Path

from cyberbird.event_driven.config import CONFIG, Config


class Workspace:
    """A fixture copy holding one benchmark case. Removed on exit unless kept."""

    def __init__(self, config: Config, case: str):
        self.config = config
        self.case = case
        self.keep = False
        self.root: Path | None = None

    def __enter__(self) -> Path:
        self.root = Path(tempfile.mkdtemp(prefix=f"event-driven-{self.case}-")).resolve()
        # dirs_exist_ok: mkdtemp has already created the target.
        shutil.copytree(self.config.fixture, self.root, dirs_exist_ok=True,
                        ignore=self._siblings)
        return self.root

    def _siblings(self, directory: str, names: list[str]) -> list[str]:
        """Every benchmark case but this one; copytree never copies them."""
        return [n for n in names
                if n.startswith("BenchmarkTest") and not n.startswith(f"{self.case}.")]

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        # Idempotent: the pool may close a workspace that gate already closed.
        if self.root is None:
            return
        if self.keep or exc_type is not None:
            print(f"workspace kept: {self.root}")
        else:
            shutil.rmtree(self.root, ignore_errors=True)
        self.root = None


class Workspaces:
    """Every alert's workspace for one run.

    gate opens and closes them one at a time. The pool's own `with` is the
    safety net: whatever the run leaves open, leaving the block closes it,
    and a run that crashed keeps its trees for inspection.
    """

    def __init__(self, config: Config = CONFIG):
        self.config = config
        self._stack = ExitStack()
        self._open: dict[str, Workspace] = {}

    def __enter__(self) -> "Workspaces":
        self._stack.__enter__()
        return self

    def __exit__(self, *exc) -> None:
        self._stack.__exit__(*exc)

    def open(self, alert: dict) -> Path:
        workspace = Workspace(self.config, case=Path(alert["file"]).stem)
        root = self._stack.enter_context(workspace)
        self._open[alert["alert_id"]] = workspace
        return root

    def close(self, alert_id: str, keep: bool) -> None:
        workspace = self._open.pop(alert_id)
        workspace.keep = keep
        workspace.__exit__(None, None, None)
