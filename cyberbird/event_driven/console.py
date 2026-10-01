"""Live commentary on a run, for showing the event flow to a room.

A run is otherwise silent while the models think, which hides the one thing
worth watching: events moving between steps.

The colour language matches the other agents deliberately:

    CYAN    a model decided something
    YELLOW  a model's reasoning, streamed as it thinks; and the planner's evidence
    WHITE   the runtime did something
    GREEN   it worked
    RED     it was refused or it failed

So "who decided this?" is readable from the colour alone.

The console is a listener, not a participant. `Trace` calls it after a line is
already on disk, so nothing printed here can change what a run recorded. And it
reads the same records the trace file holds: what you watch live is what you can
read back afterwards.
"""
from __future__ import annotations

import sys
import time

from colorama import Fore, Style
from colorama import init as colorama_init

colorama_init(autoreset=True)

MODEL = Fore.CYAN + Style.BRIGHT      # a model decided
NARRATIVE = Fore.YELLOW               # a model's reasoning, and the planner's evidence
RUNTIME = Fore.WHITE                  # the runtime acted
GOOD = Fore.GREEN
BAD = Fore.RED + Style.BRIGHT
DIM = Style.DIM
BOLD = Style.BRIGHT

# Steps that spend a model call, and the role each one plays.
THINKERS = {"plan": "planner", "controller": "controller", "validate": "validator"}


def _short(value, width: int = 62) -> str:
    text = str(value).replace("\n", "\\n")
    return text if len(text) <= width else text[:width - 1] + "…"


def _args(args: dict, width: int = 58) -> str:
    return _short(", ".join(f"{k}={_short(v, 30)!r}" for k, v in args.items()), width)


class Console:
    """Prints trace records as they happen, and a report at the end."""

    def __init__(self, enabled: bool = True, stream=sys.stdout):
        self.enabled = enabled
        self.stream = stream
        self.started = time.time()
        self._found = 0
        # Per alert, for the report: model calls and tokens, from model_call records.
        self.calls: dict[str, int] = {}
        self.tokens: dict[str, dict] = {}
        # (alert_id, role) of the reasoning being streamed on the open line, if any.
        self._thinking: tuple | None = None

    def _close_thinking(self) -> None:
        """End a streamed reasoning line before anything else is printed."""
        if self._thinking is not None:
            print(Style.RESET_ALL, file=self.stream, flush=True)
            self._thinking = None

    def _think(self, record: dict) -> None:
        """One piece of a model's reasoning, printed where the last one stopped.

        A different speaker (another role, or another alert) starts a new line,
        so two streams never run together into one.
        """
        key = (record.get("alert_id"), record.get("role"))
        if self._thinking != key:
            self._close_thinking()
            elapsed = f"{time.time() - self.started:5.1f}s"
            print(f"  {DIM}{elapsed}{Style.RESET_ALL} {NARRATIVE}{'THINK':<9}{Style.RESET_ALL}",
                  end="", file=self.stream)
            self._thinking = key
        # Continuation lines line up under the first, past the time and the tag.
        text = str(record.get("text", "")).replace("\n", "\n" + " " * 18)
        print(f"{NARRATIVE}{text}{Style.RESET_ALL}", end="", file=self.stream, flush=True)

    def _line(self, colour: str, tag: str, body: str, note: str = "") -> None:
        if not self.enabled:
            return
        self._close_thinking()
        elapsed = f"{time.time() - self.started:5.1f}s"
        trailing = f"  {DIM}{note}{Style.RESET_ALL}" if note else ""
        print(f"  {DIM}{elapsed}{Style.RESET_ALL} {colour}{tag:<9}{Style.RESET_ALL}"
              f"{colour}{body}{Style.RESET_ALL}{trailing}", file=self.stream, flush=True)

    def header(self, run_id: str, model: str, budget: int, validators: int) -> None:
        if not self.enabled:
            return
        print(f"\n{BOLD}{run_id}{Style.RESET_ALL}", file=self.stream)
        print(f"{DIM}model {model} · budget {budget} calls per alert · "
              f"{validators} validator(s){Style.RESET_ALL}", file=self.stream)
        print(f"{MODEL}cyan = a model decided{Style.RESET_ALL}   "
              f"{NARRATIVE}yellow = reasoning{Style.RESET_ALL}   "
              f"{DIM}white = the runtime acted{Style.RESET_ALL}\n", file=self.stream)

    def event(self, record: dict) -> None:
        """Called by Trace once a line is written. Never raises."""
        # Counted even when quiet: the report needs them.
        if record["kind"] == "model_call":
            alert = record.get("alert_id")
            usage = record.get("usage") or {}
            self.calls[alert] = self.calls.get(alert, 0) + 1
            total = self.tokens.setdefault(alert, {"input": 0, "output": 0})
            for key in total:
                total[key] += usage.get(key, 0)
        if not self.enabled:
            return
        try:
            self._render(record)
        except Exception:  # a broken console must never break a run
            pass

    def _render(self, r: dict) -> None:
        kind = r["kind"]

        if kind == "thinking":
            self._think(r)
            return

        if kind == "step":
            # A model step starting is a model starting to think: say so before
            # the wait, not after it.
            role = THINKERS.get(r["name"])
            if role and r["state"] == "started":
                where = f"worker {r['worker']}" if role == "validator" else ""
                self._line(MODEL, "MODEL →", f"{role} thinking…", where)
            return

        if kind == "model_call":
            usage = r.get("usage") or {}
            role = r.get("role")
            asked = (", ".join(r.get("tool_calls") or []) or "no tool call: submit"
                     if role == "controller" else "decided")
            self._line(MODEL, "MODEL", f"{role} → {asked}",
                       f"in={usage.get('input', 0)} out={usage.get('output', 0)}")
            return

        if kind == "terminal":
            ok = r["status"] == "completed"
            self._line(GOOD + BOLD if ok else BAD, "END", str(r["status"]).upper(),
                       "" if ok else _short(r.get("message", ""), 70))
            return

        t, d = r.get("type"), r.get("data") or {}

        if t == "ScanRequested":
            self._line(RUNTIME, "SCAN", "bandit over the fixture…")
        elif t == "AlertFound":
            self._found += 1
        elif t == "AlertStarted":
            if self._found:
                self._line(RUNTIME, "QUEUE", f"{self._found} alert(s) found",
                           "the gate releases one at a time")
                self._found = 0
            a = d["alert"]
            print(file=self.stream)  # a blank line between alerts
            self._line(RUNTIME + BOLD, "ALERT", f"{a['alert_id']}  {a['rule']} at {a['file']}:{a['line']}",
                       "workspace opened")
            self._line(DIM, "", _short(a.get("message", ""), 90))
        elif t == "PlanCreated":
            steps, revised = d.get("steps") or [], d.get("revised")
            self._line(MODEL, "PLAN ↻" if revised else "PLAN",
                       f"{len(steps)} steps" + (" (revised)" if revised else ""))
            for i, s in enumerate(steps, 1):
                self._line(MODEL, "", f"  {i}. {s['goal']}")
                self._line(NARRATIVE, "", f"     done when: {s['evidence']}")
        elif t == "ToolsRequested":
            for call in d.get("calls") or []:
                self._line(RUNTIME, "TOOL →", f"{call['name']}({_args(call.get('args') or {})})")
        elif t == "Observed":
            for o in d.get("observations") or []:
                if o.get("ok"):
                    self._line(GOOD, "TOOL ←", f"{o['tool']} ok", f"{len(o.get('result', ''))} chars")
                else:
                    self._line(BAD, "TOOL ←", f"{o['tool']} refused", _short(o.get("result", ""), 70))
        elif t == "Stalled":
            self._line(RUNTIME, "ROUTE", "→ plan", _short(d.get("reason", ""), 70))
        elif t == "Submitted":
            self._line(MODEL, "ROUTE", "→ submit", "decided by model")
        elif t == "ChecksFailed":
            self._line(BAD, "CHECK", "failed → back to the controller")
            for reason in d.get("reasons") or []:
                self._line(BAD, "", f"  - {reason}")
        elif t == "ValidateRequested":
            self._line(RUNTIME, "FAN-OUT", f"→ validator: {d.get('lens')}")
        elif t == "Verdict":
            ok = d.get("accepted")
            self._line(GOOD if ok else BAD, "VERDICT",
                       f"{d.get('lens')}: {'accepted' if ok else 'rejected'}",
                       f"decided by {d.get('decided_by')}")
            for reason in d.get("reasons") or []:
                self._line(GOOD if ok else BAD, "", f"  - {reason}")
        elif t == "AlertDone":
            ok = d.get("status") == "accepted"
            self._line(GOOD + BOLD if ok else BAD, "DONE",
                       f"{r['alert_id']} {str(d.get('status')).upper()}",
                       _short(d.get("reason", ""), 70))

    def report(self, results: dict | None, budget: int, trace_path, diagram_paths) -> None:
        """The end-of-run summary.

        Not gated on `enabled`: --quiet suppresses the live commentary, not the
        result.
        """
        self._close_thinking()
        print(file=self.stream)
        for alert, status in (results or {}).items():
            colour = GOOD + BOLD if status == "accepted" else BAD
            usage = self.tokens.get(alert, {"input": 0, "output": 0})
            print(f"{colour}{alert} : {status}{Style.RESET_ALL}   "
                  f"{DIM}model calls {self.calls.get(alert, 0)} of {budget} · "
                  f"tokens in={usage['input']} out={usage['output']}{Style.RESET_ALL}",
                  file=self.stream)
        if not results:
            print(f"{BAD}no alert finished{Style.RESET_ALL}", file=self.stream)
        print(f"trace   : {trace_path}", file=self.stream)
        for path in diagram_paths:
            print(f"diagram : {path}", file=self.stream)
