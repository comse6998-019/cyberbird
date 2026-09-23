"""Live commentary on a run, for showing the loop to a room.

A run is otherwise silent for a minute or more while the model thinks, which
hides the one thing worth watching: the loop taking turns.

The colour language matches the lecture's graph figures deliberately.

    BLUE    a model decided something
    YELLOW  the model's own reasoning, streamed as it thinks
    WHITE   the runtime did something
    GREEN   it worked
    RED     it was refused or it failed

So "who decided this?" — the question every version of the agent is built to
answer — is readable from the colour alone, without reading the words.

The console is a listener, not a participant. `Trace` calls it after an event is
already on disk, so nothing printed here can change what a run recorded.
"""
from __future__ import annotations

import sys
import time

from colorama import Fore, Style
from colorama import init as colorama_init

colorama_init(autoreset=True)

MODEL = Fore.CYAN + Style.BRIGHT      # a model decided
NARRATIVE = Fore.YELLOW               # the model's reasoning, as it arrives
RUNTIME = Fore.WHITE                  # the runtime acted
GOOD = Fore.GREEN
BAD = Fore.RED + Style.BRIGHT
DIM = Style.DIM
BOLD = Style.BRIGHT


def _short(value, width: int = 62) -> str:
    text = str(value).replace("\n", "\\n")
    return text if len(text) <= width else text[:width - 1] + "…"


def _args(args: dict, width: int = 58) -> str:
    rendered = ", ".join(f"{k}={_short(v, 30)!r}" for k, v in args.items())
    return _short(rendered, width)


class Console:
    """Prints trace events as they happen."""

    def __init__(self, enabled: bool = True, stream=sys.stdout):
        self.enabled = enabled
        self.stream = stream
        self.started = time.time()

    def _line(self, colour: str, tag: str, body: str, note: str = "") -> None:
        if not self.enabled:
            return
        elapsed = f"{time.time() - self.started:5.1f}s"
        trailing = f"  {DIM}{note}{Style.RESET_ALL}" if note else ""
        print(f"  {DIM}{elapsed}{Style.RESET_ALL} {colour}{tag:<9}{Style.RESET_ALL}"
              f"{colour}{body}{Style.RESET_ALL}{trailing}", file=self.stream, flush=True)

    def header(self, run_id: str, alert: dict, model: str, budget: int) -> None:
        if not self.enabled:
            return
        print(f"\n{BOLD}{run_id}{Style.RESET_ALL}  "
              f"{alert['rule']} at {alert['file']}:{alert['line']}", file=self.stream)
        print(f"{DIM}{alert['message']}{Style.RESET_ALL}", file=self.stream)
        print(f"{DIM}model {model} · budget {budget} calls{Style.RESET_ALL}", file=self.stream)
        print(f"{MODEL}blue = decided{Style.RESET_ALL}   "
              f"{NARRATIVE}yellow = reasoning{Style.RESET_ALL}   "
              f"{DIM}white = the runtime acted{Style.RESET_ALL}\n", file=self.stream)

    def event(self, record: dict) -> None:
        """Called by Trace once the event is written. Never raises."""
        if not self.enabled:
            return
        try:
            self._render(record)
        except Exception:  # a broken console must never break a run
            pass

    def _render(self, e: dict) -> None:
        kind = e["kind"]

        if kind == "model_call":
            asked = ", ".join(e.get("tool_calls") or []) or "no tool call — finished"
            usage = e.get("usage") or {}
            self._line(MODEL, "MODEL", f"controller → {asked}",
                       f"in={usage.get('input', 0)} out={usage.get('output', 0)}")

        elif kind == "tool_request":
            self._line(RUNTIME, "TOOL →", f"{e['tool']}({_args(e.get('args', {}))})")

        elif kind == "tool_result":
            if e.get("ok"):
                self._line(GOOD, "TOOL ←", f"{e['tool']} ok", f"{e.get('chars', 0)} chars")
            else:
                self._line(BAD, "TOOL ←", f"{e['tool']} refused",
                           _short(e.get("reason", ""), 70))

        elif kind == "routing":
            by = e.get("by", "")
            colour = MODEL if by == "model" else RUNTIME
            self._line(colour, "ROUTE", f"→ {e['decision']}", f"decided by {by}")

        elif kind == "state_change":
            checks = e.get("checks")
            if checks:
                marks = " ".join(
                    f"{GOOD if checks[name] else BAD}{name}={checks[name]}{Style.RESET_ALL}"
                    for name in ("applies", "touches_line", "rescan_clean"))
                self._line(RUNTIME, "CHECK", marks)
            else:
                self._line(DIM, "STATE", _short(e.get("node") or e, 70))

        elif kind == "terminal":
            colour = GOOD + Style.BRIGHT if e["status"] == "accepted" else BAD
            self._line(colour, "END", e["status"].upper())

    def calling(self, message_count: int) -> None:
        """A model call has been issued. Printed before the wait, not after."""
        self._line(MODEL, "MODEL →", f"thinking over {message_count} messages…")
        self._thinking_open = False

    def thinking(self, text: str) -> None:
        """The model's own reasoning, streamed as it arrives."""
        if not self.enabled:
            return
        if not getattr(self, "_thinking_open", False):
            print("            ", end="", file=self.stream, flush=True)
            self._thinking_open = True
        print(f"{NARRATIVE}{text}{Style.RESET_ALL}", end="", file=self.stream, flush=True)

    def thinking_done(self) -> None:
        if self.enabled and getattr(self, "_thinking_open", False):
            print(Style.RESET_ALL, file=self.stream, flush=True)
            self._thinking_open = False

    def trajectory(self, events: list[dict], style: str = "mermaid",
                   saved=None) -> None:
        """The run as a sequence diagram, printed when the trace closes.

        The graph shows what the agent could do; this shows what it did. Mermaid
        renders in the notes beside the V4 flowchart; text reads in a terminal.
        """
        from cyberbird.reactive.trajectory import to_mermaid, to_text

        if not events:
            return
        if style != "none":
            render = to_mermaid if style == "mermaid" else to_text
            print(f"\n{BOLD}trajectory{Style.RESET_ALL} {DIM}({style}){Style.RESET_ALL}")
            print(render(events))
        if saved is not None:
            print(f"{DIM}trajectory saved: {saved}{Style.RESET_ALL}")

    def report(self, state: dict, budget: int, trace_path) -> None:
        """The end-of-run summary.

        Deliberately not gated on `enabled`: --quiet suppresses the live
        commentary, not the result. A run that printed nothing at all would
        give a script no way to see what happened without parsing the trace.
        """
        usage = state["usage"].as_dict()
        accepted = state["status"] == "accepted"
        colour = GOOD + Style.BRIGHT if accepted else BAD
        print(f"\n{colour}terminal status : {state['status']}{Style.RESET_ALL}")
        print(f"model calls     : {state['model_calls']} of {budget}")
        print(f"tokens          : input={usage['input']} output={usage['output']} "
              f"cache_read={usage['cache_read']} cache_write={usage['cache_write']}"
              f"  {DIM}(four counters, never summed){Style.RESET_ALL}")
        print(f"observations    : {len(state['observations'])}")
        print(f"trace           : {trace_path}")

        patch = state.get("candidate_patch") or ""
        if not patch.strip():
            print("\npatch: none produced")
            return
        print("\npatch:")
        for line in patch.splitlines():
            if line.startswith("+++") or line.startswith("---"):
                print(f"{BOLD}{line}{Style.RESET_ALL}")
            elif line.startswith("+"):
                print(f"{GOOD}{line}{Style.RESET_ALL}")
            elif line.startswith("-"):
                print(f"{BAD}{line}{Style.RESET_ALL}")
            elif line.startswith("@@"):
                print(f"{Fore.MAGENTA}{line}{Style.RESET_ALL}")
            else:
                print(f"{DIM}{line}{Style.RESET_ALL}")
