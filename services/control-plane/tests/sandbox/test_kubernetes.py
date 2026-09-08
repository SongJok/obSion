"""仅验证 HTTP 协议模拟，不连接集群、发现凭据或宣称真实隔离。"""

from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import replace

import httpx
import pytest

from obsion.sandbox.contracts import SandboxCommand, SandboxLimits, SandboxRequest
from obsion.sandbox.kubernetes import (
    KubernetesSandboxAdapter,
    KubernetesSandboxSettings,
    SandboxCancelled,
    SandboxError,
    SandboxUncertain,
)

IMAGE = "registry.invalid/obsion/sandbox@sha256:" + "a" * 64
REQUEST = SandboxRequest("1" * 32, SandboxCommand("/usr/local/bin/python3", ("-c", "print(42)")))
LABEL = "obsion.io/sandbox"


class LogStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.consumed = 0
        self.closed = False

    async def __aiter__(self):
        for _ in range(100):
            self.consumed += 1
            yield b"0123456789"

    async def aclose(self):
        self.closed = True


class KubernetesProtocol:
    """HTTP 边界上的有状态服务端替身，adapter 自身使用真实实现。"""

    def __init__(self) -> None:
        self.resources = {}
        self.requests = []
        self.pods = []
        self.runtime_handler = "runsc"
        self.lost_job_response = False
        self.persist_lost_job = True
        self.job_delete_pending = False
        self.policy_delete_pending = False
        self.log_stream = LogStream()
        self.job_error = None
        self.mutate_job = None
        self.pod_continue = ""

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if "runtimeclasses" in path:
            return httpx.Response(
                200, json={"metadata": {"name": "gvisor"}, "handler": self.runtime_handler}
            )
        if path.endswith("/log"):
            return httpx.Response(200, stream=self.log_stream)
        if path.endswith("/pods"):
            return httpx.Response(
                200, json={"items": self.pods, "metadata": {"continue": self.pod_continue}}
            )
        if request.method == "GET":
            return (
                httpx.Response(200, json=self.resources[path])
                if path in self.resources
                else httpx.Response(404)
            )
        if request.method == "POST":
            body = json.loads(request.content)
            full_path = f"{path}/{body['metadata']['name']}"
            is_job = path.endswith("/jobs")
            if is_job and self.job_error:
                return httpx.Response(self.job_error, json={"message": "upstream-secret"})
            if full_path in self.resources:
                return httpx.Response(409)
            body["metadata"]["uid"] = "job-uid" if is_job else "policy-uid"
            if is_job and self.mutate_job:
                self.mutate_job(body)
            if is_job:
                # 模拟真实 Go API 对非指针 bool 的 omitempty，以及服务器补充默认字段。
                pod = body["spec"]["template"]["spec"]
                for key in ("hostNetwork", "hostPID", "hostIPC"):
                    if pod[key] is False:
                        del pod[key]
                pod["dnsPolicy"] = "ClusterFirst"
                pod["serviceAccountName"] = "default"
                pod["containers"][0]["terminationMessagePath"] = "/dev/termination-log"
                # 真 API 会规范化资源数量字符串，不仅原样回显请求 JSON。
                canonical = {"2000m": "2", "4096Mi": "4Gi", "10240Mi": "10Gi"}
                for resources in pod["containers"][0]["resources"].values():
                    for key, value in resources.items():
                        resources[key] = canonical.get(value, value)
                volume = pod["volumes"][0]["emptyDir"]
                volume["sizeLimit"] = canonical.get(volume["sizeLimit"], volume["sizeLimit"])
            else:
                # 空 ingress/egress 列表在 NetworkPolicy JSON 响应中可能被省略。
                del body["spec"]["ingress"]
                del body["spec"]["egress"]
            if not (is_job and self.lost_job_response and not self.persist_lost_job):
                self.resources[full_path] = body
            if is_job and self.lost_job_response:
                raise httpx.ReadTimeout("upstream-secret", request=request)
            return httpx.Response(201, json=body)
        if request.method == "DELETE":
            if path not in self.resources:
                return httpx.Response(404)
            body = json.loads(request.content)
            assert body["preconditions"]["uid"] == self.resources[path]["metadata"]["uid"]
            if "/jobs/" in path and self.job_delete_pending:
                self.resources[path]["metadata"]["deletionTimestamp"] = "2026-09-06T00:00:00Z"
            elif "/networkpolicies/" in path and self.policy_delete_pending:
                pass
            else:
                del self.resources[path]
            return httpx.Response(200, json={"kind": "Status", "status": "Success"})
        raise AssertionError(f"Unexpected method {request.method}")

    def pod(self, *, phase="Running", exit_code=None):
        pod = {
            "metadata": {
                "name": f"obsion-sb-{REQUEST.sandbox_id}-pod",
                "uid": "pod-uid",
                "labels": {LABEL: f"obsion-sb-{REQUEST.sandbox_id}"},
                "ownerReferences": [
                    {
                        "kind": "Job",
                        "name": f"obsion-sb-{REQUEST.sandbox_id}",
                        "uid": "job-uid",
                        "controller": True,
                    }
                ],
            },
            "status": {"phase": phase},
        }
        if exit_code is not None:
            pod["status"]["containerStatuses"] = [
                {
                    "name": "command",
                    "state": {
                        "terminated": {"exitCode": exit_code},
                    },
                }
            ]
        self.pods = [pod]
        return pod


@pytest.fixture
async def setup():
    server = KubernetesProtocol()
    # 仅为合成测试标记，禁止复制到 Job 或命令契约。
    async with httpx.AsyncClient(
        base_url="https://kubernetes.invalid/",
        transport=httpx.MockTransport(server),
        headers={"Authorization": "Bearer synthetic-test-marker"},
        trust_env=False,
    ) as client:
        settings = KubernetesSandboxSettings(
            "sandbox-tests", IMAGE, enabled=True, environment="test"
        )
        yield server, KubernetesSandboxAdapter(client, settings), client


async def test_real_http_contract_security_and_no_credentials(setup):
    server, adapter, _ = setup
    handle = await adapter.create(REQUEST)
    assert handle.job_uid == "job-uid"
    assert handle.policy_uid == "policy-uid"
    posts = [r for r in server.requests if r.method == "POST"]
    assert (
        posts[0].url.path == "/apis/networking.k8s.io/v1/namespaces/sandbox-tests/networkpolicies"
    )
    assert posts[1].url.path == "/apis/batch/v1/namespaces/sandbox-tests/jobs"
    policy, job = (json.loads(r.content) for r in posts)
    assert policy["spec"] == {
        "podSelector": {"matchLabels": {LABEL: handle.name}},
        "policyTypes": ["Ingress", "Egress"],
        "ingress": [],
        "egress": [],
    }
    pod = job["spec"]["template"]["spec"]
    assert pod["runtimeClassName"] == "gvisor"
    assert pod["automountServiceAccountToken"] is False
    assert pod["enableServiceLinks"] is False
    assert all(pod[key] is False for key in ("hostNetwork", "hostPID", "hostIPC"))
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert pod["securityContext"]["runAsUser"] == 65532
    assert pod["volumes"] == [{"name": "workspace", "emptyDir": {"sizeLimit": "10240Mi"}}]
    container = pod["containers"][0]
    assert container["image"] == IMAGE
    assert container["command"] == [
        "/usr/local/bin/python3",
        "-I",
        "-B",
        "-m",
        "obsion.sandbox.entrypoint",
    ]
    assert json.loads(container["args"][1]) == REQUEST.command.to_wire()
    security = container["securityContext"]
    assert security["readOnlyRootFilesystem"] is True
    assert security["allowPrivilegeEscalation"] is security["privileged"] is False
    assert security["capabilities"] == {"drop": ["ALL"]}
    assert security["seccompProfile"] == {"type": "RuntimeDefault"}
    assert (
        container["resources"]["requests"]
        == container["resources"]["limits"]
        == {
            "cpu": "2000m",
            "memory": "4096Mi",
            "ephemeral-storage": "10240Mi",
        }
    )
    assert job["spec"]["backoffLimit"] == 0
    assert job["spec"]["activeDeadlineSeconds"] == 150
    assert "ttlSecondsAfterFinished" not in job["spec"]
    serialized = json.dumps(job) + repr(handle) + repr(REQUEST)
    for forbidden in ("synthetic-test-marker", "hostPath", "secretKeyRef", "envFrom", "kubeconfig"):
        assert forbidden not in serialized
    assert "Authorization" in posts[1].headers  # 只有可信 client 携带认证信息。


async def test_default_disabled_and_production_blocked_without_http(setup):
    server, _, client = setup
    for settings in (
        KubernetesSandboxSettings("sandbox-tests", IMAGE),
        KubernetesSandboxSettings("sandbox-tests", IMAGE, enabled=True),
        KubernetesSandboxSettings("sandbox-tests", IMAGE, environment="test"),
    ):
        adapter = KubernetesSandboxAdapter(client, settings)
        with pytest.raises(SandboxError, match="sandbox_disabled"):
            await adapter.create(REQUEST)
    assert not server.requests


@pytest.mark.parametrize(
    "image", ["python:latest", "python:3.12", "python@sha256:123", "https://x@y"]
)
def test_unpinned_image_rejected(image):
    with pytest.raises(ValueError, match="sha256"):
        KubernetesSandboxSettings("sandbox-tests", image)


@pytest.mark.parametrize("namespace", ["../default", "default?x", "UPPER", "", "a" * 64])
def test_invalid_namespace_rejected(namespace):
    with pytest.raises(ValueError, match="DNS"):
        KubernetesSandboxSettings(namespace, IMAGE)


@pytest.mark.parametrize(
    "url",
    [
        "http://kubernetes.invalid",
        "https://u:p@kubernetes.invalid/",
        "https://kubernetes.invalid/proxy/",
        "https://kubernetes.invalid/?a=1",
    ],
)
async def test_client_requires_trusted_https_origin(url):
    async with httpx.AsyncClient(
        base_url=url, transport=httpx.MockTransport(lambda r: None)
    ) as client:
        with pytest.raises(ValueError, match="HTTPS"):
            KubernetesSandboxAdapter(client, KubernetesSandboxSettings("sandbox-tests", IMAGE))


async def test_missing_gvisor_never_creates_policy_or_job(setup):
    server, adapter, _ = setup
    server.runtime_handler = "runc"
    with pytest.raises(SandboxError, match="gvisor_runtimeclass_required"):
        await adapter.create(REQUEST)
    assert all(r.method == "GET" for r in server.requests)


async def test_idempotent_create_and_per_job_workspace(setup):
    server, adapter, _ = setup
    first = await adapter.create(REQUEST)
    assert await adapter.create(REQUEST) == first
    assert len([r for r in server.requests if r.method == "POST"]) == 2
    second = await adapter.create(replace(REQUEST, sandbox_id="2" * 32))
    assert first.name != second.name
    jobs = [body for body in server.resources.values() if body["kind"] == "Job"]
    assert len(jobs) == 2
    assert (
        jobs[0]["spec"]["template"]["metadata"]["labels"]
        != jobs[1]["spec"]["template"]["metadata"]["labels"]
    )
    assert all(job["spec"]["template"]["spec"]["volumes"][0].get("emptyDir") for job in jobs)


async def test_same_id_changed_command_fails_closed(setup):
    server, adapter, _ = setup
    await adapter.create(REQUEST)
    changed = replace(REQUEST, command=SandboxCommand("/bin/false"))
    with pytest.raises(SandboxError, match="identity_conflict"):
        await adapter.create(changed)
    assert len([r for r in server.requests if r.method == "POST"]) == 2


async def test_lost_create_response_reconciles_without_second_post(setup):
    server, adapter, _ = setup
    server.lost_job_response = True
    handle = await adapter.create(REQUEST)
    assert handle.job_uid == "job-uid"
    assert (
        len([r for r in server.requests if r.method == "POST" and r.url.path.endswith("/jobs")])
        == 1
    )
    assert server.requests[-1].method == "GET"


async def test_ambiguous_create_returns_uncertain_and_preserves_network_deny(setup):
    server, adapter, _ = setup
    server.lost_job_response = True
    server.persist_lost_job = False
    with pytest.raises(SandboxUncertain, match="creation_uncertain") as caught:
        await adapter.create(REQUEST)
    assert caught.value.handle.policy_uid == "policy-uid"
    assert "upstream-secret" not in str(caught.value)
    assert all(resource["kind"] == "NetworkPolicy" for resource in server.resources.values())
    assert not any(r.method == "DELETE" for r in server.requests)
    # 新 adapter 模拟控制面重启：既有策略是尝试标记，Job 404 不能导致重复 POST。
    restarted = KubernetesSandboxAdapter(adapter._client, adapter._settings)
    with pytest.raises(SandboxUncertain, match="creation_uncertain"):
        await restarted.create(REQUEST)
    assert (
        len([r for r in server.requests if r.method == "POST" and r.url.path.endswith("/jobs")])
        == 1
    )
    with pytest.raises(SandboxUncertain, match="requires_job_identity"):
        await adapter.cancel(caught.value.handle)
    assert not any(r.method == "DELETE" for r in server.requests)


async def test_definite_forbidden_response_sanitized_without_job_replay(setup):
    server, adapter, _ = setup
    server.job_error = 403
    with pytest.raises(SandboxError) as caught:
        await adapter.create(REQUEST)
    assert caught.value.status_code == 403
    assert str(caught.value) == "kubernetes_http_error"
    assert caught.value.handle.policy_uid == "policy-uid"
    assert not any(r.method == "DELETE" for r in server.requests)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda job: job["spec"]["template"]["spec"].update(runtimeClassName="runc"),
        lambda job: job["spec"]["template"]["spec"].update(hostNetwork=True),
        lambda job: job["spec"]["template"]["spec"]["containers"][0]["resources"]["limits"].update(
            memory="8Gi"
        ),
        lambda job: job["spec"]["template"]["spec"].update(initContainers=[{"name": "injected"}]),
        lambda job: job["spec"]["template"]["spec"]["volumes"][0].update(hostPath={"path": "/"}),
        lambda job: job["spec"]["template"]["spec"]["containers"][0]["securityContext"][
            "capabilities"
        ].update(add=["SYS_ADMIN"]),
    ],
)
async def test_admission_mutation_of_security_fields_rejected(setup, mutation):
    server, adapter, _ = setup
    server.mutate_job = mutation
    with pytest.raises(SandboxError, match="spec_conflict"):
        await adapter.create(REQUEST)


@pytest.mark.parametrize(
    "phase,exit_code,expected",
    [
        ("Pending", None, "pending"),
        ("Running", None, "running"),
        ("Succeeded", 0, "succeeded"),
        ("Failed", 2, "failed"),
        ("Failed", 124, "failed"),
        ("Succeeded", None, "unknown"),
    ],
)
async def test_pod_status_and_command_exit_code(setup, phase, exit_code, expected):
    server, adapter, _ = setup
    handle = await adapter.create(REQUEST)
    server.pod(phase=phase, exit_code=exit_code)
    status = await adapter.status(handle)
    assert status.phase == expected
    assert status.exit_code == exit_code
    assert "labelSelector" in server.requests[-1].url.params


async def test_job_deadline_without_pods_is_failed(setup):
    server, adapter, _ = setup
    handle = await adapter.create(REQUEST)
    job = next(r for r in server.resources.values() if r["kind"] == "Job")
    job["status"] = {
        "conditions": [{"type": "Failed", "status": "True", "reason": "DeadlineExceeded"}]
    }
    status = await adapter.status(handle)
    assert status.phase == "failed"
    assert status.reason == "deadline_exceeded"


async def test_unowned_pod_never_returned_or_read(setup):
    server, adapter, _ = setup
    handle = await adapter.create(REQUEST)
    server.pod()["metadata"]["ownerReferences"][0]["uid"] = "other-job"
    for operation in (adapter.status, adapter.logs, adapter.cancel):
        with pytest.raises(SandboxError, match="ownership_conflict"):
            await operation(handle)
    assert not any(r.url.path.endswith("/log") for r in server.requests)
    assert any(r["kind"] == "NetworkPolicy" for r in server.resources.values())


async def test_pod_list_pagination_does_not_claim_clean(setup):
    server, adapter, _ = setup
    handle = await adapter.create(REQUEST)
    server.pod_continue = "opaque-next-page"
    with pytest.raises(SandboxError, match="list_incomplete"):
        await adapter.cancel(handle)
    assert any(r["kind"] == "NetworkPolicy" for r in server.resources.values())


async def test_logs_are_bounded_locally_and_at_protocol_boundary(setup):
    server, adapter, _ = setup
    handle = await adapter.create(REQUEST)
    server.pod()
    logs = await adapter.logs(handle, limit_bytes=25)
    assert logs.text == "0123456789012345678901234"
    assert logs.bytes_returned == 25
    assert logs.truncated is True
    assert server.log_stream.consumed == 3
    assert server.log_stream.closed is True
    assert dict(server.requests[-1].url.params) == {
        "container": "command",
        "follow": "false",
        "timestamps": "false",
        "limitBytes": "25",
    }


async def test_log_limits_validated_before_http(setup):
    server, adapter, _ = setup
    handle = await adapter.create(REQUEST)
    before = len(server.requests)
    for limit in (0, -1, 1048577, True):
        with pytest.raises(ValueError):
            await adapter.logs(handle, limit_bytes=limit)
    assert len(server.requests) == before


async def test_cancel_waits_for_job_and_pods_before_removing_policy(setup):
    server, adapter, _ = setup
    handle = await adapter.create(REQUEST)
    server.pod()
    server.job_delete_pending = True
    assert not (await adapter.cancel(handle)).complete
    assert (await adapter.status(handle)).phase == "cancelling"
    assert not any(
        r.method == "DELETE" and "networkpolicies" in r.url.path for r in server.requests
    )
    server.job_delete_pending = False
    assert not (await adapter.cancel(handle)).complete  # Job 已消失，Pod 仍在。
    assert not any(
        r.method == "DELETE" and "networkpolicies" in r.url.path for r in server.requests
    )
    server.pods = []
    server.policy_delete_pending = True
    assert not (await adapter.cancel(handle)).complete
    server.policy_delete_pending = False
    assert (await adapter.cancel(handle)).complete
    assert (await adapter.cancel(handle)).complete  # 重复取消不重复产生副作用。
    assert not server.resources
    delete = next(r for r in server.requests if r.method == "DELETE")
    assert json.loads(delete.content)["propagationPolicy"] == "Foreground"
    assert json.loads(delete.content)["preconditions"] == {"uid": "job-uid"}


async def test_recreated_job_uid_cannot_be_deleted(setup):
    server, adapter, _ = setup
    handle = await adapter.create(REQUEST)
    job = next(r for r in server.resources.values() if r["kind"] == "Job")
    job["metadata"]["uid"] = "recreated-job"
    with pytest.raises(SandboxError, match="identity_conflict"):
        await adapter.cancel(handle)
    assert not any(r.method == "DELETE" for r in server.requests)


async def test_recreated_policy_uid_cannot_be_deleted(setup):
    server, adapter, _ = setup
    handle = await adapter.create(REQUEST)
    policy = next(r for r in server.resources.values() if r["kind"] == "NetworkPolicy")
    policy["metadata"]["uid"] = "recreated-policy"
    with pytest.raises(SandboxError, match="identity_conflict"):
        await adapter.cancel(handle)
    assert not any(
        r.method == "DELETE" and "networkpolicies" in r.url.path for r in server.requests
    )


async def test_redirect_never_forwards_credentials():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://other.invalid/token"})

    async with httpx.AsyncClient(
        base_url="https://kubernetes.invalid/",
        follow_redirects=True,
        transport=httpx.MockTransport(respond),
    ) as client:
        adapter = KubernetesSandboxAdapter(
            client,
            KubernetesSandboxSettings(
                "sandbox-tests",
                IMAGE,
                True,
                "test",
            ),
        )
        with pytest.raises(SandboxError) as caught:
            await adapter.create(REQUEST)
        assert caught.value.status_code == 302
    assert len(requests) == 1


@pytest.mark.parametrize(
    "response,code",
    [
        (httpx.Response(200, content=b"not-json-secret"), "invalid_json"),
        (httpx.Response(200, json=[]), "invalid_json"),
        (httpx.Response(200, content=b"x" * 262145), "response_too_large"),
    ],
)
async def test_malformed_and_large_api_responses_fail_closed(response, code):
    async with httpx.AsyncClient(
        base_url="https://kubernetes.invalid/",
        transport=httpx.MockTransport(lambda request: copy.copy(response)),
    ) as client:
        adapter = KubernetesSandboxAdapter(
            client,
            KubernetesSandboxSettings(
                "sandbox-tests",
                IMAGE,
                True,
                "test",
            ),
        )
        with pytest.raises(SandboxError, match=code):
            await adapter.create(REQUEST)


@pytest.mark.parametrize(
    "limits",
    [
        {"cpu_millis": 2001},
        {"memory_mib": 4097},
        {"disk_mib": 10241},
        {"processes": 129},
        {"timeout_seconds": 601},
        {"processes": True},
        {"memory_mib": 0},
        {"timeout_seconds": 0.5},
    ],
)
def test_limits_cannot_expand_product_ceiling(limits):
    with pytest.raises(ValueError):
        SandboxLimits(**limits)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"executable": "sh"},
        {"executable": "/bin/sh\x00"},
        {"cwd": "/workspace/../etc"},
        {"cwd": "/workspace2"},
        {"cwd": "/workspace//x"},
        {"cwd": "/workspace/./x"},
        {"argv": ("bad\x00",)},
        {"argv": ["x"]},
        {"argv": ("x" * 32769,)},
    ],
)
def test_command_contract_rejects_ambiguous_or_unbounded_values(kwargs):
    with pytest.raises(ValueError):
        SandboxCommand(**{"executable": "/bin/sh", **kwargs})


async def test_network_policy_selector_cannot_be_mutated(setup):
    server, adapter, _ = setup
    await adapter.create(REQUEST)
    policy = next(r for r in server.resources.values() if r["kind"] == "NetworkPolicy")
    policy["spec"]["podSelector"]["matchLabels"]["extra-selector"] = "never-matches"
    with pytest.raises(SandboxError, match="spec_conflict"):
        await adapter.create(REQUEST)


async def test_drip_feed_log_has_total_deadline_and_closes_stream(setup):
    server, adapter, client = setup
    handle = await adapter.create(REQUEST)
    server.pod()

    class SlowStream(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            while True:
                await asyncio.sleep(0.05)
                yield b"x"

        async def aclose(self):
            self.closed = True

    stream = SlowStream()
    server.log_stream = stream
    bounded = KubernetesSandboxAdapter(
        client, replace(adapter._settings, request_timeout_seconds=1)
    )
    with pytest.raises(SandboxError, match="transport_error"):
        await bounded.logs(handle)
    assert stream.closed


async def test_small_log_is_not_marked_truncated(setup):
    server, adapter, _ = setup
    handle = await adapter.create(REQUEST)
    server.pod()
    logs = await adapter.logs(handle, limit_bytes=2000)
    assert logs.bytes_returned == 1000
    assert logs.truncated is False


async def test_async_task_cancellation_reconciles_without_duplicate_create(setup):
    server, _, _ = setup
    attempted = asyncio.Event()

    async def respond(request):
        if request.method == "POST" and request.url.path.endswith("/jobs"):
            server(request)
            attempted.set()
            await asyncio.Event().wait()
        return server(request)

    async with httpx.AsyncClient(
        base_url="https://kubernetes.invalid/", transport=httpx.MockTransport(respond)
    ) as client:
        settings = KubernetesSandboxSettings("sandbox-tests", IMAGE, True, "test")
        adapter = KubernetesSandboxAdapter(client, settings)
        task = asyncio.create_task(adapter.create(REQUEST))
        await asyncio.wait_for(attempted.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert any(r["kind"] == "NetworkPolicy" for r in server.resources.values())
        restarted = KubernetesSandboxAdapter(client, settings)
        handle = await restarted.create(REQUEST)
        assert handle.job_uid == "job-uid"
        assert (
            len([r for r in server.requests if r.method == "POST" and r.url.path.endswith("/jobs")])
            == 1
        )
        assert (await restarted.cancel(handle)).complete


@pytest.mark.parametrize(
    "reason,code", [("BackoffLimitExceeded", 2), ("DeadlineExceeded", 124), ("DeadlineExceeded", 0)]
)
async def test_failed_job_keeps_owned_pod_and_real_exit_code(setup, reason, code):
    server, adapter, _ = setup
    handle = await adapter.create(REQUEST)
    pod = server.pod(phase="Failed", exit_code=code)
    job = next(r for r in server.resources.values() if r["kind"] == "Job")
    job["status"] = {"conditions": [{"type": "Failed", "status": "True", "reason": reason}]}
    status = await adapter.status(handle)
    assert status.phase == "failed"
    assert status.pod_name == pod["metadata"]["name"]
    assert status.exit_code == code
    assert status.reason == ("deadline_exceeded" if reason == "DeadlineExceeded" else "job_failed")


@pytest.mark.parametrize("pod_present", [False, True])
async def test_complete_job_without_command_evidence_is_unknown_not_pending(setup, pod_present):
    server, adapter, _ = setup
    handle = await adapter.create(REQUEST)
    if pod_present:
        server.pod(phase="Succeeded")
    job = next(r for r in server.resources.values() if r["kind"] == "Job")
    job["status"] = {"conditions": [{"type": "Complete", "status": "True"}]}
    status = await adapter.status(handle)
    assert status.phase == "unknown"
    assert status.exit_code is None
    assert status.reason == "job_complete_exit_code_unavailable"


async def test_unknown_cleanup_retains_discovered_uid_across_rounds(setup):
    server, adapter, client = setup
    created = await adapter.create(REQUEST)
    unknown = replace(created, job_uid=None)
    server.pod()
    first = await adapter.cancel(unknown)
    assert first.complete is False
    assert first.handle.job_uid == "job-uid"
    assert not any(r["kind"] == "Job" for r in server.resources.values())
    assert any(r["kind"] == "NetworkPolicy" for r in server.resources.values())
    server.pods = []
    restarted = KubernetesSandboxAdapter(client, adapter._settings)
    assert (await restarted.cancel(first.handle)).complete is True
    assert not server.resources


@pytest.mark.parametrize("pending", ["job", "policy"])
async def test_cleanup_pending_result_keeps_all_discovered_identities(setup, pending):
    server, adapter, _ = setup
    unknown = replace(await adapter.create(REQUEST), job_uid=None, policy_uid=None)
    server.job_delete_pending = pending == "job"
    server.policy_delete_pending = pending == "policy"
    result = await adapter.cancel(unknown)
    assert not result.complete
    assert result.handle.job_uid == "job-uid"
    if pending == "policy":
        assert result.handle.policy_uid == "policy-uid"
    server.job_delete_pending = server.policy_delete_pending = False
    assert (await adapter.cancel(result.handle)).complete


@pytest.mark.parametrize("failure_stage", ["delete_response", "get_after_delete"])
async def test_cleanup_transport_failure_keeps_discovered_job_uid(setup, failure_stage):
    server, adapter, _ = setup
    unknown = replace(await adapter.create(REQUEST), job_uid=None)
    deleted = False
    lost = False

    def respond(request):
        nonlocal deleted, lost
        is_job = "/jobs/" in request.url.path
        response = server(request)
        if is_job and request.method == "DELETE":
            deleted = True
            if failure_stage == "delete_response":
                lost = True
                raise httpx.ReadTimeout("lost-delete-response", request=request)
        elif is_job and request.method == "GET" and deleted and not lost:
            lost = True
            raise httpx.ReadTimeout("lost-get-response", request=request)
        return response

    async with httpx.AsyncClient(
        base_url="https://kubernetes.invalid/", transport=httpx.MockTransport(respond)
    ) as client:
        failing = KubernetesSandboxAdapter(client, adapter._settings)
        with pytest.raises(SandboxError, match="transport_error") as caught:
            await failing.cancel(unknown)
        assert caught.value.handle.job_uid == "job-uid"
        assert (await adapter.cancel(caught.value.handle)).complete


async def test_cleanup_task_cancellation_keeps_discovered_uid_without_swallowing_cancel(setup):
    server, adapter, _ = setup
    unknown = replace(await adapter.create(REQUEST), job_uid=None)
    deleted = asyncio.Event()

    async def respond(request):
        response = server(request)
        if request.method == "DELETE" and "/jobs/" in request.url.path:
            deleted.set()
            await asyncio.Event().wait()
        return response

    async with httpx.AsyncClient(
        base_url="https://kubernetes.invalid/", transport=httpx.MockTransport(respond)
    ) as client:
        cancellable = KubernetesSandboxAdapter(client, adapter._settings)
        task = asyncio.create_task(cancellable.cancel(unknown))
        await asyncio.wait_for(deleted.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError) as caught:
            await task
        assert isinstance(caught.value, SandboxCancelled)
        assert task.cancelled()
        assert caught.value.handle.job_uid == "job-uid"
        assert any(r["kind"] == "NetworkPolicy" for r in server.resources.values())
        restarted = KubernetesSandboxAdapter(client, adapter._settings)
        assert (await restarted.cancel(caught.value.handle)).complete


async def test_cleanup_error_retains_uid_after_job_disappears(setup):
    server, adapter, _ = setup
    unknown = replace(await adapter.create(REQUEST), job_uid=None)
    server.pod_continue = "next-page"
    with pytest.raises(SandboxError, match="list_incomplete") as caught:
        await adapter.cancel(unknown)
    assert caught.value.handle.job_uid == "job-uid"
    server.pod_continue = ""
    assert (await adapter.cancel(caught.value.handle)).complete


@pytest.mark.parametrize(
    "argument", ["中" * 10922, '"' * 32000, "\x01" * 10000], ids=["unicode", "quotes", "control"]
)
async def test_serialized_command_has_shared_entrypoint_size_contract(setup, argument):
    server, adapter, _ = setup
    command = SandboxCommand("/bin/printf", (argument,))
    await adapter.create(replace(REQUEST, command=command))
    post = next(r for r in server.requests if r.method == "POST" and r.url.path.endswith("/jobs"))
    wire = json.loads(post.content)["spec"]["template"]["spec"]["containers"][0]["args"][1]
    assert len(wire.encode("utf-8")) <= 65536
    assert SandboxCommand.from_json(wire) == command


@pytest.mark.parametrize(
    "argument",
    ['"' * 32768, "\x01" * 11000, "#" + "\n" * 32765],
    ids=["quotes", "control", "newlines"],
)
async def test_serialization_expansion_rejected_before_http(setup, argument):
    server, _, _ = setup
    with pytest.raises(ValueError, match="serialized command"):
        SandboxCommand("/bin/printf", (argument,))
    assert not server.requests


def test_wire_contract_round_trip_and_rejects_environment_override():
    command = SandboxCommand("/bin/sh", ("-c", "printf '%s' '$HOME;literal'"))
    assert SandboxCommand.from_wire(command.to_wire()) == command
    with pytest.raises(ValueError):
        SandboxCommand.from_wire({**command.to_wire(), "env": {"KUBECONFIG": "secret"}})
    # 可显式运行脚本；shell 字符串过滤不是隔离边界。
    assert command.argv[1].endswith("'$HOME;literal'")
