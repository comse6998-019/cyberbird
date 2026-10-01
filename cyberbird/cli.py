"""The `cyberbird` command: one entry point for every agent.

    cyberbird reactive --alert-id bc284c6b
    cyberbird plan-and-validation --alert-id bc284c6b

Each agent is a subcommand. A command forwards its raw arguments to that
agent's argparse `main(argv)`, so every agent keeps its own flags and its own
--help.
"""
from __future__ import annotations

import importlib
import sys

import click

# Every argument, --help included, goes to the agent's own parser.
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
        # Imported only now, so `cyberbird --help` stays fast and one agent
        # never loads another's code.
        raise SystemExit(importlib.import_module(module).main(list(argv)))


@click.group()
def cli() -> None:
    """Cyberbird: vulnerability-patching agents, one subcommand each."""


_forward(cli, "reactive", "cyberbird.reactive.__main__",
         "The model picks each tool call; the runtime checks the result.")
_forward(cli, "plan-and-validation", "cyberbird.plan_and_validation.__main__",
         "A planner up front, replanning, and a validator.")
_forward(cli, "event-driven", "cyberbird.event_driven.__main__",
         "An event-driven agent with planning and validation.")