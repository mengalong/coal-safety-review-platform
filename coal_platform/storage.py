from __future__ import annotations

from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Protocol

from minio import Minio

from coal_platform.config import Settings


class ObjectStorage(Protocol):
    def initialize(self) -> None: ...

    def put(self, key: str, content: bytes, content_type: str | None = None) -> None: ...

    def delete(self, key: str) -> None: ...

    def get(self, key: str) -> bytes | None: ...


class LocalObjectStorage:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, key: str, content: bytes, content_type: str | None = None) -> None:
        del content_type
        target = (self.root / key).resolve()
        if not target.is_relative_to(self.root):
            raise ValueError("storage key escapes configured root")
        target.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=target.parent, delete=False) as temporary:
            temporary.write(content)
            temporary_path = Path(temporary.name)
        temporary_path.replace(target)

    def delete(self, key: str) -> None:
        target = (self.root / key).resolve()
        if not target.is_relative_to(self.root):
            raise ValueError("storage key escapes configured root")
        target.unlink(missing_ok=True)

    def get(self, key: str) -> bytes | None:
        target = (self.root / key).resolve()
        if not target.is_relative_to(self.root):
            raise ValueError("storage key escapes configured root")
        try:
            return target.read_bytes()
        except FileNotFoundError:
            return None


class MinioObjectStorage:
    def __init__(self, settings: Settings) -> None:
        self.bucket = settings.minio_bucket
        self.client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )

    def initialize(self) -> None:
        if not self.client.bucket_exists(self.bucket):
            self.client.make_bucket(self.bucket)

    def put(self, key: str, content: bytes, content_type: str | None = None) -> None:
        self.client.put_object(
            self.bucket,
            key,
            BytesIO(content),
            length=len(content),
            content_type=content_type or "application/octet-stream",
        )

    def delete(self, key: str) -> None:
        self.client.remove_object(self.bucket, key)

    def get(self, key: str) -> bytes | None:
        try:
            response = self.client.get_object(self.bucket, key)
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()
        except Exception as exc:
            if exc.__class__.__name__ == "S3Error" and getattr(exc, "code", "") in {"NoSuchKey", "NoSuchBucket"}:
                return None
            raise


class InMemoryObjectStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def initialize(self) -> None:
        return None

    def put(self, key: str, content: bytes, content_type: str | None = None) -> None:
        del content_type
        self.objects[key] = content

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    def get(self, key: str) -> bytes | None:
        return self.objects.get(key)


def build_object_storage(settings: Settings) -> ObjectStorage:
    if settings.storage_backend == "minio":
        return MinioObjectStorage(settings)
    return LocalObjectStorage(settings.local_storage_path)
