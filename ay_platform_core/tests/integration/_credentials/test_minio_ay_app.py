# =============================================================================
# File: test_minio_ay_app.py
# Version: 1
# Path: ay_platform_core/tests/integration/_credentials/test_minio_ay_app.py
# Description: End-to-end usability test for the dedicated MinIO app user
#              `ay_app` (R-100-118 v2). Replays the bootstrap logic that
#              `minio_init` performs in the compose stack:
#                1. Create the platform's buckets.
#                2. Declare a readwrite policy scoped to those buckets.
#                3. Create the app user with a known secret key.
#                4. Attach the policy to the user.
#              Then connects AS `ay_app` and verifies that:
#                - PUT / GET / LIST / DELETE on a granted bucket succeed.
#                - The same operations on a non-scoped bucket are rejected.
#
# @relation implements:R-100-118
# =============================================================================

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from minio import Minio
from minio.credentials.providers import StaticProvider
from minio.error import S3Error
from minio.minioadmin import MinioAdmin

from tests.fixtures.containers import MinioEndpoint, cleanup_minio_bucket

pytestmark = pytest.mark.integration

_AY_APP_SECRET = "ay-app-integration-test-secret"


@pytest.fixture(scope="function")
def app_user(
    minio_container: MinioEndpoint,
) -> Iterator[tuple[list[str], str]]:
    """Bootstrap an isolated `(buckets, ay_app_username)` pair.

    Mirrors what `minio_init` does in the compose stack:
      - Create the platform's buckets (suffixed for test isolation).
      - Create a `s3:*`-on-those-buckets policy.
      - Create the user; attach the policy.
    Tears down all created resources at the end.
    """
    suffix = uuid.uuid4().hex[:8]
    user_name = f"ay-app-test-{suffix}"
    policy_name = f"ay-app-readwrite-test-{suffix}"
    bucket_names = [
        f"orchestrator-test-{suffix}",
        f"requirements-test-{suffix}",
        f"validation-test-{suffix}",
        f"memory-test-{suffix}",
    ]

    admin = MinioAdmin(
        endpoint=minio_container.endpoint,
        credentials=StaticProvider(
            minio_container.access_key, minio_container.secret_key
        ),
        secure=False,
    )
    root_client = Minio(
        minio_container.endpoint,
        access_key=minio_container.access_key,
        secret_key=minio_container.secret_key,
        secure=False,
    )

    # Buckets.
    for b in bucket_names:
        if not root_client.bucket_exists(b):
            root_client.make_bucket(b)

    # Policy: s3:* on the four buckets and their objects, nothing else.
    policy_doc = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["s3:*"],
                "Resource": [
                    item
                    for b in bucket_names
                    for item in (f"arn:aws:s3:::{b}", f"arn:aws:s3:::{b}/*")
                ],
            }
        ],
    }
    admin.policy_add(policy_name, _write_temp_json(policy_doc))

    # User + attach.
    admin.user_add(user_name, _AY_APP_SECRET)
    admin.policy_set(policy_name, user=user_name)

    try:
        yield (bucket_names, user_name)
    finally:
        # Detach + remove user, drop policy, drop buckets.
        with contextlib.suppress(Exception):
            admin.user_remove(user_name)
        with contextlib.suppress(Exception):
            admin.policy_remove(policy_name)
        for b in bucket_names:
            cleanup_minio_bucket(minio_container, b)


def _write_temp_json(doc: dict[str, Any]) -> str:
    """Persist a policy doc to a temp file and return its path.

    `minio.MinioAdmin.policy_add` accepts a path on disk, not a Python
    object, so we materialise it. The OS reclaims `/tmp` files on
    container teardown; explicit cleanup is not required for a test
    fixture.
    """
    # `delete=False` is required: `policy_add` reads the file by path
    # AFTER we've returned, so we must NOT auto-delete on context exit.
    # SIM115 is silenced because the close-then-read-by-path pattern is
    # the documented contract of `MinioAdmin.policy_add`.
    handle = tempfile.NamedTemporaryFile(  # noqa: SIM115
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    )
    try:
        json.dump(doc, handle)
        handle.flush()
    finally:
        handle.close()
    return handle.name


class TestMinioAyAppUsability:
    """The `ay_app` user SHALL hold full S3 access on the platform's buckets
    and nothing more."""

    def test_can_round_trip_an_object_in_a_granted_bucket(
        self,
        minio_container: MinioEndpoint,
        app_user: tuple[list[str], str],
    ) -> None:
        buckets, user_name = app_user
        bucket = buckets[0]

        app_client = Minio(
            minio_container.endpoint,
            access_key=user_name,
            secret_key=_AY_APP_SECRET,
            secure=False,
        )

        payload = b"hello from ay_app"
        key = f"obj-{uuid.uuid4().hex[:6]}"
        app_client.put_object(
            bucket_name=bucket,
            object_name=key,
            data=io.BytesIO(payload),
            length=len(payload),
        )

        with app_client.get_object(bucket, key) as resp:
            assert resp.read() == payload

        names = [obj.object_name for obj in app_client.list_objects(bucket)]
        assert key in names

        app_client.remove_object(bucket, key)
        names_after = [obj.object_name for obj in app_client.list_objects(bucket)]
        assert key not in names_after

    def test_can_access_every_granted_bucket(
        self,
        minio_container: MinioEndpoint,
        app_user: tuple[list[str], str],
    ) -> None:
        buckets, user_name = app_user
        app_client = Minio(
            minio_container.endpoint,
            access_key=user_name,
            secret_key=_AY_APP_SECRET,
            secure=False,
        )
        for bucket in buckets:
            # `bucket_exists` requires `s3:ListBucket` — the policy grants it.
            assert app_client.bucket_exists(bucket), bucket

    def test_cannot_access_a_foreign_bucket(
        self,
        minio_container: MinioEndpoint,
        app_user: tuple[list[str], str],
    ) -> None:
        """Authorization scope is enforced: a bucket outside the policy's
        Resource list is forbidden, even though it exists."""
        _, user_name = app_user

        foreign = f"foreign-test-{uuid.uuid4().hex[:8]}"
        root_client = Minio(
            minio_container.endpoint,
            access_key=minio_container.access_key,
            secret_key=minio_container.secret_key,
            secure=False,
        )
        root_client.make_bucket(foreign)
        try:
            app_client = Minio(
                minio_container.endpoint,
                access_key=user_name,
                secret_key=_AY_APP_SECRET,
                secure=False,
            )
            with pytest.raises(S3Error) as exc_info:
                # Any privileged operation surfaces the policy denial.
                app_client.put_object(
                    bucket_name=foreign,
                    object_name="x",
                    data=io.BytesIO(b"y"),
                    length=1,
                )
            assert exc_info.value.code in {"AccessDenied", "AccessDeniedException"}
        finally:
            cleanup_minio_bucket(minio_container, foreign)

    def test_wrong_secret_key_is_rejected(
        self,
        minio_container: MinioEndpoint,
        app_user: tuple[list[str], str],
    ) -> None:
        buckets, user_name = app_user
        bad_client = Minio(
            minio_container.endpoint,
            access_key=user_name,
            secret_key="not-the-secret",
            secure=False,
        )
        with pytest.raises(S3Error) as exc_info:
            bad_client.bucket_exists(buckets[0])
        # Codes vary across MinIO releases (SignatureDoesNotMatch /
        # InvalidAccessKeyId); both indicate the credential is rejected.
        assert exc_info.value.code in {
            "SignatureDoesNotMatch",
            "InvalidAccessKeyId",
            "AccessDenied",
        }
