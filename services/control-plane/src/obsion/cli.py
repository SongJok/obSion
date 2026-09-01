import argparse
import json
from pathlib import Path
from typing import Any

import uvicorn

from obsion.contracts.validation import validate_contracts
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
    evaluate_datasets.set_defaults(handler=_evaluate_datasets)

    secrets = commands.add_parser(
        "scan-secrets", help="Scan source for credential literals outside tests"
    )
    secrets.add_argument("--root", default=".")
    secrets.set_defaults(handler=_scan_secrets)

    release_notes = commands.add_parser(
        "validate-release-notes",
        help="Validate the current operator release-note contract",
    )
    release_notes.add_argument("--manifest", default="docs/release/0.94.0-dev.yaml")
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
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
