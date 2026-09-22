"""The mechanical checks, and the diff against the pinned fixture.

Three checks, each reported separately with a reason when it fails:

    applies        the diff is non-empty and applies cleanly to the fixture
    touches_line   the edit changed the line the alert names
    rescan_clean   a fresh scan no longer reports that rule in that file

Note:
-----
We do not check that the endpoint still returns what it returned before. 
Nothing here observes behaviour: a patch that deletes the query entirely 
passes all three of the above. A clean rescan does not establish a correct 
fix — it establishes that one scanner no longer complains.
"""
from __future__ import annotations

import difflib
import json
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from lec2_reactive_agent.config import CONFIG, Config
from lec2_reactive_agent.workspace import AgentWorkspace


@dataclass
class Checks:
    applies: bool = False
    touches_line: bool = False
    rescan_clean: bool = False
    reasons: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """All three. The routing predicate reads this."""
        return self.applies and self.touches_line and self.rescan_clean

    def as_dict(self) -> dict:
        return asdict(self)


class Submission:
    """One candidate patch: the workspace as it now stands, against one alert."""

    def __init__(self, ws: Path, alert: dict, config: Config = CONFIG):
        self.ws = Path(ws)
        self.alert = alert
        self.config = config

    # the patch
    def changed_files(self) -> list[Path]:
        """Paths, relative to the fixture root, that differ from the pinned commit.

        Walks the WORKSPACE, not the fixture. Walking the fixture would report
        every withheld benchmark case as a deletion — the workspace deliberately
        holds one case out of 1,230, and a patch claiming to delete the other
        2,458 files is not a patch anyone can read.

        A file present here and absent from the fixture is a new file, and is
        included. A file absent here was withheld, never deleted: the agent has
        no tool that removes a file, so absence cannot be its doing. If a delete
        tool is ever added, this has to learn the difference.
        """
        changed = []
        for target in sorted(self.ws.rglob("*")):
            if not target.is_file():
                continue
            relative = target.relative_to(self.ws)
            source = self.config.fixture / relative
            if not source.exists() or source.read_bytes() != target.read_bytes():
                changed.append(relative)
        return changed

    def diff(self) -> str:
        """Unified diff of the workspace against the pinned fixture.

        `a/` and `b/` prefixes so the result applies with `patch -p1`.
        """
        chunks = []
        for relative in self.changed_files():
            before = self._lines(self.config.fixture / relative)
            after = self._lines(self.ws / relative)
            chunks.extend(difflib.unified_diff(
                before, after,
                fromfile=f"a/{relative}", tofile=f"b/{relative}", lineterm=""))
        return "\n".join(chunks) + ("\n" if chunks else "")

    def touched_lines(self, relative: str) -> set[int]:
        """1-based line numbers of the *original* file that this edit changed.

        Computed from opcodes rather than by reading the diff's hunk headers: a
        hunk carries three lines of context either side, so a hunk covering the
        alert's line does not mean the alert's line was edited. Opcodes say
        exactly which original lines were replaced or deleted.
        """
        before = self._lines(self.config.fixture / relative)
        after = self._lines(self.ws / relative)
        touched: set[int] = set()
        for tag, i1, i2, _j1, _j2 in difflib.SequenceMatcher(
                None, before, after, autojunk=False).get_opcodes():
            if tag in ("replace", "delete"):
                touched.update(range(i1 + 1, i2 + 1))
            elif tag == "insert":
                # An insertion sits between originals; credit both sides.
                touched.update({i1, i1 + 1})
        return touched

    # the three checks
    def check(self) -> Checks:
        result = Checks()
        patch_text = self.diff()

        if not patch_text.strip():
            result.reasons.append("no edit was made: the diff is empty")
            result.reasons.append("the flagged line was not touched")
            result.reasons.append("the alert is still reported")
            return result

        result.applies, why = self._applies(patch_text)
        if not result.applies:
            result.reasons.append(why)

        result.touches_line = self.alert["line"] in self.touched_lines(self.alert["file"])
        if not result.touches_line:
            edited = ", ".join(str(p) for p in self.changed_files()) or "nothing"
            result.reasons.append(
                f"the edit did not touch {self.alert['file']}:{self.alert['line']} "
                f"(changed: {edited})")

        result.rescan_clean, why = self._rescan()
        if not result.rescan_clean:
            result.reasons.append(why)
        return result

    def _applies(self, patch_text: str) -> tuple[bool, str]:
        """Apply the patch to a pristine fixture copy, without keeping it.

        We generated the diff, so this rarely fails — which is the point: when
        it does, something is genuinely malformed and worth hearing about.
        """
        # A pristine copy, nothing withheld: the patch must apply against the
        # fixture as pinned, not against the tree the agent was shown.
        with AgentWorkspace(self.config) as fresh:
            proc = subprocess.run(
                ["patch", "-p1", "--dry-run", "--force", "-d", str(fresh)],
                input=patch_text, capture_output=True, text=True, timeout=60)
        if proc.returncode == 0:
            return True, ""
        return False, ("the patch does not apply cleanly: "
                       f"{proc.stdout.strip() or proc.stderr.strip()}")

    def _rescan(self) -> tuple[bool, str]:
        """Re-run the alert's own scanner over the edited file.

        Scoped to the file, not the line. An edit that adds or removes lines
        shifts everything below it, so a line-pinned check would report clean
        merely because the weakness moved.
        """
        scanner, _, rule = self.alert["rule"].partition(":")
        target = self.ws / self.alert["file"]
        if not target.is_file():
            return False, f"{self.alert['file']} no longer exists"

        if scanner == "bandit":
            still = self._bandit_hits(target, rule)
        elif scanner == "semgrep":
            still = self._semgrep_hits(target, rule)
        else:
            return False, f"no rescanner for {scanner!r}"

        if still:
            lines = ", ".join(str(n) for n in sorted(still))
            return False, f"{self.alert['rule']} is still reported in {self.alert['file']} at line {lines}"
        return True, ""

    def _bandit_hits(self, target: Path, rule: str) -> set[int]:
        proc = subprocess.run(["bandit", str(target), "-f", "json", "-q"],
                              capture_output=True, text=True, timeout=self.config.scan_timeout)
        # bandit exits 1 when it finds something; only a crash has no JSON.
        try:
            report = json.loads(proc.stdout)
        except json.JSONDecodeError:
            raise RuntimeError(f"bandit produced no report: {proc.stderr.strip()}")
        return {r["line_number"] for r in report["results"] if r["test_id"] == rule}

    def _semgrep_hits(self, target: Path, rule: str) -> set[int]:
        configs = []
        for yml in sorted(self.config.rules_dir.glob("*.yml")):
            configs += ["--config", str(yml)]
        proc = subprocess.run(
            ["semgrep", "scan", *configs, "--json", "--metrics=off", "--quiet", str(target)],
            capture_output=True, text=True, timeout=self.config.scan_timeout)
        try:
            report = json.loads(proc.stdout)
        except json.JSONDecodeError:
            raise RuntimeError(f"semgrep produced no report: {proc.stderr.strip()}")
        return {r["start"]["line"] for r in report["results"]
                if r["check_id"].endswith(rule)}

    @staticmethod
    def _lines(path: Path) -> list[str]:
        if not path.exists():
            return []
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
