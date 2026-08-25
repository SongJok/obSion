import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from io import BytesIO
from typing import Protocol
from urllib.parse import urlparse

from minio import Minio
from minio.error import S3Error

from obsion.common.errors import ObsionError
from obsion.config import Settings


@dataclass(frozen=True, slots=True)
class StoredObject:
    data: bytes
    media_type: str
    metadata: Mapping[str, str]


class ObjectStore(Protocol):
    async def put(
        self,
        key: str,
        data: bytes,
        *,
        media_type: str,
        metadata: Mapping[str, str] | None = None,
    ) -> None: ...

    async def get(self, key: str) -> StoredObject: ...

    async def delete(self, key: str) -> None: ...


class InMemoryObjectStore:
    def __init__(self) -> None:
        self._objects: dict[str, StoredObject] = {}
        self._lock = asyncio.Lock()

    async def put(
        self,
        key: str,
        data: bytes,
        *,
        media_type: str,
        metadata: Mapping[str, str] | None = None,
    ) -> None:
        async with self._lock:
            self._objects[key] = StoredObject(data, media_type, dict(metadata or {}))

    async def get(self, key: str) -> StoredObject:
        async with self._lock:
            item = self._objects.get(key)
        if item is None:
            raise ObsionError(
                "artifact_content_missing",
                "Artifact content is not available",
                status_code=404,
            )
        return item

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._objects.pop(key, None)


class MinioObjectStore:
    def __init__(self, settings: Settings) -> None:
        endpoint = urlparse(str(settings.object_store_endpoint))
        if endpoint.hostname is None or endpoint.path not in {"", "/"}:
            raise ValueError("Object-store endpoint must contain only scheme, host, and port")
        authority = endpoint.hostname
        if endpoint.port is not None:
            authority = f"{authority}:{endpoint.port}"
        self.bucket = settings.object_store_bucket
        self._client = Minio(
            authority,
            access_key=settings.object_store_access_key.get_secret_value(),
            secret_key=settings.object_store_secret_key.get_secret_value(),
            secure=endpoint.scheme == "https",
            cert_check=True,
        )

    async def put(
        self,
        key: str,
        data: bytes,
        *,
        media_type: str,
        metadata: Mapping[str, str] | None = None,
    ) -> None:
        try:
            await asyncio.to_thread(
                self._client.put_object,
                self.bucket,
                key,
                BytesIO(data),
                len(data),
                content_type=media_type,
                metadata=dict(metadata or {}),
            )
        except S3Error as exc:
            raise ObsionError(
                "artifact_store_unavailable",
                "Artifact content could not be stored",
                status_code=503,
            ) from exc

    async def get(self, key: str) -> StoredObject:
        def read_object() -> StoredObject:
            try:
                response = self._client.get_object(self.bucket, key)
                try:
                    return StoredObject(
                        data=response.read(),
                        media_type=response.headers.get("Content-Type", "application/octet-stream"),
                        metadata=dict(response.headers),
                    )
                finally:
                    response.close()
                    response.release_conn()
            except S3Error as exc:
                code = (
                    "artifact_content_missing"
                    if exc.code == "NoSuchKey"
                    else ("artifact_store_unavailable")
                )
                status_code = 404 if exc.code == "NoSuchKey" else 503
                raise ObsionError(
                    code,
                    "Artifact content is not available",
                    status_code=status_code,
                ) from exc

        return await asyncio.to_thread(read_object)

    async def delete(self, key: str) -> None:
        try:
            await asyncio.to_thread(self._client.remove_object, self.bucket, key)
        except S3Error as exc:
            raise ObsionError(
                "artifact_store_unavailable",
                "Artifact content could not be removed",
                status_code=503,
            ) from exc
