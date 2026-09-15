import argparse
import asyncio
import getpass
import json
import os
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

import uvicorn

from obsion.config import get_settings
from obsion.contracts.validation import validate_contracts
from obsion.db.session import Database
from obsion.domain.enums import SystemRole
from obsion.evaluations.acceptance import AcceptanceError, AcceptanceProfile, AcceptanceRunner
from obsion.evaluations.manifests import EvaluationManifestError, validate_evaluation_root
from obsion.evaluations.offline import OfflineEvaluationError, execute_offline_evaluations
from obsion.main import create_app
from obsion.registry.manifests import RegistryManifestError, validate_registry_root
from obsion.release.artifact_drill import record_artifact_drill_evidence
from obsion.release.candidate import ReleaseCandidateError, validate_release_candidate
from obsion.release.drill import DrillError, record_drill_evidence
from obsion.release.hardening import (
    EvaluationGateError,
    cyclonedx_sbom,
    scan_secrets,
    validate_evaluation_gate,
)
from obsion.release.live_evidence import LiveEvidenceError, record_live_evidence
from obsion.release.notes import ReleaseNotesError, read_project_version, validate_release_notes
from obsion.release.project_status import ProjectStatusError, validate_project_status
from obsion.security.provisioning import provision_password_identity

_PROVISION_INPUT_ENV = "OBSION_PROVISION_PASSWORD"


def _serve(args: argparse.Namespace) -> None:
    settings = get_settings()
    uvicorn.run(
        "obsion.main:create_app",
        factory=True,
        host=settings.api_host if args.host is None else args.host,
        port=settings.api_port if args.port is None else args.port,
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


def _validate_evaluations(args: argparse.Namespace) -> None:
    try:
        summary = validate_evaluation_root(Path(args.root))
    except EvaluationManifestError as exc:
        raise SystemExit(str(exc)) from exc
    summary["routes"] = sorted(_dataset_routes(Path(args.root)))
    print(json.dumps(summary, sort_keys=True))  # noqa: T201


def _dataset_routes(root: Path) -> set[str]:
    routes: set[str] = set()
    for path in sorted(root.glob("*.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        cases = document.get("cases") if isinstance(document, dict) else None
        if not isinstance(cases, list):
            continue
        for case in cases:
            if not isinstance(case, dict):
                continue
            expected = case.get("expected")
            if isinstance(expected, dict) and isinstance(expected.get("route"), str):
                routes.add(expected["route"])
    return routes


def _validate_eval_gates(args: argparse.Namespace) -> None:
    try:
        summary = validate_evaluation_root(Path(args.datasets))
        summary["routes"] = sorted(_dataset_routes(Path(args.datasets)))
        result = validate_evaluation_gate(Path(args.gate), summary)
    except (EvaluationManifestError, EvaluationGateError) as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result, sort_keys=True))  # noqa: T201


def _evaluate_datasets(args: argparse.Namespace) -> None:
    try:
        result = execute_offline_evaluations(Path(args.datasets))
    except (EvaluationManifestError, OfflineEvaluationError) as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result, sort_keys=True))  # noqa: T201
    if args.require_complete and result["acceptance_status"] != "PASS":
        raise SystemExit(2)


def _acceptance_run(args: argparse.Namespace) -> None:
    import httpx

    root = Path(args.root).resolve()
    profile_path = Path(args.profile)
    if not profile_path.is_file():
        profile_path = root / "evaluations" / "profiles" / f"{args.profile}.json"
    try:
        profile = AcceptanceProfile.model_validate_json(profile_path.read_bytes())
        if profile.phase != args.phase:
            raise AcceptanceError("acceptance_profile_phase_mismatch")
    except (OSError, ValueError) as exc:
        raise SystemExit("acceptance_profile_invalid_or_missing") from exc
    token = os.environ.get("OBSION_ACCEPTANCE_TOKEN")
    if not token:
        raise SystemExit("OBSION_ACCEPTANCE_TOKEN is required; do not pass secrets as arguments")
    output = Path(args.output).resolve()

    async def run() -> dict[str, Any]:
        async with httpx.AsyncClient(
            base_url=profile.api_base_url,
            headers={"Authorization": f"Bearer {token}"},
            follow_redirects=False,
            trust_env=False,
            timeout=httpx.Timeout(15),
        ) as client:
            runner = AcceptanceRunner(
                client,
                profile,
                candidate=args.candidate,
                scorer_model_profile=getattr(args, "scorer_model_profile", None),
            )
            return await runner.run(
                root, output, resume_from=Path(args.resume_from) if args.resume_from else None
            )

    try:
        result = asyncio.run(run())
    except (OSError, ValueError) as exc:
        raise SystemExit("acceptance_inputs_invalid_or_output_unavailable") from exc
    print(  # noqa: T201
        json.dumps(
            {
                key: result[key]
                for key in ("status", "phase_status", "promotion_eligible", "counts", "candidate")
            },
            sort_keys=True,
        )
    )
    if result["phase_status"] != "PASS":
        raise SystemExit(2)


def _acceptance_freeze(args: argparse.Namespace) -> None:
    import httpx

    token = os.environ.get("OBSION_ACCEPTANCE_TOKEN")
    if not token:
        raise SystemExit("OBSION_ACCEPTANCE_TOKEN is required; do not pass secrets as arguments")
    root = Path(args.root).resolve()
    destination = Path(args.profile).resolve()
    try:
        origin = AcceptanceProfile.validate_origin(args.api_url)
        workspace_id = UUID(args.workspace_id)
    except ValueError as exc:
        raise SystemExit("acceptance_origin_or_workspace_invalid") from exc

    async def freeze() -> AcceptanceProfile:
        async with httpx.AsyncClient(
            base_url=origin,
            headers={"Authorization": f"Bearer {token}"},
            follow_redirects=False,
            trust_env=False,
            timeout=httpx.Timeout(15),
        ) as client:
            return await AcceptanceRunner.freeze(
                client,
                root,
                name=destination.stem,
                candidate=args.candidate,
                image_digest=args.image_digest,
                workspace_id=workspace_id,
                model_profile=args.model_profile,
                dataset=args.dataset,
                dataset_sha256=args.dataset_sha256,
            )

    try:
        profile = asyncio.run(freeze())
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(profile.model_dump_json(indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    except (OSError, ValueError, httpx.HTTPError, KeyError, TypeError) as exc:
        raise SystemExit(
            "acceptance_freeze_failed_check_candidate_sources_and_new_profile_path"
        ) from exc
    print(str(destination))  # noqa: T201


def _scan_secrets(args: argparse.Namespace) -> None:
    findings = scan_secrets(Path(args.root))
    payload = [finding.__dict__ for finding in findings]
    print(json.dumps({"findings": payload, "count": len(payload)}, sort_keys=True))  # noqa: T201
    if findings:
        raise SystemExit(1)


def _write_sbom(args: argparse.Namespace) -> None:
    try:
        version = args.version or read_project_version(Path(args.project_status))
    except ReleaseNotesError as exc:
        raise SystemExit(str(exc)) from exc
    document = cyclonedx_sbom(
        Path(args.lockfile),
        component_name=args.name,
        component_version=version,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output)  # noqa: T201


def _validate_project_status(args: argparse.Namespace) -> None:
    try:
        result = validate_project_status(Path(args.root))
    except ProjectStatusError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result, sort_keys=True))  # noqa: T201


def _validate_release_notes(args: argparse.Namespace) -> None:
    try:
        result = validate_release_notes(Path(args.manifest), Path(args.root))
    except ReleaseNotesError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result, sort_keys=True))  # noqa: T201


def _validate_release_candidate(args: argparse.Namespace) -> None:
    root = Path(args.root).resolve()
    contract = Path(args.contract)
    if not contract.is_absolute():
        contract = root / contract
    artifact_manifest: Path | None = None
    if not args.contract_only:
        if args.artifact_manifest:
            artifact_manifest = Path(args.artifact_manifest)
            if not artifact_manifest.is_absolute():
                artifact_manifest = root / artifact_manifest
        else:
            try:
                version = read_project_version(root / "docs/project-status.yaml")
            except ReleaseNotesError as exc:
                raise SystemExit(str(exc)) from exc
            artifact_manifest = root / "dist" / "release" / version / "artifact-manifest.json"
    try:
        result = validate_release_candidate(
            contract,
            artifact_manifest,
            root,
            contract_only=args.contract_only,
            require_promotion_eligible=args.require_promotion_eligible,
        )
    except ReleaseCandidateError as exc:
        raise SystemExit(str(exc)) from exc
    if args.write_report:
        if artifact_manifest is None:
            raise SystemExit("--write-report requires full artifact validation")
        report = artifact_manifest.parent / "release-candidate-report.json"
        report.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        result["report"] = report.relative_to(root).as_posix()
    print(json.dumps(result, sort_keys=True))  # noqa: T201


def _record_live_evidence(args: argparse.Namespace) -> None:
    root = Path(args.root).resolve()
    contract = Path(args.contract)
    if not contract.is_absolute():
        contract = root / contract
    output = Path(args.output) if args.output else None
    if output is None:
        output = (
            root
            / "docs"
            / "release"
            / "evidence"
            / "alpha1"
            / f"feishu-{args.profile_label}-live.yaml"
        )
    elif not output.is_absolute():
        output = root / output
    try:
        result = record_live_evidence(
            contract,
            output,
            root,
            profile_label=args.profile_label,
            include_optional=args.include_send_probe,
        )
    except LiveEvidenceError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result, sort_keys=True))  # noqa: T201
    if result["failed"]:
        raise SystemExit("live evidence probes failed: " + ", ".join(result["failed"]))


def _record_drill_evidence(args: argparse.Namespace) -> None:
    root = Path(args.root).resolve()
    contract = Path(args.contract)
    if not contract.is_absolute():
        contract = root / contract
    output = Path(args.output) if args.output else None
    if output is None:
        output = root / "docs" / "release" / "evidence" / "alpha1" / "backup-restore-drill.yaml"
    elif not output.is_absolute():
        output = root / output
    try:
        result = record_drill_evidence(contract, output, root)
    except DrillError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result, sort_keys=True))  # noqa: T201
    if result["failed"]:
        raise SystemExit("drill evidence checks failed: " + ", ".join(result["failed"]))


def _record_artifact_drill_evidence(args: argparse.Namespace) -> None:
    root = Path(args.root).resolve()
    contract = Path(args.contract)
    if not contract.is_absolute():
        contract = root / contract
    output = Path(args.output) if args.output else None
    if output is None:
        output = root / "docs" / "release" / "evidence" / "alpha1" / "artifact-store-drill.yaml"
    elif not output.is_absolute():
        output = root / output
    try:
        result = record_artifact_drill_evidence(contract, output, root)
    except DrillError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result, sort_keys=True))  # noqa: T201
    if result["failed"]:
        raise SystemExit("artifact drill evidence checks failed: " + ", ".join(result["failed"]))


def _validate_contracts(_: argparse.Namespace) -> None:
    summary = validate_contracts()
    print(  # noqa: T201
        json.dumps(
            {
                "error_codes": summary.error_code_count,
                "event_registry_version": summary.event_registry_version,
                "event_versions": summary.event_version_count,
                "events": summary.event_count,
            },
            sort_keys=True,
        )
    )


def _read_provision_password() -> str:
    """Collect the password from stdin, the environment, or an interactive prompt.

    It is never accepted as an argument: process arguments are readable by any
    local user through the process table.
    """

    interactive = sys.stdin.isatty()
    if not interactive:
        piped = sys.stdin.read()
        if piped.strip("\r\n"):
            return piped.split("\n", 1)[0].rstrip("\r")
    from_environment = os.environ.get(_PROVISION_INPUT_ENV)
    if from_environment:
        return from_environment
    if not interactive:
        raise SystemExit(
            f"A password is required on stdin or in {_PROVISION_INPUT_ENV} for non-interactive use"
        )
    return getpass.getpass("Password: ")


def _provision_user(args: argparse.Namespace) -> None:
    password = _read_provision_password()
    settings = get_settings()
    database = Database(settings)

    async def run() -> dict[str, Any]:
        try:
            async with database.sessions() as session:
                identity = await provision_password_identity(
                    session,
                    settings,
                    email=args.email,
                    password=password,
                    role=SystemRole(args.role),
                    display_name=args.display_name,
                    organization=args.organization,
                    allow_weak_password=args.allow_weak_password,
                    must_change=args.must_change,
                )
                await session.commit()
        finally:
            await database.dispose()
        return {
            "created": identity.created,
            "display_name": identity.display_name,
            "email": identity.email,
            "organization_id": str(identity.organization_id),
            "organization_slug": identity.organization_slug,
            "roles": list(identity.roles),
            "user_id": str(identity.user_id),
        }

    print(json.dumps(asyncio.run(run()), sort_keys=True))  # noqa: T201


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="obsion", description="Operate the Obsion control plane")
    commands = parser.add_subparsers(dest="command", required=True)

    acceptance = commands.add_parser("acceptance", help="Execute frozen product acceptance")
    acceptance_commands = acceptance.add_subparsers(dest="acceptance_command", required=True)
    acceptance_run = acceptance_commands.add_parser(
        "run", help="Run normal tasks and collect answers"
    )
    acceptance_run.add_argument("--phase", required=True, choices=["P1"])
    acceptance_run.add_argument("--profile", required=True)
    acceptance_run.add_argument("--candidate", required=True)
    acceptance_run.add_argument("--root", default=".")
    acceptance_run.add_argument("--output", required=True)
    acceptance_run.add_argument(
        "--scorer-model-profile",
        help="Registered independent evaluator in the frozen model configuration",
    )
    acceptance_run.add_argument(
        "--resume-from", help="Reconcile recorded tasks into a new report without resubmission"
    )
    acceptance_run.set_defaults(handler=_acceptance_run)
    acceptance_freeze = acceptance_commands.add_parser(
        "freeze", help="Freeze observed acceptance inputs"
    )
    acceptance_freeze.add_argument("--profile", required=True)
    acceptance_freeze.add_argument("--candidate", required=True)
    acceptance_freeze.add_argument("--api-url", required=True)
    acceptance_freeze.add_argument("--image-digest", required=True)
    acceptance_freeze.add_argument("--workspace-id", required=True)
    acceptance_freeze.add_argument("--model-profile", required=True)
    acceptance_freeze.add_argument("--dataset", required=True)
    acceptance_freeze.add_argument("--dataset-sha256", required=True)
    acceptance_freeze.add_argument("--root", default=".")
    acceptance_freeze.set_defaults(handler=_acceptance_freeze)

    serve = commands.add_parser("serve", help="Start the control-plane API and run workers")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", default=None, type=int)
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

    evaluations = commands.add_parser(
        "validate-evaluations", help="Validate version-controlled Golden Dataset contracts"
    )
    evaluations.add_argument("--root", default="evaluations/datasets")
    evaluations.set_defaults(handler=_validate_evaluations)

    contracts = commands.add_parser(
        "validate-contracts",
        help="Validate the frozen Event and domain error contracts",
    )
    contracts.set_defaults(handler=_validate_contracts)

    eval_gates = commands.add_parser(
        "validate-eval-gates",
        help="Validate the release evaluation gate against Golden Datasets",
    )
    eval_gates.add_argument("--gate", default="evaluations/gates/v1-release.yaml")
    eval_gates.add_argument("--datasets", default="evaluations/datasets")
    eval_gates.set_defaults(handler=_validate_eval_gates)

    evaluate_datasets = commands.add_parser(
        "evaluate-datasets",
        help="Execute Golden Dataset ROUTING and SQL_POLICY cases against production code",
    )
    evaluate_datasets.add_argument("--datasets", default="evaluations/datasets")
    evaluate_datasets.add_argument(
        "--require-complete",
        action="store_true",
        help="Exit 2 when any required Run-output case was not executed",
    )
    evaluate_datasets.set_defaults(handler=_evaluate_datasets)

    secrets = commands.add_parser(
        "scan-secrets", help="Scan source for credential literals outside tests"
    )
    secrets.add_argument("--root", default=".")
    secrets.set_defaults(handler=_scan_secrets)

    project_status = commands.add_parser(
        "validate-project-status", help="校验本地阶段声明，不评估生产晋级授权"
    )
    project_status.add_argument("--root", default=".")
    project_status.set_defaults(handler=_validate_project_status)

    release_notes = commands.add_parser(
        "validate-release-notes",
        help="Validate the current operator release-note contract",
    )
    release_notes.add_argument("--manifest", default="docs/release/0.98.0-dev.yaml")
    release_notes.add_argument("--root", default=".")
    release_notes.set_defaults(handler=_validate_release_notes)

    release_candidate = commands.add_parser(
        "validate-release-candidate",
        help="Validate Alpha.1 requirement, artifact, and operator-gate evidence",
    )
    release_candidate.add_argument(
        "--contract",
        default="docs/release/alpha1-candidate-gates.yaml",
    )
    release_candidate.add_argument("--artifact-manifest")
    release_candidate.add_argument("--root", default=".")
    release_candidate.add_argument("--contract-only", action="store_true")
    release_candidate.add_argument("--require-promotion-eligible", action="store_true")
    release_candidate.add_argument("--write-report", action="store_true")
    release_candidate.set_defaults(handler=_validate_release_candidate)

    live_evidence = commands.add_parser(
        "record-live-evidence",
        help="Run the opt-in live Feishu ladder and write a redacted evidence ledger",
    )
    live_evidence.add_argument(
        "--contract",
        default="docs/release/alpha1-live-evidence-contract.yaml",
    )
    live_evidence.add_argument("--output")
    live_evidence.add_argument("--root", default=".")
    live_evidence.add_argument("--profile-label", required=True)
    live_evidence.add_argument("--include-send-probe", action="store_true")
    live_evidence.set_defaults(handler=_record_live_evidence)

    drill_evidence = commands.add_parser(
        "record-drill-evidence",
        help="Run the opt-in backup/restore drill and write a redacted evidence ledger",
    )
    drill_evidence.add_argument(
        "--contract",
        default="docs/release/alpha1-drill-evidence-contract.yaml",
    )
    drill_evidence.add_argument("--output")
    drill_evidence.add_argument("--root", default=".")
    drill_evidence.set_defaults(handler=_record_drill_evidence)

    artifact_drill = commands.add_parser(
        "record-artifact-drill-evidence",
        help="Run the opt-in artifact-store drill and write a redacted evidence ledger",
    )
    artifact_drill.add_argument(
        "--contract",
        default="docs/release/alpha1-artifact-drill-evidence-contract.yaml",
    )
    artifact_drill.add_argument("--output")
    artifact_drill.add_argument("--root", default=".")
    artifact_drill.set_defaults(handler=_record_artifact_drill_evidence)

    sbom = commands.add_parser("sbom", help="Generate a CycloneDX SBOM from uv.lock")
    sbom.add_argument("--lockfile", default="uv.lock")
    sbom.add_argument("--output", default="docs/release/sbom.cdx.json")
    sbom.add_argument("--name", default="obsion")
    sbom.add_argument("--version")
    sbom.add_argument("--project-status", default="docs/project-status.yaml")
    sbom.set_defaults(handler=_write_sbom)

    provision = commands.add_parser(
        "provision-user",
        help="Create or update an identity with a local password credential",
        description=(
            "The password is read from stdin, the "
            f"{_PROVISION_INPUT_ENV} environment variable, or an interactive prompt. "
            "It is never accepted as a command-line argument."
        ),
    )
    provision.add_argument("--email", required=True)
    provision.add_argument(
        "--role",
        default=SystemRole.VIEWER.value,
        choices=[role.value for role in SystemRole],
    )
    provision.add_argument("--display-name")
    provision.add_argument(
        "--organization",
        help="Organization slug or id; defaults to the configured local organization",
    )
    provision.add_argument(
        "--allow-weak-password",
        action="store_true",
        help="Permit a password below policy; rejected outside development and test",
    )
    provision.add_argument(
        "--must-change",
        action="store_true",
        help="Require the password to be replaced on first use",
    )
    provision.set_defaults(handler=_provision_user)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
