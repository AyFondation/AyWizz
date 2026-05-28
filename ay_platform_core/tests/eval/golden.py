# =============================================================================
# File: golden.py
# Version: 2
# Path: ay_platform_core/tests/eval/golden.py
# Description: Held-out, versioned golden dataset for the retrieval-quality
#              eval (D-017 T2 / Q-400-011). A small labelled corpus + queries
#              with binary relevance labels (the source_id(s) a query SHOULD
#              retrieve). Deliberately STRESSES the flat-RAG failure modes so
#              the dense-vs-hybrid comparison — and the D-010 "is v1 retrieval
#              demonstrably insufficient?" question — get a real signal :
#                - near-duplicate topics (precision / discrimination) ;
#                - exact-token / identifier queries (favour the BM25 arm) ;
#                - a MULTI-HOP query whose answer is split across two docs
#                  (the case flat top-k similarity is known to miss — the
#                  motivating example for the v2 iterative-retrieval loop B).
#
#              ANTI teaching-to-the-test (D-017) : a fixed reference, NOT tuned
#              against the retriever. Bump GOLDEN_VERSION on any change.
#
#              v2 : expanded from 4 docs / 4 queries to 10 / 8 with the stress
#              patterns above.
# =============================================================================

from __future__ import annotations

from dataclasses import dataclass

GOLDEN_VERSION = 2


@dataclass(frozen=True, slots=True)
class GoldenDoc:
    source_id: str
    content: str


@dataclass(frozen=True, slots=True)
class GoldenQuery:
    query: str
    relevant: frozenset[str]  # source_ids that SHOULD be retrieved
    kind: str  # semantic | near-dup | exact-token | multi-hop


GOLDEN_DOCS: tuple[GoldenDoc, ...] = (
    # gateway — two near-duplicate topics (routing/budgets vs caching/cost)
    GoldenDoc(
        "gw_budget",
        "The C8 gateway routes every model call through a single egress and "
        "enforces per-tenant monthly budget caps before dispatching.",
    ),
    GoldenDoc(
        "gw_cache",
        "The C8 gateway exploits provider prompt caching to cut cost and "
        "records cache-read tokens per call for cost attribution.",
    ),
    # memory — two near-duplicate topics (vectors vs graph)
    GoldenDoc(
        "mem_vec",
        "C7 memory embeds chunks into dense vectors and ranks them by cosine "
        "similarity for top-k retrieval.",
    ),
    GoldenDoc(
        "mem_kg",
        "C7 memory also builds a knowledge graph of entities and relations in "
        "ArangoDB to expand retrieval beyond direct vector matches.",
    ),
    # auth — two near-duplicate topics (jwt issuance vs role verification)
    GoldenDoc(
        "auth_jwt",
        "C2 authentication issues signed JSON web tokens to a user after a "
        "successful login against the local identity store.",
    ),
    GoldenDoc(
        "auth_role",
        "C2 verifies the caller's role and tenant scope on every request "
        "behind the Traefik forward-auth gateway.",
    ),
    # exact-token / identifier docs
    GoldenDoc(
        "id_202",
        "The frobulator widget references identifier R-400-202, the hybrid "
        "BM25-plus-dense retrieval fused by reciprocal rank.",
    ),
    GoldenDoc(
        "id_029",
        "Identifier R-200-029 specifies the OpenHands engine as the runtime of "
        "the generate phase, routed through the C8 gateway.",
    ),
    # multi-hop pair : the answer to "what runs the implementer agent" needs
    # BOTH — hop_a names the agent+phase, hop_b names the engine for that phase.
    GoldenDoc(
        "hop_a",
        "During the generate phase the implementer agent produces the project "
        "artifacts under the orchestrator's gates.",
    ),
    GoldenDoc(
        "hop_b",
        "The generate phase is executed by the OpenHands engine, which loops "
        "over tools to edit files and run tests.",
    ),
)

GOLDEN_QUERIES: tuple[GoldenQuery, ...] = (
    GoldenQuery(
        "per-tenant monthly budget caps at the egress",
        frozenset({"gw_budget"}),
        kind="near-dup",  # gw_cache is the distractor
    ),
    GoldenQuery(
        "cutting cost with prompt caching",
        frozenset({"gw_cache"}),
        kind="near-dup",  # gw_budget is the distractor
    ),
    GoldenQuery(
        "ranking chunks by cosine similarity",
        frozenset({"mem_vec"}),
        kind="near-dup",
    ),
    GoldenQuery(
        "graph of entities and relations to expand retrieval",
        frozenset({"mem_kg"}),
        kind="near-dup",
    ),
    GoldenQuery(
        "verifying caller role and tenant scope",
        frozenset({"auth_role"}),
        kind="near-dup",  # auth_jwt is the distractor
    ),
    GoldenQuery(
        "R-400-202",
        frozenset({"id_202"}),
        kind="exact-token",
    ),
    GoldenQuery(
        "R-200-029",
        frozenset({"id_029"}),
        kind="exact-token",
    ),
    GoldenQuery(
        "which engine runs the implementer agent",
        frozenset({"hop_a", "hop_b"}),
        kind="multi-hop",  # answer split across two docs — flat top-k misses
    ),
)
