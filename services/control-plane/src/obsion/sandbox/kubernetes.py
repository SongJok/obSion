"""通过注入可信 httpx client 调用真实 Kubernetes REST 协议。

不读取 kubeconfig、环境凭据、ServiceAccount token，也不调用 kubectl。
client 只由可信控制面持有，永不进入命令契约、Agent 上下文或容器环境。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any

import httpx

from obsion.sandbox.contracts import (
    CleanupResult,
    SandboxHandle,
    SandboxLogs,
    SandboxRequest,
    SandboxStatus,
)

_LABEL = "obsion.io/sandbox"
_FINGERPRINT = "obsion.io/sandbox-fingerprint"
_MAX_RESPONSE = 262144
type JsonObject = dict[str, Any]


class SandboxError(RuntimeError):
    """脱敏错误，不携带上游响应正文、URL 或请求头。"""

    def __init__(
        self, code: str, *, handle: SandboxHandle | None = None, status_code: int | None = None
    ) -> None:
        super().__init__(code)
        self.handle = handle
        self.status_code = status_code


class SandboxUncertain(SandboxError):
    """创建结果不确定，只能对同一请求对账，不能换 ID 盲目重试。"""


class SandboxCancelled(asyncio.CancelledError):
    """保留协程取消语义及已确认资源身份，不表示集群实例已经终止。"""

    def __init__(self, handle: SandboxHandle) -> None:
        super().__init__("sandbox_cleanup_cancelled")
        self.handle = handle


@dataclass(frozen=True, slots=True)
class KubernetesSandboxSettings:
    namespace: str
    image: str
    enabled: bool = False
    environment: str = "production"
    request_timeout_seconds: int = 10

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", self.namespace):
            raise ValueError("namespace must be a DNS label")
        if not re.fullmatch(r"[a-z0-9][a-z0-9./:_-]*@sha256:[0-9a-f]{64}", self.image):
            raise ValueError("a trusted image pinned by sha256 digest is required")
        if type(self.enabled) is not bool:
            raise ValueError("enabled must be boolean")
        if self.environment not in {"test", "development", "staging", "production"}:
            raise ValueError("unknown environment")
        if (
            type(self.request_timeout_seconds) is not int
            or not 1 <= self.request_timeout_seconds <= 30
        ):
            raise ValueError("request timeout must be between 1 and 30 seconds")


class KubernetesSandboxAdapter:
    """仅供内部使用，每次操作都须由上游 Gateway/Policy 鉴权。

    本切片在生产环境中即使 enabled=True 也拒绝启动。
    不新增公开路由，也未注册 Harness/Connector 能力。
    """

    def __init__(self, client: httpx.AsyncClient, settings: KubernetesSandboxSettings) -> None:
        url = client.base_url
        if (
            url.scheme != "https"
            or not url.host
            or url.username
            or url.password
            or url.path != "/"
            or url.query
            or url.fragment
        ):
            raise ValueError(
                "trusted client requires an HTTPS API origin without embedded credentials"
            )
        self._client = client
        self._settings = settings
        self._jobs = f"/apis/batch/v1/namespaces/{settings.namespace}/jobs"
        self._policies = (
            f"/apis/networking.k8s.io/v1/namespaces/{settings.namespace}/networkpolicies"
        )
        self._pods = f"/api/v1/namespaces/{settings.namespace}/pods"

    def _check_enabled(self) -> None:
        if not self._settings.enabled or self._settings.environment == "production":
            raise SandboxError("sandbox_disabled")

    def _specifications(
        self, request: SandboxRequest
    ) -> tuple[SandboxHandle, JsonObject, JsonObject]:
        name = f"obsion-sb-{request.sandbox_id}"
        command = request.command.to_wire()
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "v": 1,
                    "command": command,
                    "image": self._settings.image,
                    "namespace": self._settings.namespace,
                    "runtimeClassName": "gvisor",
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        handle = SandboxHandle(name, fingerprint)
        metadata = {
            "name": name,
            "namespace": self._settings.namespace,
            "labels": {_LABEL: name},
            "annotations": {_FINGERPRINT: fingerprint},
        }
        policy = {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": metadata,
            "spec": {
                "podSelector": {"matchLabels": {_LABEL: name}},
                "policyTypes": ["Ingress", "Egress"],
                "ingress": [],
                "egress": [],
            },
        }
        limits = request.command.limits
        resources = {
            "cpu": f"{limits.cpu_millis}m",
            "memory": f"{limits.memory_mib}Mi",
            "ephemeral-storage": f"{limits.disk_mib}Mi",
        }
        job = {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": metadata,
            "spec": {
                "parallelism": 1,
                "completions": 1,
                "backoffLimit": 0,
                # 预留监督进程启动/回收时间；命令本身的期限由可信入口执行。
                "activeDeadlineSeconds": limits.timeout_seconds + 30,
                "template": {
                    "metadata": {"labels": {_LABEL: name}},
                    "spec": {
                        "runtimeClassName": "gvisor",
                        "restartPolicy": "Never",
                        "automountServiceAccountToken": False,
                        "enableServiceLinks": False,
                        "hostNetwork": False,
                        "hostPID": False,
                        "hostIPC": False,
                        "terminationGracePeriodSeconds": 5,
                        "securityContext": {
                            "runAsNonRoot": True,
                            "runAsUser": 65532,
                            "runAsGroup": 65532,
                            "fsGroup": 65532,
                            "seccompProfile": {"type": "RuntimeDefault"},
                        },
                        "containers": [
                            {
                                "name": "command",
                                "image": self._settings.image,
                                "imagePullPolicy": "IfNotPresent",
                                # -I 排除 workspace/PYTHONPATH 启动注入，-B 禁止写 pyc。
                                # 模块必须安装在运维构建的不可变镜像内。
                                "command": [
                                    "/usr/local/bin/python3",
                                    "-I",
                                    "-B",
                                    "-m",
                                    "obsion.sandbox.entrypoint",
                                ],
                                "args": ["--command", request.command.to_json()],
                                "workingDir": "/workspace",
                                "env": [{"name": "PYTHONDONTWRITEBYTECODE", "value": "1"}],
                                "securityContext": {
                                    "runAsNonRoot": True,
                                    "runAsUser": 65532,
                                    "runAsGroup": 65532,
                                    "allowPrivilegeEscalation": False,
                                    "readOnlyRootFilesystem": True,
                                    "privileged": False,
                                    "capabilities": {"drop": ["ALL"]},
                                    "seccompProfile": {"type": "RuntimeDefault"},
                                },
                                "resources": {"requests": resources, "limits": resources},
                                "volumeMounts": [{"name": "workspace", "mountPath": "/workspace"}],
                            }
                        ],
                        # 不挂载 PVC、hostPath、secret、token 或共享项目工作目录。
                        "volumes": [
                            {
                                "name": "workspace",
                                "emptyDir": {
                                    "sizeLimit": f"{limits.disk_mib}Mi",
                                },
                            }
                        ],
                    },
                },
            },
        }
        return handle, policy, job

    async def _json(
        self,
        method: str,
        path: str,
        *,
        body: JsonObject | None = None,
        params: JsonObject | None = None,
        missing_ok: bool = False,
    ) -> JsonObject | None:
        try:
            async with (
                asyncio.timeout(self._settings.request_timeout_seconds),
                self._client.stream(
                    method,
                    path,
                    json=body,
                    params=params,
                    headers={"Accept": "application/json", "Accept-Encoding": "identity"},
                    follow_redirects=False,
                    timeout=self._settings.request_timeout_seconds,
                ) as response,
            ):
                if missing_ok and response.status_code == 404:
                    return None
                if not 200 <= response.status_code < 300:
                    raise SandboxError("kubernetes_http_error", status_code=response.status_code)
                if response.headers.get("content-encoding", "identity") != "identity":
                    raise SandboxError("kubernetes_encoded_response")
                data = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=8192):
                    if len(data) + len(chunk) > _MAX_RESPONSE:
                        raise SandboxError("kubernetes_response_too_large")
                    data.extend(chunk)
                try:
                    result = json.loads(data)
                except (ValueError, UnicodeError):
                    raise SandboxError("kubernetes_invalid_json") from None
                if not isinstance(result, dict):
                    raise SandboxError("kubernetes_invalid_json")
                return result
        except (httpx.HTTPError, TimeoutError):
            raise SandboxError("kubernetes_transport_error") from None

    @staticmethod
    def _identity(resource: JsonObject, handle: SandboxHandle, uid: str | None = None) -> str:
        meta = resource.get("metadata", {})
        found_uid = meta.get("uid")
        if (
            meta.get("name") != handle.name
            or meta.get("labels", {}).get(_LABEL) != handle.name
            or meta.get("annotations", {}).get(_FINGERPRINT) != handle.fingerprint
            or not isinstance(found_uid, str)
            or not found_uid
            or (uid is not None and uid != found_uid)
        ):
            raise SandboxError("sandbox_resource_identity_conflict", handle=handle)
        return found_uid

    @staticmethod
    def _quantity(value: Any) -> Decimal | None:
        # resource.Quantity 回读会规范化，例如 2000m -> 2、4096Mi -> 4Gi。
        if not isinstance(value, str):
            return None
        matched = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(m|[KMGTPE]i)?", value)
        if matched is None:
            return None
        suffix = matched[2]
        scale = Decimal(1)
        if suffix == "m":
            scale = Decimal("0.001")
        elif suffix:
            scale = Decimal(1024) ** ("KMGTPE".index(suffix[0]) + 1)
        return Decimal(matched[1]) * scale

    @staticmethod
    def _contains(actual: Any, expected: Any) -> bool:
        """允许 Kubernetes 补默认值，但显式请求的安全字段必须一致。"""
        if isinstance(expected, dict):
            return isinstance(actual, dict) and all(
                (
                    key in actual
                    and (
                        KubernetesSandboxAdapter._contains(actual[key], value)
                        or (
                            key in {"cpu", "memory", "ephemeral-storage", "sizeLimit"}
                            and KubernetesSandboxAdapter._quantity(value) is not None
                            and KubernetesSandboxAdapter._quantity(actual[key])
                            == KubernetesSandboxAdapter._quantity(value)
                        )
                    )
                )
                or (
                    key not in actual
                    and value is False
                    and key in {"hostNetwork", "hostPID", "hostIPC"}
                )
                for key, value in expected.items()
            )
        if isinstance(expected, list):
            return (
                isinstance(actual, list)
                and len(actual) == len(expected)
                and all(
                    KubernetesSandboxAdapter._contains(a, b)
                    for a, b in zip(actual, expected, strict=True)
                )
            )
        return type(actual) is type(expected) and actual == expected

    def _verify_spec(self, resource: JsonObject, expected: JsonObject) -> None:
        if expected["kind"] == "NetworkPolicy":
            # Go omitempty 会省略空规则列表；缺省规则与 [] 都是拒绝，不能误拒真实 API。
            spec = resource.get("spec", {})
            if (
                not isinstance(spec, dict)
                or set(spec) - {"podSelector", "policyTypes", "ingress", "egress"}
                or spec.get("podSelector") != expected["spec"]["podSelector"]
                or spec.get("policyTypes") != expected["spec"]["policyTypes"]
                or spec.get("ingress", []) != []
                or spec.get("egress", []) != []
            ):
                # 选择器不得追加条件，否则可能不再选中本 Job 的 Pod。
                raise SandboxError("sandbox_resource_spec_conflict")
            return
        if not self._contains(resource.get("spec"), expected["spec"]):
            raise SandboxError("sandbox_resource_spec_conflict")
        if expected["kind"] == "Job":
            pod = resource["spec"]["template"]["spec"]
            container = pod["containers"][0]
            if (
                pod.get("initContainers")
                or pod.get("ephemeralContainers")
                or pod.get("imagePullSecrets")
                or container.get("envFrom")
                or container.get("lifecycle")
                or any("valueFrom" in item for item in container.get("env", []))
                or set(pod["volumes"][0]) != {"name", "emptyDir"}
                or container["securityContext"].get("capabilities", {}).get("add")
            ):
                raise SandboxError("sandbox_resource_spec_conflict")

    async def _ensure(
        self,
        path: str,
        body: JsonObject,
        handle: SandboxHandle,
        *,
        allow_create: bool = True,
    ) -> tuple[str, bool]:
        resource = await self._json("GET", f"{path}/{handle.name}", missing_ok=True)
        created_here = resource is None
        if resource is None:
            if not allow_create:
                raise SandboxUncertain("sandbox_creation_uncertain", handle=handle)
            try:
                resource = await self._json("POST", path, body=body)
            except SandboxError as exc:
                if exc.status_code is not None and exc.status_code < 500 and exc.status_code != 409:
                    raise
                # 响应丢失或冲突时，仅回读同名资源，不能再次 POST。
                created_here = False
                try:
                    resource = await self._json("GET", f"{path}/{handle.name}", missing_ok=True)
                except SandboxError:
                    raise SandboxUncertain("sandbox_creation_uncertain", handle=handle) from None
                if resource is None:
                    raise SandboxUncertain("sandbox_creation_uncertain", handle=handle) from None
        if resource is None:
            raise SandboxUncertain("sandbox_creation_uncertain", handle=handle)
        uid = self._identity(resource, handle)
        self._verify_spec(resource, body)
        return uid, created_here

    async def create(self, request: SandboxRequest) -> SandboxHandle:
        self._check_enabled()
        handle, policy, job = self._specifications(request)
        try:
            runtime = await self._json("GET", "/apis/node.k8s.io/v1/runtimeclasses/gvisor")
            if runtime is None or runtime.get("handler") != "runsc":
                raise SandboxError("gvisor_runtimeclass_required")
            policy_uid, fresh_policy = await self._ensure(self._policies, policy, handle)
            handle = replace(handle, policy_uid=policy_uid)
            # 已存在的策略也充当创建尝试标记：Job 404 不能证明从未创建，
            # 尤其响应丢失后 Job 可能已完成/被删除。宁可保留 UNKNOWN，也不重复执行。
            job_uid, _ = await self._ensure(
                self._jobs,
                job,
                handle,
                allow_create=fresh_policy,
            )
            return replace(handle, job_uid=job_uid)
        except SandboxError as exc:
            exc.handle = handle
            # 失败/不确定时保留默认拒绝策略，只能经显式清理流程移除。
            raise

    async def _job(self, handle: SandboxHandle) -> JsonObject | None:
        job = await self._json("GET", f"{self._jobs}/{handle.name}", missing_ok=True)
        if job is not None:
            self._identity(job, handle, handle.job_uid)
        return job

    async def _pod_list(self, handle: SandboxHandle, job_uid: str) -> list[JsonObject]:
        result = await self._json(
            "GET",
            self._pods,
            params={
                "labelSelector": f"{_LABEL}={handle.name}",
                "limit": "10",
            },
        )
        if result is None or not isinstance(result.get("items"), list):
            raise SandboxError("kubernetes_invalid_pod_list")
        if result.get("metadata", {}).get("continue"):
            raise SandboxError("sandbox_pod_list_incomplete")
        pods: list[JsonObject] = result["items"]
        for pod in pods:
            if not isinstance(pod, dict):
                raise SandboxError("kubernetes_invalid_pod_list")
            meta = pod.get("metadata", {})
            if meta.get("labels", {}).get(_LABEL) != handle.name or not any(
                ref.get("kind") == "Job"
                and ref.get("name") == handle.name
                and ref.get("uid") == job_uid
                and ref.get("controller") is True
                for ref in meta.get("ownerReferences", [])
            ):
                raise SandboxError("sandbox_pod_ownership_conflict")
            if not isinstance(meta.get("name"), str) or not re.fullmatch(
                r"[a-z0-9][a-z0-9.-]{0,252}", meta["name"]
            ):
                raise SandboxError("kubernetes_invalid_pod_name")
        return pods

    async def status(self, handle: SandboxHandle) -> SandboxStatus:
        self._check_enabled()
        job = await self._job(handle)
        if job is None:
            return SandboxStatus("missing")
        pods = await self._pod_list(handle, job["metadata"]["uid"])
        if job["metadata"].get("deletionTimestamp"):
            return SandboxStatus("cancelling")
        if len(pods) > 1:
            return SandboxStatus("unknown", reason="multiple_pods")
        failure_reason = None
        complete = False
        for condition in job.get("status", {}).get("conditions", []):
            if condition.get("type") == "Failed" and condition.get("status") == "True":
                failure_reason = (
                    "deadline_exceeded"
                    if condition.get("reason") == "DeadlineExceeded"
                    else "job_failed"
                )
            if condition.get("type") == "Complete" and condition.get("status") == "True":
                complete = True

        name = pods[0]["metadata"]["name"] if pods else None
        state: JsonObject = pods[0].get("status", {}) if pods else {}
        code = None
        for container in state.get("containerStatuses", []):
            terminated = container.get("state", {}).get("terminated")
            if container.get("name") == "command" and isinstance(terminated, dict):
                code = terminated.get("exitCode")
                if type(code) is not int:
                    raise SandboxError("kubernetes_invalid_exit_code")
                break

        # Job 失败判定不能抹掉已验证 Pod 的命令退出证据，也不能被单个 code=0 覆盖。
        if failure_reason is not None:
            return SandboxStatus("failed", name, code, failure_reason)
        if code is not None:
            return SandboxStatus("succeeded" if code == 0 else "failed", name, code)
        if complete:
            # Job 控制器已终态但 Pod 被 GC/终止证据缺失，不再 pending，也不伪造成功。
            return SandboxStatus("unknown", name, reason="job_complete_exit_code_unavailable")
        if not pods:
            return SandboxStatus("pending")
        phases = {"Pending": "pending", "Running": "running", "Failed": "failed"}
        # 没有命令终止记录，不能仅凭 Pod 的 Succeeded 字段宣称命令成功。
        pod_phase = state.get("phase")
        phase = phases.get(pod_phase, "unknown") if isinstance(pod_phase, str) else "unknown"
        return SandboxStatus(phase, name)

    async def logs(self, handle: SandboxHandle, *, limit_bytes: int = 65536) -> SandboxLogs:
        self._check_enabled()
        if type(limit_bytes) is not int or not 1 <= limit_bytes <= 1048576:
            raise ValueError("log limit must be between 1 and 1048576 bytes")
        job = await self._job(handle)
        if job is None:
            raise SandboxError("sandbox_job_missing")
        pods = await self._pod_list(handle, job["metadata"]["uid"])
        if len(pods) != 1:
            raise SandboxError("sandbox_log_pod_unavailable")
        pod_name = pods[0]["metadata"]["name"]
        data = bytearray()
        try:
            async with (
                asyncio.timeout(self._settings.request_timeout_seconds),
                self._client.stream(
                    "GET",
                    f"{self._pods}/{pod_name}/log",
                    params={
                        "container": "command",
                        "follow": "false",
                        "timestamps": "false",
                        "limitBytes": str(limit_bytes),
                    },
                    headers={"Accept-Encoding": "identity"},
                    follow_redirects=False,
                    timeout=self._settings.request_timeout_seconds,
                ) as response,
            ):
                if response.status_code != 200:
                    raise SandboxError("kubernetes_log_error", status_code=response.status_code)
                if response.headers.get("content-encoding", "identity") != "identity":
                    raise SandboxError("kubernetes_encoded_response")
                async for chunk in response.aiter_bytes(chunk_size=min(8192, limit_bytes)):
                    data.extend(chunk[: limit_bytes - len(data)])
                    if len(data) >= limit_bytes:
                        break
        except (httpx.HTTPError, TimeoutError):
            raise SandboxError("kubernetes_transport_error") from None
        # 达到上限即保守标为截断；即使服务端恰好返回该字节数也不宣称完整。
        return SandboxLogs(
            data.decode("utf-8", errors="replace"), len(data) >= limit_bytes, len(data)
        )

    async def cancel(self, handle: SandboxHandle) -> CleanupResult:
        """UID 前置条件的前台删除，调用者须保存结果/异常 handle 后继续对账。

        DELETE 成功不等于进程已终止。观察到 Job 和全部 Pod 消失后才删除策略。
        不启用 Job TTL，避免提前丢失用于对账的对象身份。
        """
        self._check_enabled()
        try:
            job = await self._job(handle)
            if job is not None:
                # 首次 UNKNOWN 回读到 Job 后立即携带可信 UID，后续失败也不能丢失。
                handle = replace(handle, job_uid=job["metadata"]["uid"])
                await self._json(
                    "DELETE",
                    f"{self._jobs}/{handle.name}",
                    body={
                        "apiVersion": "v1",
                        "kind": "DeleteOptions",
                        "propagationPolicy": "Foreground",
                        "gracePeriodSeconds": 5,
                        "preconditions": {"uid": handle.job_uid},
                    },
                    missing_ok=True,
                )
                if await self._job(handle) is not None:
                    return CleanupResult(False, handle)
            if handle.job_uid is None:
                # 从未确认 Job UID，仍不能撤掉可能在运行实例的网络拒绝。
                raise SandboxUncertain("sandbox_cleanup_requires_job_identity", handle=handle)
            if await self._pod_list(handle, handle.job_uid):
                return CleanupResult(False, handle)
            policy = await self._json("GET", f"{self._policies}/{handle.name}", missing_ok=True)
            if policy is not None:
                policy_uid = self._identity(policy, handle, handle.policy_uid)
                handle = replace(handle, policy_uid=policy_uid)
                await self._json(
                    "DELETE",
                    f"{self._policies}/{handle.name}",
                    body={
                        "apiVersion": "v1",
                        "kind": "DeleteOptions",
                        "preconditions": {"uid": policy_uid},
                    },
                    missing_ok=True,
                )
                remaining = await self._json(
                    "GET", f"{self._policies}/{handle.name}", missing_ok=True
                )
                if remaining is not None:
                    self._identity(remaining, handle, policy_uid)
                    return CleanupResult(False, handle)
            return CleanupResult(True, handle)
        except SandboxError as exc:
            exc.handle = handle
            raise
        except asyncio.CancelledError:
            # 使用 CancelledError 子类保留 task.cancelled() 语义，不吞取消/不返回成功。
            raise SandboxCancelled(handle) from None
