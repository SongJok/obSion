import argparse
import json
from pathlib import Path
from typing import Any

import uvicorn

from obsion.main import create_app
from obsion.registry.manifests import RegistryManifestError, validate_registry_root


def _serve(args: argparse.Namespace) -> None:
    uvicorn.run(
        "obsion.main:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
        proxy_headers=True,
    )


def _write_openapi(args: argparse.Namespace) -> None:
    destination = Path(args.output).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    document: dict[str, Any] = create_app().openapi()
    destination.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(destination)  # noqa: T201


def _validate_registry(args: argparse.Namespace) -> None:
    try:
        agents, skills, connectors = validate_registry_root(Path(args.root))
    except RegistryManifestError as exc:
        raise SystemExit(str(exc)) from exc
    print(  # noqa: T201
        json.dumps(
            {"agents": agents, "skills": skills, "connectors": connectors},
            sort_keys=True,
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="obsion", description="Operate the Obsion control plane")
    commands = parser.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("serve", help="Start the control-plane API and run workers")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=8080, type=int)
    serve.add_argument("--reload", action="store_true")
    serve.set_defaults(handler=_serve)

    openapi = commands.add_parser("openapi", help="Generate the OpenAPI contract")
    openapi.add_argument("--output", default="docs/api/openapi.json")
    openapi.set_defaults(handler=_write_openapi)

    registry = commands.add_parser(
        "validate-registry", help="Validate declarative Agent, Skill, and Connector manifests"
    )
    registry.add_argument("--root", default=".")
    registry.set_defaults(handler=_validate_registry)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
