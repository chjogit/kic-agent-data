"""Neon Object Storage (S3 호환) 접근. 접근 정보는 환경변수로만 받고 출력·로그에 남기지 않습니다.

필요한 환경변수 (모두 있어야 켜짐; 하나라도 비면 get_store() 는 None → 호출 쪽이 '건너뜀'으로 기록):
  NEON_STORAGE_ENDPOINT           예: https://<branch-id>.storage.<region>.aws.neon.tech
  NEON_STORAGE_BUCKET             버킷 이름
  NEON_STORAGE_ACCESS_KEY_ID      S3 접근 키
  NEON_STORAGE_SECRET_ACCESS_KEY  S3 비밀 키
  NEON_STORAGE_REGION             (선택, 기본 ap-southeast-1)
AWS_ENDPOINT_URL_S3 · AWS_ACCESS_KEY_ID · AWS_SECRET_ACCESS_KEY · AWS_REGION 이름도 같은 값으로 인정합니다 (NEON_STORAGE_* 가 우선).
boto3 는 함수 안에서만 불러오므로 테스트·다른 명령은 설치 없이 동작합니다.
"""
from __future__ import annotations

from .config import env

REQUIRED = ("NEON_STORAGE_ENDPOINT", "NEON_STORAGE_BUCKET", "NEON_STORAGE_ACCESS_KEY_ID", "NEON_STORAGE_SECRET_ACCESS_KEY")
ALIASES = {"NEON_STORAGE_ENDPOINT": "AWS_ENDPOINT_URL_S3", "NEON_STORAGE_ACCESS_KEY_ID": "AWS_ACCESS_KEY_ID",
           "NEON_STORAGE_SECRET_ACCESS_KEY": "AWS_SECRET_ACCESS_KEY", "NEON_STORAGE_REGION": "AWS_REGION"}


def _get(name: str) -> str | None:
    return env(name) or (env(ALIASES[name]) if name in ALIASES else None)


def missing_settings() -> list[str]:
    """비어 있는 환경변수 이름 (값은 다루지 않음)."""
    return [name for name in REQUIRED if not _get(name)]


class S3Store:
    """put / get / size 만 가진 얇은 저장소. 테스트는 같은 메서드를 가진 모의 객체를 씁니다."""

    def __init__(self, client, bucket: str):
        self.client, self.bucket = client, bucket

    def size(self, key: str) -> int | None:
        """저장된 파일 크기(바이트), 없으면 None."""
        from botocore.exceptions import ClientError

        try:
            return int(self.client.head_object(Bucket=self.bucket, Key=key)["ContentLength"])
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return None
            raise

    def put(self, key: str, data: bytes, content_type: str = "application/zip") -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    def get(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()


def get_store():
    """설정이 모두 있으면 S3Store, 없으면 None."""
    if missing_settings():
        return None
    import boto3
    from botocore.config import Config

    client = boto3.client(
        "s3", endpoint_url=_get("NEON_STORAGE_ENDPOINT"), region_name=_get("NEON_STORAGE_REGION") or "ap-southeast-1",
        aws_access_key_id=_get("NEON_STORAGE_ACCESS_KEY_ID"), aws_secret_access_key=_get("NEON_STORAGE_SECRET_ACCESS_KEY"),
        config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 3}))
    return S3Store(client, _get("NEON_STORAGE_BUCKET"))
