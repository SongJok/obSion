from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TextIO

from obsion_cli.config import CliError, CliSettings, load_settings
from obsion_cli.render import render_ask, render_value
from obsion_cli.runtime import ExperienceRuntime
from obsion_sdk import ObsionAPIError, ObsionAppServerError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="obsion-cli",
        description=(
            "Obsion Experience CLI. Submits work to the App Server and inspects "
            "Runs, Evidence, Claims, and Approvals. It does not run Agents locally."
        ),
    )
    parser.add_argument("--url", help="Control plane origin, for example http://127.0.0.1:8080")
    parser.add_argument(
        "--token",
        help="Bearer token. Prefer OBSION_TOKEN instead of shell history.",
    )
    parser.add_argument(
        "--protocol",
        choices=("app-server", "rest"),
        help="Lifecycle protocol. Default app-server; rest is for environments without WebSocket.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Machine-readable output",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="TOML config path. Must not contain credentials.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    workspace = commands.add_parser("workspace", help="Workspace management (create is REST)")
    workspace_commands = workspace.add_subparsers(dest="workspace_command", required=True)
    workspace_commands.add_parser("list", help="List workspaces")
    workspace_create = workspace_commands.add_parser("create", help="Create a workspace")
    workspace_create.add_argument("name")
    workspace_create.add_argument("--description", default="")

    thread = commands.add_parser("thread", help="Thread lifecycle")
    thread_commands = thread.add_subparsers(dest="thread_command", required=True)
    thread_list = thread_commands.add_parser("list", help="List threads in a workspace")
    thread_list.add_argument("workspace_id")
    thread_list.add_argument("--archived", action="store_true")
    thread_create = thread_commands.add_parser("create", help="Create a thread")
    thread_create.add_argument("workspace_id")
    thread_create.add_argument("title")
    thread_archive = thread_commands.add_parser("archive", help="Archive a thread")
    thread_archive.add_argument("thread_id")
    thread_resume = thread_commands.add_parser("resume", help="Resume an archived thread")
    thread_resume.add_argument("thread_id")
    thread_fork = thread_commands.add_parser("fork", help="Fork a thread at a frozen turn")
    thread_fork.add_argument("thread_id")
    thread_fork.add_argument("--title")
    thread_fork.add_argument("--from-turn")

    ask = commands.add_parser("ask", help="Create a Turn and wait for the governed Run")
    ask.add_argument("text", nargs="+", help="User question. Routing stays inside the Harness.")
    ask.add_argument("--workspace")
    ask.add_argument("--workspace-name")
    ask.add_argument("--thread")
    ask.add_argument("--title")

    run = commands.add_parser("run", help="Inspect or control a Run")
    run_commands = run.add_subparsers(dest="run_command", required=True)
    for name, help_text in (
        ("get", "Show run status"),
        ("cancel", "Cancel an active run"),
        ("replay", "Replay a terminal run snapshot"),
        ("events", "List run events"),
        ("steps", "List harness steps"),
        ("evidence", "List evidence"),
        ("claims", "List claims"),
        ("artifacts", "List artifacts"),
    ):
        command = run_commands.add_parser(name, help=help_text)
        command.add_argument("run_id")

    approval = commands.add_parser("approval", help="Capability approval decisions")
    approval_commands = approval.add_subparsers(dest="approval_command", required=True)
    approval_list = approval_commands.add_parser("list", help="List approvals")
    approval_list.add_argument("--status")
    decide = approval_commands.add_parser("decide", help="Approve or reject an approval")
    decide.add_argument("approval_id")
    decide.add_argument("decision", choices=("approve", "reject"))
    decide.add_argument("--reason", required=True)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        settings = load_settings(
            url=args.url,
            token=args.token,
            protocol=args.protocol,
            json_output=args.json_output,
            config_path=args.config,
        )
        if settings.token is None:
            raise CliError(
                "Set OBSION_TOKEN or pass --token. Tokens are never written to config files."
            )
        output = asyncio.run(_dispatch(settings, args))
        out.write(output)
        return 0
    except (CliError, ObsionAPIError, ObsionAppServerError) as exc:
        err.write(f"{exc}\n")
        return 1
    except KeyboardInterrupt:
        err.write("Interrupted\n")
        return 130


async def _dispatch(settings: CliSettings, args: argparse.Namespace) -> str:
    runtime = await ExperienceRuntime.connect(settings)
    try:
        value = await _execute(runtime, args)
    finally:
        await runtime.aclose()
    if args.command == "ask":
        from obsion_cli.runtime import AskResult

        assert isinstance(value, AskResult)
        return render_ask(value, json_output=settings.json_output)
    return render_value(value, json_output=settings.json_output)


async def _execute(runtime: ExperienceRuntime, args: argparse.Namespace) -> Any:
    if args.command == "workspace":
        if args.workspace_command == "list":
            return await runtime.list_workspaces()
        return await runtime.create_workspace(args.name, description=args.description)
    if args.command == "thread":
        if args.thread_command == "list":
            return await runtime.list_threads(args.workspace_id, include_archived=args.archived)
        if args.thread_command == "create":
            return await runtime.create_thread(args.workspace_id, args.title)
        if args.thread_command == "archive":
            return await runtime.archive_thread(args.thread_id)
        if args.thread_command == "resume":
            return await runtime.resume_thread(args.thread_id)
        return await runtime.fork_thread(
            args.thread_id, title=args.title, from_turn_id=args.from_turn
        )
    if args.command == "ask":
        return await runtime.ask(
            " ".join(args.text),
            workspace_id=args.workspace,
            workspace_name=args.workspace_name,
            thread_id=args.thread,
            title=args.title,
        )
    if args.command == "run":
        run_id = args.run_id
        if args.run_command == "get":
            return await runtime.get_run(run_id)
        if args.run_command == "cancel":
            return await runtime.cancel_run(run_id)
        if args.run_command == "replay":
            return await runtime.replay_run(run_id)
        if args.run_command == "events":
            return await runtime.list_run_events(run_id)
        if args.run_command == "steps":
            return await runtime.list_run_steps(run_id)
        if args.run_command == "evidence":
            return await runtime.list_run_evidence(run_id)
        if args.run_command == "claims":
            return await runtime.list_run_claims(run_id)
        return await runtime.list_run_artifacts(run_id)
    if args.approval_command == "list":
        return await runtime.list_approvals(status=args.status)
    return await runtime.decide_approval(
        args.approval_id,
        approve=args.decision == "approve",
        reason=args.reason,
    )


if __name__ == "__main__":
    raise SystemExit(main())
