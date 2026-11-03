from io import BytesIO

from flask import current_app
from minio import Minio
from urllib3 import PoolManager, Retry, Timeout


class StorageService:
    def __init__(self) -> None:
        self.client = Minio(
            current_app.config["MINIO_ENDPOINT"],
            access_key=current_app.config["MINIO_ACCESS_KEY"],
            secret_key=current_app.config["MINIO_SECRET_KEY"],
            secure=current_app.config["MINIO_SECURE"],
            region=current_app.config["MINIO_REGION"],
            http_client=PoolManager(
                timeout=Timeout(connect=5, read=20),
                retries=Retry(total=1, connect=1, read=1, status=0, backoff_factor=0.25),
            ),
        )
        self.bucket = current_app.config["MINIO_BUCKET_NAME"]

    def ensure_bucket(self) -> None:
        if not self.client.bucket_exists(self.bucket):
            self.client.make_bucket(self.bucket, location=current_app.config["MINIO_REGION"])

    def put(self, object_key: str, content: bytes) -> None:
        self.ensure_bucket()
        self.client.put_object(
            self.bucket,
            object_key,
            BytesIO(content),
            len(content),
            content_type="application/octet-stream",
        )

    def remove(self, object_key: str) -> None:
        self.client.remove_object(self.bucket, object_key)

    def get(self, object_key: str) -> bytes:
        response = self.client.get_object(self.bucket, object_key)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    def ready(self) -> bool:
        return self.client.bucket_exists(self.bucket)
