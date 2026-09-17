import uuid
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Iterator, Protocol

from .config import settings


class DocumentStorage(Protocol):
    def save(self, name: str, content: bytes) -> str: ...
    def read(self, name: str) -> bytes: ...
    def delete(self, name: str) -> None: ...
    def exists(self, name: str) -> bool: ...
    def path(self, name: str) -> Path: ...
    def writable_path(self, name: str) -> Path: ...
    def release_path(self, path: Path) -> None: ...
    def materialize(self, name: str) -> AbstractContextManager[Path]: ...


class LocalFileStorage:
    """Filesystem-backed storage for local development.

    All filenames are validated against path traversal: only a bare
    filename (no path components) may be used, and the resolved path
    must remain inside the storage root.

    Local storage does not create temporary files: ``path()`` and
    ``writable_path()`` return persistent paths inside the storage root,
    so ``release_path()`` and ``materialize()`` are no-ops with respect
    to deletion.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _safe_path(self, name: str) -> Path:
        raw = Path(name)
        candidate = (self.root / raw).resolve()
        if raw.name != name or ".." in raw.parts or candidate.parent != self.root.resolve():
            raise ValueError("Invalid storage filename.")
        return candidate

    def save(self, name: str, content: bytes) -> str:
        self._safe_path(name).write_bytes(content)
        return name

    def read(self, name: str) -> bytes:
        return self._safe_path(name).read_bytes()

    def delete(self, name: str) -> None:
        self._safe_path(name).unlink(missing_ok=True)

    def exists(self, name: str) -> bool:
        return self._safe_path(name).is_file()

    def path(self, name: str) -> Path:
        return self._safe_path(name)

    def writable_path(self, name: str) -> Path:
        return self._safe_path(name)

    def release_path(self, path: Path) -> None:
        """Persistent local paths are not temporary; nothing to release."""
        return None

    @contextmanager
    def materialize(self, name: str) -> Iterator[Path]:
        """Yield the persistent local path for *name*.

        No temporary file is created and nothing is deleted on exit.
        """
        yield self.path(name)


class ObjectStorage:
    """S3-compatible object storage backend.

    Used for production deployments.  ``boto3`` is imported lazily so that
    local development (which uses :class:`LocalFileStorage`) does not require
    the dependency.

    The ``path()`` method downloads the object to a temporary file so that
    callers expecting a local filesystem path (PDF extraction, ZIP creation)
    continue to work.  Temporary files are tracked in ``temp_files`` and
    removed by :meth:`release_path` or :meth:`materialize`; callers that
    obtain a path directly must release it explicitly.  S3 object
    persistence is never affected by local temporary-file cleanup.
    """

    def __init__(self, bucket: str, endpoint_url: str | None, region: str | None, **kwargs) -> None:
        self.bucket = bucket
        self.temp_files: list[str] = []
        self._config = {"bucket": bucket, "endpoint_url": endpoint_url, "region_name": region}
        if "aws_access_key_id" in kwargs and "aws_secret_access_key" in kwargs:
            self._config["aws_access_key_id"] = kwargs["aws_access_key_id"]
            self._config["aws_secret_access_key"] = kwargs["aws_secret_access_key"]
        import boto3
        self._client = boto3.client("s3", **self._config)

    def _key(self, name: str) -> str:
        """Validate and normalise *name* to an S3 key.

        Only bare filenames (no path separators) are accepted, mirroring
        the ``LocalFileStorage`` traversal protection.
        """
        raw = Path(name)
        if raw.name != name or ".." in raw.parts or "/" in name or "\\" in name:
            raise ValueError("Invalid storage filename.")
        return name

    def save(self, name: str, content: bytes) -> str:
        key = self._key(name)
        self._client.put_object(Bucket=self.bucket, Key=key, Body=content)
        return name

    def read(self, name: str) -> bytes:
        key = self._key(name)
        response = self._client.get_object(Bucket=self.bucket, Key=key)
        return response["Body"].read()

    def delete(self, name: str) -> None:
        key = self._key(name)
        try:
            self._client.delete_object(Bucket=self.bucket, Key=key)
        except Exception:
            pass  # Idempotent delete

    def exists(self, name: str) -> bool:
        key = self._key(name)
        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False

    def path(self, name: str) -> Path:
        """Download the object to a temporary file and return its path."""
        content = self.read(name)
        suffix = Path(name).suffix
        tmp = NamedTemporaryFile(delete=False, suffix=suffix)
        tmp.write(content)
        tmp.close()
        self.temp_files.append(tmp.name)
        return Path(tmp.name)

    def writable_path(self, name: str) -> Path:
        self._key(name)
        suffix = Path(name).suffix
        tmp = NamedTemporaryFile(delete=False, suffix=suffix)
        tmp.close()
        self.temp_files.append(tmp.name)
        return Path(tmp.name)

    def release_path(self, path: Path) -> None:
        """Remove a temporary file created by this backend, if tracked.

        Safe to call for paths that were not created here (no-op).  The
        underlying S3 object is never deleted by this method.
        """
        key = str(path)
        if key not in self.temp_files:
            return
        try:
            Path(path).unlink(missing_ok=True)
        finally:
            self.temp_files.remove(key)

    @contextmanager
    def materialize(self, name: str) -> Iterator[Path]:
        """Download *name* to a temporary file and clean it up on exit.

        Cleanup runs on both success and failure of the consuming
        operation.  The S3 object itself is never deleted.
        """
        path = self.path(name)
        try:
            yield path
        finally:
            self.release_path(path)


def get_storage_backend(backend: str | None = None, **kwargs) -> DocumentStorage:
    """Factory that selects the appropriate storage backend.

    Parameters
    ----------
    backend
        ``"local"`` (default) or ``"s3"``.
    **kwargs
        For ``local``: ``root`` (Path).  For ``s3``: ``bucket``,
        ``endpoint_url``, ``region``.
    """
    backend = (backend or settings.storage_backend or "local").lower()
    if backend == "s3":
        access_key = kwargs.get("aws_access_key_id", settings.s3_access_key_id)
        secret_key = kwargs.get("aws_secret_access_key", settings.s3_secret_access_key)
        return ObjectStorage(
            bucket=kwargs.get("bucket", settings.s3_bucket),
            endpoint_url=kwargs.get("endpoint_url", settings.s3_endpoint_url or None),
            region=kwargs.get("region", settings.s3_region),
            **({"aws_access_key_id": access_key, "aws_secret_access_key": secret_key} if access_key and secret_key else {}),
        )
    if backend == "local":
        root = kwargs.get("root", settings.upload_dir)
        return LocalFileStorage(Path(root))
    raise ValueError(f"Unknown storage backend: {backend}")


document_storage = get_storage_backend(root=settings.upload_dir)
evidence_storage = get_storage_backend(root=settings.company_evidence_dir)
export_storage = get_storage_backend(root=settings.export_dir)