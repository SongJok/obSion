"""Deterministic incident evidence fusion.

IncidentAgent is deliberately a read-only investigator.  This module turns the
immutable Evidence rows collected by the Gateway into ranked *candidate* root
causes without calling a provider, mutating production, or treating a single
signal as a conclusion.  A candidate is publishable only when it is supported
by at least two different Evidence types.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from itertools import combinations
from typing import Any

from obsion.common.time import ensure_utc
from obsion.db.models import Evidence
from obsion.domain.enums import EvidenceType


@dataclass(frozen=True, slots=True)
class IncidentCandidate:
    """One ranked, explicitly non-conclusive root-cause hypothesis."""

    rank: int
    statement: str
    evidence_ids: tuple[str, ...]
    evidence_types: tuple[str, ...]
    score: float
    reason_codes: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["evidence_ids"] = list(self.evidence_ids)
        value["evidence_types"] = list(self.evidence_types)
        value["reason_codes"] = list(self.reason_codes)
        return value


@dataclass(frozen=True, slots=True)
class IncidentFusionResult:
    """Stable projection persisted with the answer Artifact."""

    candidates: tuple[IncidentCandidate, ...]
    evidence_type_coverage: tuple[str, ...]
    timeline: tuple[dict[str, Any], ...]
    conflicts: tuple[dict[str, Any], ...]

    @property
    def top1(self) -> IncidentCandidate | None:
        return self.candidates[0] if self.candidates else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_count": len(self.candidates),
            "top1": self.top1.as_dict() if self.top1 else None,
            "top3": [candidate.as_dict() for candidate in self.candidates[:3]],
            "evidence_type_coverage": list(self.evidence_type_coverage),
            "timeline": list(self.timeline),
            "conflicts": list(self.conflicts),
        }


@dataclass(frozen=True, slots=True)
class _Signal:
    evidence: Evidence
    record: dict[str, Any]
    timestamp: datetime
    service: str
    environment: str
    deployment_id: str
    commit_id: str
    marker: str


class IncidentEvidenceFusion:
    """Fuse normalized Evidence using bounded, explainable heuristics."""

    _PAIR_PRIORITIES: dict[frozenset[str], float] = {
        frozenset({"METRIC", "DEPLOYMENT"}): 0.72,
        frozenset({"DEPLOYMENT", "CODE"}): 0.70,
        frozenset({"DEPLOYMENT", "GIT"}): 0.70,
        frozenset({"METRIC", "GIT"}): 0.68,
        frozenset({"METRIC", "LOG"}): 0.66,
        frozenset({"LOG", "CODE"}): 0.64,
        frozenset({"LOG", "GIT"}): 0.64,
        frozenset({"LOG", "DEPLOYMENT"}): 0.60,
        frozenset({"METRIC", "CONFIG"}): 0.58,
        frozenset({"TRACE", "LOG"}): 0.52,
        frozenset({"TRACE", "DEPLOYMENT"}): 0.48,
        frozenset({"CONFIG", "DEPLOYMENT"}): 0.46,
    }
    _MAX_CANDIDATES = 3
    _MAX_CORRELATION_SECONDS = 24 * 60 * 60

    def fuse(self, evidence: list[Evidence]) -> IncidentFusionResult:
        signals = [signal for item in evidence for signal in self._signals(item)]
        substantive = [item for item in evidence if self._substantive(item)]
        coverage = tuple(
            sorted(
                {
                    self._evidence_type(item)
                    for item in substantive
                    if self._evidence_type(item)
                    in {"METRIC", "DEPLOYMENT", "LOG", "TRACE", "CODE", "GIT", "CONFIG"}
                }
            )
        )
        candidates_by_pair: dict[tuple[str, ...], IncidentCandidate] = {}
        for left, right in combinations(signals, 2):
            left_type = self._evidence_type(left.evidence)
            right_type = self._evidence_type(right.evidence)
            pair = frozenset({left_type, right_type})
            if len(pair) != 2 or pair not in self._PAIR_PRIORITIES:
                continue
            if (
                abs((left.timestamp - right.timestamp).total_seconds())
                > self._MAX_CORRELATION_SECONDS
            ):
                continue
            score, reasons = self._score(left, right, self._PAIR_PRIORITIES[pair])
            candidate = IncidentCandidate(
                rank=0,
                statement=self._statement(left, right),
                evidence_ids=(str(left.evidence.id), str(right.evidence.id)),
                evidence_types=tuple(sorted(pair)),
                score=round(score, 4),
                reason_codes=tuple(reasons),
            )
            pair_key = tuple(sorted(candidate.evidence_ids))
            previous = candidates_by_pair.get(pair_key)
            if previous is None or candidate.score > previous.score:
                candidates_by_pair[pair_key] = candidate
        candidates = list(candidates_by_pair.values())
        candidates.sort(
            key=lambda item: (
                -item.score,
                -self._PAIR_PRIORITIES.get(frozenset(item.evidence_types), 0.0),
                item.evidence_types,
                item.evidence_ids,
            )
        )
        ranked = tuple(
            IncidentCandidate(
                rank=index,
                statement=item.statement,
                evidence_ids=item.evidence_ids,
                evidence_types=item.evidence_types,
                score=item.score,
                reason_codes=item.reason_codes,
            )
            for index, item in enumerate(candidates[: self._MAX_CANDIDATES], start=1)
        )
        timeline = self._timeline(substantive)
        conflicts = self._conflicts(signals)
        return IncidentFusionResult(
            candidates=ranked,
            evidence_type_coverage=coverage,
            timeline=timeline,
            conflicts=conflicts,
        )

    @staticmethod
    def _evidence_type(item: Evidence) -> str:
        value = item.evidence_type
        return value.value if isinstance(value, EvidenceType) else str(value)

    @staticmethod
    def _substantive(item: Evidence) -> bool:
        for key in ("hits", "events", "items", "records"):
            values = item.content.get(key)
            if isinstance(values, list) and not values:
                return False
        return True

    def _signals(self, item: Evidence) -> list[_Signal]:
        if not self._substantive(item):
            return []
        records = self._records(item.content)
        if not records:
            records = [{}]
        result: list[_Signal] = []
        for record in records[:100]:
            timestamp = self._timestamp(record, item.observed_at)
            service = self._text(record, "service", "service_name", "application")
            environment = self._text(record, "environment", "env")
            deployment_id = self._text(record, "deployment_id", "deployment", "release_id")
            commit_id = self._text(record, "commit_id", "commit", "revision", "sha")
            marker = self._marker(record)
            result.append(
                _Signal(
                    item, record, timestamp, service, environment, deployment_id, commit_id, marker
                )
            )
        return result

    @staticmethod
    def _records(content: dict[str, Any]) -> list[dict[str, Any]]:
        for key in ("events", "items", "hits", "results", "records"):
            value = content.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        data = content.get("data")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        series = content.get("series")
        if isinstance(series, list):
            flattened: list[dict[str, Any]] = []
            for item in series:
                if not isinstance(item, dict):
                    continue
                values = item.get("values")
                if isinstance(values, list):
                    for value in values:
                        if isinstance(value, (list, tuple)) and len(value) >= 2:
                            flattened.append(
                                {
                                    "timestamp": value[0],
                                    "value": value[1],
                                    "service": item.get("service"),
                                    "metric": item.get("metric"),
                                }
                            )
                        elif isinstance(value, dict):
                            flattened.append({**item, **value})
                else:
                    flattened.append(item)
            return flattened
        return [content] if isinstance(content, dict) and content else []

    @staticmethod
    def _text(record: dict[str, Any], *keys: str) -> str:
        for key in keys:
            value = record.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        attributes = record.get("attributes")
        if isinstance(attributes, dict):
            for key in keys:
                value = attributes.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return ""

    @classmethod
    def _timestamp(cls, record: dict[str, Any], fallback: datetime) -> datetime:
        value: Any = None
        for key in ("timestamp", "observed_at", "occurred_at", "started_at", "time"):
            if key in record:
                value = record[key]
                break
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            # Providers use either seconds or milliseconds since Unix epoch.
            seconds = float(value) / 1000 if abs(float(value)) > 10_000_000_000 else float(value)
            try:
                return datetime.fromtimestamp(seconds, tz=UTC)
            except (OverflowError, OSError, ValueError):
                return ensure_utc(fallback)
        if isinstance(value, str):
            try:
                return ensure_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
            except ValueError:
                pass
        return ensure_utc(fallback)

    @classmethod
    def _marker(cls, record: dict[str, Any]) -> str:
        values: list[str] = []
        for key in ("status", "severity", "level", "error_type", "exception", "metric", "query"):
            value = record.get(key)
            if isinstance(value, (str, int, float)):
                values.append(str(value).casefold())
        attributes = record.get("attributes")
        if isinstance(attributes, dict):
            for key, value in attributes.items():
                if isinstance(value, (str, int, float)) and key.casefold() not in {
                    "user_id",
                    "order_id",
                    "email",
                }:
                    values.append(f"{key.casefold()}={value}".casefold())
        return " ".join(values)[:500]

    @classmethod
    def _score(cls, left: _Signal, right: _Signal, base: float) -> tuple[float, list[str]]:
        score = base
        reasons: list[str] = []
        if left.service and right.service and left.service == right.service:
            score += 0.12
            reasons.append("shared_service")
        if left.environment and right.environment and left.environment == right.environment:
            score += 0.04
            reasons.append("shared_environment")
        if left.deployment_id and left.deployment_id == right.deployment_id:
            score += 0.16
            reasons.append("shared_deployment")
        if left.commit_id and left.commit_id == right.commit_id:
            score += 0.16
            reasons.append("shared_commit")
        delta = abs((left.timestamp - right.timestamp).total_seconds())
        if delta <= 15 * 60:
            score += 0.12
            reasons.append("temporal_window_15m")
        elif delta <= 60 * 60:
            score += 0.08
            reasons.append("temporal_window_1h")
        elif delta <= 6 * 60 * 60:
            score += 0.03
            reasons.append("temporal_window_6h")
        marker_left = {part for part in left.marker.replace("=", " ").split() if len(part) > 2}
        marker_right = {part for part in right.marker.replace("=", " ").split() if len(part) > 2}
        if marker_left & marker_right:
            score += 0.05
            reasons.append("shared_signal_marker")
        return min(0.99, score), reasons

    @classmethod
    def _statement(cls, left: _Signal, right: _Signal) -> str:
        types = {cls._evidence_type(left.evidence), cls._evidence_type(right.evidence)}
        service = left.service or right.service or "目标服务"
        release = left.deployment_id or right.deployment_id or left.commit_id or right.commit_id
        if types == {"METRIC", "DEPLOYMENT"}:
            suffix = f"（发布/部署 {release}）" if release else ""
            return f"{service} 的指标异常与发布变更在同一时间窗出现，{suffix}该变更是候选根因。"
        if types == {"DEPLOYMENT", "CODE"}:
            suffix = f"（commit {release}）" if release else ""
            return f"{service} 的部署记录与代码变更相互关联，{suffix}该变更是候选根因。"
        if types == {"METRIC", "LOG"}:
            return f"{service} 的指标异常与错误日志在同一时间窗共同出现，相关错误链是候选根因。"
        if types == {"LOG", "CODE"}:
            suffix = f"（commit {release}）" if release else ""
            return f"{service} 的错误日志与代码差异存在时间/信号关联，{suffix}该变更是候选根因。"
        return f"{service} 的 {' + '.join(sorted(types))} 证据在同一调查窗口关联，形成候选根因。"

    @classmethod
    def _timeline(cls, evidence: list[Evidence]) -> tuple[dict[str, Any], ...]:
        rows = sorted(evidence, key=lambda item: (ensure_utc(item.observed_at), str(item.id)))
        return tuple(
            {
                "evidence_id": str(item.id),
                "type": cls._evidence_type(item),
                "source": item.source,
                "observed_at": ensure_utc(item.observed_at).isoformat(),
            }
            for item in rows
        )

    @classmethod
    def _conflicts(cls, signals: list[_Signal]) -> tuple[dict[str, Any], ...]:
        conflicts: list[dict[str, Any]] = []
        metric_signals = [item for item in signals if cls._evidence_type(item.evidence) == "METRIC"]
        for left, right in combinations(metric_signals, 2):
            if left.service and right.service and left.service != right.service:
                continue
            left_status = cls._text(left.record, "status", "state")
            right_status = cls._text(right.record, "status", "state")
            if (
                not left_status
                or not right_status
                or left_status.casefold() == right_status.casefold()
            ):
                continue
            conflicts.append(
                {
                    "left_evidence_id": str(left.evidence.id),
                    "right_evidence_id": str(right.evidence.id),
                    "kind": "VALUE",
                    "severity": "MEDIUM",
                    "reason": "Metric signals for the same service report different states",
                }
            )
        return tuple(conflicts[:20])
