"""The `cyberbird` command: one entry point for every lecture's agent.

    cyberbird lec02 reactive --alert-id bc284c6b
    cyberbird lec02 plan-and-validation --alert-id bc284c6b
    cyberbird alerts --list

Lectures are command groups, and each agent version is a command inside its
lecture. A command forwards its raw arguments to that version's argparse
`main(argv)`, so every version keeps its own flags and its own --help.
"""
from __future__ import annotations

import importlib
import sys

import click

# Every argument, --help included, goes to the version's own parser.
_PASSTHROUGH = dict(
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
    add_help_option=False,
)


def _forward(group: click.Group, name: str, module: str, help: str) -> None:
    """Register `name` on `group`; running it calls `module.main(argv)`."""

    @group.command(name, help=help, short_help=help, **_PASSTHROUGH)
    @click.argument("argv", nargs=-1, type=click.UNPROCESSED)
    def command(argv: tuple[str, ...]) -> None:
        # argparse names itself after sys.argv[0]; show the full command path.
        sys.argv[0] = click.get_current_context().command_path
        # Imported only now, so `cyberbird --help` stays fast and one version
        # never loads another's code.
        raise SystemExit(importlib.import_module(module).main(list(argv)))


@click.group()
def cli() -> None:
    """Cyberbird: a vulnerability-patching agent, built one lecture at a time."""


@cli.group()
def lec02() -> None:
    """Lecture 2: reactive agent, then planning and validation."""


_forward(lec02, "reactive", "cyberbird.reactive.__main__",
         "V1: the model picks each tool call; the runtime checks the result.")
_forward(lec02, "plan-and-validation", "cyberbird.plan_and_validation.__main__",
         "V2/V3: a planner up front, replanning, and a validator.")

# Shared across lectures, so they use the newest version's code.
_forward(cli, "alerts", "cyberbird.plan_and_validation.cli",
         "Browse the alert queue.")
_forward(cli, "trace", "cyberbird.plan_and_validation.trajectory",
         "Render a run's trace as text, mermaid, or a summary.")
_forward(cli, "intake", "intake.build_findings",
         "Rebuild data/findings.json from the scanner output.")
