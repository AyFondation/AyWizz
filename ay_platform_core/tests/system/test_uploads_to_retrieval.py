# =============================================================================
# File: test_uploads_to_retrieval.py
# Version: 2
# Path: ay_platform_core/tests/system/test_uploads_to_retrieval.py
# Description: End-to-end system test that exercises the ingestion pipeline
#              through Traefik, as the UI actually drives it (D-020 /
#              R-100-081 v3):
#                 POST /api/v1/memory/projects/<p>/sources/upload (multipart)
#                 → C7 writes the raw bytes to MinIO
#                 → C7 triggers the C12 (n8n) `extract-and-ingest` workflow
#                 → C13 extractor /analyze, polled to a terminal status
#                 → C12 POSTs the chunks back to C7 /ingest-chunks
#                 → C7 embeds, writes to Arango
#                 → POST /api/v1/memory/retrieve returns the chunk.
#
#              v2 (2026-10-05): rewritten off `POST /uploads/ingest-text`.
#              That webhook is declared by NO workflow in the repository —
#              n8n answered `unknown webhook "POST ingest-text"` — and the
#              client-facing entry point moved off the n8n webhook entirely
#              in apiClient v9: byte custody is C7's, and C7 triggers C12
#              with metadata only. So the old test encoded a contract that
#              had been retired twice over, and no redirect to
#              `/uploads/extract-and-ingest` would have been right either:
#              that webhook now expects a `raw_object_key` for bytes ALREADY
#              in MinIO, which only C7 can produce.
#
#              The functional assertion is unchanged, and is the whole point
#              of the test: a source uploaded by a user comes back out of
#              retrieval. Only the door it knocks on changed.
#
#              Requires the docker-compose stack up
#              (scripts/e2e_stack.sh up) with c12 AND c13-extractor, plus
#              the workflow imported+published by the c12_workflow_seed
#              one-shot container.
#
# @relation validates:R-100-080
# @relation validates:R-100-081
# =============================================================================

from __future__ import annotations

import asyncio
import os
import uuid

import httpx
import pytest

pytestmark = pytest.mark.system

#: Opt in to the retrieval half of the chain. OFF by default because the
#: compose `test` profile CANNOT satisfy it: C13's embedding pass 404s
#: against mock_llm by operator decision, so chunks never carry vectors and
#: nothing lands in the index. The skip below names that decision rather
#: than hiding behind a bare `pytest.skip` (§10.2 #4) — and the WIRING half
#: of this test still runs unconditionally, so a regression in byte custody,
#: the source record, or the multipart contract still fails CI.
_FULL_EXTRACTION = os.environ.get("AY_SYSTEM_FULL_EXTRACTION") == "1"


# THE `xfail` THAT USED TO SIT HERE IS GONE (2026-09-20). Its reason was that
# `import:workflow` wrote the workflow as active to SQLite while the RUNNING
# n8n kept its in-memory webhook router unchanged, so /uploads/ingest-text was
# only registered after a c12 restart. Both halves of that reason have since
# been resolved:
#   - the c12 Deployment gained an `import-workflows` initContainer that
#     imports AND publishes BEFORE the server starts, so n8n now boots with
#     every webhook registered — there is no "after a restart" any more ;
#   - `update:workflow --all --active=true`, which the reason also named, was
#     removed by n8n 2.x and replaced by per-id `n8n publish:workflow`.
#
# It was also `strict=False`, which is what made it dangerous: had the chain
# started working, pytest would have reported xpass and NOBODY would have
# learned that the platform's only upload→retrieval proof was passing again.
# Removed rather than flipped to strict=True: a marker that documents a fixed
# defect is worse than no marker, and a genuine failure now surfaces as a
# failure, with this comment as the history to check first.
@pytest.mark.asyncio
async def test_upload_text_source_ends_up_retrievable(
    gateway_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Upload a text file carrying a unique marker the way the UI does —
    multipart to C7 — then retrieve it back through
    `/api/v1/memory/retrieve`. At least one hit SHALL contain the marker,
    proving the C7→MinIO→C12→C13→C7→Arango→retrieval chain is intact."""
    unique_phrase = f"kzrl-marker-{uuid.uuid4().hex[:12]}"
    source_id = f"sys-test-{uuid.uuid4().hex[:8]}"
    body = (
        f"System test marker sentence. {unique_phrase} "
        "This paragraph exists only in this test run and should be "
        "retrievable after the upload workflow fires."
    )

    # Multipart, exactly as `apiClient.uploadSource` builds it: `format` is
    # the lowercased file extension, and C7 derives the object key itself.
    upload_resp = await gateway_client.post(
        "/api/v1/memory/projects/demo/sources/upload",
        files={"file": (f"{source_id}.txt", body.encode(), "text/plain")},
        data={
            "source_id": source_id,
            "mime_type": "text/plain",
            "format": "txt",
        },
        headers=auth_headers,
    )
    # 202: C7 has taken byte custody and fired the C12 workflow. The
    # extraction that follows is asynchronous — hence the poll below.
    assert upload_resp.status_code == 202, (
        f"multipart upload to C7 failed: {upload_resp.status_code} "
        f"{upload_resp.text}. A 403 means forward-auth did not resolve a "
        "project role on `demo` (check the seeder's grant); a 422 means the "
        "multipart contract drifted."
    )
    accept = upload_resp.json()
    assert accept.get("source_id") == source_id, accept

    # The source record SHALL be visible immediately: C7 creates it in the
    # same request that takes byte custody. This half holds in every profile
    # and is what proves the wiring — C7 wrote to MinIO, recorded the source,
    # and accepted responsibility for triggering C12.
    listing = await gateway_client.get(
        "/api/v1/memory/projects/demo/sources", headers=auth_headers
    )
    assert listing.status_code == 200, listing.text
    ids = {s["source_id"] for s in listing.json()["sources"]}
    assert source_id in ids, (
        f"{source_id!r} was accepted with 202 but does not appear in C7's "
        f"source listing. Present: {sorted(ids)[:10]}"
    )

    if not _FULL_EXTRACTION:
        pytest.skip(
            "Retrieval half requires a real embedding provider. The compose "
            "`test` profile points C13 at mock_llm, which serves only "
            "/v1/chat/completions and 404s /v1/embeddings — an explicit "
            "operator decision (2026-05-29, see the CAVEAT above the "
            "c13-extractor service in tests/docker-compose.yml): that stack "
            "proves the WIRING, not real extraction. The wiring half above "
            "DID run and passed. Set AY_SYSTEM_FULL_EXTRACTION=1 against the "
            "`litellm` profile with a real key to exercise the rest."
        )

    # Bounded poll. The budget is 60s, not the 10s this test used while the
    # chain was synchronous: it now crosses C13, whose n8n status poll alone
    # waits 5s per iteration. A shorter budget would make this test fail on
    # timing rather than on the behaviour it validates.
    for _ in range(60):
        retrieve = await gateway_client.post(
            "/api/v1/memory/retrieve",
            json={
                "project_id": "demo",
                "query": unique_phrase,
                "indexes": ["external_sources"],
                "limit": 5,
            },
            headers=auth_headers,
        )
        if retrieve.status_code == 200 and retrieve.json().get("hits"):
            break
        await asyncio.sleep(1.0)
    else:
        pytest.fail(
            f"retrieval never returned hits for {unique_phrase!r} within 60s. "
            "The upload was accepted (202), so the break is downstream: "
            "check the n8n execution list for `extract-and-ingest`, then "
            "c13-extractor's /analyze, then C7's /ingest-chunks."
        )

    hits = retrieve.json()["hits"]
    # The marker MUST appear in at least one chunk content — proves the
    # upload landed, got chunked, and got embedded under the index we
    # queried.
    assert any(unique_phrase in hit.get("content", "") for hit in hits), (
        f"no retrieval hit contained {unique_phrase!r}. Hits: "
        f"{[h.get('content', '')[:120] for h in hits]}"
    )
