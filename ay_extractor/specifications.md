# ayExtractor — Multi-Agent Document Analyzer Specification

**Version:** 2.1.8
**Date:** 2026-02-15
**Status:** Draft

---

## 1. Overview

### 1.1 Purpose

**ayExtractor** est un système multi-agents permettant l'analyse approfondie de documents (livres, articles, rapports) afin d'en extraire :

- Les thèmes et domaines abordés
- Les idées principales et arguments clés
- Les logiques sous-jacentes et relations causales
- Une cartographie conceptuelle explorable

### 1.2 Design Principles

- **Clean Architecture + Façade** : une API publique unique masquant la complexité interne
- **Ségrégation stricte** : chaque responsabilité = un fichier dédié
- **Persistance intermédiaire** : chaque étape produit des fichiers texte traçables et versionnables
- **Reprise sur erreur** : checkpoint à chaque étape, reprise possible sans tout relancer
- **Extensibilité** : ajout de nouveaux formats, agents ou stratégies sans impact sur l'existant
- **GPU-ready by design** : toutes les opérations compute-intensive (graph, clustering, embeddings, PageRank) passent par des API abstraites compatibles accélération GPU sans changement de code applicatif (voir §33)

### 1.3 Supported Input Formats

| Format | Extension | Library | Notes |
|--------|-----------|---------|-------|
| PDF | `.pdf` | PyMuPDF (fitz), pdfplumber | Text + images + tables |
| EPUB | `.epub` | ebooklib | Text + images |
| Word | `.docx` | python-docx | Text + images + tables |
| Markdown | `.md` | Built-in | Text + image refs |
| Plain Text | `.txt` | Built-in | Text only |
| Image | `.png`, `.jpg`, `.jpeg`, `.webp`, `.tiff`, `.bmp` | Pillow, LLM Vision | Screenshots, diagrams, scanned pages |
| Multi-image | Directory or archive | Pillow | Batch of images treated as single document |

#### Image-as-Input Workflow

Quand l'entrée est une ou plusieurs images (screenshots, pages scannées, diagrammes) :

1. **OCR + Vision analysis** : chaque image est envoyée au LLM Vision pour extraction de texte et description structurelle
2. **Ordering** : si plusieurs images, l'ordre est déterminé par le nom de fichier (alphabétique/numérique) ou par métadonnée
3. **Assembly** : les extractions sont assemblées en un texte enrichi unique, chaque image donnant un bloc `<<<IMAGE_CONTENT>>>`
4. Le pipeline continue normalement à partir de l'étape chunking

Ce mode est utile pour analyser des captures d'écran de présentations, des documents scannés sans OCR préexistant, ou des infographies.

---

## 2. Public API

### 2.1 Facade — `api/facade.py`

Single entry point:

```python
from ayextractor.api.facade import analyze
from ayextractor.api.models import DocumentInput, Metadata, AnalysisResult

result: AnalysisResult = analyze(document: DocumentInput, metadata: Metadata)
```

### 2.2 Input Models — `api/models.py`

#### `DocumentInput`

```python
class DocumentInput(BaseModel):
    """Input document for analysis."""
    content: bytes | str | Path | list[Path]  # Raw content, file path, or list of image paths
    format: str                               # File format (pdf, epub, docx, md, txt, image)
    filename: str                             # Original filename (or directory name for multi-image)
```

#### `Metadata`

```python
class Metadata(BaseModel):
    """Execution metadata provided by the caller."""
    document_id: str | None = None            # Auto-generated if None: yyyymmdd_hhmmss_{uuid4_short}
    document_type: str                        # Category (book, article, report, whitepaper)
    output_path: Path                         # Root directory for generated output files
    language: str | None = None               # Language hint (auto-detected if None)
    resume_from_run: str | None = None        # Run ID (yyyymmdd_hhmm_{uuid5}) to resume from
    resume_from_step: int | None = None       # Step number to restart from (steps before this are carried)
    config_overrides: ConfigOverrides | None = None  # Per-document overrides (validated)
```

#### `ConfigOverrides` — `api/models.py`

Sous-ensemble typé de `Settings` permettant des overrides per-document. Seules les clés explicitement listées sont acceptées — les clés inconnues lèvent une `ValidationError` :

```python
class ConfigOverrides(BaseModel):
    """Per-document overrides — validated subset of Settings."""
    llm_assignments: dict[str, str] | None = None        # Per-agent provider:model overrides
    chunking_strategy: str | None = None
    chunk_target_size: int | None = None
    chunk_overlap: int | None = None                     # Should be consistent with chunk_target_size
    density_iterations: int | None = None
    decontextualization_enabled: bool | None = None
    critic_agent_enabled: bool | None = None
    output_format: str | None = None
    entity_similarity_threshold: float | None = None
    relation_taxonomy_extensible: bool | None = None
    community_detection_resolution: float | None = None
    community_detection_seed: int | None = None
    community_summary_enabled: bool | None = None
    profile_generation_enabled: bool | None = None
    consolidator_enabled: bool | None = None
```

Les overrides appliquées sont tracées dans le `run_manifest.json` via le champ `config_overrides_applied`.

#### `AnalysisResult`

```python
class AnalysisResult(BaseModel):
    """Return value of facade.analyze() — API-level result."""
    document_id: str                          # yyyymmdd_hhmmss_{uuid}
    run_id: str                               # yyyymmdd_hhmm_{uuid5}
    summary: str                              # Dense final summary
    themes: list[Theme]                       # Identified themes and domains
    concepts: list[Concept]                   # Key concepts extracted
    relations: list[Relation]                 # Relations between concepts (triplets)
    community_count: int                      # Number of L1 communities detected
    graph_path: Path                          # Path to exported knowledge graph
    communities_path: Path                    # Path to community hierarchy JSON
    profiles_path: Path                       # Path to entity profiles JSON
    output_dir: Path                          # Root of all generated output files
    run_dir: Path                             # Path to this specific run directory
    confidence_scores: dict[str, float]       # Per-step confidence scores
    fingerprint: DocumentFingerprint          # Multi-level fingerprint of the document
    usage_stats: SessionStats                 # Token consumption and cost for this execution
```

---

## 3. Project Structure

```
.
├── Makefile                       # Build, test, lint commands (see §34)
├── pyproject.toml                 # Project metadata, dependencies
├── .env                           # Environment configuration
│
├── src/
│   ├── __init__.py                    # Package root (name mapped via pyproject.toml)
│   │
│   ├── core/
│   │   ├── __init__.py
│   │   ├── models.py                  # Shared Pydantic models used across modules:
│   │   │                              #   Chunk, ChunkSourceSection, ChunkDecontextualization,
│   │   │                              #   ResolvedReference, QualifiedTriplet, ConsolidatedTriplet,
│   │   │                              #   EntityNormalization, RelationTaxonomyEntry,
│   │   │                              #   Reference, DocumentStructure, Section, Footnote,
│   │   │                              #   Theme, Concept, Relation (API views),
│   │   │                              #   ImageAnalysis, TableData, ExtractionResult,
│   │   │                              #   SourceMetadata, TokenBudget,
│   │   │                              #   SourceProvenance, TemporalScope
│   │   └── similarity.py             # GPU-aware cosine similarity utility (see §33.5)
│   │
│   ├── api/
│   │   ├── __init__.py
│   │   ├── facade.py                  # Public entry point: analyze()
│   │   └── models.py                  # API-level models (DocumentInput, Metadata,
│   │                                  #   AnalysisResult, ConfigOverrides)
│   │
│   ├── extraction/
│   │   ├── __init__.py
│   │   ├── base_extractor.py          # Abstract extractor interface (BaseExtractor)
│   │   ├── extractor_factory.py       # Instantiate extractor from format
│   │   ├── pdf_extractor.py           # Text + images + tables from PDF
│   │   ├── epub_extractor.py          # Text + images from EPUB
│   │   ├── docx_extractor.py          # Text + images + tables from DOCX
│   │   ├── md_extractor.py            # Text + image refs from Markdown
│   │   ├── txt_extractor.py           # Plain text passthrough
│   │   ├── image_input_extractor.py   # Standalone image(s) as document input (OCR + Vision)
│   │   ├── table_extractor.py         # Structured table extraction (all formats)
│   │   ├── image_analyzer.py          # Image analysis via LLM Vision (embedded images)
│   │   ├── content_merger.py          # Inject image/table descriptions into text
│   │   ├── language_detector.py       # Auto-detect document language (doc + chunk level)
│   │   └── structure_detector.py      # Detect TOC, index, bibliography, annexes
│   │
│   ├── chunking/
│   │   ├── __init__.py
│   │   ├── base_chunker.py            # Abstract chunker interface (BaseChunker)
│   │   ├── chunker_factory.py         # Instantiate chunker from strategy name
│   │   ├── structural_chunker.py      # Section/heading-based segmentation
│   │   ├── semantic_chunker.py        # Embedding-based semantic segmentation
│   │   └── chunk_validator.py         # Ensure IMAGE_CONTENT blocks are atomic
│   │
│   ├── pipeline/
│   │   ├── __init__.py
│   │   ├── orchestrator.py            # LangGraph workflow definition
│   │   ├── state.py                   # ExtractionContext (phases 1-2) + PipelineState (phase 3)
│   │   │
│   │   ├── agents/
│   │   │   ├── __init__.py
│   │   │   ├── reference_extractor.py # Phase 1 (1g) — cross-reference, citation extraction
│   │   │   ├── summarizer.py          # Phase 2 (2c-ii) — Refine agent on decontextualized chunks
│   │   │   ├── densifier.py           # Phase 2 (2d) — Chain of Density → global_summary
│   │   │   ├── decontextualizer.py    # Phase 2 (2c-i) — chunk disambiguation (coreference resolution)
│   │   │   ├── concept_extractor.py   # Phase 3 — Entity/relation extraction agent
│   │   │   ├── community_summarizer.py # Phase 3 — LLM summary per community (all hierarchy levels)
│   │   │   ├── profile_generator.py   # Phase 3 — LLM entity/relation profiles for L2 entities
│   │   │   ├── synthesizer.py         # Phase 3 — Final synthesis agent (summary + graph → output)
│   │   │   └── critic.py              # Phase 3 — Optional validation agent (configurable via .env)
│   │   │
│   │   ├── plugin_kit/
│   │   │   ├── __init__.py
│   │   │   ├── base_agent.py          # BaseAgent ABC (standard interface)
│   │   │   ├── registry.py            # Agent registration and discovery
│   │   │   ├── dag_builder.py         # Auto-construct LangGraph DAG from agents
│   │   │   └── models.py              # AgentOutput, AgentMetadata
│   │   │
│   │   └── prompts/                   # English-only prompt templates
│   │       ├── decontextualizer.txt
│   │       ├── summarizer.txt
│   │       ├── densifier.txt
│   │       ├── concept_extractor.txt
│   │       ├── reference_extractor.txt
│   │       ├── synthesizer.txt
│   │       ├── critic.txt
│   │       ├── community_summarizer.txt
│   │       ├── profile_generator.txt
│   │       ├── entity_normalizer.txt     # Used by graph/entity_normalizer.py [LLM-DEPENDENT]
│   │       ├── relation_normalizer.txt   # Used by graph/relation_normalizer.py [LLM-DEPENDENT]
│   │       └── image_analyzer.txt
│   │
│   ├── storage/
│   │   ├── __init__.py
│   │   ├── base_output_writer.py      # Abstract output writer interface (BaseOutputWriter)
│   │   ├── writer_factory.py          # Instantiate writer from config
│   │   ├── local_writer.py            # Write to local filesystem (default)
│   │   ├── s3_writer.py               # Write to S3-compatible storage (optional)
│   │   ├── reader.py                  # Read for resume or consultation
│   │   ├── layout.py                  # Output directory structure definition
│   │   ├── models.py                  # RunManifest (Pydantic model for run_manifest.json schema)
│   │   └── run_manager.py             # Create runs, copy carried steps, manage symlinks
│   │
│   ├── graph/
│   │   ├── __init__.py
│   │   ├── builder.py                 # Build knowledge graph (L2 + L3) from consolidated triplets [PURE]
│   │   ├── merger.py                  # Triplet consolidation orchestrator (3 passes) [LLM-DEPENDENT]
│   │   ├── entity_normalizer.py       # Pass 1: Entity dedup via embedding clustering + LLM [LLM-DEPENDENT]
│   │   ├── relation_normalizer.py     # Pass 2: Relation taxonomy mapping + LLM classification [LLM-DEPENDENT]
│   │   ├── triplet_consolidator.py    # Pass 3: Triplet dedup, merge, aggregate (incl. qualifiers) [PURE]
│   │   ├── reference_linker.py        # Link cross-references and citations into graph [PURE]
│   │   ├── base_graph_exporter.py     # Abstract graph export interface (BaseGraphExporter)
│   │   ├── exporter_factory.py        # Instantiate exporter from format name
│   │   ├── json_exporter.py           # NetworkX JSON export
│   │   ├── graphml_exporter.py        # GraphML export
│   │   ├── gexf_exporter.py           # GEXF export (Gephi-compatible)
│   │   ├── cypher_exporter.py         # Cypher export (Neo4j direct import)
│   │   ├── taxonomy.py                # Default relation taxonomy constants (the base table from §13.4).
│   │   │                              # NOT Pydantic models — those are in core/models.py.
│   │   │                              # Contains: DEFAULT_RELATION_TAXONOMY: list[dict] and helper functions.
│   │   │
│   │   ├── layers/                    # --- Layer classification & community detection ---
│   │   │   ├── __init__.py
│   │   │   ├── layer_classifier.py    # Assign L2/L3 layers based on entity_type + literal detection [PURE]
│   │   │   ├── community_detector.py  # Hierarchical Leiden on Document Graph → L1 communities [PURE]
│   │   │   ├── community_integrator.py # Inject L1 community nodes + encompasses edges into graph [PURE, SIDE-EFFECTS]
│   │   │   └── models.py              # Community, CommunityHierarchy, CommunitySummary
│   │   │
│   │   └── profiles/                  # --- Entity & relation profile data models + embedding ---
│   │       ├── __init__.py
│   │       ├── profile_embedder.py    # Compute embeddings for profiles (for RAG vector search) [PURE, uses BaseEmbedder]
│   │       └── models.py              # EntityProfile, RelationProfile
│   │
│   │   > **Dependency legend:** [PURE] = no LLM calls, testable without mocks.
│   │   > [LLM-DEPENDENT] = requires BaseLLMClient mock for testing.
│   │   > [SIDE-EFFECTS] = modifies input graph in place.
│   │
│   ├── consolidator/                  # --- Corpus Graph consolidation (async, periodic) ---
│   │   ├── __init__.py
│   │   ├── orchestrator.py            # Run consolidation passes (configurable schedule)
│   │   ├── entity_linker.py           # Pass 1 — Linking: merge Document Graph → Corpus Graph C-nodes
│   │   ├── community_clusterer.py     # Pass 2 — Clustering: Leiden on Corpus Graph → T-nodes
│   │   ├── inference_engine.py        # Pass 3 — Inference: discover implicit transitive relations
│   │   ├── decay_manager.py           # Pass 4 — Decay: reduce staleness, prune low-value nodes
│   │   ├── contradiction_detector.py  # Pass 5 — Contradiction: detect conflicting claims
│   │   └── models.py                  # CNode, TNode, XEdge, Contradiction,
│   │                                  # ConsolidationReport, LinkingReport, ClusteringReport,
│   │                                  # InferenceReport, DecayReport, ContradictionReport, PassResult
│   │
│   ├── cache/
│   │   ├── __init__.py
│   │   ├── fingerprint.py             # Multi-level document fingerprinting
│   │   ├── base_cache_store.py        # Abstract cache store interface (BaseCacheStore)
│   │   ├── cache_factory.py           # Instantiate cache store from config
│   │   ├── json_cache_store.py        # JSON file-based cache (default)
│   │   ├── sqlite_cache_store.py      # SQLite-based cache (optional)
│   │   ├── redis_cache_store.py       # Redis-based cache (optional)
│   │   ├── arangodb_cache_store.py    # ArangoDB-based cache (optional)
│   │   └── models.py                  # DocumentFingerprint, CacheEntry, CacheLookupResult
│   │
│   ├── tracking/
│   │   ├── __init__.py
│   │   ├── call_logger.py             # Per-LLM-call token logging (input/output/latency)
│   │   ├── agent_tracker.py           # Per-agent aggregation across calls
│   │   ├── session_tracker.py         # Per-document execution totals
│   │   ├── stats_aggregator.py        # Cross-document cumulative statistics
│   │   ├── cost_calculator.py         # Token-to-cost conversion (model pricing)
│   │   ├── exporter.py                # Export stats (JSON, CSV, dashboard-ready)
│   │   └── models.py                  # LLMCallRecord, AgentStats, SessionStats, GlobalStats,
│   │                                  #   TypeStats, CumulativeAgentStats, ModelStats, DailyStats, ModelPricing
│   │
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── base_client.py             # Abstract LLM client interface (BaseLLMClient)
│   │   ├── client_factory.py          # Factory: instantiate client from provider name
│   │   ├── models.py                  # LLM-specific types: Message, ImageInput, LLMResponse
│   │   ├── adapters/
│   │   │   ├── __init__.py
│   │   │   ├── anthropic_adapter.py   # Claude API adapter
│   │   │   ├── openai_adapter.py      # ChatGPT / GPT-4 adapter
│   │   │   ├── google_adapter.py      # Gemini adapter
│   │   │   └── ollama_adapter.py      # Ollama (local models) adapter
│   │   ├── config.py                  # Per-agent LLM assignment, model routing
│   │   ├── token_budget.py            # Token budget estimation and allocation
│   │   └── retry.py                   # Per-agent retry policy (rate limit, timeout)
│   │
│   ├── config/
│   │   ├── __init__.py
│   │   ├── settings.py                # Load .env, expose typed Settings via pydantic-settings
│   │   └── agents.py                  # Agent registry configuration
│   │
│   ├── logging/
│   │   ├── __init__.py
│   │   ├── logger.py                  # Logger factory, formatters (JSON/text)
│   │   ├── context.py                 # Contextual logging (document_id, run_id, agent)
│   │   └── handlers.py                # File rotation handler
│   │
│   ├── batch/
│   │   ├── __init__.py
│   │   ├── scanner.py                 # Directory scanning and file discovery
│   │   ├── dedup.py                   # Fingerprint comparison against cache
│   │   └── models.py                  # BatchResult, ScanEntry
│   │
│   ├── rag/
│   │   ├── __init__.py
│   │   ├── enricher.py                # Query stores, inject context into agent prompts
│   │   ├── indexer.py                 # Post-analysis indexing into stores (chunks + profiles + communities)
│   │   ├── models.py                  # RAGContext, SearchResult, RetrievalPlan, CorpusContext
│   │   │
│   │   ├── retriever/                 # --- Hierarchical retrieval pipeline ---
│   │   │   ├── __init__.py
│   │   │   ├── query_classifier.py    # Classify query type (conceptual/factual/relational/exploratory)
│   │   │   ├── community_retriever.py # Level 1: retrieve + rank community summaries
│   │   │   ├── entity_retriever.py    # Level 2: retrieve entity/relation profiles via vector + PPR
│   │   │   ├── chunk_retriever.py     # Level 3: retrieve source chunks (fallback for evidence)
│   │   │   ├── corpus_retriever.py    # Cross-document: C-nodes, T-nodes, X-edges from Corpus Graph
│   │   │   ├── context_assembler.py   # Assemble final LLM context from all levels
│   │   │   ├── ppr_scorer.py          # Personalized PageRank scoring on knowledge graph
│   │   │   └── pipeline.py            # Orchestrate hierarchical retrieval (community→entity→chunk)
│   │   │
│   │   ├── vector_store/
│   │   │   ├── __init__.py
│   │   │   ├── base_vector_store.py   # Abstract vector store interface
│   │   │   ├── chromadb_adapter.py    # ChromaDB adapter
│   │   │   ├── qdrant_adapter.py      # Qdrant adapter
│   │   │   ├── arangodb_adapter.py    # ArangoDB adapter (multi-model: vector + graph)
│   │   │   └── factory.py             # Instantiate from config
│   │   │
│   │   ├── graph_store/
│   │   │   ├── __init__.py
│   │   │   ├── base_graph_store.py    # Abstract graph store interface
│   │   │   ├── neo4j_adapter.py       # Neo4j adapter
│   │   │   ├── arangodb_adapter.py    # ArangoDB adapter
│   │   │   └── factory.py             # Instantiate from config
│   │   │
│   │   └── embeddings/
│   │       ├── __init__.py
│   │       ├── base_embedder.py       # Abstract embeddings interface
│   │       ├── anthropic_embedder.py  # Anthropic Voyage embeddings
│   │       ├── openai_embedder.py     # OpenAI embeddings
│   │       ├── sentence_tf_embedder.py # sentence-transformers (local)
│   │       ├── ollama_embedder.py     # Ollama embeddings (local)
│   │       └── factory.py             # Instantiate from config
│   │
│   ├── version.py                     # Project version
│   └── main.py                        # CLI entry point
│
└── tests/
    ├── conftest.py                        # Shared fixtures (mock LLM, sample chunks, temp dirs)
    ├── unit/                              # Mirror src/ structure — no external deps, mocked I/O
    │   ├── core/
    │   │   ├── test_models.py
    │   │   └── test_similarity.py
    │   ├── api/
    │   │   └── test_models.py
    │   ├── config/
    │   │   ├── test_settings.py
    │   │   └── test_agents.py
    │   ├── extraction/
    │   │   ├── test_pdf_extractor.py
    │   │   ├── test_md_extractor.py
    │   │   └── ...                        # One test file per extractor
    │   ├── chunking/
    │   │   ├── test_structural_chunker.py
    │   │   └── test_chunk_validator.py
    │   ├── graph/
    │   │   ├── test_builder.py
    │   │   ├── test_merger.py
    │   │   ├── test_entity_normalizer.py
    │   │   ├── test_triplet_consolidator.py
    │   │   ├── test_taxonomy.py
    │   │   ├── layers/
    │   │   │   ├── test_layer_classifier.py
    │   │   │   ├── test_community_detector.py
    │   │   │   └── test_community_integrator.py
    │   │   └── profiles/
    │   │       └── test_profile_embedder.py
    │   ├── consolidator/
    │   │   ├── test_entity_linker.py
    │   │   ├── test_community_clusterer.py
    │   │   ├── test_inference_engine.py
    │   │   ├── test_decay_manager.py
    │   │   └── test_contradiction_detector.py
    │   ├── pipeline/
    │   │   ├── agents/
    │   │   │   ├── test_decontextualizer.py
    │   │   │   ├── test_summarizer.py
    │   │   │   ├── test_concept_extractor.py
    │   │   │   └── ...                    # One test file per agent
    │   │   └── plugin_kit/
    │   │       ├── test_registry.py
    │   │       └── test_dag_builder.py
    │   ├── cache/
    │   │   ├── test_fingerprint.py
    │   │   └── test_json_cache_store.py
    │   ├── storage/
    │   │   ├── test_local_writer.py
    │   │   └── test_layout.py
    │   ├── llm/
    │   │   ├── test_client_factory.py
    │   │   └── test_retry.py
    │   ├── tracking/
    │   │   ├── test_call_logger.py
    │   │   └── test_cost_calculator.py
    │   ├── rag/
    │   │   ├── test_indexer.py
    │   │   └── retriever/
    │   │       ├── test_query_classifier.py
    │   │       ├── test_ppr_scorer.py
    │   │       └── test_context_assembler.py
    │   └── batch/
    │       ├── test_scanner.py
    │       └── test_dedup.py
    │
    └── integration/                       # End-to-end, requires real LLM / DB
        ├── test_pipeline_pdf_e2e.py       # PDF → full pipeline → AnalysisResult
        ├── test_pipeline_resume.py        # Run interrupted → resume from checkpoint
        ├── test_batch_processing.py       # Batch scan + dedup + cache hit
        ├── test_rag_retrieval.py          # Index + hierarchical retrieval
        ├── test_consolidator_e2e.py       # 5-pass consolidation on corpus
        └── test_cache_hit.py              # Same doc twice → cache hit
```

> **Package mapping :** `pyproject.toml` configure `[tool.setuptools.package-dir]` avec `ayextractor = "src"`. Les imports utilisent `from ayextractor.core.models import ...` tandis que le code source est dans `src/core/models.py`.

### 3.2 Shared Models — `core/models.py`

Types Pydantic partagés entre plusieurs modules. Aucun module ne redéfinit ces types — ils importent depuis `core.models`.

```python
# Domain models shared across modules
# NOTE: All classes in this module inherit from pydantic.BaseModel.
# For brevity, (BaseModel) is omitted from most class definitions below
# but MUST be included in generated code. Only SourceProvenance and
# TemporalScope show it explicitly as examples.

# === CHUNK MODELS ===

class Chunk(BaseModel):
    # --- Identity ---
    id: str                         # "chunk_001"
    position: int                   # Ordinal position in document (0-based)
    preceding_chunk_id: str | None  # "chunk_000" or None (first chunk)
    following_chunk_id: str | None  # "chunk_002" or None (last chunk)

    # --- Content ---
    content: str                    # Decontextualized text (or original if decontextualization disabled)
    original_content: str | None    # Before decontextualization (None if disabled)
    content_type: str               # "text" | "mixed" (contains IMAGE_CONTENT or TABLE_CONTENT blocks)
    embedded_images: list[str]      # Image IDs contained in this chunk (e.g., ["img_001", "img_003"])
    embedded_tables: list[str]      # Table IDs contained in this chunk (e.g., ["tbl_002"])

    # --- Source traceability ---
    source_file: str                # Original filename (e.g., "report_q3.pdf")
    source_pages: list[int]         # Pages from which this chunk was extracted (can span 2+ pages)
    source_sections: list[ChunkSourceSection]  # Sections this chunk belongs to
    byte_offset_start: int          # Start offset in enriched_text.txt
    byte_offset_end: int            # End offset in enriched_text.txt

    # --- Metrics ---
    char_count: int
    word_count: int
    token_count_est: int            # Estimated token count (tiktoken or provider-specific)
    overlap_with_previous: int      # Token overlap with preceding chunk (from CHUNK_OVERLAP setting)
    fingerprint: str                # SHA-256 of normalized content (for chunk-level dedup)

    # --- Language ---
    primary_language: str
    secondary_languages: list[str]
    is_multilingual: bool

    # --- Decontextualization ---
    decontextualization: ChunkDecontextualization | None

    # --- Embedding (null in JSON files; only set in-memory during vector DB indexation) ---
    embedding: list[float] | None   # null in persisted .json files; set in-memory by rag/indexer.py
    embedding_model: str | None     # Model used (e.g., "voyage-3") — informational for reproducibility

    # --- Context for disambiguation ---
    context_summary: str | None       # Cumulative Refine summary up to this chunk (progressive narrative context)
    global_summary: str | None        # Dense summary of full document, injected after Densifier (step 2d)
    key_entities: list[str]         # Main entities present in this chunk (lightweight pre-extraction by
                                    # decontextualizer — NOT the full concept extraction from §13).
                                    # Purpose: RAG indexing key + quick entity lookup without running full pipeline.
                                    # The Concept Extractor (§13) performs the authoritative deep extraction.
    acronyms_expanded: dict[str, str]  # Acronyms found and expanded (e.g., {"EU": "European Union"})

class ChunkSourceSection:
    title: str                      # Section title (e.g., "3.2 Risk Assessment")
    level: int                      # Heading level (1-6)

class ChunkDecontextualization:
    applied: bool                   # Whether decontextualization was performed
    resolved_references: list[ResolvedReference]
    context_window_size: int        # Number of preceding chunks used as context
    confidence: float               # Decontextualizer confidence score for this chunk

class ResolvedReference:
    original_text: str              # The ambiguous text (e.g., "il", "l'entreprise")
    resolved_text: str              # The resolved text (e.g., "Marc Dupont (CEO d'Acme Corp)")
    reference_type: str             # "pronoun" | "definite_article" | "acronym" | "implicit_ref"
    resolution_source: str          # "preceding_chunk" | "document_title" | "toc" | "rag_lookup"
    position_in_chunk: int          # Character offset in original chunk

# === TRIPLET MODELS ===

class QualifiedTriplet:
    """Triplet qualifié extrait d'un seul chunk, avant normalisation."""
    subject: str                    # Raw entity name as extracted
    predicate: str                  # Raw relation as extracted
    object: str                     # Raw entity name as extracted
    source_chunk_id: str            # Chunk from which this triplet was extracted
    confidence: float               # Extraction confidence (0.0-1.0)
    context_sentence: str           # Source sentence for traceability
    qualifiers: dict[str, str] | None  # N-ary relation qualifiers (scope, instrument, condition, etc.)
    temporal_scope: TemporalScope | None  # When the fact is true (content temporality)

class ConsolidatedTriplet:
    """Triplet normalisé et consolidé après fusion inter-chunks."""
    subject: str                    # Canonical entity name (after normalization)
    predicate: str                  # Canonical relation type (from taxonomy)
    object: str                     # Canonical entity name (after normalization)
    source_chunk_ids: list[str]     # All chunks where this triplet was found
    occurrence_count: int           # Number of times this triplet appeared (pre-merge)
    confidence: float               # Aggregated confidence (max of sources)
    original_forms: list[str]       # Original predicate forms before normalization (e.g., ["regulates", "réglemente"])
    qualifiers: dict[str, str | list[str]] | None  # Merged n-ary qualifiers (same key + different values → list)
    temporal_scope: TemporalScope | None  # Merged content temporality (most precise kept)
    context_sentences: list[str]    # Source sentences from all contributing QualifiedTriplets

class EntityNormalization:
    """Table de normalisation des entités (nodes)."""
    canonical_name: str             # Chosen canonical form (e.g., "European Union")
    aliases: list[str]              # All variant forms found (e.g., ["EU", "l'UE", "Union européenne"])
    entity_type: str | None         # "person" | "organization" | "concept" | "location" | "document" | "technology" | None
    occurrence_count: int           # Total occurrences across all chunks
    source_chunk_ids: list[str]     # Chunks where this entity appeared

class RelationTaxonomyEntry:
    """Mapping d'une relation brute vers la taxonomie canonique."""
    canonical_relation: str         # Canonical form (e.g., "regulates")
    original_forms: list[str]       # All raw forms mapped to this relation
    category: str                   # Taxonomy category (e.g., "governance", "composition", "causality")
    is_directional: bool            # Whether order matters (A→B ≠ B→A)

# === OTHER MODELS ===

class Reference:
    type: str                       # "citation" | "footnote" | "bibliography" | "internal_ref"
    text: str                       # Reference text
    target: str | None              # Resolved target (document_id, section, URL)
    source_chunk_id: str

class DocumentStructure:
    has_toc: bool
    sections: list[Section]
    has_bibliography: bool
    bibliography_position: int | None
    has_annexes: bool
    annexes: list[Section]
    footnotes: list[Footnote]
    has_index: bool

class Section:
    title: str
    level: int
    start_position: int
    end_position: int

class Footnote:
    id: str
    content: str
    position: int

class Theme:
    """API view — produced by Synthesizer structured output (step 3g)."""
    name: str
    description: str
    relevance_score: float              # 0.0-1.0

class Concept:
    """API view — derived from EntityNormalization during Finalization (step 4).
    Mapping: canonical_name→name, entity_type+context→description, aliases→aliases."""
    name: str                           # From EntityNormalization.canonical_name
    description: str                    # Generated: "{entity_type}: {contextual description from graph}"
    aliases: list[str]                  # From EntityNormalization.aliases

class Relation:
    """API view — derived from ConsolidatedTriplet during Finalization (step 4).
    Mapping: subject→source, predicate→relation_type, object→target, confidence→weight."""
    source: str                         # From ConsolidatedTriplet.subject
    relation_type: str                  # From ConsolidatedTriplet.predicate
    target: str                         # From ConsolidatedTriplet.object
    weight: float                       # From ConsolidatedTriplet.confidence

class ExtractionResult:
    raw_text: str
    enriched_text: str
    images: list[ImageAnalysis]
    tables: list[TableData]
    structure: DocumentStructure
    language: str

# ExtractionContext is defined in pipeline/state.py (see §6.3)

class ImageAnalysis:
    id: str
    type: str                       # diagram | chart | table_image | photo | screenshot | decorative
    description: str
    entities: list[str]
    source_page: int | None

class TableData:
    id: str
    content_markdown: str
    source_page: int | None
    origin: str                     # "structured" | "image"

class TokenBudget:
    total_estimated: int
    per_agent: dict[str, int]
    consumed: dict[str, int]

class SourceMetadata:
    """Metadata about the original source document, stored once in source/ directory."""
    original_filename: str          # "my_report.pdf"
    format: str                     # "pdf"
    size_bytes: int                 # 2450000
    sha256: str                     # Hash of original file
    stored_at: datetime             # Storage timestamp

class SourceProvenance(BaseModel):
    """Tracks exactly where a node or edge was extracted from (see §13.9.6 for usage)."""
    document_id: str
    run_id: str
    chunk_ids: list[str]
    context_sentences: list[str]     # Sentence-level traceability
    first_seen_at: datetime
    extraction_confidence: float

class TemporalScope(BaseModel):
    """When a fact is true — content temporality, NOT ingestion temporality (see §13.9.7 for usage)."""
    type: Literal["point", "range", "recurring"]
    start: str | None = None          # ISO date or descriptive ("Q3 2025")
    end: str | None = None            # For ranges
    granularity: Literal["day", "month", "quarter", "year", "decade"] | None = None
    raw_expression: str               # Original text ("depuis 2020", "au T3 2025")
```

---

## 4. Execution Pipeline

### 4.1 Pipeline Flow

```
facade.analyze(document, metadata)
│
├── 0. CACHE CHECK + RUN SETUP
│   ├── cache/fingerprint.py → compute multi-level fingerprint
│   ├── cache/base_cache_store.py → lookup existing analysis (via configured backend)
│   │   → if exact match found: return cached result (skip pipeline)
│   │   → if partial match: log and continue (new version of known doc)
│   │
│   ├── storage/run_manager.py → create run directory
│   │   → generate run_id (yyyymmdd_hhmm_{uuid5})
│   │   → if resume: copy carried steps from source run
│   │   → initialize run_manifest.json
│   │
│   └── storage/base_output_writer.py → store source document (via configured backend, if first run for this document_id)
│
├── 1. EXTRACTION PHASE
│   ├── 1a. extraction/language_detector.py → detect language
│   │       → select appropriate prompt set for all agents
│   │
│   ├── 1b. extraction/extractor_factory.py → dispatch to format extractor
│   │       → produces: raw text, images, positions
│   │
│   ├── 1c. extraction/structure_detector.py → detect TOC, index, annexes, bibliography
│   │       → produces: document structure map
│   │
│   ├── 1d. extraction/table_extractor.py → extract structured tables
│   │       → produces: tables as markdown or structured data
│   │
│   ├── 1e. extraction/image_analyzer.py → analyze each image via LLM Vision
│   │       → produces: per-image analysis (description, type, structured data)
│   │
│   ├── 1f. extraction/content_merger.py → merge text + image descriptions + table descriptions
│   │       → produces: enriched_text.txt
│   │
│   ├── 1g. agents/reference_extractor.py → extract cross-references, citations, bibliography
│   │       → input: enriched_text + structure map (bibliography, footnotes)
│   │       → produces: references.json + confidence score
│   │       → references available for decontextualizer and downstream agents
│   │
│   └── update run_manifest.json (step=1, origin=fresh)
│
├── 2. CHUNKING + INTERLEAVED SUMMARIZATION/DECONTEXTUALIZATION PHASE
│   ├── 2a. chunking/chunker_factory.py → dispatch to configured chunker
│   │       → uses structure map from 1c to guide segmentation
│   │       → respects IMAGE_CONTENT and TABLE_CONTENT block atomicity
│   │
│   ├── 2b. chunking/chunk_validator.py → validate chunk integrity
│   │
│   ├── 2c. INTERLEAVED LOOP — for each chunk N (sequential):
│   │   ├── 2c-i.  agents/decontextualizer.py (if DECONTEXTUALIZATION_ENABLED=true)
│   │   │           → uses: refine_summary_{N-1} + references from 1g + sliding window
│   │   │           → resolve ambiguous references in chunk N
│   │   │           → preserve original as chunk_xxx_original.txt
│   │   │           → if DECONTEXTUALIZER_TOOL_USE=auto and confidence < threshold:
│   │   │             re-process with chunk_lookup tool (see §28.7)
│   │   │
│   │   └── 2c-ii. agents/summarizer.py → Refine step on decontextualized chunk N
│   │               → input: decontextualized chunk N + refine_summary_{N-1}
│   │               → output: refine_summary_N (cumulative)
│   │               → store refine_summary_N as chunk.context_summary
│   │
│   ├── 2d. agents/densifier.py → Chain of Density (5 iterations)
│   │       → input: refine_summary_final (after all chunks processed)
│   │       → produces: dense_summary.txt (= global_summary)
│   │       → inject dense_summary as chunk.global_summary in ALL chunks
│   │
│   ├── 2e. Write chunk files (for each chunk):
│   │       → chunk_xxx.json (full enriched metadata — see §5.7)
│   │       →   includes context_summary (per-chunk) + global_summary (document-wide)
│   │       → chunk_xxx_original.txt (if decontextualization enabled, for CLI diff)
│   │       → chunks_index.json (ordered index)
│   │
│   ├── 2f. rag/indexer.py → if RAG_ENABLED=true AND VECTOR_DB_TYPE != none:
│   │       → compute embeddings for all chunks (via configured embedding provider)
│   │       → index chunks into vector DB (via configured adapter)
│   │       → PURPOSE: temporary indexation for RAG enrichment of agents in phase 3
│   │       → NOTE: embeddings NOT written back to chunk .json files (immutability)
│   │       → NOTE: step 4 will upsert (not duplicate) chunks and add profiles + community summaries
│   │
│   └── update run_manifest.json (step=2, origin=fresh)
│
├── 3. ANALYSIS PIPELINE (LangGraph)
│   ├── 3a. agents/concept_extractor.py → extract entity/relation QUALIFIED TRIPLETS PER CHUNK
│   │       → produces: triplets_raw/triplets_chunk_xxx.json (one file per chunk)
│   │       → each file contains list[QualifiedTriplet] with qualifiers + source provenance
│   │
│   ├── 3b. graph/merger.py → TRIPLET CONSOLIDATION (3 passes)
│   │   ├── Pass 1: Entity normalization (embedding clustering + LLM validation)
│   │   │   → produces: entity_normalization.json
│   │   ├── Pass 2: Relation normalization (taxonomy mapping + LLM classification)
│   │   │   → produces: relation_taxonomy.json
│   │   └── Pass 3: Triplet dedup + merge (apply normalizations, aggregate, merge qualifiers)
│   │       → produces: triplets.json (consolidated)
│   │
│   ├── 3c. graph/builder.py + reference_linker.py → knowledge graph (L2/L3 ONLY) from consolidated triplets
│   │       → reference_linker uses references extracted in 1g
│   │       → layer assignment (L2 entities, L3 evidence) via graph/layers/layer_classifier.py
│   │       → produces: graph.json (L2+L3 nodes + edges, NO L1 yet)
│   │
│   ├── 3d. graph/layers/community_detector.py → HIERARCHICAL COMMUNITY DETECTION
│   │       → Leiden algorithm on L2 subgraph → hierarchical community tree
│   │       → produces: communities.json (hierarchy + member lists)
│   │
│   ├── 3d'. graph/layers/community_integrator.py → INJECT L1 INTO GRAPH
│   │       → creates L1 community_topic nodes + encompasses edges (L1→L2)
│   │       → creates related_to edges (L1↔L1) for communities sharing ≥3 chunks
│   │       → updates graph.json (now complete with L1+L2+L3)
│   │
│   ├── 3e. pipeline/agents/community_summarizer.py → COMMUNITY SUMMARIES
│   │       → LLM generates community summaries (one per community, all levels)
│   │       → produces: community_summaries.json
│   │
│   ├── 3f. pipeline/agents/profile_generator.py → ENTITY & RELATION PROFILES
│   │       → for each L2 entity: LLM generates a concise textual profile
│   │         from all its relations, qualifiers, and source sentences
│   │       → for key relations: LLM generates relation profiles
│   │       → produces: entity_profiles.json, relation_profiles.json
│   │       → profiles are embedded for vector search (used by RAG retrieval)
│   │
│   ├── 3g. agents/synthesizer.py → final structured synthesis
│   │       → uses: dense_summary + community summaries + knowledge graph + references
│   │       → produces: final_analysis.txt + themes (list[Theme]) + confidence score
│   │
│   └── update run_manifest.json (steps 3a-3g, origin=fresh)
│
├── 4. FINALIZATION
│   ├── API view mapping → convert internal models to API-level views:
│   │   ├── EntityNormalization → list[Concept] (canonical_name→name, aliases→aliases)
│   │   ├── ConsolidatedTriplet → list[Relation] (subject→source, predicate→relation_type)
│   │   └── themes already produced as list[Theme] by Synthesizer
│   ├── tracking/session_tracker.py → aggregate call records into 04_synthesis/execution_stats.json
│   ├── tracking/stats_aggregator.py → update global cumulative stats
│   ├── cache/cache_factory.py → store result with fingerprint (via configured cache backend)
│   │   → also writes 00_metadata/cache_entry.json (JSON-always principle)
│   ├── rag/indexer.py → if CHUNK_OUTPUT_MODE includes vectordb:
│   │   └── upsert chunks + entity profiles + relation profiles + community summaries into vector DB
│   │       (chunks upserted by id — no duplicates if already indexed in step 2f)
│   ├── rag/indexer.py → if CHUNK_OUTPUT_MODE includes graphdb:
│   │   └── import Document Graph into graph DB (incremental merge — see §13.10)
│   ├── consolidator/entity_linker.py → if CONSOLIDATOR_ENABLED=true:
│   │   └── Pass 1 (Linking): merge Document Graph entities into Corpus Graph (always on ingestion)
│   ├── graph/exporter_factory.py → export graph in all configured formats to 04_synthesis/
│   │   → graph.json is ALWAYS generated (JSON-always principle)
│   │   → additional formats per GRAPH_EXPORT_FORMATS (.graphml, .gexf, .cypher)
│   ├── update latest symlink → point to this run
│   └── return AnalysisResult (includes run_id, usage_stats)
```

### 4.2 Run Management and Resume Strategy

#### Principle: Immutable Runs

Chaque exécution du pipeline crée un **run** identifié par `yyyymmdd_hhmm_{uuid5}` (5 caractères UUID pour éviter les collisions en exécution parallèle). Un run est **immutable** — une fois terminé (ou échoué), il n'est jamais modifié. Toute reprise ou re-exécution crée un **nouveau run**.

#### New Run (normal execution)

```
run_id = datetime.now().strftime("%Y%m%d_%H%M") + "_" + uuid4().hex[:5]
→ Creates: {output_path}/{document_id}/runs/{run_id}/
→ All agents execute from scratch
→ run_manifest.json: all steps marked "fresh"
```

#### Resume Run (reprise après échec ou re-exécution partielle)

```
metadata.resume_from_run = "20260207_1430_e7f2a"
metadata.resume_from_step = 3

→ Creates NEW run: {output_path}/{document_id}/runs/20260207_1615_b3c8d/
→ Steps 1-2: COPIED from run 20260207_1430_e7f2a (files copied, not symlinked)
→ Steps 3+: executed fresh in new run
→ run_manifest.json: steps 1-2 marked "carried_from: 20260207_1430_e7f2a", steps 3+ marked "fresh"
```

#### Run Manifest — `run_manifest.json`

Chaque run contient un manifeste complet décrivant l'exécution :

```json
{
    "run_id": "20260207_1615_b3c8d",
    "document_id": "20260207_140000_a1b2c3d4",
    "created_at": "2026-02-07T16:15:00Z",
    "status": "completed",
    "pipeline_version": "2.1.1",
    "llm_assignments": {
        "image_analyzer": "anthropic:claude-sonnet-4-20250514",
        "reference_extractor": "anthropic:claude-haiku-4-5-20251001",
        "summarizer": "anthropic:claude-sonnet-4-20250514",
        "densifier": "anthropic:claude-sonnet-4-20250514",
        "decontextualizer": "anthropic:claude-haiku-4-5-20251001",
        "concept_extractor": "anthropic:claude-sonnet-4-20250514",
        "entity_normalizer": "anthropic:claude-haiku-4-5-20251001",
        "relation_normalizer": "anthropic:claude-haiku-4-5-20251001",
        "community_summarizer": "anthropic:claude-haiku-4-5-20251001",
        "profile_generator": "anthropic:claude-haiku-4-5-20251001",
        "synthesizer": "anthropic:claude-sonnet-4-20250514",
        "critic": null
    },
    "embedding": "anthropic:voyage-3",
    "config_overrides_applied": {},
    "prompt_hashes": {
        "image_analyzer": "sha256:444ddd...",
        "reference_extractor": "sha256:012def...",
        "summarizer": "sha256:abc123...",
        "densifier": "sha256:def456...",
        "decontextualizer": "sha256:111aaa...",
        "concept_extractor": "sha256:789abc...",
        "entity_normalizer": "sha256:555eee...",
        "relation_normalizer": "sha256:666fff...",
        "community_summarizer": "sha256:222bbb...",
        "profile_generator": "sha256:333ccc...",
        "synthesizer": "sha256:345ghi...",
        "critic": null
    },
    "steps": {
        "01_extraction": {
            "origin": "carried_from",
            "source_run": "20260207_1430_e7f2a",
            "output_hash": "sha256:..."
        },
        "02_chunks": {
            "origin": "carried_from",
            "source_run": "20260207_1430_e7f2a",
            "output_hash": "sha256:...",
            "decontextualization": true,
            "summarization": true
        },
        "03_concepts": {
            "origin": "fresh",
            "started_at": "2026-02-07T16:15:12Z",
            "completed_at": "2026-02-07T16:17:02Z",
            "output_hash": "sha256:...",
            "substeps": {
                "3a_concept_extraction": "completed",
                "3b_triplet_consolidation": "completed",
                "3c_graph_build": "completed",
                "3d_community_detection": "completed",
                "3d_community_integration": "completed",
                "3e_community_summaries": "completed",
                "3f_profile_generation": "completed",
                "3g_synthesis": "completed"
            }
        },
        "04_synthesis": {
            "origin": "fresh",
            "started_at": "2026-02-07T16:17:03Z",
            "completed_at": "2026-02-07T16:17:58Z",
            "output_hash": "sha256:..."
        }
    }
}
```

**Pydantic model** (`storage/models.py`) :

```python
class StepManifest(BaseModel):
    """Manifest for a single pipeline step."""
    origin: Literal["fresh", "carried_from"]
    carried_from: str | None = None   # Previous run_id if carried
    started_at: datetime | None = None
    completed_at: datetime | None = None
    output_hash: str | None = None    # sha256 of step output

class RunManifest(BaseModel):
    """Full manifest for a pipeline run, written to run_manifest.json."""
    run_id: str
    document_id: str
    pipeline_version: str
    created_at: datetime
    completed_at: datetime | None = None
    status: Literal["running", "completed", "failed", "partial"]
    config_overrides_applied: dict[str, Any]
    llm_assignments: dict[str, str]        # agent_name → "provider:model"
    prompt_hashes: dict[str, str]          # agent_name → sha256 of prompt
    steps: dict[str, StepManifest]         # step_name → manifest
```

#### Diff Between Runs

Pour comparer deux runs :

1. **Comparer les `run_manifest.json`** → identifie immédiatement ce qui a changé (version, modèle, prompts)
2. **Comparer les `output_hash`** par step → identifie quels résultats ont divergé
3. **Diff fichier par fichier** → même structure dans chaque run, `diff` standard possible

Exemples de scénarios de diff :

| Changement | Impact visible dans le manifest |
|------------|-------------------------------|
| Prompt modifié | `prompt_hashes.{agent}` différent |
| Modèle LLM changé | `llm_assignments.{agent}` différent |
| Code source modifié | `pipeline_version` différent |
| Config override appliquée | `config_overrides_applied` différent |
| Même config, résultat différent | `output_hash` différent (non-déterminisme LLM) |

---

## 5. Output Structure

### 5.1 Document ID Format

```
document_id = yyyymmdd_hhmmss_{uuid4_short}
```

Example: `20260207_140000_a1b2c3d4`

- `yyyymmdd_hhmmss` : timestamp de soumission du document
- `uuid4_short` : 8 premiers caractères d'un UUID v4 (unicité)

Généré automatiquement si non fourni dans `Metadata`.

### 5.2 Top-Level Layout

```
{output_path}/{document_id}/
│
├── source/
│   ├── original_document.{ext}     # Original source document (copy)
│   └── source_metadata.json        # Original filename, format, size, hash
│
├── runs/
│   ├── 20260207_1430_e7f2a/            # First execution
│   ├── 20260207_1615_b3c8d/            # Second execution (resume or re-run)
│   └── ...
│
└── latest → runs/20260207_1615_b3c8d   # Symlink to latest completed run
```

### 5.3 Run Layout (each run directory)

```
{output_path}/{document_id}/runs/{run_id}/
│
├── run_manifest.json               # Run metadata, step origins, hashes, prompt versions
│
├── 00_metadata/
│   ├── input_metadata.json         # Metadata d'entrée
│   ├── fingerprint.json            # Multi-level fingerprint
│   ├── cache_entry.json            # Cache entry snapshot (JSON-always principle — for DB rebuild)
│   ├── language.txt                # Detected language
│   └── document_structure.json     # TOC, sections, annexes map
│
├── 01_extraction/
│   ├── raw_text.txt                # Raw text (no image content)
│   ├── enriched_text.txt           # Text + injected image/table descriptions
│   ├── tables/
│   │   ├── table_001.md            # Each table as markdown
│   │   └── tables_index.json       # Position + metadata per table
│   ├── images/
│   │   ├── img_001.png
│   │   ├── img_001_analysis.txt    # LLM Vision analysis
│   │   └── images_index.json       # Position + type + ref per image
│   └── references.json             # Cross-references and citations (step 1g)
│
├── 02_chunks/
│   ├── refine_summary.txt          # Incremental summary (Refine — step 2c)
│   ├── dense_summary.txt           # Condensed summary (Chain of Density — step 2d)
│   ├── chunk_001.json              # Full enriched chunk metadata (content + original_content + all fields)
│   ├── chunk_001_original.txt      # Original text before decontextualization (if enabled, for CLI diff)
│   ├── chunk_002.json
│   ├── chunk_002_original.txt
│   └── chunks_index.json           # Ordered list of chunk IDs + summary metadata per chunk
│
├── 03_concepts/
│   ├── triplets_raw/               # Per-chunk qualified triplets (traceability)
│   │   ├── triplets_chunk_001.json # Qualified triplets extracted from chunk_001
│   │   ├── triplets_chunk_002.json
│   │   └── ...
│   ├── entity_normalization.json   # Entity canonical names + aliases + types
│   ├── relation_taxonomy.json      # Relation taxonomy + raw-to-canonical mappings
│   ├── triplets.json               # Consolidated triplets (final, deduplicated)
│
└── 04_synthesis/
    ├── final_analysis.txt          # Structured final output
    ├── themes.json                 # Identified themes (list[Theme] from Synthesizer)
    ├── graph.json                  # Knowledge graph (ALWAYS generated — JSON-always principle)
    ├── graph.graphml               # Knowledge graph (if in GRAPH_EXPORT_FORMATS)
    ├── graph.gexf                  # Knowledge graph (if in GRAPH_EXPORT_FORMATS)
    ├── graph.cypher                # Knowledge graph (if in GRAPH_EXPORT_FORMATS)
    ├── communities.json            # Community hierarchy (levels, members, parent/child)
    ├── community_summaries.json    # LLM-generated summaries per community (all levels)
    ├── entity_profiles.json        # L2 entity textual profiles (without embeddings)
    ├── relation_profiles.json      # Key relation textual profiles (without embeddings)
    ├── confidence.json             # Per-step and global confidence scores
    ├── critic_report.json          # Critic findings (if CRITIC_AGENT_ENABLED=true)
    ├── execution_stats.json        # Merged: estimation budget + token tracking + cost
    └── calls_log.jsonl             # Raw per-call LLM log (append-only)
```

> **Note :** Les fichiers `graph.*` présents dans `04_synthesis/` dépendent de la variable `GRAPH_EXPORT_FORMATS` (défaut: `json,graphml`). Les fichiers `chunk_xxx_original.txt` ne sont générés que si `DECONTEXTUALIZATION_ENABLED=true` (pour permettre le diff CLI entre texte original et décontextualisé). Le format principal de chaque chunk est le `.json` qui contient l'intégralité des métadonnées et du contenu (voir §5.7). Les `triplets_raw/` conservent la traçabilité chunk→triplets avant consolidation.

### 5.4 Execution Stats — `04_synthesis/execution_stats.json`

Fichier unique regroupant toutes les statistiques d'estimation et d'exécution :

```json
{
    "run_id": "20260207_1615_b3c8d",
    "document_id": "20260207_140000_a1b2c3d4",

    "estimation": {
        "total_tokens_estimated": 45000,
        "budget_per_agent": {
            "image_analyzer": 3000,
            "reference_extractor": 5000,
            "summarizer": 15000,
            "densifier": 8000,
            "decontextualizer": 18000,
            "concept_extractor": 12000,
            "entity_normalizer": 3000,
            "relation_normalizer": 2000,
            "community_summarizer": 4000,
            "profile_generator": 5000,
            "synthesizer": 5000,
            "critic": null
        }
    },

    "execution": {
        "start_time": "2026-02-07T16:15:00Z",
        "end_time": "2026-02-07T16:17:58Z",
        "duration_seconds": 178,
        "document_size_chars": 125000,
        "document_size_tokens_est": 31250,

        "totals": {
            "total_llm_calls": 42,
            "total_input_tokens": 38500,
            "total_output_tokens": 12300,
            "total_tokens": 50800,
            "total_cache_read_tokens": 8200,
            "total_cache_write_tokens": 3100,
            "estimated_cost_usd": 0.47,
            "cost_per_1k_chars": 0.00376
        },

        "per_agent": {
            "summarizer": {
                "calls": 15,
                "input_tokens": 18000,
                "output_tokens": 5500,
                "total_tokens": 23500,
                "cache_read_tokens": 4000,
                "cache_write_tokens": 1500,
                "avg_latency_ms": 2300,
                "max_latency_ms": 4100,
                "retry_count": 1,
                "failure_count": 0,
                "estimated_cost_usd": 0.21,
                "budget_allocated": 15000,
                "budget_usage_pct": 0.82,
                "status": "completed"
            }
        },

        "per_call_log": "calls_log.jsonl",

        "budget_summary": {
            "total_allocated": 45000,
            "total_consumed": 50800,
            "usage_pct": 1.13,
            "steps_degraded": [],
            "steps_failed": []
        }
    }
}
```

### 5.5 Per-Call Log — `04_synthesis/calls_log.jsonl`

Fichier JSONL append-only contenant chaque appel LLM individuel (voir section 20.2 pour le format `LLMCallRecord`). Colocalisé avec `execution_stats.json` dans `04_synthesis/` pour centraliser toutes les données de run.

### 5.6 Source Document Preservation

Le répertoire `source/` est créé une seule fois à la première exécution. Le modèle `SourceMetadata` est défini dans `core/models.py` (voir §3.2).

Le document source n'est **jamais dupliqué** dans les runs — les runs référencent `../../source/`.

### 5.7 Chunk Enriched JSON Format — `02_chunks/chunk_xxx.json`

Chaque chunk est persisté au format `.json` comme format principal et unique pour l'exploitation programmatique :
- **`.json`** — métadonnées complètes incluant le contenu texte (`content` + `original_content`). Exploitable par le pipeline, le RAG, et les outils d'analyse.
- **`_original.txt`** — texte brut avant décontextualisation (si activée). Permet le `diff` en CLI contre le contenu du `.json` pour vérifier les résolutions de références.

Le `.json` contient l'intégralité des champs du modèle `Chunk` (voir §3.2). Exemple :

```json
{
    "id": "chunk_003",
    "position": 2,
    "preceding_chunk_id": "chunk_002",
    "following_chunk_id": "chunk_004",

    "content": "Marc Dupont (CEO d'Acme Corp) a décidé de restructurer l'équipe produit d'Acme Corp...",
    "original_content": "Il a décidé de restructurer l'équipe produit. L'entreprise prévoit...",
    "content_type": "text",
    "embedded_images": [],
    "embedded_tables": [],

    "source_file": "rapport_annuel_2025.pdf",
    "source_pages": [12, 13],
    "source_sections": [
        {"title": "3.2 Stratégie organisationnelle", "level": 2}
    ],
    "byte_offset_start": 24580,
    "byte_offset_end": 26742,

    "char_count": 2162,
    "word_count": 341,
    "token_count_est": 520,
    "overlap_with_previous": 0,
    "fingerprint": "sha256:a4f8e2c1...",

    "primary_language": "fr",
    "secondary_languages": [],
    "is_multilingual": false,

    "decontextualization": {
        "applied": true,
        "resolved_references": [
            {
                "original_text": "Il",
                "resolved_text": "Marc Dupont (CEO d'Acme Corp)",
                "reference_type": "pronoun",
                "resolution_source": "preceding_chunk",
                "position_in_chunk": 0
            },
            {
                "original_text": "L'entreprise",
                "resolved_text": "Acme Corp",
                "reference_type": "definite_article",
                "resolution_source": "document_title",
                "position_in_chunk": 68
            }
        ],
        "context_window_size": 3,
        "confidence": 0.92
    },

    "embedding": null,
    "embedding_model": null,

    "context_summary": "Rapport annuel 2025 d'Acme Corp. Les chapitres 1-3 couvrent la performance financière : CA en hausse de 15%, marge opérationnelle stable. Marc Dupont (CEO) annonce une stratégie de restructuration de la division européenne avec un plan d'investissement de 50M€.",
    "global_summary": "Rapport annuel 2025 d'Acme Corp couvrant la performance financière (+15% CA), la restructuration de la division européenne sous la direction de Marc Dupont (CEO), le plan d'investissement 50M€, les perspectives 2026 dans le contexte de la transition énergétique, et les risques réglementaires liés à la directive CSRD.",
    "key_entities": ["Marc Dupont", "Acme Corp", "équipe produit"],
    "acronyms_expanded": {}
}
```

Le champ `embedding` est `null` pendant le pipeline d'analyse et reste `null` dans les fichiers `.json` persistés. Les embeddings sont stockés **uniquement** dans le vector DB (si `CHUNK_OUTPUT_MODE` inclut vectordb). Les fichiers `.json` ne sont jamais mis à jour après l'étape 2d — conformément au principe d'immutabilité des runs (§4.2). Le `embedding_model` est renseigné dans les fichiers `.json` uniquement à titre informatif pour la reproductibilité, avec la valeur configurée à l'exécution.

#### Interleaved Summarization + Decontextualization (step 2c)

Le Summarizer (Refine) et le Decontextualizer sont **entrelacés** dans une boucle séquentielle sur les chunks. Pour chaque chunk N :

1. **Decontextualize** : le chunk brut est décontextualisé en utilisant `refine_summary_{N-1}` (résumé cumulatif des chunks 1 à N-1) + `references` (de l'étape 1g) + la fenêtre glissante de chunks précédents.
2. **Refine** : le chunk décontextualisé alimente le Summarizer Refine, qui met à jour le résumé cumulatif → `refine_summary_N`.
3. **Store** : `refine_summary_N` est stocké dans `chunk_N.context_summary` — il capture la progression narrative du document jusqu'à ce point.

Ce cercle vertueux assure que :
- Le decontextualizer reçoit un résumé cumulatif de plus en plus riche à chaque chunk
- Le Refine travaille sur des chunks décontextualisés (entités résolues) → meilleure qualité du résumé
- Chaque chunk porte son propre `context_summary` qui reflète sa position dans la narration du document

Après la boucle, le Densifier (2d) produit le `global_summary` (vision d'ensemble du document entier), injecté dans **tous** les chunks avant écriture.

#### Deux niveaux de contexte par chunk

| Champ | Contenu | Produit par | Usage RAG |
|-------|---------|-------------|-----------|
| `context_summary` | Résumé cumulatif Refine jusqu'à ce chunk | Summarizer (step 2c-ii, per-chunk) | "D'où on vient, où en est la narration" — utile pour reranking et contextualisation d'un chunk retrouvé |
| `global_summary` | Résumé dense du document complet | Densifier (step 2d, post-loop) | "De quoi parle ce document" — utile pour filtrage et classification |

> **Note :** Le `context_summary` du premier chunk est minimal (résumé d'un seul chunk). Il s'enrichit progressivement. Le `context_summary` du dernier chunk est quasi-identique au `refine_summary_final` (avant densification). Le `global_summary` est identique pour tous les chunks d'un même document.

#### `chunks_index.json`

Index ordonné de tous les chunks avec métadonnées résumées (sans le contenu texte ni l'embedding) :

```json
{
    "total_chunks": 15,
    "chunking_strategy": "structural",
    "chunk_target_size": 2000,
    "chunk_overlap": 0,
    "decontextualization_enabled": true,
    "chunks": [
        {
            "id": "chunk_001",
            "position": 0,
            "source_pages": [1, 2],
            "source_sections": [{"title": "1. Introduction", "level": 1}],
            "char_count": 1856,
            "token_count_est": 445,
            "primary_language": "fr",
            "content_type": "text",
            "decontextualization_confidence": 0.95,
            "key_entities": ["Acme Corp", "rapport annuel"]
        }
    ]
}
```

---

## 6. Multi-Agent Architecture

### 6.1 Agent Definitions

| Agent | Strategy | Input | Output | Prompt File |
|-------|----------|-------|--------|-------------|
| **Reference Extractor** | Cross-ref detection (Phase 1) | Enriched text + structure map (bibliography, footnotes) | Citations, notes de bas de page, bibliographie | `reference_extractor.txt` |
| **Summarizer** | Refine incremental (Phase 2) | Decontextualized chunk + refine_summary_{N-1} | refine_summary_N → stored as chunk.context_summary | `summarizer.txt` |
| **Densifier** | Chain of Density 5 passes (Phase 2) | refine_summary_final | dense_summary → injected as chunk.global_summary in all chunks | `densifier.txt` |
| **Decontextualizer** | Coreference resolution (Phase 2) | Chunk + refine_summary_{N-1} + references + sliding window | Chunk with all ambiguous references resolved inline | `decontextualizer.txt` |
| **Concept Extractor** | Per-chunk qualified triplet extraction (Phase 3) | Decontextualized chunks (one at a time) | Qualified triplets per chunk `list[QualifiedTriplet]` | `concept_extractor.txt` |
| **Community Summarizer** | LLM summary per community (Phase 3) | Community member entities + relations + snippets | Community summary text per hierarchy level | `community_summarizer.txt` |
| **Profile Generator** | LLM entity/relation profiles (Phase 3) | Entity with all relations, qualifiers, source sentences | Concise textual profile (embedded for RAG vector search) | `profile_generator.txt` |
| **Synthesizer** | Structured synthesis (Phase 3) | Dense summary + community summaries + graphe + références | Analyse finale structurée + `list[Theme]` identifiés | `synthesizer.txt` |
| **Critic** | Cross-validation (Phase 3, optional) | All outputs | Validation report + adjusted confidence scores | `critic.txt` |

### 6.2 Orchestration — LangGraph

Le workflow est défini comme un graphe orienté acyclique (DAG) dans `pipeline/orchestrator.py` :

```
Phase 1 (sequential, orchestrator-driven):
  [Language] → [Extractor] → [Structure] → [Tables] → [Images] → [Content Merger] → [Reference Extractor]

Phase 2 (sequential, orchestrator-driven):
  [Chunker] → [Validator] → INTERLEAVED LOOP per chunk N:
                                [Decontextualizer(N)] → [Refine(N)]
                             → [Densifier] → [Write] → [VectorDB?]

Phase 3 DAG (LangGraph):
  [Concept Extractor] → [Merger (3 passes)] → [Graph Builder (L2/L3)] → [Community Detector] → [Community Integrator (L1→graph)] → [Community Summarizer] → [Profile Generator] → [Synthesizer] → [Critic?]

Phase 4 (sequential, orchestrator-driven):
  [Export] → [Vector Index (chunks + entity profiles + relation profiles + communities)] → [Graph DB Merge] → [Corpus Graph Linking]
```

- **Phase 1 + Phase 2** : séquentielles, pilotées directement par l'orchestrateur.
- **Interleaved Loop** (2c) : pour chaque chunk N, le decontextualizer s'exécute d'abord (en utilisant `refine_summary_{N-1}` comme contexte), puis le Refine met à jour le résumé cumulatif avec le chunk décontextualisé. Ce cercle vertueux améliore progressivement la qualité du résumé et de la décontextualisation.
- **Densifier** (2d) : s'exécute une seule fois après la boucle. Produit le `global_summary` injecté dans tous les chunks.
- **Decontextualizer** (2c-i) : peut activer le tool `chunk_lookup` si `DECONTEXTUALIZER_TOOL_USE=auto` et que la confidence est basse (voir §28.7).
- **Phase 3 DAG** : pipeline linéaire étendu. Le Concept Extractor produit des triplets qualifiés per-chunk. Après consolidation, le Graph Builder construit le graphe L2+L3 (sans L1). Le Community Detector identifie les communautés hiérarchiques (Leiden) sur le sous-graphe L2. Le Community Integrator injecte les nœuds L1 et arêtes `encompasses` dans le graphe. Le Community Summarizer génère les résumés par communauté, et le Profile Generator crée les profils textuels des entités et relations clés. Le Synthesizer utilise l'ensemble (dense_summary + community summaries + graph + profiles).
- **Phase 4** : indexation étendue (chunks + entity profiles + relation profiles + community summaries en vector DB). Si `CONSOLIDATOR_ENABLED=true`, le Pass 1 (Linking) s'exécute immédiatement après ingestion.
- **Critic** : optionnel, après Synthesizer (activé via `CRITIC_AGENT_ENABLED=true`)

### 6.3 Shared State — `pipeline/state.py`

#### `ExtractionContext` — data flow for phases 1-2

Les phases 1 (extraction) et 2 (chunking + pré-analyse) produisent des données intermédiaires qui alimentent le pipeline d'analyse (phase 3). Ces données sont regroupées dans un objet `ExtractionContext`, construit incrémentalement par l'orchestrateur :

```python
# pipeline/state.py
class ExtractionContext:
    """Intermediate data from phases 1-2, consumed by orchestrator. Not passed to DAG agents."""
    extraction_result: ExtractionResult
    enriched_text: str              # Copied from extraction_result.enriched_text for convenience — always identical
    references: list[Reference]     # Produced by Reference Extractor (step 1g)
    source_metadata: SourceMetadata
```

| Field | Type | Set By |
|-------|------|--------|
| `extraction_result` | `ExtractionResult` | Extraction phase (1b-1f) |
| `enriched_text` | `str` | Content merger (1f) |
| `references` | `list[Reference]` | Reference Extractor (1g) |
| `source_metadata` | `SourceMetadata` | Run manager |

L'orchestrateur utilise `ExtractionContext` pour :

1. Alimenter le Reference Extractor avec `enriched_text` + `extraction_result.structure` (1g)
2. Alimenter le chunker avec `enriched_text` + `extraction_result.structure` (2a)
3. Piloter la boucle entrelacée Decontextualizer + Refine (2c) avec `references`
4. Alimenter le Densifier avec le `refine_summary_final` (2d)
5. Initialiser le `PipelineState` avec `language`, `structure_map`, `chunks`, `references`, `dense_summary`

`ExtractionContext` n'est **pas** passé aux agents de la phase 3 — seul `PipelineState` circule dans le DAG LangGraph.

#### `PipelineState` — data flow for phase 3 (DAG LangGraph)

Le `PipelineState` est un objet Pydantic qui circule entre les agents :

```python
class PipelineState(BaseModel):
    """Shared state circulated through the LangGraph DAG during phase 3."""
    # --- Set by orchestrator ---
    run_id: str                                           # Run manager
    chunks: list[Chunk]                                   # Chunking (2a) + Interleaved (2c) + Densifier (2d)
    language: str                                         # Language detector
    structure_map: DocumentStructure                      # Structure detector
    references: list[Reference]                           # Reference Extractor (phase 1, step 1g)

    # --- Set by phase 2 agents ---
    refine_summary: str                                   # Summarizer (phase 2, step 2c)
    dense_summary: str                                    # Densifier (phase 2, step 2d)

    # --- Set by phase 3 agents ---
    raw_triplets: dict[str, list[QualifiedTriplet]]       # Concept Extractor (keyed by chunk_id)
    entity_normalizations: list[EntityNormalization]       # Graph Merger (entity normalization pass)
    relation_taxonomy: list[RelationTaxonomyEntry]        # Graph Merger (relation normalization pass)
    consolidated_triplets: list[ConsolidatedTriplet]       # Graph Merger (consolidation pass)
    graph: Any                                            # NetworkX Graph — Graph Builder
    communities: CommunityHierarchy                       # Community Detector (Leiden)
    community_summaries: dict[str, CommunitySummary]       # Community Summarizer (keyed by community_id)
    entity_profiles: dict[str, EntityProfile]              # Profile Generator (keyed by canonical_name)
    relation_profiles: list[RelationProfile]               # Profile Generator (key relations only)
    final_analysis: str                                   # Synthesizer
    themes: list[Theme]                                   # Synthesizer (structured output)
    critic_report: dict | None = None                     # Critic findings (if CRITIC_AGENT_ENABLED)

    # --- Cross-cutting ---
    confidence_scores: dict[str, float]                   # Each agent
    token_budget: TokenBudget                             # Token budget manager
    call_records: list[LLMCallRecord]                     # Call logger (accumulated)
    rag_context: RAGContext | None = None                  # RAG enricher (set per-agent by orchestrator, §26.4.1)
```

> **Note :** Le champ `chunks` contient les objets `Chunk` complets. Après la boucle entrelacée (2c), chaque chunk porte : `content` (version décontextualisée), `original_content` (texte d'origine), `context_summary` (résumé cumulatif Refine jusqu'à ce chunk). Après le Densifier (2d), le champ `global_summary` est injecté dans tous les chunks. Les agents en aval travaillent toujours sur `chunk.content`.

---

## 7. Image Analysis

### 7.1 Image Classification

`image_analyzer.py` détecte le type d'image avant analyse :

| Type | Prompt Strategy | Output |
|------|----------------|--------|
| `diagram` | Extract structure, flows, components, relationships | Structured description + entity list |
| `chart` | Extract data, axes, legends, trends | Data description + key figures |
| `table_image` | Reconstruct as markdown table | Markdown table |
| `photo` | Factual description, contextual relevance | Descriptive text |
| `screenshot` | Extract visible text + UI structure | Text content + layout description |
| `decorative` | Skip or minimal description | Short caption or `[decorative image]` |

### 7.2 Content Injection Format

`content_merger.py` injecte les descriptions dans le texte enrichi avec des balises distinctes :

```
[...preceding text...]

<<<IMAGE_CONTENT id="img_001" type="diagram" source="page_12_fig_3">>>
Description: 3-layer architecture showing data flows between
the acquisition module, the processing pipeline, and storage.
Entities: Acquisition Module, ETL Pipeline, PostgreSQL Database
Relations: acquisition → feeds → pipeline ; pipeline → writes to → database
<<<END_IMAGE_CONTENT>>>

[...following text...]
```

Pour les tableaux extraits depuis des images :

```
<<<TABLE_CONTENT id="tbl_001" source="page_5_fig_1" origin="image">>>
| Column A | Column B | Column C |
|----------|----------|----------|
| Value 1  | Value 2  | Value 3  |
<<<END_TABLE_CONTENT>>>
```

Pour les tableaux extraits structurellement (pas depuis des images) :

```
<<<TABLE_CONTENT id="tbl_002" source="page_8" origin="structured">>>
| Column A | Column B |
|----------|----------|
| Value 1  | Value 2  |
<<<END_TABLE_CONTENT>>>
```

### 7.3 Chunking Constraints

Le chunker **ne doit jamais** couper au milieu d'un bloc `<<<IMAGE_CONTENT>>>...<<<END_IMAGE_CONTENT>>>` ou `<<<TABLE_CONTENT>>>...<<<END_TABLE_CONTENT>>>`. Ces blocs sont traités comme atomiques et rattachés au chunk contenant le texte qui les précède.

---

## 8. Document Fingerprinting and Cache

### 8.1 Multi-Level Fingerprinting Strategy

Inspiré de l'approche Shazam (constellation fingerprinting), le système utilise plusieurs niveaux de hash pour détecter les correspondances exactes et approximatives.

#### Level 1 — Exact Hash (SHA-256)

```
hash(raw_bytes_of_document) → exact_hash
```

- Détecte les fichiers strictement identiques (octet par octet)
- Rapide, déterministe
- Échoue si un seul octet change (reformatage, métadonnées modifiées)

#### Level 2 — Content Hash (SHA-256 on normalized text)

```
normalize(extracted_text) → strip whitespace, lowercase, remove punctuation → content_hash
```

- Détecte les documents identiques en contenu mais différents en format (PDF vs DOCX du même texte)
- Insensible aux changements de mise en forme

#### Level 3 — Structural Fingerprint (SimHash)

```
simhash(shingles(extracted_text, n=3)) → structural_fingerprint
```

- Locality-Sensitive Hashing : documents proches produisent des hash proches
- Détecte les versions modifiées d'un même document (ajout/suppression de paragraphes)
- Seuil de distance de Hamming configurable pour définir "similarité suffisante"

#### Level 4 — Semantic Fingerprint (MinHash on embeddings)

```
embed(chunks) → minhash(chunk_embeddings) → semantic_fingerprint
```

- Détecte les documents sémantiquement proches même si reformulés
- Utile pour détecter des traductions, résumés, ou versions réécrites
- Plus coûteux (nécessite des embeddings)

#### Level 5 — Section Constellation (inspired by Shazam)

```
for each section:
    anchor_point = hash(section_title + first_sentence + last_sentence)
constellation = sorted(anchor_points)
```

- Crée une "constellation" de points d'ancrage sur le document
- Si N% des points d'ancrage matchent un document connu, c'est une variante
- Détecte les rééditions, versions augmentées, extraits partiels
- Seuil configurable (ex: 70% de match = même document)

### 8.2 Fingerprint Model — `cache/models.py`

```python
class DocumentFingerprint:
    exact_hash: str              # Level 1 - SHA-256 on raw bytes
    content_hash: str            # Level 2 - SHA-256 on normalized text
    structural_hash: str         # Level 3 - SimHash
    semantic_hash: str           # Level 4 - MinHash
    constellation: list[str]     # Level 5 - Section anchor points
    timestamp: datetime
    source_format: str

class CacheEntry:
    document_id: str
    fingerprint: DocumentFingerprint
    result_path: str             # Path to analysis output directory
    created_at: datetime
    pipeline_version: str

class CacheLookupResult:
    hit_level: str | None        # "exact" | "content" | "structural" | "semantic" | "constellation" | None
    matched_entry: CacheEntry | None
    similarity_score: float | None  # For levels 3-5 (distance/similarity metric)
    is_reusable: bool            # Whether cached result can be directly returned
```

### 8.3 Cache Lookup Logic

```
1. exact_hash match?        → EXACT HIT    → return cached result
2. content_hash match?      → CONTENT HIT  → return cached result
3. simhash distance < T1?   → NEAR MATCH   → log warning, optionally reuse partial results
4. minhash similarity > T2? → SEMANTIC HIT  → log, suggest reuse
5. constellation overlap > T3? → VARIANT   → log, link to known doc
6. No match                 → NEW DOCUMENT → full pipeline
```

### 8.4 Cache Storage — `cache/base_cache_store.py`

Le backend de cache est abstrait derrière l'interface `BaseCacheStore` (voir section 30.4). Le backend par défaut est JSON :

```
{cache_root}/
    ├── index.json                  # Fingerprint → document_id mapping
    └── entries/
        ├── {document_id}.json      # Full fingerprint + result reference
        └── ...
```

Les backends SQLite, Redis, et ArangoDB offrent de meilleures performances pour de grands volumes de documents.

> **Principe : JSON toujours en parallèle.** Quel que soit le backend de cache configuré, un export JSON de chaque `CacheEntry` est **systématiquement** écrit dans la structure documentaire (`00_metadata/cache_entry.json`). Ce fichier permet de reconstruire les bases de données de cache à partir de l'arborescence de fichiers, indépendamment du backend configuré. C'est la source de vérité pour le rebuild.

---

## 9. Language Detection and Prompt Selection

### 9.1 Document-Level Detection — `extraction/language_detector.py`

- Utilise `lingua-py` sur le texte extrait pour déterminer la langue principale
- Si `metadata.language` est fourni, il prend priorité
- La langue principale détermine le jeu de prompts par défaut

### 9.2 Chunk-Level Detection

Chaque chunk peut contenir du contenu dans une langue différente (citations, références, passages traduits). Le chunker enrichit chaque `Chunk` avec :

| Field | Type | Description |
|-------|------|-------------|
| `primary_language` | `str` | Langue principale du chunk |
| `secondary_languages` | `list[str]` | Langues secondaires détectées |
| `is_multilingual` | `bool` | `true` si plus d'une langue détectée |

La détection chunk-level utilise `lingua-py` en mode multi-langue. Seuil de détection : un passage de >50 tokens dans une langue différente est signalé.

### 9.3 Prompt Selection

All prompts are English-only. Each agent uses a single prompt template regardless of document language. The prompt explicitly instructs the LLM to process content in its original language without translation.

```
pipeline/prompts/
    ├── summarizer.txt
    ├── densifier.txt
    ├── decontextualizer.txt
    ├── concept_extractor.txt
    ├── reference_extractor.txt
    ├── synthesizer.txt
    ├── critic.txt
    ├── community_summarizer.txt
    ├── profile_generator.txt
    ├── entity_normalizer.txt
    ├── relation_normalizer.txt
    └── image_analyzer.txt
```

Convention de nommage : `{agent}.txt`.

---

## 10. Structure Detection

### 10.1 Détection — `extraction/structure_detector.py`

Analyse le texte extrait et les métadonnées du document pour identifier :

| Element | Detection Method | Usage |
|---------|-----------------|-------|
| Table des matières | Pattern matching (numérotation, indentation) | Guide le chunking structurel |
| Chapitres / Sections | Heading detection (formatting, numbering) | Délimite les chunks |
| Annexes | Keywords ("Annex", "Appendix", position en fin) | Traitement séparé |
| Bibliographie | Keywords ("References", "Bibliography") + citation patterns | Alimenter Reference Extractor |
| Notes de bas de page | Superscript detection, footnote sections | Réinjecter dans le texte |
| Index | Alphabetical structure + page numbers | Enrichir le graphe conceptuel |

### 10.2 Output : `DocumentStructure`

Defined in `core/models.py` (see section 3.2). Fields: `has_toc`, `sections: list[Section]`, `has_bibliography`, `bibliography_position`, `has_annexes`, `annexes: list[Section]`, `footnotes: list[Footnote]`, `has_index`.

---

## 11. Table Extraction

### 11.1 Sources

| Source | Method |
|--------|--------|
| Structured tables (PDF, DOCX) | Direct extraction via format-specific parser |
| Table images | LLM Vision reconstruction |
| Markdown tables | Regex parsing |

### 11.2 Output Format

Toutes les tables sont normalisées en Markdown et injectées via `<<<TABLE_CONTENT>>>` blocs dans le texte enrichi (voir section 7.2).

---

## 12. Cross-Reference and Citation Extraction

### 12.1 Agent — `agents/reference_extractor.py`

**Pipeline position :** Phase 1, step 1g (after content merger, before chunking).

Operates on the **full enriched text** (not individual chunks), which provides better context for resolving cross-references, citations, and bibliographic entries. Extracting references at the document level before chunking ensures that:

- The decontextualizer (step 2c-i) can resolve "voir chapitre 3" or "cf. [12]" against known references
- Cross-document citation resolution (via RAG) is available early in the pipeline
- The reference data enriches metadata used for chunk decontextualization

Extrait :

- Citations inline (auteur, année)
- Notes de bas de page et renvois
- Références bibliographiques complètes
- Renvois internes ("voir chapitre 3", "cf. figure 2")

### 12.2 Integration into Knowledge Graph

Les références extraites sont injectées dans le graphe via `graph/reference_linker.py` :

- Les citations créent des relations `(document) → cites → (source)`
- Les renvois internes créent des relations `(section A) → references → (section B)`
- Les notes de bas de page enrichissent les nœuds concepts associés

---

## 13. Triplet Extraction and Consolidation Strategy

### 13.1 Problem

L'extraction de triplets `(subject, predicate, object)` se fait par chunk. Cela produit des entités non normalisées et des doublons sémantiques inter-chunks :

- `("EU", "regulates", "AI")` et `("European Union", "regulates", "artificial intelligence")` — même triplet, formes différentes
- `("Acme Corp", "employs", "500 people")` et `("Acme", "has_employees", "500")` — même fait, relation et entités variantes
- `("CSMS", "est requis par", "UN R155")` et `("Cybersecurity Management System", "required_by", "UN R155")` — acronyme + multilingue

Sans consolidation, le knowledge graph contient des nœuds dupliqués et des arêtes redondantes, ce qui dégrade la qualité de l'analyse, du RAG, et de la visualisation.

### 13.2 Three-Pass Consolidation Pipeline

La consolidation s'exécute dans `graph/merger.py` qui orchestre 3 passes séquentielles :

```
[Concept Extractor]                   [graph/merger.py]
       │                                     │
       ├── chunk_001 → QualifiedTriplets ─────────►│
       ├── chunk_002 → QualifiedTriplets ─────────►├── Pass 1: Entity Normalization
       ├── chunk_003 → QualifiedTriplets ─────────►│     → entity_normalization.json
       └── ...                               │
                                             ├── Pass 2: Relation Normalization
                                             │     → relation_taxonomy.json
                                             │
                                             ├── Pass 3: Triplet Dedup + Merge
                                             │     → triplets.json (consolidated)
                                             │
                                             └──► [graph/builder.py] → graph.json (L2+L3 only)
                                                          │
                                                  [community_detector.py] → communities.json
                                                          │
                                                  [community_integrator.py] → graph.json (L1+L2+L3)
                                                          │
                                                  [community_summarizer.py] → community_summaries.json
                                                          │
                                                  [profile_generator.py] → entity_profiles.json
```

### 13.3 Pass 1 — Entity Normalization (`graph/entity_normalizer.py`)

**Objectif :** Regrouper les variantes d'une même entité sous un nom canonique.

**Technique hybride (embedding clustering + LLM validation) :**

1. **Extraction de toutes les entités uniques** — parcourir tous les `QualifiedTriplet` et collecter l'ensemble des subjects et objects distincts (forme brute)
2. **Embedding** — calculer l'embedding de chaque entité unique via le provider configuré (`EMBEDDING_PROVIDER`)
3. **Clustering par similarité cosinus** — regrouper les entités dont la similarité dépasse un seuil configurable (`ENTITY_SIMILARITY_THRESHOLD`, défaut: `0.85`). Algorithme : agglomerative clustering avec seuil de distance. Compatible GPU via cuML (voir §33)
4. **Validation LLM** — pour chaque cluster de taille > 1, un appel LLM :
   - Confirme que les entités du cluster désignent bien la même chose
   - Choisit le nom canonique (forme la plus complète et non ambiguë)
   - Attribue un `entity_type` (`person`, `organization`, `concept`, `location`, `document`, `technology`)
   - Sépare les faux positifs (entités proches mais distinctes → clusters séparés)

**Output : `entity_normalization.json`**

```json
[
    {
        "canonical_name": "European Union",
        "aliases": ["EU", "l'UE", "Union européenne", "the EU"],
        "entity_type": "organization",
        "occurrence_count": 23,
        "source_chunk_ids": ["chunk_001", "chunk_003", "chunk_007", "chunk_012"]
    },
    {
        "canonical_name": "Cybersecurity Management System",
        "aliases": ["CSMS", "système de gestion de la cybersécurité"],
        "entity_type": "concept",
        "occurrence_count": 15,
        "source_chunk_ids": ["chunk_002", "chunk_004", "chunk_005"]
    }
]
```

**Optimisation de coût :** Les entités singletons (cluster de taille 1) ne nécessitent pas d'appel LLM. Seuls les clusters ambigus (taille > 1) sont validés. En pratique, ~60-70% des entités sont singletons.

### 13.4 Pass 2 — Relation Normalization (`graph/relation_normalizer.py`)

**Objectif :** Mapper les relations brutes vers une taxonomie canonique pour homogénéiser les arêtes du graphe.

**Taxonomie de base :** Le système utilise une taxonomie prédéfinie extensible, stockée dans `graph/taxonomy.py` :

| Category | Canonical Relations |
|----------|-------------------|
| **Hierarchical** | `is_a`, `part_of`, `contains`, `subclass_of` |
| **Composition** | `has_component`, `composed_of`, `member_of` |
| **Causality** | `causes`, `enables`, `prevents`, `leads_to` |
| **Governance** | `regulates`, `requires`, `complies_with`, `enforces` |
| **Temporal** | `precedes`, `follows`, `concurrent_with` |
| **Attribution** | `created_by`, `authored_by`, `owned_by`, `employs` |
| **Location** | `located_in`, `applies_to`, `operates_in` |
| **Association** | `related_to`, `similar_to`, `contrasts_with`, `references` |
| **Production** | `produces`, `generates`, `implements`, `defines` |

**Technique (LLM batch classification) :**

1. **Extraction des relations uniques** — collecter toutes les formes brutes de `predicate` distinctes
2. **Classification par batch** — envoyer les relations uniques au LLM en un seul appel (ou par batch de ~50) avec la taxonomie comme référence
3. Le LLM retourne pour chaque relation brute : la relation canonique, la catégorie, et la directionnalité
4. Les relations qui ne correspondent à aucune entrée de la taxonomie sont soit ajoutées comme nouvelles entrées (si pertinentes et récurrentes), soit mappées vers `related_to` (fourre-tout)

**Output : `relation_taxonomy.json`**

```json
[
    {
        "canonical_relation": "regulates",
        "original_forms": ["regulates", "réglemente", "governs", "encadre", "est régulé par"],
        "category": "governance",
        "is_directional": true
    },
    {
        "canonical_relation": "part_of",
        "original_forms": ["is part of", "fait partie de", "belongs to", "composant de"],
        "category": "composition",
        "is_directional": true
    }
]
```

**Optimisation :** Les relations identiques (string exact match) ne nécessitent pas de classification. Seules les formes uniques non triviales sont envoyées au LLM. Typiquement ~30-50 relations uniques par document.

### 13.5 Pass 3 — Triplet Dedup and Merge (`graph/triplet_consolidator.py`)

**Objectif :** Appliquer les normalisations et fusionner les triplets identiques.

**Algorithme :**

1. **Substitution** — pour chaque `QualifiedTriplet`, remplacer subject et object par leur `canonical_name`, et predicate par sa `canonical_relation`
2. **Hachage** — calculer un hash de `(canonical_subject, canonical_predicate, canonical_object)` normalisé (lowercase, strip)
3. **Groupement** — regrouper les triplets ayant le même hash
4. **Fusion** — pour chaque groupe, produire un `ConsolidatedTriplet` :
   - `confidence` = `max(confidence de chaque QualifiedTriplet source)` — on garde le signal le plus fort
   - `source_chunk_ids` = union de tous les `source_chunk_id`
   - `occurrence_count` = nombre de QualifiedTriplets dans le groupe
   - `original_forms` = formes brutes uniques du predicate avant normalisation
   - `context_sentences` = collecte de tous les `context_sentence` uniques
5. **Qualifier merge** — pour chaque groupe :
   - Union de toutes les clés de qualifiers
   - Si même clé avec valeurs identiques → garder la valeur unique
   - Si même clé avec valeurs différentes → stocker comme `list[str]`
   - `temporal_scope` : garder le scope le plus précis (granularité la plus fine)

**Règle de confiance :** Les triplets confirmés par plusieurs chunks (occurrence_count > 1) voient leur confiance boostée par un facteur : `final_confidence = min(1.0, max_confidence × (1 + 0.1 × (occurrence_count - 1)))`. Un triplet extrait 5 fois indépendamment est plus fiable qu'un triplet extrait une seule fois.

**Output : `triplets.json` (consolidated)**

```json
{
    "consolidation_stats": {
        "total_raw_triplets": 187,
        "unique_entities_before": 95,
        "unique_entities_after": 62,
        "unique_relations_before": 43,
        "unique_relations_after": 18,
        "consolidated_triplets": 124,
        "dedup_ratio": 0.34
    },
    "triplets": [
        {
            "subject": "European Union",
            "predicate": "regulates",
            "object": "Artificial Intelligence",
            "source_chunk_ids": ["chunk_003", "chunk_007", "chunk_012"],
            "occurrence_count": 3,
            "confidence": 0.95,
            "original_forms": ["regulates", "réglemente"],
            "qualifiers": {
                "instrument": "AI Act",
                "scope": "high-risk systems"
            },
            "temporal_scope": {
                "type": "point",
                "start": "2025",
                "granularity": "year",
                "raw_expression": "through the AI Act"
            },
            "context_sentences": [
                "The EU has introduced comprehensive regulation of AI systems through the AI Act.",
                "L'Union européenne réglemente les systèmes d'IA via le règlement sur l'IA."
            ]
        }
    ]
}
```

### 13.6 Per-Chunk Qualified Triplets Format — `03_concepts/triplets_raw/triplets_chunk_xxx.json`

Chaque fichier contient les triplets qualifiés extraits d'un seul chunk, avant normalisation :

```json
{
    "chunk_id": "chunk_003",
    "extraction_model": "anthropic:claude-sonnet-4-20250514",
    "extraction_confidence": 0.88,
    "triplets": [
        {
            "subject": "EU",
            "predicate": "regulates",
            "object": "AI systems",
            "confidence": 0.92,
            "context_sentence": "The EU has introduced comprehensive regulation of AI systems through the AI Act.",
            "qualifiers": {
                "instrument": "AI Act",
                "scope": "high-risk systems",
                "effective_date": "2025"
            },
            "temporal_scope": {
                "type": "point",
                "start": "2025",
                "granularity": "year",
                "raw_expression": "through the AI Act"
            }
        },
        {
            "subject": "AI Act",
            "predicate": "requires",
            "object": "risk assessment",
            "confidence": 0.87,
            "context_sentence": "The AI Act requires providers of high-risk AI systems to conduct thorough risk assessments.",
            "qualifiers": {
                "target": "providers of high-risk AI systems",
                "type": "mandatory"
            },
            "temporal_scope": null
        }
    ]
}
```

**Qualifiers** capturent les relations n-aires sans nécessiter d'hypergraphe : les informations contextuelles (scope, instrument, conditions, montants, durées) sont stockées comme métadonnées de l'arête. Le Concept Extractor est instruit de détecter ces qualificateurs dans sa prompt.

**Temporal scope** encode la dimension temporelle interne du contenu (quand le fait est vrai), avec les types `point`, `range`, `recurring`. C'est distinct de la temporalité externe (quand le fait a été ingéré) qui est gérée par les métadonnées de cycle de vie du nœud/arête.

Ces fichiers sont conservés pour la traçabilité — ils permettent de remonter de n'importe quel triplet consolidé vers le(s) chunk(s) source(s) et la phrase d'origine.

> **Clarification :** `extraction_confidence` est le score global du Concept Extractor pour ce chunk (sa confiance dans la qualité globale de l'extraction). Chaque triplet a son propre `confidence` score (confiance dans ce triplet spécifique). `extraction_confidence` = score de confiance de l'agent (utilisé dans §14.1), tandis que `triplet.confidence` = score per-triplet (utilisé par le consolidator §13.5 pour le merge).

### 13.7 Configuration

| Env Var | Default | Description |
|---------|---------|-------------|
| `ENTITY_SIMILARITY_THRESHOLD` | `0.85` | Cosine similarity threshold for entity clustering |
| `RELATION_TAXONOMY_EXTENSIBLE` | `true` | Allow LLM to add new relation types not in base taxonomy |
| `TRIPLET_CONFIDENCE_BOOST` | `true` | Boost confidence for multi-chunk triplets |

### 13.8 Impact on Downstream Components

- **`graph/builder.py`** travaille exclusivement sur `triplets.json` (consolidé) — construit les nœuds L2+L3 et les arêtes du graphe. Les nœuds du graphe sont les `canonical_name` des entités, les arêtes sont les `canonical_relation`. Les nœuds L1 (communities) sont ajoutés ultérieurement par `community_integrator.py`.
- **`graph/reference_linker.py`** utilise `entity_normalization.json` pour mapper les références extraites (§12) vers les nœuds canoniques du graphe
- **RAG indexing** (`rag/indexer.py`) indexe les entités canoniques et leurs aliases dans le graph DB, permettant une recherche par n'importe quelle forme variante
- **Synthesizer** reçoit le graphe consolidé — pas de bruit lié aux doublons

### 13.9 Contextual Pyramid v2 — "Living Knowledge" Graph Architecture

Le knowledge graph est structuré en **Contextual Pyramid** : les layers représentent le **rôle fonctionnel** du nœud dans la narration, pas son type ontologique. La classification repose sur des critères mesurables.

L'architecture sépare deux espaces :

- **Document Graph** (un par document) : L1/L2/L3 spécifiques, stocké dans `04_synthesis/graph.json`
- **Corpus Graph** (un seul, partagé, graph DB) : connecte les connaissances de tous les documents (voir §13.12)

#### 13.9.1 Layer Definitions

| Layer | Name | Content | Critères mesurables | Created By |
|:-----:|------|---------|---------------------|------------|
| **1** | **TOPICS** | Communautés thématiques émergentes (5-15 par document) | Clusters Leiden de densité ≥ seuil modularity | Community Detector (Leiden) |
| **2** | **ACTORS** | Entités nommées, référençables, participent à des relations | Nommé + ≥1 relation + ≥1 propriété | Concept Extractor → Entity Normalizer (layer par défaut) |
| **3** | **EVIDENCE** | Valeurs littérales, métriques, attributs | Valeur littérale + n'a de sens qu'attaché à L2 + vérifiable | Concept Extractor (literal detection) |

**L1 — TOPICS** : **ne sont PAS extraits directement du texte**. Ils émergent de la structure relationnelle via l'algorithme de Leiden appliqué sur les nœuds L2 du Document Graph (étape 3d). Chaque communauté Leiden de niveau supérieur devient un nœud L1 avec un `community_summary` généré par LLM.

**L2 — ACTORS** : layer par défaut du Concept Extractor. Tout nœud nommé et participant à au moins une relation est L2.

**L3 — EVIDENCE** : valeurs littérales détectées par le Concept Extractor (nombres, dates, pourcentages, mesures). Pas de sens isolément — toujours rattaché à un L2 via `has_attribute` ou `measured_at`.

#### 13.9.2 Layer Classification Logic (`graph/layers/layer_classifier.py`)

Module **pur** (aucun side-effect sur le graphe existant). Prend un graphe NetworkX en entrée, retourne un mapping `{node_id: layer}`.

```python
def classify_layers(graph: nx.Graph) -> dict[str, int]:
    """
    Assign layer to each node based on measurable criteria.
    L2 is default. L3 for literals. L1 assigned later by community_detector.
    """
    layers = {}
    for node, data in graph.nodes(data=True):
        if data.get("is_literal", False):
            layers[node] = 3  # EVIDENCE
        else:
            layers[node] = 2  # ACTORS (default)
    return layers
    # L1 assignment is done by community_detector.py AFTER Leiden
```

**Principe de ségrégation :** le layer_classifier ne fait QUE la classification L2/L3 statique. L'assignation L1 est faite par un module séparé (community_detector.py) après le clustering Leiden. Aucun couplage entre les deux.

#### 13.9.3 Inter-Layer Relations

| From → To | Relation Types | Created By |
|-----------|----------------|------------|
| L1 → L2 | `encompasses` (Actor dans ≥50% chunks de ce Topic) | Community Detector |
| L1 ↔ L1 | `related_to` (co-occurrence ≥3 chunks), `part_of` (structure doc) | Community Detector |
| L2 → L3 | `has_attribute` (littéral statique), `measured_at` (valeur temporelle) | Graph Builder |
| L2 ↔ L2 | Toutes les relations canoniques standard (§13.4) | Graph Builder |
| L3 → Chunk | `source_evidence` (ancrage au texte source) | Graph Builder |

#### 13.9.4 Node Schema

Chaque nœud porte les attributs suivants :

| Attribute | Type | Description |
|-----------|------|-------------|
| `canonical_name` | `str` | Nom canonique (clé du nœud) |
| `layer` | `int` | 1 (topic), 2 (actor), 3 (evidence) |
| `entity_type` | `str` | Type détaillé (`person`, `organization`, `concept`, `value`, etc.) |
| `aliases` | `list[str]` | Formes variantes |
| `occurrence_count` | `int` | Nombre de mentions dans le document |
| `source_chunk_ids` | `list[str]` | Chunks où l'entité apparaît |
| `source_documents` | `list[SourceProvenance]` | Provenance multi-document (voir §13.9.6) |
| `confidence` | `float` | [0,1] — fiabilité de l'extraction |
| `salience` | `float` | [0,1] — importance (chunk_coverage × document_coverage) |
| `corroboration` | `int` | Nombre de sources indépendantes ayant mentionné ce nœud |
| `staleness` | `float` | [0,1] — fraîcheur (decay function sur last_updated_at) |
| `first_seen_at` | `datetime` | Première ingestion |
| `last_updated_at` | `datetime` | Dernière mise à jour |
| `last_corroborated_at` | `datetime` | Dernière corroboration par un document |
| `ingestion_version` | `str` | Version pipeline lors de la dernière mise à jour |
| `community_id` | `str \| None` | ID de la communauté L1 (L2 nodes only) |

> **Note :** Les profils textuels des entités L2 sont stockés dans `entity_profiles.json` (fichier séparé), PAS sur le nœud du graphe. Cela évite un couplage write-back entre profile_generator et graph.json. Le RAG les récupère depuis le vector DB (collection `entity_profiles`), pas depuis le graphe.

#### 13.9.5 Edge Schema

Chaque arête porte les attributs suivants :

| Attribute | Type | Description |
|-----------|------|-------------|
| `relation_type` | `str` | Type de relation canonique (§13.4) |
| `confidence` | `float` | [0,1] — fiabilité |
| `occurrence_count` | `int` | Nombre d'occurrences cross-chunks |
| `source_chunk_ids` | `list[str]` | Chunks où la relation apparaît |
| `source_documents` | `list[SourceProvenance]` | Provenance multi-document |
| `qualifiers` | `dict[str, str] \| None` | Qualificateurs n-aires (scope, instrument, condition, etc.) |
| `temporal_scope` | `TemporalScope \| None` | Quand le fait est vrai (contenu) |
| `context_sentences` | `list[str]` | Phrases source (sentence-level traceability) |
| `first_seen_at` | `datetime` | Première ingestion |
| `last_updated_at` | `datetime` | Dernière mise à jour |
| `corroboration` | `int` | Sources indépendantes |
| `original_forms` | `list[str]` | Formes verbales d'origine avant normalisation |

#### 13.9.6 Source Provenance Model

> Défini dans `core/models.py` (voir §3.2). Utilisé dans Node Schema (`source_documents`), Edge Schema (`source_documents`), CNode, XEdge.

#### 13.9.7 Temporal Scope Model

> Défini dans `core/models.py` (voir §3.2). Utilisé dans QualifiedTriplet, ConsolidatedTriplet, Edge Schema, RelationProfile.

#### 13.9.8 Scoring Metrics

Chaque nœud et arête porte 4 métriques combinables en un score composite RAG :

| Metric | Range | Calcul | Usage |
|--------|-------|--------|-------|
| `confidence` | [0,1] | Moyenne pondérée des scores d'extraction | Fiabilité intrinsèque |
| `salience` | [0,1] | chunk_coverage × document_coverage | Importance dans le document |
| `corroboration` | int ≥ 1 | Count de sources indépendantes | Robustesse cross-documents |
| `staleness` | [0,1] | `1 - exp(-ln(2) × days_since_update / CONSOLIDATOR_DECAY_HALFLIFE_DAYS)` | Fraîcheur |

**Score composite RAG :**

```
relevance_score = w_confidence × confidence
               + w_salience × salience
               + w_freshness × (1 - staleness)
               + w_corroboration × min(corroboration / cap, 1.0)
```

Poids configurables via `.env` (voir §13.14).

### 13.10 Community Detection & Summaries (`graph/layers/`)

#### 13.10.1 Purpose

Identifier les clusters thématiques émergents dans le Document Graph pour créer les L1 Topics. Remplace la classification `entity_type → layer` par un clustering basé sur la structure relationnelle réelle.

#### 13.10.2 Algorithm — Hierarchical Leiden (`community_detector.py`)

Module **pur** : prend un graphe NetworkX (L2 nodes + edges), retourne une `CommunityHierarchy`.

**Input/Output models :**

```python
class Community(BaseModel):
    """Single community detected by Leiden algorithm."""
    community_id: str                 # "comm_001"
    level: int                        # Hierarchy level (0 = leaf, higher = broader)
    members: list[str]                # L2 entity canonical_names
    parent_id: str | None             # Parent community (higher level)
    children_ids: list[str]           # Child communities (lower level)
    modularity_score: float           # Leiden quality metric for this community
    chunk_coverage: list[str]         # chunk_ids represented by member entities

class CommunityHierarchy(BaseModel):
    """Full hierarchical community structure from Leiden."""
    communities: list[Community]
    num_levels: int                   # Number of hierarchy levels
    resolution: float                 # Leiden resolution parameter used
    seed: int | None                  # Random seed used (None = non-deterministic)
    total_communities: int
    modularity: float                 # Global modularity score
```

```python
def detect_communities(graph: nx.Graph, resolution: float = 1.0,
                       min_community_size: int = 3,
                       seed: int | None = 42) -> CommunityHierarchy:
    """
    Apply hierarchical Leiden algorithm on L2 subgraph.
    Returns multi-level community tree.
    
    Args:
        seed: Random seed for reproducibility. None = non-deterministic.
              Propagated to leidenalg (or cugraph Leiden) for deterministic runs.
    
    Dependencies: leidenalg, igraph (or cugraph — see §33 GPU Acceleration)
    Side-effects: NONE on input graph
    """
```

**Processus :**

1. Extraire le sous-graphe L2 (actors uniquement)
2. Appliquer Leiden récursif (resolution et seed paramétrables)
   - **CPU path** : Convertir NetworkX → igraph, appeler `leidenalg`
   - **GPU path** : Utiliser `nx-cugraph` backend (transparent — voir §33)
3. Construire la hiérarchie : chaque communauté top-level → nœud L1
4. Retourner `CommunityHierarchy` sans modifier le graphe source

**Intégration au graphe** (fait par `graph/layers/community_integrator.py`, étape séparée APRÈS community_detector) :

```python
def integrate_communities(graph: nx.Graph, hierarchy: CommunityHierarchy) -> nx.Graph:
    """
    Inject L1 community nodes and encompasses edges into the graph.
    
    1. Create L1 nodes with entity_type="community_topic" for each top-level community
    2. Create 'encompasses' edges (L1 → L2 member) for each community member
    3. Create 'related_to' edges (L1 ↔ L1) for communities sharing ≥3 chunks
    4. Set community_id attribute on L2 member nodes
    
    Side-effects: MODIFIES input graph (adds nodes + edges)
    Returns: modified graph
    """
```

> **Note :** `community_integrator.py` est le SEUL module qui modifie le graphe après `builder.py`. Cette séparation garantit que `community_detector.py` reste pur (lecture seule).

#### 13.10.3 Community Summaries (`pipeline/agents/community_summarizer.py`)

Module **LLM-dependent** : pour chaque communauté, génère un résumé textuel.

```python
def summarize_community(community: Community, graph: nx.Graph,
                        llm: BaseLLMClient) -> CommunitySummary:
    """
    Generate a concise textual summary of a community.
    Input: member entities, their relations, source sentences.
    Output: CommunitySummary with title, summary, key_entities.
    """
```

**Prompt input :** liste des membres (canonical_name, entity_type, top relations), relations intra-communauté, et les phrases source les plus représentatives.

**Output model :**

```python
class CommunitySummary(BaseModel):
    community_id: str
    level: int                        # Hierarchy level (0 = leaf, higher = broader)
    title: str                        # Generated topic title ("Cybersecurity Governance")
    summary: str                      # 2-4 sentence summary
    key_entities: list[str]           # Top 5-10 canonical_names
    chunk_coverage: list[str]         # chunk_ids covered by this community
    member_count: int
```

#### 13.10.4 Output Files

| File | Location | Content |
|------|----------|---------|
| `communities.json` | `04_synthesis/` | Community hierarchy (levels, members, parent/child) |
| `community_summaries.json` | `04_synthesis/` | Summaries per community (all levels) |

### 13.11 Entity & Relation Profiles (`graph/profiles/`)

#### 13.11.1 Purpose

Générer des représentations textuelles concises pour chaque entité L2 et relation clé. Ces profils servent de **premier niveau de contexte RAG** — plus compacts que les chunks, ils contiennent le signal utile sans le bruit.

#### 13.11.2 Profile Generation (`pipeline/agents/profile_generator.py`)

Module **LLM-dependent** : prend un nœud L2 + ses relations + ses qualifiers, produit un profil textuel.

```python
def generate_entity_profile(entity: str, graph: nx.Graph,
                            llm: BaseLLMClient) -> EntityProfile:
    """
    Generate concise profile for an L2 entity.
    Aggregates: all relations, qualifiers, temporal scopes, source sentences.
    Output: 3-5 sentence profile capturing the entity's role and attributes.
    """
```

**Entity Profile model :**

```python
class EntityProfile(BaseModel):
    canonical_name: str
    entity_type: str
    profile_text: str                 # 3-5 sentences, LLM-generated
    key_relations: list[str]          # Top relations (predicate + target)
    community_id: str | None          # L1 community membership
    embedding: list[float] | None     # Computed by profile_embedder.py (NOT by this module)
```

**Relation Profile model** (pour les relations les plus fréquentes/importantes) :

```python
class RelationProfile(BaseModel):
    subject: str
    predicate: str
    object: str
    profile_text: str                 # 1-2 sentences summarizing the relation
    qualifiers: dict[str, str] | None
    temporal_scope: TemporalScope | None
    embedding: list[float] | None
```

#### 13.11.3 Profile Embedding (`profile_embedder.py`)

Module **pur** (pas de LLM, juste embedding provider) : calcule les embeddings des profils pour indexation en vector DB.

```python
def embed_profiles(profiles: list[EntityProfile | RelationProfile],
                   embedder: BaseEmbedder) -> list[EntityProfile | RelationProfile]:
    """
    Compute embeddings for all profiles. Returns profiles with embedding field set.
    Side-effects: NONE — returns new objects.
    """
```

#### 13.11.4 Output Files

| File | Location | Content |
|------|----------|---------|
| `entity_profiles.json` | `04_synthesis/` | All L2 entity profiles (without embeddings) |
| `relation_profiles.json` | `04_synthesis/` | Key relation profiles (without embeddings) |

> **Note :** les embeddings ne sont PAS écrits dans les fichiers JSON (immutabilité). Ils sont indexés directement en vector DB par `rag/indexer.py`.

### 13.12 Corpus Graph — Cross-Document Knowledge Space

#### 13.12.1 Architecture

Le Corpus Graph est un espace transversal unique qui connecte les connaissances de tous les documents ingérés. Il vit exclusivement dans le graph DB (pas de fichier JSON local — il est trop volumineux et évolue continuellement).

```
┌─────────────────────────────────────────────────────────┐
│                    CORPUS GRAPH (Graph DB)               │
│                                                          │
│   ┌──────────┐    classifies    ┌──────────┐            │
│   │ T-nodes  │─────────────────→│ C-nodes  │            │
│   │(Taxonomy)│                  │(Canonical)│            │
│   │          │                  │          │            │
│   │ Domaine  │                  │ ISO21434 │            │
│   │  └─Sous  │                  │ (5 docs) │            │
│   │    └─Conc │                  │          │            │
│   └──────────┘                  └────┬─────┘            │
│                                      │ X-edges          │
│                                      ▼                  │
│                                ┌──────────┐            │
│                                │ C-nodes  │            │
│                                │ (other)  │            │
│                                └──────────┘            │
│                                                          │
│   Document Graphs feed into Corpus Graph via             │
│   consolidator/entity_linker.py (Pass 1)                │
└─────────────────────────────────────────────────────────┘
```

#### 13.12.2 Three Structures

**T-nodes (Taxonomy)** — vocabulaire contrôlé, hiérarchies :

```python
class TNode(BaseModel):
    """Taxonomy node — domain/sub-domain/concept hierarchy."""
    canonical_name: str
    level: Literal["domain", "subdomain", "concept"]
    parent: str | None                 # Parent T-node
    children: list[str]                # Child T-nodes
    classified_cnodes: list[str]       # C-nodes classified under this T-node
    created_by: Literal["manual", "consolidator_clustering"]
    created_at: datetime
```

**C-nodes (Canonical Concepts)** — entités cross-documents fusionnées :

```python
class CNode(BaseModel):
    """Canonical concept — entity merged across multiple documents."""
    canonical_name: str
    entity_type: str
    aliases: list[str]                 # Union of all document-level aliases
    source_documents: list[SourceProvenance]
    corroboration: int                 # Number of independent source documents
    consolidated_attributes: dict[str, Any]  # Merged key attributes
    taxonomy_path: str | None          # "Cybersecurity > Standards > ISO 21434"
    confidence: float                  # Aggregate confidence
    salience: float                    # Aggregate salience
    first_seen_at: datetime
    last_updated_at: datetime
```

> **Note :** Les profils textuels des C-nodes sont gérés séparément dans le vector DB (collection `entity_profiles`), PAS dans le graphe. Le Consolidator Pass 1 (Linking) crée le C-node dans le graph DB, tandis que le profile est indexé dans le vector DB pour le RAG.

**X-edges (Cross-Document Relations)** — liens découverts entre C-nodes :

```python
class XEdge(BaseModel):
    """Cross-document relation between C-nodes."""
    source: str                        # C-node canonical_name
    relation_type: str
    target: str                        # C-node canonical_name
    source_documents: list[SourceProvenance]
    corroboration: int
    confidence: float
    qualifiers: dict[str, str] | None
    temporal_scope: TemporalScope | None
    inferred: bool = False             # True if discovered by inference pass
    first_seen_at: datetime
    last_updated_at: datetime
```

#### 13.12.3 Separation of Concerns

| Aspect | Document Graph | Corpus Graph |
|--------|---------------|--------------|
| **Scope** | Un document | Tous les documents |
| **Storage** | JSON file + optionally Graph DB | Graph DB only |
| **L1 Topics** | Leiden communities intra-document | T-nodes (taxonomy) cross-document |
| **L2 Actors** | Document-specific entities | C-nodes (merged entities) |
| **L3 Evidence** | Document-specific literals | Pas de L3 dans Corpus (trop granulaire) |
| **Lifecycle** | Créé une fois, immutable | Évolue à chaque ingestion + consolidation |
| **Owner** | Pipeline (phase 3-4) | Consolidator (phase 4 + async) |

### 13.13 Graph Persistence — JSON-Always + Graph DB Merge

#### JSON-Always Principle

Le fichier `graph.json` est **systématiquement** généré dans `04_synthesis/` quel que soit le paramétrage. Il contient l'intégralité du Document Graph (nœuds, arêtes, attributs, layers, communities, qualifiers) au format JSON et constitue la source de vérité pour reconstruire les bases de données de graphe à partir de l'arborescence de fichiers.

Les formats additionnels (`.graphml`, `.gexf`, `.cypher`) sont contrôlés par `GRAPH_EXPORT_FORMATS`.

#### Graph DB Incremental Merge (`GRAPH_DB_MERGE_STRATEGY=incremental`)

Lorsqu'un graph DB est configuré (`GRAPH_DB_TYPE=neo4j|arangodb`), le Document Graph du run courant est injecté de manière **incrémentale et cohérente** avec les données déjà existantes :

**Nœuds :**
- Merge par `canonical_name` (upsert) — un nœud existant est mis à jour, pas dupliqué
- `aliases` : union des aliases existants et nouveaux
- `occurrence_count` : somme des occurrences cross-documents
- `source_chunk_ids` : append des nouveaux chunk IDs
- `source_documents` : append du nouveau `SourceProvenance`
- `confidence` : max(existing, new)
- `corroboration` : increment +1 si nouveau document
- `last_updated_at` : now()
- `last_corroborated_at` : now()
- `layer` et `entity_type` : conservent la valeur existante sauf si le nouveau document apporte un type plus spécifique

**Arêtes :**
- Merge par `(source, relation_type, target)` (upsert)
- `confidence` : max(existing, new)
- `occurrence_count` : sum(existing, new)
- `source_documents` : append du nouveau `SourceProvenance`
- `qualifiers` : merge (existing qualifiers enrichis, pas écrasés)
- `corroboration` : increment +1 si nouveau document
- `context_sentences` : append

**Document Deletion** (`GRAPH_DB_MERGE_STRATEGY=replace` ou suppression explicite) :
- Identifier nœuds et arêtes dont `source_documents` contient le document à supprimer
- Pour chaque élément :
  - Si `corroboration == 1` (seul document source) → supprimer le nœud/arête
  - Si `corroboration > 1` → retirer la `SourceProvenance` du document, décrémenter corroboration, recalculer confidence/salience à partir des provenances restantes
- **Préserve les connaissances partagées** sans casser le graphe

#### Rebuild from JSON

Un utilitaire CLI `rebuild_graph_db` permet de reconstruire une base de données de graphe complète à partir de l'arborescence de fichiers JSON :

```bash
ayextractor rebuild-graph-db --source /path/to/output --target neo4j://localhost:7687
```

Il parcourt tous les `04_synthesis/graph.json` de chaque run et les merge incrémentalement.

### 13.14 Configuration

| Env Var | Default | Description |
|---------|---------|-------------|
| `ENTITY_SIMILARITY_THRESHOLD` | `0.85` | Cosine similarity threshold for entity clustering |
| `RELATION_TAXONOMY_EXTENSIBLE` | `true` | Allow LLM to add new relation types not in base taxonomy |
| `TRIPLET_CONFIDENCE_BOOST` | `true` | Boost confidence for multi-chunk triplets |
| `COMMUNITY_DETECTION_RESOLUTION` | `1.0` | Leiden algorithm resolution parameter |
| `COMMUNITY_DETECTION_SEED` | `42` | Random seed for Leiden reproducibility (`null` = non-deterministic) |
| `COMMUNITY_MIN_SIZE` | `3` | Minimum number of members per community |
| `COMMUNITY_SUMMARY_ENABLED` | `true` | Generate LLM summaries for communities |
| `PROFILE_GENERATION_ENABLED` | `true` | Generate entity/relation profiles |
| `PROFILE_MIN_RELATIONS` | `2` | Minimum relations for an entity to get a profile |
| `SCORING_W_CONFIDENCE` | `0.3` | Weight for confidence in composite score |
| `SCORING_W_SALIENCE` | `0.3` | Weight for salience in composite score |
| `SCORING_W_FRESHNESS` | `0.2` | Weight for freshness in composite score |
| `SCORING_W_CORROBORATION` | `0.2` | Weight for corroboration in composite score |
| `SCORING_CORROBORATION_CAP` | `5` | Max corroboration value for normalization |

> **Note :** Le half-life utilisé dans la formule de staleness (`HALF_LIFE` dans §13.9.8) est `CONSOLIDATOR_DECAY_HALFLIFE_DAYS` (défaut: 90 jours). Une seule variable contrôle les deux usages (scoring composite ET pruning consolidator) pour garantir la cohérence.

### 13.15 Consolidator — "Memory During Sleep" (`consolidator/`)

#### 13.15.1 Purpose

Processus asynchrone périodique qui maintient le Corpus Graph vivant. Inspiré de la consolidation mnésique pendant le sommeil : le cerveau ne crée pas de nouvelles connexions pendant la journée — il les consolide, renforce, et élague la nuit.

Le Consolidator est un **module séparé** (`consolidator/`) avec son propre orchestrateur. Il n'est PAS couplé au pipeline d'analyse principal. La seule interface entre les deux est le graph DB (Corpus Graph) et le fichier `graph.json` (Document Graph).

#### 13.15.2 Architecture — 5 Passes Indépendantes

Chaque pass est un module **isolé** avec des entrées/sorties explicites. Les passes peuvent être activées/désactivées individuellement. L'ordre d'exécution est fixe mais chaque pass est idempotente.

> **Dépendance :** Toutes les passes opèrent sur un `corpus_store: BaseGraphStore` (voir §30.7 pour l'interface complète). Le graph store est injecté par `consolidator/orchestrator.py` via la factory.

```
consolidator/
├── orchestrator.py          # Schedule + run passes
├── entity_linker.py         # Pass 1 — Linking
├── community_clusterer.py   # Pass 2 — Clustering
├── inference_engine.py      # Pass 3 — Inference
├── decay_manager.py         # Pass 4 — Decay
├── contradiction_detector.py # Pass 5 — Contradiction
├── models.py                # CNode, TNode, XEdge, Contradiction,
│                            # ConsolidationReport, LinkingReport, ClusteringReport,
│                            # InferenceReport, DecayReport, ContradictionReport, PassResult
```

| Pass | Name | Analogie | Opération | Fréquence | Module |
|:----:|------|----------|-----------|-----------|--------|
| **1** | **Linking** | Hippocampe→cortex | Entity resolution cross-documents, création/renforcement C-nodes, merge descriptions | À chaque ingestion (synchrone) | `entity_linker.py` |
| **2** | **Clustering** | Formation catégories | Leiden sur Corpus Graph C-nodes → proposer T-nodes si cluster ≥3 | Hebdomadaire ou tous les N docs | `community_clusterer.py` |
| **3** | **Inference** | Consolidation sémantique | Découvrir relations transitives (A→B + B→C ⇒ A→C, flag `inferred=true`) | Hebdomadaire | `inference_engine.py` |
| **4** | **Decay** | Oubli actif | Recalculer staleness, réduire salience non-corroborés, pruner L3 avec `staleness > threshold AND corroboration == 1` | Quotidien | `decay_manager.py` |
| **5** | **Contradiction** | Détection anomalies | Trouver arêtes contradictoires (même sujet+predicate, objets incompatibles), flagger, rapport | À chaque consolidation | `contradiction_detector.py` |

#### 13.15.3 Pass 1 — Linking (`entity_linker.py`)

**Toujours synchrone** — s'exécute immédiatement après l'ingestion d'un document (phase 4 du pipeline).

```python
def link_document_to_corpus(document_graph: nx.Graph,
                            corpus_store: BaseGraphStore) -> LinkingReport:
    """
    Merge Document Graph entities into Corpus Graph C-nodes.
    
    For each L2 entity in document_graph:
    1. Search Corpus Graph for existing C-node by canonical_name + aliases
    2. If found: merge (union aliases, append source_documents, increment corroboration,
       merge consolidated_attributes, update confidence/salience/last_updated_at)
    3. If not found: create new C-node from document entity
    4. For each edge: merge into X-edge (same logic)
    
    Side-effects: writes to corpus_store (Graph DB)
    Returns: LinkingReport (created, merged, stats)
    """
```

**Smart merge des descriptions :** quand un C-node existe déjà, les descriptions (profile_text) sont mergées par LLM si les sources divergent significativement (cosine similarity < 0.8). Sinon, la plus récente est conservée.

#### 13.15.4 Pass 2 — Clustering (`community_clusterer.py`)

```python
def cluster_corpus(corpus_store: BaseGraphStore,
                   min_cluster_size: int = 3,
                   seed: int | None = 42) -> ClusteringReport:
    """
    Apply Leiden on Corpus Graph C-nodes → propose T-nodes.
    
    1. Extract C-nodes + X-edges from corpus_store
    2. Apply Leiden (resolution from config, seed for reproducibility)
    3. For each cluster of size ≥ min_cluster_size:
       - If no matching T-node exists: propose new T-node
       - If matching T-node exists: update its classified_cnodes
    4. Write T-nodes to corpus_store
    
    Side-effects: writes to corpus_store
    Returns: ClusteringReport (new_tnodes, updated_tnodes)
    """
```

#### 13.15.5 Pass 3 — Inference (`inference_engine.py`)

```python
def infer_relations(corpus_store: BaseGraphStore,
                    min_confidence: float = 0.6) -> InferenceReport:
    """
    Discover implicit transitive relations in Corpus Graph.
    
    Rule: if A →[r1] B and B →[r2] C, and r1/r2 are compatible,
    then propose A →[inferred_relation] C with:
      - inferred=True
      - confidence = min(conf_r1, conf_r2) × INFERENCE_DISCOUNT
      - source = "inference_pass"
    
    Only proposes relations where confidence ≥ min_confidence.
    Side-effects: writes to corpus_store (new X-edges with inferred=True)
    Returns: InferenceReport (proposed, accepted, rejected)
    """
```

#### 13.15.6 Pass 4 — Decay (`decay_manager.py`)

```python
def apply_decay(corpus_store: BaseGraphStore,
                halflife_days: int = 90,
                prune_threshold: float = 0.2) -> DecayReport:
    """
    Recalculate staleness and prune stale low-value nodes.
    
    For all C-nodes and X-edges:
    1. Recalculate staleness = 1 - exp(-ln(2) × days / halflife)
    2. If staleness > prune_threshold AND corroboration == 1:
       - Mark as "stale" (not deleted immediately)
       - If stale for > 2 × halflife: delete
    3. Reduce salience of non-corroborated items proportionally
    
    Side-effects: updates/deletes in corpus_store
    Returns: DecayReport (recalculated, marked_stale, pruned)
    """
```

#### 13.15.7 Pass 5 — Contradiction Detection (`contradiction_detector.py`)

```python
def detect_contradictions(corpus_store: BaseGraphStore) -> ContradictionReport:
    """
    Find conflicting claims in Corpus Graph.
    
    Detection rules:
    - Same subject + same predicate + incompatible objects
      (e.g., "CEO of X" = "Alice" vs "CEO of X" = "Bob")
    - Temporal conflicts (overlapping temporal scopes with different values)
    - Negation patterns ("X requires Y" vs "X does not require Y")
    
    For each contradiction:
    - Flag both edges with contradiction_id
    - Do NOT resolve (human decision needed)
    - Add to ContradictionReport
    
    Side-effects: flags in corpus_store (adds contradiction_id attribute)
    Returns: ContradictionReport (contradictions found, details)
    """
```

#### 13.15.8 Configuration

| Env Var | Default | Description |
|---------|---------|-------------|
| `CONSOLIDATOR_ENABLED` | `false` | Enable Corpus Graph consolidation |
| `CONSOLIDATOR_TRIGGER` | `on_ingestion` | `on_ingestion` / `scheduled` / `manual` |
| `CONSOLIDATOR_SCHEDULE` | `0 3 * * 0` | Cron schedule (for `scheduled` trigger) |
| `CONSOLIDATOR_PASSES` | `linking,clustering,inference,decay,contradiction` | Comma-separated active passes |
| `CONSOLIDATOR_DECAY_HALFLIFE_DAYS` | `90` | Half-life for staleness decay |
| `CONSOLIDATOR_INFERENCE_MIN_CONFIDENCE` | `0.6` | Min confidence for inferred relations |
| `CONSOLIDATOR_INFERENCE_DISCOUNT` | `0.8` | Confidence discount for inferred relations |
| `CONSOLIDATOR_PRUNE_THRESHOLD` | `0.2` | Staleness threshold for pruning candidates |
| `CONSOLIDATOR_CLUSTER_MIN_SIZE` | `3` | Min cluster size for T-node creation |

> **Note :** Pass 1 (Linking) est **toujours synchrone** post-ingestion quand `CONSOLIDATOR_ENABLED=true`, quel que soit `CONSOLIDATOR_TRIGGER`. Les passes 2-5 respectent le trigger configuré.

#### 13.15.9 Consolidation Report

Chaque exécution du Consolidator produit un rapport JSON :

```json
{
    "consolidation_id": "cons_20260210_030015",
    "timestamp": "2026-02-10T03:00:15Z",
    "trigger": "scheduled",
    "passes_executed": ["clustering", "inference", "decay", "contradiction"],
    "results": {
        "clustering": {"new_tnodes": 3, "updated_tnodes": 12},
        "inference": {"proposed": 45, "accepted": 28, "rejected": 17},
        "decay": {"recalculated": 1523, "marked_stale": 34, "pruned": 7},
        "contradiction": {"found": 2, "details": ["..."]}
    },
    "corpus_stats": {
        "total_cnodes": 4521,
        "total_tnodes": 87,
        "total_xedges": 12034,
        "total_documents": 156
    }
}
```

**Report models** (`consolidator/models.py`) :

```python
class PassResult(BaseModel):
    """Result of a single consolidation pass."""
    pass_name: str                    # "linking", "clustering", etc.
    duration_ms: int                  # Execution time
    items_processed: int              # Input items examined
    items_modified: int               # Items created/updated/deleted
    details: dict[str, Any]           # Pass-specific metrics (see per-pass reports)

class LinkingReport(BaseModel):
    new_cnodes: int                   # New C-nodes created
    updated_cnodes: int               # Existing C-nodes reinforced
    new_xedges: int                   # New X-edges created
    updated_xedges: int               # Existing X-edges reinforced
    documents_linked: int             # Documents processed in this pass

class ClusteringReport(BaseModel):
    new_tnodes: int                   # New T-nodes proposed
    updated_tnodes: int               # Existing T-nodes updated
    clusters_found: int               # Total Leiden clusters detected

class InferenceReport(BaseModel):
    proposed: int                     # Transitive relations proposed
    accepted: int                     # Relations above confidence threshold
    rejected: int                     # Relations below threshold

class DecayReport(BaseModel):
    recalculated: int                 # Nodes with recalculated staleness
    marked_stale: int                 # Nodes newly exceeding staleness threshold
    pruned: int                       # Nodes/edges removed (low value + stale)

class ContradictionReport(BaseModel):
    found: int                        # Contradictions detected
    details: list[Contradiction]      # Contradiction objects with full context

class ConsolidationReport(BaseModel):
    """Top-level report for a full consolidation run."""
    consolidation_id: str             # "cons_20260210_030015"
    timestamp: datetime
    trigger: Literal["on_ingestion", "scheduled", "manual"]
    passes_executed: list[str]        # ["linking", "clustering", ...]
    results: dict[str, PassResult]    # Keyed by pass_name
    corpus_stats: dict[str, int]      # total_cnodes, total_tnodes, total_xedges, total_documents
```

---

## 14. Confidence Scoring

### 14.1 Principle

Chaque agent produit un score de confiance (`0.0` à `1.0`) sur son output.

| Agent | Confidence Based On |
|-------|-------------------|
| Reference Extractor | Completeness: ratio of detected markers (footnotes, citations) successfully resolved |
| Summarizer | Coverage rate: ratio of chunks whose key points appear in the summary |
| Densifier | Entity preservation: ratio of entities from Refine summary preserved in dense version |
| Decontextualizer | Resolution rate: ratio of ambiguous references successfully resolved per chunk. This score also drives tool-use activation in `auto` mode (see §28.7) |
| Concept Extractor | Extraction completeness: ratio of chunks producing ≥1 triplet with confidence > 0.5. Low score suggests prompt issues or incompatible content |
| Community Detector | Modularity: Leiden quality metric — higher modularity indicates well-separated communities. No LLM involved (pure algorithmic) |
| Community Summarizer | Coverage: ratio of member entities mentioned in generated summary text |
| Profile Generator | Completeness: ratio of top-K relations reflected in generated profile text |
| Synthesizer | Self-consistency: LLM self-evaluation of coherence between synthesis and inputs |

### 14.2 Usage

- Scores persistés dans `04_synthesis/confidence.json` (un seul fichier centralisé par run)
- Score global = moyenne pondérée des scores par agent
- Si un score est sous un seuil configurable (default: `0.6`), un warning est émis dans le résultat final

---

## 15. Token Budget Management

### 15.1 Budget Estimation — `llm/token_budget.py`

Avant exécution du pipeline, le système estime le budget total nécessaire :

```
total_tokens = extraction_tokens (images × vision_cost)
             + reference_extractor_tokens (enriched_text × extraction_prompt)
             + interleaved_loop_tokens:
               + decontextualizer_tokens (n_chunks × (chunk_size + refine_summary_progressive + context_window))
               + summarizer_tokens (n_chunks × (decontextualized_chunk_size + cumulative_summary))
               + decontextualizer_tool_tokens (if auto/always: ~5-15% extra for tool round-trips)
             + densifier_tokens (5 iterations × refine_summary_final_size)
             + concept_extractor_tokens (n_chunks × avg_chunk_size)
             + entity_normalizer_tokens (n_clusters × cluster_size_prompt)
             + relation_normalizer_tokens (n_unique_relations × classification_prompt)
             + graph_build_overhead
             + community_summarizer_tokens (n_communities × (member_list + relations + summary_prompt))
             + profile_generator_tokens (n_entities_with_profiles × (relations + qualifiers + profile_prompt))
             + synthesizer_tokens (dense_summary + community_summaries + graph_serialized)
             + critic_tokens (if enabled: all_outputs × validation_prompt)
```

**Note :** le decontextualizer est potentiellement le poste le plus coûteux car il fait un appel LLM par chunk avec une fenêtre de contexte glissante. Le budget doit en tenir compte.

Le budget est estimé **par provider:model** car les coûts varient selon l'assignation per-agent (section 17.3).

### 15.2 Budget Allocation

Chaque agent reçoit un budget max_tokens calculé proportionnellement. Si un agent dépasse son budget :
1. Warning dans les logs
2. Tentative de complétion avec un budget réduit (truncation strategy)
3. Si échec : le step est marqué `degraded` avec le score de confiance ajusté

---

## 16. Error Handling and Retry

### 16.1 Retry Policy — `llm/retry.py`

Chaque agent a une retry policy indépendante :

| Error Type | Strategy | Default |
|------------|----------|---------|
| Rate limit (429) | Exponential backoff | 3 retries, base 2s |
| Timeout | Retry with same params | 2 retries |
| Server error (5xx) | Exponential backoff | 3 retries, base 5s |
| Invalid response (parse error) | Retry with reinforced prompt | 2 retries |
| Token limit exceeded | Reduce input, retry | 1 retry |

### 16.2 Circuit Breaker

Si un agent échoue après tous les retries :
1. Son output est marqué `failed`
2. Le pipeline continue si possible (graceful degradation)
3. Le synthesizer travaille avec les données disponibles
4. Le résultat final indique les étapes manquantes

---

## 17. Configuration

### 17.1 Environment Variables — `.env`

All configuration is loaded from a `.env` file at the project root, with fallback to defaults. The `.env` file is the single source of truth for deployment-specific settings.

```dotenv
# === LLM PROVIDERS ===
# Default provider and model (used when no per-agent override is set)
LLM_DEFAULT_PROVIDER=anthropic      # anthropic | openai | google | ollama
LLM_DEFAULT_MODEL=claude-sonnet-4-20250514
LLM_DEFAULT_TEMPERATURE=0.2
LLM_MAX_TOKENS_PER_AGENT=4096

# Provider API keys
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
GOOGLE_API_KEY=AIza...
OLLAMA_BASE_URL=http://localhost:11434

# === PER-PHASE LLM ASSIGNMENT ===
# Override provider:model for an entire pipeline phase. Format: provider:model
# If not set, falls back to LLM_DEFAULT_PROVIDER + LLM_DEFAULT_MODEL
LLM_PHASE_EXTRACTION=                # Phase 1: image_analyzer, reference_extractor
LLM_PHASE_CHUNKING=                  # Phase 2: summarizer, densifier, decontextualizer
LLM_PHASE_ANALYSIS=                  # Phase 3: concept_extractor, synthesizer, critic
LLM_PHASE_NORMALIZATION=             # Phase 3 (graph normalization): entity_normalizer, relation_normalizer
                                     # NOTE: NOT related to consolidator/ module (Corpus Graph maintenance)

# === PER-COMPONENT LLM ASSIGNMENT (highest priority) ===
# Override provider:model for a specific component. Format: provider:model
# If not set, falls back to LLM_PHASE_* → LLM_DEFAULT
LLM_IMAGE_ANALYZER=anthropic:claude-sonnet-4-20250514
LLM_REFERENCE_EXTRACTOR=anthropic:claude-haiku-4-5-20251001
LLM_SUMMARIZER=anthropic:claude-sonnet-4-20250514
LLM_DENSIFIER=anthropic:claude-sonnet-4-20250514
LLM_DECONTEXTUALIZER=anthropic:claude-haiku-4-5-20251001
LLM_CONCEPT_EXTRACTOR=anthropic:claude-sonnet-4-20250514
LLM_COMMUNITY_SUMMARIZER=anthropic:claude-haiku-4-5-20251001
LLM_PROFILE_GENERATOR=anthropic:claude-haiku-4-5-20251001
LLM_SYNTHESIZER=anthropic:claude-sonnet-4-20250514
LLM_CRITIC=anthropic:claude-sonnet-4-20250514
LLM_ENTITY_NORMALIZER=anthropic:claude-haiku-4-5-20251001
LLM_RELATION_NORMALIZER=anthropic:claude-haiku-4-5-20251001

# === EMBEDDINGS ===
EMBEDDING_PROVIDER=anthropic         # anthropic | openai | sentence_transformers | ollama
EMBEDDING_MODEL=voyage-3             # Model name (provider-specific)
EMBEDDING_DIMENSIONS=1024            # Output vector dimensions
# Provider-specific settings
EMBEDDING_OLLAMA_MODEL=nomic-embed-text
EMBEDDING_ST_MODEL=all-MiniLM-L6-v2  # sentence-transformers model name

# === Document limits ===
MAX_DOCUMENT_SIZE_MB=50
MAX_DOCUMENT_PAGES=2000
MAX_DOCUMENT_TOKENS=500000
OVERSIZE_STRATEGY=reject             # reject | truncate | sample

# === Chunking ===
CHUNKING_STRATEGY=structural         # structural | semantic
CHUNK_TARGET_SIZE=2000
CHUNK_OVERLAP=0
DECONTEXTUALIZATION_ENABLED=true      # Resolve ambiguous references in chunks
DECONTEXTUALIZER_TOOL_USE=auto       # auto | always | never
# auto: activates chunk_lookup tool when per-chunk resolution confidence < threshold
# always: tool available on every chunk (higher quality, higher cost/latency)
# never: no tool use, relies solely on dense_summary + references + sliding window
DECONTEXTUALIZER_TOOL_CONFIDENCE_THRESHOLD=0.7  # Below this, tool is activated (auto mode only)

# === Pipeline ===
DENSITY_ITERATIONS=5
CONFIDENCE_THRESHOLD=0.6
CRITIC_AGENT_ENABLED=false            # Optional validation agent
CRITIC_STRICTNESS=medium             # low | medium | high

# === Triplet Consolidation ===
ENTITY_SIMILARITY_THRESHOLD=0.85     # Cosine similarity for entity clustering
RELATION_TAXONOMY_EXTENSIBLE=true    # Allow LLM to add new relation types
TRIPLET_CONFIDENCE_BOOST=true        # Boost confidence for multi-chunk triplets

# === Community Detection (Phase 3d) ===
COMMUNITY_DETECTION_RESOLUTION=1.0   # Leiden algorithm resolution parameter
COMMUNITY_DETECTION_SEED=42          # Random seed for Leiden reproducibility (null = non-deterministic)
COMMUNITY_MIN_SIZE=3                 # Minimum members per community
COMMUNITY_SUMMARY_ENABLED=true       # Generate LLM summaries for communities

# === Entity/Relation Profiles (Phase 3e) ===
PROFILE_GENERATION_ENABLED=true      # Generate entity/relation textual profiles
PROFILE_MIN_RELATIONS=2              # Minimum relations for an entity to get a profile

# === Scoring Weights (RAG composite score) ===
SCORING_W_CONFIDENCE=0.3             # Weight for confidence in composite score
SCORING_W_SALIENCE=0.3               # Weight for salience in composite score
SCORING_W_FRESHNESS=0.2              # Weight for freshness (1 - staleness)
SCORING_W_CORROBORATION=0.2          # Weight for corroboration
SCORING_CORROBORATION_CAP=5          # Max corroboration value for normalization
# NOTE: staleness half-life uses CONSOLIDATOR_DECAY_HALFLIFE_DAYS (single source of truth)

# === CHUNK OUTPUT MODE ===
# Controls whether analysis results are WRITTEN to databases after processing
# Reading from databases during analysis is controlled by RAG_ENABLED (see RAG section)
CHUNK_OUTPUT_MODE=files_only         # files_only | files_and_vectordb | files_and_graphdb | files_and_both_db
# When vectordb or graphdb is enabled, files are ALWAYS generated as well

# === Cache ===
CACHE_ENABLED=true
CACHE_BACKEND=json                   # json | sqlite | redis | arangodb
CACHE_ROOT=~/.ayextractor/cache
CACHE_REDIS_URL=                     # Only if CACHE_BACKEND=redis
CACHE_ARANGODB_URL=                  # Only if CACHE_BACKEND=arangodb (e.g., http://localhost:8529)
CACHE_ARANGODB_DATABASE=ayextractor_cache
SIMHASH_THRESHOLD=3
MINHASH_THRESHOLD=0.8
CONSTELLATION_THRESHOLD=0.7

# === Output storage ===
OUTPUT_WRITER=local                  # local | s3
OUTPUT_S3_BUCKET=                    # Only if OUTPUT_WRITER=s3
OUTPUT_S3_PREFIX=ayextractor/
OUTPUT_S3_REGION=

# === Graph export ===
GRAPH_EXPORT_FORMATS=graphml         # Comma-separated: graphml | gexf | cypher
# NOTE: graph.json is ALWAYS generated regardless of this setting (JSON-always principle)
# This variable controls ADDITIONAL export formats beyond the mandatory JSON.

# === Batch scan ===
BATCH_SCAN_ENABLED=false
BATCH_SCAN_ROOT=                     # Root directory to scan for documents
BATCH_SCAN_RECURSIVE=true
BATCH_SCAN_FORMATS=pdf,epub,docx,md,txt,png,jpg,jpeg,webp

# === Output ===
OUTPUT_FORMAT=both                   # markdown | json | both

# === Logging ===
LOG_LEVEL=INFO                       # DEBUG | INFO | WARNING | ERROR
LOG_FORMAT=json                      # json | text
LOG_FILE=~/.ayextractor/logs/analyzer.log
LOG_ROTATION=10MB
LOG_RETENTION=30                     # days

# === Vector Database (optional) ===
VECTOR_DB_TYPE=none                  # none | chromadb | qdrant | arangodb
VECTOR_DB_PATH=~/.ayextractor/vectordb  # For local DBs (ChromaDB)
VECTOR_DB_URL=                       # For remote DBs (Qdrant, ArangoDB)
VECTOR_DB_API_KEY=                   # For remote DBs requiring auth
VECTOR_DB_COLLECTION=ayextractor

# === Graph Database (optional) ===
GRAPH_DB_TYPE=none                   # none | neo4j | arangodb
GRAPH_DB_URI=bolt://localhost:7687
GRAPH_DB_DATABASE=ayextractor
GRAPH_DB_USER=
GRAPH_DB_PASSWORD=
GRAPH_DB_MERGE_STRATEGY=incremental  # incremental | replace
# incremental: merge nodes by canonical_name, aggregate edge confidence/occurrence
# replace: drop existing graph for this document_id and re-import from scratch

# === RAG (optional) ===
RAG_ENABLED=false
RAG_ENRICH_AGENTS=decontextualizer,concept_extractor,reference_extractor,synthesizer

# === RAG Hierarchical Retrieval ===
RAG_RETRIEVAL_TOP_K_COMMUNITIES=5    # Max community summaries retrieved (Level 1)
RAG_RETRIEVAL_TOP_K_ENTITIES=20      # Max entity profiles retrieved (Level 2)
RAG_RETRIEVAL_TOP_K_CHUNKS=10        # Max chunks retrieved (Level 3 fallback)
RAG_CHUNK_FALLBACK_THRESHOLD=0.6     # Entity confidence below which chunks are also retrieved
RAG_COMPOSITE_WEIGHT=0.3             # Weight of composite score vs PPR (α in formula: α×composite + (1-α)×ppr)
RAG_PPR_ALPHA=0.15                   # PPR teleport probability
RAG_CONTEXT_TOKEN_BUDGET=4000        # Max tokens for assembled retrieval context
RAG_INCLUDE_CORPUS_GRAPH=true        # Include C-nodes/T-nodes/X-edges (if CONSOLIDATOR_ENABLED)

# === Consolidator (Corpus Graph maintenance) ===
CONSOLIDATOR_ENABLED=false           # Enable Corpus Graph consolidation
CONSOLIDATOR_TRIGGER=on_ingestion    # on_ingestion | scheduled | manual
CONSOLIDATOR_SCHEDULE=0 3 * * 0      # Cron schedule (for 'scheduled' trigger)
CONSOLIDATOR_PASSES=linking,clustering,inference,decay,contradiction
CONSOLIDATOR_DECAY_HALFLIFE_DAYS=90  # Half-life for staleness decay
CONSOLIDATOR_INFERENCE_MIN_CONFIDENCE=0.6  # Min confidence for inferred relations
CONSOLIDATOR_INFERENCE_DISCOUNT=0.8  # Confidence discount for inferred relations
CONSOLIDATOR_PRUNE_THRESHOLD=0.2     # Staleness threshold for pruning candidates
CONSOLIDATOR_CLUSTER_MIN_SIZE=3      # Min cluster size for T-node creation

# === GPU Acceleration (optional, see §33) ===
# NETWORKX_BACKEND=cugraph            # Route NetworkX graph algorithms to GPU (requires nx-cugraph-cu12)
# GPU_CLUSTERING_BACKEND=sklearn      # sklearn (CPU, default) | cuml (GPU, requires cuml)
# GPU_SIMILARITY_BACKEND=sklearn      # sklearn (CPU, default) | cupy | torch (GPU)
```

### 17.2 Settings Model — `config/settings.py`

Le fichier `settings.py` charge le `.env` via `pydantic-settings` et expose un objet `Settings` typé et validé.

### 17.3 Per-Component LLM Routing — `llm/config.py`

Chaque composant (agent, normalizer) peut utiliser un provider et modèle différent. La résolution se fait en cascade à 3 niveaux :

```
1. Per-component env var (LLM_SUMMARIZER=openai:gpt-4o)
    ↓ if not set
2. Per-phase env var (LLM_PHASE_ANALYSIS=anthropic:claude-sonnet-4-20250514)
    ↓ if not set
3. Default provider + model (LLM_DEFAULT_PROVIDER + LLM_DEFAULT_MODEL)
    ↓ if not set
4. Hardcoded fallback (anthropic:claude-sonnet-4-20250514)
```

**Phase-to-component mapping :**

| Phase | Env Var | Components |
|-------|---------|------------|
| Extraction | `LLM_PHASE_EXTRACTION` | `image_analyzer`, `reference_extractor` |
| Chunking | `LLM_PHASE_CHUNKING` | `summarizer`, `densifier`, `decontextualizer` |
| Analysis | `LLM_PHASE_ANALYSIS` | `concept_extractor`, `community_summarizer`, `profile_generator`, `synthesizer`, `critic` |
| Normalization | `LLM_PHASE_NORMALIZATION` | `entity_normalizer`, `relation_normalizer` |

Cela permet par exemple :
- De définir un modèle rapide/peu coûteux pour toute la phase d'extraction : `LLM_PHASE_EXTRACTION=anthropic:claude-haiku-4-5-20251001`
- D'override un seul composant avec un modèle plus puissant : `LLM_IMAGE_ANALYZER=anthropic:claude-sonnet-4-20250514`
- Un modèle local via Ollama pour le développement/test : `LLM_DEFAULT_PROVIDER=ollama`, `LLM_DEFAULT_MODEL=llama3`
- Gemini ou GPT-4 pour des comparaisons de qualité

> **Note :** Les composants `entity_normalizer` et `relation_normalizer` (§13) ne sont pas des agents au sens PluginKit — ils sont orchestrés par `graph/merger.py`. Leur routing LLM suit la même cascade que les agents.

### 17.4 Chunk Output Mode (`CHUNK_OUTPUT_MODE`)

| Mode | Behavior |
|------|----------|
| `files_only` | Les fichiers texte/JSON sont générés dans l'arborescence de sortie. Aucune injection en base. |
| `files_and_vectordb` | Fichiers + indexation des chunks et résumés dans le vector DB configuré |
| `files_and_graphdb` | Fichiers + import du knowledge graph dans le graph DB configuré |
| `files_and_both_db` | Fichiers + vectordb + graphdb |

Les fichiers texte sont **toujours** générés, quel que soit le mode. Les bases de données sont un complément pour l'exploitation ultérieure (RAG, requêtes, visualisation).

### 17.5 Per-Document Override

All settings can be overridden via `Metadata.config_overrides: ConfigOverrides | None` (see section 2.2 for the typed schema). Only whitelisted keys are accepted — unknown keys raise a validation error. Applied overrides are traced in `run_manifest.json`.

### 17.6 Startup Config Validation

`config/settings.py` doit valider la cohérence des env vars au chargement. Les combinaisons incohérentes lèvent une `ConfigurationError` immédiate :

| Rule | Condition | Error |
|------|-----------|-------|
| V-01 | `CHUNK_OUTPUT_MODE` includes `vectordb` AND `VECTOR_DB_TYPE=none` | "CHUNK_OUTPUT_MODE requires vectordb but VECTOR_DB_TYPE is none" |
| V-02 | `CHUNK_OUTPUT_MODE` includes `graphdb` AND `GRAPH_DB_TYPE=none` | "CHUNK_OUTPUT_MODE requires graphdb but GRAPH_DB_TYPE is none" |
| V-03 | `RAG_ENABLED=true` AND `VECTOR_DB_TYPE=none` AND `GRAPH_DB_TYPE=none` | "RAG_ENABLED requires at least one DB configured" |
| V-04 | `CONSOLIDATOR_ENABLED=true` AND `GRAPH_DB_TYPE=none` | "Consolidator requires a graph DB" |
| V-05 | `CHUNK_OVERLAP >= CHUNK_TARGET_SIZE` | "CHUNK_OVERLAP must be < CHUNK_TARGET_SIZE" |

---

## 18. Output Formats

### 18.1 Configurable via `output_format`

| Value | Description |
|-------|-------------|
| `markdown` | All outputs as `.txt` and `.md` files |
| `json` | All outputs as structured `.json` files |
| `both` | Both formats generated side by side |

### 18.2 JSON Structured Output

When `json` is selected, each step also produces a structured JSON version :

- `refine_summary.json` : `{summary, key_points[], themes[]}`
- `dense_summary.json` : `{summary, entities[], density_score}`
- `final_analysis.json` : `{themes[], concepts[], relations[], summary, confidence}`

---

## 19. Technology Stack

| Component | Library | Rationale |
|-----------|---------|-----------|
| Orchestration | LangGraph | Mature, DAG support, checkpoints, shared state |
| LLM — Anthropic | anthropic SDK | Claude, Claude Vision |
| LLM — OpenAI | openai SDK | GPT-4, GPT-4 Vision |
| LLM — Google | google-generativeai | Gemini, Gemini Vision |
| LLM — Ollama | ollama SDK | Local models (Llama, Mistral, etc.) |
| PDF Extraction | PyMuPDF (fitz) | Fast, reliable, image extraction |
| EPUB Extraction | ebooklib | Standard EPUB parser |
| DOCX Extraction | python-docx | Standard DOCX parser |
| Image Processing | Pillow | Image input handling, format conversion |
| Table Extraction | pdfplumber (PDF), python-docx (DOCX) | Structured table support |
| Chunking | LangChain Text Splitters / Chonkie | Semantic + structural chunking |
| Language Detection | lingua-py | Accurate multi-language detection (doc + chunk) |
| Knowledge Graph | NetworkX | Lightweight, no DB dependency. GPU-accelerated via nx-cugraph backend (§33) |
| Community Detection | leidenalg + python-igraph (CPU) / cugraph (GPU) | Hierarchical Leiden — see §33 for GPU path |
| Graph Export | NetworkX built-in + custom exporters | JSON, GraphML, GEXF, Cypher |
| SimHash | simhash (Python lib) | Locality-sensitive hashing |
| MinHash | datasketch | Standard MinHash / LSH implementation |
| Embeddings — Anthropic | voyageai SDK | Voyage embeddings |
| Embeddings — OpenAI | openai SDK | text-embedding-3 family |
| Embeddings — Local | sentence-transformers | Local embedding models |
| Embeddings — Ollama | ollama SDK | Local embedding via Ollama |
| Pydantic | pydantic v2 | Type-safe models, validation, serialization |
| Configuration | pydantic-settings | .env loading, typed settings |
| Logging | structlog | Structured JSON/text logging with context |
| Token Tracking | Built-in (JSONL + JSON) | No external dependency, append-only log |
| Cache — JSON | Built-in | Default, no dependency |
| Cache — SQLite | sqlite3 (stdlib) | Optional, better performance at scale |
| Cache — Redis | redis-py | Optional, distributed cache |
| Output — Local | Built-in (pathlib) | Default, local filesystem |
| Output — S3 | boto3 | Optional, cloud storage |
| Vector DB (optional) | ChromaDB / Qdrant | Embedding storage for RAG |
| Graph DB (optional) | Neo4j / ArangoDB | Knowledge graph persistence for RAG |
| **GPU — Graph** (optional) | nx-cugraph (NVIDIA RAPIDS) | Drop-in NetworkX backend — accelerates Leiden, PageRank, graph algorithms (see §33) |
| **GPU — Clustering** (optional) | cuML (NVIDIA RAPIDS) | GPU-accelerated agglomerative clustering for entity normalization (see §33) |
| **GPU — Similarity** (optional) | cupy / torch | GPU-accelerated cosine similarity for large entity sets (see §33) |

---

## 20. Token Tracking and Usage Statistics

### 20.1 Architecture

Le tracking opère à 3 niveaux de granularité, du plus fin au plus agrégé :

```
[LLM Client] → call_logger.py (Level 1: per-call)
                    ↓
               agent_tracker.py (Level 2: per-agent)
                    ↓
               session_tracker.py (Level 3a: per-document)
                    ↓
               stats_aggregator.py (Level 3b: cross-document cumulative)
```

### 20.2 Level 1 — Per-Call Logging (`tracking/call_logger.py`)

Chaque appel API au LLM est enregistré dans un log append-only (`calls_log.jsonl`).

#### `LLMCallRecord`

```python
class LLMCallRecord(BaseModel):
    """Individual LLM API call log entry."""
    call_id: str                      # UUID unique de l'appel
    timestamp: datetime               # Heure de l'appel
    agent: str                        # Nom de l'agent appelant (summarizer, densifier, etc.)
    step: str                         # Sous-étape (e.g., refine_chunk_003, density_pass_2)
    provider: str                     # LLM provider (anthropic, openai, google, ollama)
    model: str                        # Modèle utilisé (claude-sonnet-4-20250514, gpt-4o, etc.)
    input_tokens: int                 # Tokens envoyés (prompt + context)
    output_tokens: int                # Tokens reçus (completion)
    total_tokens: int                 # input_tokens + output_tokens
    cache_read_tokens: int            # Tokens lus depuis le prompt cache (si applicable)
    cache_write_tokens: int           # Tokens écrits dans le prompt cache (si applicable)
    latency_ms: int                   # Temps de réponse en millisecondes
    status: Literal["success", "retry", "failed"]
    retry_count: int                  # Nombre de retries effectués
    estimated_cost_usd: float         # Coût estimé de cet appel
```

#### Storage Format

JSONL (une ligne JSON par appel), append-only :

```
{output_path}/{document_id}/runs/{run_id}/04_synthesis/calls_log.jsonl
```

Ce format permet le streaming, le parsing partiel, et l'ajout sans réécriture.

### 20.3 Level 2 — Per-Agent Aggregation (`tracking/agent_tracker.py`)

Agrège les `LLMCallRecord` par agent pour une exécution donnée.

#### `AgentStats`

```python
class AgentStats(BaseModel):
    """Per-agent aggregated stats for a single execution."""
    agent: str                        # Nom de l'agent
    total_calls: int                  # Nombre d'appels LLM
    total_input_tokens: int
    total_output_tokens: int
    total_tokens: int
    total_cache_read_tokens: int
    total_cache_write_tokens: int
    avg_latency_ms: float             # Latence moyenne par appel
    max_latency_ms: int               # Latence max (détection d'anomalies)
    retry_count: int
    failure_count: int                # Appels échoués après retries
    estimated_cost_usd: float
    budget_usage_pct: float           # % du budget tokens consommé vs alloué
```

#### Storage

Merged into `04_synthesis/execution_stats.json` under the `execution.per_agent` key (see section 5.4).

### 20.4 Level 3a — Per-Document Session (`tracking/session_tracker.py`)

Vue consolidée de toute l'exécution pour un document.

#### `SessionStats`

```python
class SessionStats(BaseModel):
    """Consolidated view of a full document execution."""
    document_id: str
    session_id: str                   # UUID de cette exécution
    start_time: datetime
    end_time: datetime
    duration_seconds: float
    document_size_chars: int
    document_size_tokens_est: int
    total_llm_calls: int
    total_input_tokens: int
    total_output_tokens: int
    total_tokens: int
    total_cache_read_tokens: int
    total_cache_write_tokens: int
    estimated_cost_usd: float
    cost_per_1k_chars: float          # Coût normalisé par 1000 caractères source
    agents: dict[str, AgentStats]     # Stats détaillées par agent
    budget_total_allocated: int
    budget_total_consumed: int
    budget_usage_pct: float           # % global de consommation vs budget
    steps_degraded: list[str]         # Agents ayant dépassé leur budget
    steps_failed: list[str]           # Agents en échec
```

#### Storage

Merged into `04_synthesis/execution_stats.json` (see section 5.4). The `SessionStats` model is the source for the `execution` block in that file.

### 20.5 Level 3b — Cross-Document Cumulative (`tracking/stats_aggregator.py`)

Statistiques cumulées sur l'ensemble des documents traités, stockées dans un fichier central.

#### `GlobalStats`

```python
class TypeStats(BaseModel):
    """Per document-type aggregated stats."""
    count: int
    avg_tokens: float
    avg_cost_usd: float
    avg_duration_seconds: float
    avg_chunks: float

class CumulativeAgentStats(BaseModel):
    """Per-agent cumulative stats across all documents."""
    total_calls: int
    total_tokens: int
    avg_tokens_per_call: float
    failure_rate: float
    avg_latency_ms: float
    pct_of_total_cost: float

class ModelStats(BaseModel):
    """Per-LLM-model aggregated stats."""
    total_calls: int
    total_input_tokens: int
    total_output_tokens: int
    total_cost_usd: float

class DailyStats(BaseModel):
    """Daily consumption entry for cost trend tracking."""
    date: str                         # ISO date (YYYY-MM-DD)
    documents_processed: int
    total_tokens: int
    total_cost_usd: float

class GlobalStats(BaseModel):
    """Cross-document cumulative statistics."""
    total_documents_processed: int
    total_tokens_consumed: int
    total_estimated_cost_usd: float
    avg_tokens_per_document: float
    avg_cost_per_document: float
    avg_duration_per_document: float
    by_document_type: dict[str, TypeStats]
    by_agent: dict[str, CumulativeAgentStats]
    by_model: dict[str, ModelStats]
    cost_trend: list[DailyStats]      # Consommation quotidienne (historique)
    last_updated: datetime
```

#### Storage

```
{cache_root}/global_stats.json
```

### 20.6 Cost Calculation — `tracking/cost_calculator.py`

Conversion tokens → coût basée sur un pricing configurable :

```python
class ModelPricing(BaseModel):
    model: str
    input_price_per_1m: float       # USD per 1M input tokens
    output_price_per_1m: float      # USD per 1M output tokens
    cache_read_per_1m: float        # USD per 1M cache read tokens
    cache_write_per_1m: float       # USD per 1M cache write tokens
```

Le pricing est configurable dans `config/settings.py` et peut être mis à jour sans modifier le code.

### 20.7 Export — `tracking/exporter.py`

Formats d'export supportés :

| Format | Usage |
|--------|-------|
| JSON | Intégration programmatique, dashboards |
| CSV | Analyse dans un tableur, import BI |
| Summary text | Inclusion dans les logs ou rapports |

### 20.8 Integration Point

Le tracking est intégré dans chaque LLM adapter (`llm/adapters/*_adapter.py`) via la classe de base `BaseLLMClient`. Chaque appel au LLM passe par l'adapter qui :

1. Enregistre le `LLMCallRecord` via `call_logger` → append dans `calls_log.jsonl`
2. Les tokens input/output sont lus depuis la réponse API (normalisés dans `LLMResponse`)
3. Le `provider` et `model` sont automatiquement renseignés par l'adapter
4. En fin d'exécution, `session_tracker` agrège tout dans `execution_stats.json` (section 5.4)
5. `stats_aggregator` met à jour les stats globales cross-document

Aucun agent n'a besoin de connaître le système de tracking — il est transparent.

---

## 21. Critic Agent (Optional Validation)

### 21.1 Purpose

Agent optionnel de validation croisée qui vérifie la cohérence et la complétude des outputs des autres agents. Activé via `CRITIC_AGENT_ENABLED=true` dans le `.env`.

### 21.2 Position in Pipeline

```
[Decontextualizer] → [Summarizer] → [Densifier] ────────────────────────→ [Synthesizer] → [Critic]
                                                                                 ↑              │
                     [Concept Extractor] → [Merger (3 passes)] → [Graph Builder] ┘              │
                                                                                                │
                     [Reference Extractor] → [Reference Linker] ───────────────────────────────┘
                                                                                                │
                                                 ← feedback loop (if strictness=high) ←────────┘
```

Le Critic intervient **après** le Synthesizer. En mode `high`, il peut demander au Synthesizer de corriger et re-générer. Ce diagramme correspond au flux conceptuel complet de la section 6.2 (premier diagramme, incluant le Decontextualizer) — à ne pas confondre avec le DAG LangGraph (second diagramme de §6.2) qui exclut le Decontextualizer.

### 21.3 Validation Checks

| Check | Description | Score Impact |
|-------|-------------|-------------|
| **Coverage** | Le résumé dense couvre-t-il tous les triplets clés du graphe ? | Dégrade `densifier` confidence |
| **Consistency** | La synthèse finale est-elle cohérente avec le résumé ET le graphe ? | Dégrade `synthesizer` confidence |
| **Completeness** | Les thèmes identifiés sont-ils tous présents dans la synthèse ? | Dégrade `synthesizer` confidence |
| **Hallucination check** | Les affirmations de la synthèse sont-elles traçables aux chunks sources ? | Critical flag si détecté |
| **Entity coverage** | Les entités clés du graphe apparaissent-elles dans le résumé ? | Dégrade `densifier` confidence |

### 21.4 Strictness Levels

| Level | Behavior |
|-------|----------|
| `low` | Log-only — les warnings sont enregistrés mais n'affectent pas les scores |
| `medium` | Les scores de confiance sont ajustés à la baisse selon les findings |
| `high` | Le Critic peut déclencher une re-génération du Synthesizer (1 retry max) |

### 21.5 Project Structure Addition

```
├── pipeline/
│   ├── agents/
│   │   ├── ...
│   │   └── critic.py              # Optional validation agent
│   └── prompts/
│       ├── ...
│       └── critic.txt
```

---

## 22. Document Size Limits and Degradation

### 22.1 Limits (configurable via `.env`)

| Limit | Env Var | Default | Description |
|-------|---------|---------|-------------|
| File size | `MAX_DOCUMENT_SIZE_MB` | `50` | Taille maximale du fichier source |
| Page count | `MAX_DOCUMENT_PAGES` | `2000` | Nombre max de pages |
| Token count | `MAX_DOCUMENT_TOKENS` | `500000` | Tokens estimés après extraction |

### 22.2 Oversize Strategies (`OVERSIZE_STRATEGY`)

| Strategy | Behavior |
|----------|----------|
| `reject` | Le pipeline refuse le document, retourne une erreur avec les limites dépassées |
| `truncate` | Le document est tronqué à la limite. Un warning est émis. Les résultats portent un flag `truncated=true` |
| `sample` | Échantillonnage intelligent : introduction + conclusion + N sections clés (détectées via structure). Flag `sampled=true` + liste des sections traitées vs ignorées |

### 22.3 Size Check Position

Le contrôle de taille intervient **après l'extraction** (étape 1b), car la taille en tokens ne peut être estimée qu'après extraction du texte brut. L'ordre est :

```
1a. Language detection
1b. Text extraction → SIZE CHECK HERE
    → if oversize: apply strategy (reject/truncate/sample)
1c. Structure detection
...
```

---

## 23. Structured Logging

### 23.1 Architecture

```
src/
├── logging/
│   ├── __init__.py
│   ├── logger.py                  # Logger factory, formatters
│   ├── context.py                 # Contextual logging (document_id, run_id, agent)
│   └── handlers.py                # File rotation, optional remote handler
```

### 23.2 Log Configuration (via `.env`)

| Parameter | Env Var | Default |
|-----------|---------|---------|
| Level | `LOG_LEVEL` | `INFO` |
| Format | `LOG_FORMAT` | `json` |
| File path | `LOG_FILE` | `~/.ayextractor/logs/analyzer.log` |
| Rotation | `LOG_ROTATION` | `10MB` |
| Retention | `LOG_RETENTION` | `30` (days) |

### 23.3 Log Format — JSON

```json
{
    "timestamp": "2026-02-07T16:15:12.345Z",
    "level": "INFO",
    "logger": "pipeline.agents.summarizer",
    "message": "Refine pass completed for chunk 3/15",
    "context": {
        "document_id": "20260207_140000_a1b2c3d4",
        "run_id": "20260207_1615_b3c8d",
        "agent": "summarizer",
        "step": "refine_chunk_003"
    },
    "data": {
        "input_tokens": 2100,
        "output_tokens": 850,
        "latency_ms": 2300
    }
}
```

### 23.4 Log Format — Text (human-readable)

```
2026-02-07 16:15:12.345 | INFO  | summarizer | [20260207_1615_b3c8d] Refine pass completed for chunk 3/15 (2100→850 tokens, 2300ms)
```

### 23.5 Contextual Logging

Chaque composant hérite d'un contexte automatique via `logging/context.py` :

- **Facade level** : `document_id`
- **Run level** : `document_id` + `run_id`
- **Agent level** : `document_id` + `run_id` + `agent` + `step`

Les développeurs n'ont pas à passer ces champs manuellement — ils sont injectés via un context manager.

### 23.6 Log Levels Usage

| Level | Usage |
|-------|-------|
| `DEBUG` | Contenu détaillé des prompts, réponses LLM brutes, états intermédiaires |
| `INFO` | Progression du pipeline, début/fin de chaque étape, résultats de cache lookup |
| `WARNING` | Score de confiance sous le seuil, budget tokens dépassé, retry en cours, document tronqué |
| `ERROR` | Échec d'un agent après retries, parsing error, document corrompu |

---

## 24. Batch Scanning

### 24.1 Purpose

Scan automatique d'un répertoire de documents pour détecter les nouveaux fichiers et lancer leur analyse. Utilise le système de fingerprinting multi-niveaux (section 8) pour éviter le retraitement.

### 24.2 Configuration (via `.env`)

| Parameter | Env Var | Default |
|-----------|---------|---------|
| Enabled | `BATCH_SCAN_ENABLED` | `false` |
| Root directory | `BATCH_SCAN_ROOT` | (required if enabled) |
| Recursive | `BATCH_SCAN_RECURSIVE` | `true` |
| Formats | `BATCH_SCAN_FORMATS` | `pdf,epub,docx,md,txt,png,jpg,jpeg,webp` |

### 24.3 Scan Flow

```
batch_scanner.scan(scan_root)
│
├── 1. List all files matching supported formats (recursive if enabled)
│
├── 2. For each file:
│   ├── Compute Level 1 (exact_hash) and Level 2 (content_hash)
│   ├── Lookup in cache index
│   │
│   ├── EXACT MATCH → skip, log "already processed"
│   ├── CONTENT MATCH → skip, log "content identical, different file"
│   ├── NEAR MATCH (simhash) → log warning "similar document found", skip or process based on config
│   ├── NO MATCH → queue for processing
│   │
│   └── 3. Process queue
│       └── For each new document: facade.analyze(document, auto_metadata)
│
└── 4. Return BatchResult (processed, skipped, errors)
```

### 24.4 Project Structure Addition

```
src/
├── batch/
│   ├── __init__.py
│   ├── scanner.py                 # Directory scanning and file discovery
│   ├── dedup.py                   # Fingerprint comparison against cache
│   └── models.py                  # BatchResult, ScanEntry
```

### 24.5 Batch Models — `batch/models.py`

```python
class ScanEntry(BaseModel):
    """A single file discovered during batch scan."""
    file_path: str                    # Absolute path to the file
    filename: str                     # "report_q3.pdf"
    format: str                       # "pdf"
    size_bytes: int
    fingerprint_exact: str            # Level 1 hash (SHA-256 of file bytes)
    fingerprint_content: str          # Level 2 hash (SHA-256 of extracted text)
    cache_status: Literal["exact_match", "content_match", "near_match", "no_match"]
    matched_document_id: str | None   # Existing document_id if cache hit

class BatchResult(BaseModel):
    """Summary result of a batch scan + processing run."""
    scan_root: str                    # Root directory scanned
    total_files_found: int            # Total files matching formats
    processed: int                    # Documents successfully analyzed
    skipped: int                      # Documents skipped (cache hit)
    errors: int                       # Documents that failed analysis
    entries: list[ScanEntry]          # All scanned entries with status
    duration_seconds: float           # Total wall-clock time
```

---

## 25. Agent PluginKit

### 25.1 Purpose

Interface standardisée permettant d'ajouter de nouveaux agents au pipeline sans modifier le code existant. Chaque agent est un plugin auto-descriptif qui respecte un contrat d'interface.

### 25.2 Agent Models — `pipeline/plugin_kit/models.py`

```python
class AgentMetadata(BaseModel):
    """Metadata about an agent execution, attached to every AgentOutput."""
    agent_name: str                   # "summarizer", "concept_extractor"
    agent_version: str                # "1.0.0"
    execution_time_ms: int            # Wall-clock time in milliseconds
    llm_calls: int                    # Number of LLM calls made during execution
    tokens_used: int                  # Total tokens consumed (input + output)
    prompt_hash: str | None = None    # SHA256 of prompt template used

class AgentOutput(BaseModel):
    """Standard return type for all BaseAgent.execute() calls."""
    data: dict[str, Any]              # Output fields matching agent's output_schema
    confidence: float                 # Self-assessed confidence score [0.0-1.0]
    metadata: AgentMetadata           # Execution metadata for tracking
    warnings: list[str] = []          # Non-fatal issues encountered during execution
```

> **Note :** `data` contient les champs qui seront mergés dans `PipelineState` par l'orchestrateur. Les clés doivent correspondre aux champs définis dans `output_schema`. Le merge est effectué par `orchestrator.py` après validation par `validate_output()`.

### 25.3 Agent Interface — `pipeline/plugin_kit/base_agent.py`

```python
class BaseAgent(ABC):
    """Standard interface for all pipeline agents."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique agent identifier (e.g., 'summarizer', 'fact_checker')."""

    @property
    @abstractmethod
    def version(self) -> str:
        """Agent version (semver)."""

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description of what this agent does."""

    @property
    @abstractmethod
    def input_schema(self) -> type[BaseModel]:
        """Pydantic model defining required input fields from PipelineState."""

    @property
    @abstractmethod
    def output_schema(self) -> type[BaseModel]:
        """Pydantic model defining output fields added to PipelineState."""

    @property
    def dependencies(self) -> list[str]:
        """List of agent names that must run before this one."""
        return []

    @property
    def prompt_file(self) -> str | None:
        """Path to prompt template file (supports {lang} placeholder)."""
        return None

    @abstractmethod
    async def execute(self, state: PipelineState, llm: BaseLLMClient) -> AgentOutput:
        """Execute the agent's logic."""

    def validate_output(self, output: AgentOutput) -> float:
        """Self-validation returning confidence score (0.0-1.0). Override for custom logic."""
        return 1.0
```

### 25.4 Agent Registration

Les agents sont enregistrés via un registre central. Deux modes :

**Déclaratif (recommandé)** — via configuration :

```python
# config/agents.py
AGENT_REGISTRY = [
    "pipeline.agents.summarizer.SummarizerAgent",
    "pipeline.agents.densifier.DensifierAgent",
    "pipeline.agents.concept_extractor.ConceptExtractorAgent",
    "pipeline.agents.reference_extractor.ReferenceExtractorAgent",
    "pipeline.agents.community_summarizer.CommunitySummarizerAgent",
    "pipeline.agents.profile_generator.ProfileGeneratorAgent",
    "pipeline.agents.synthesizer.SynthesizerAgent",
    # Optional agents
    "pipeline.agents.critic.CriticAgent",
    # Custom plugins
    # "my_plugins.fact_checker.FactCheckerAgent",
    # "my_plugins.sentiment_analyzer.SentimentAgent",
]
```

> **Note :** Le `decontextualizer` n'apparaît pas dans le registre car il s'exécute en pré-traitement (phase 2c), avant la construction du DAG LangGraph. Il est invoqué directement par l'orchestrateur, pas via le PluginKit. De même, `community_integrator` (graph/layers/) est un module utilitaire invoqué par l'orchestrateur entre community_detector et community_summarizer — ce n'est pas un agent LLM. Seuls les agents participant au DAG de la phase 3 sont enregistrés ici.

**Programmatique** — via API :

```python
from ayextractor.pipeline.plugin_kit import AgentRegistry

registry = AgentRegistry()
registry.register(MyCustomAgent())
```

### 25.5 DAG Auto-Construction

L'orchestrateur LangGraph construit automatiquement le DAG à partir des agents enregistrés et de leurs `dependencies` :

1. Charger tous les agents du registre
2. Résoudre les dépendances → construire le DAG
3. Valider l'absence de cycles
4. Identifier les agents parallélisables (pas de dépendance entre eux)
5. Exécuter selon le graphe

### 25.6 Adding a Custom Agent — Example

Pour ajouter un agent "Fact Checker" :

```python
# Example plugin: my_plugins/fact_checker.py

class FactCheckerInput(BaseModel):
    """Input schema — fields read from PipelineState."""
    chunks: list[Chunk]
    dense_summary: str

class FactCheckerOutput(BaseModel):
    """Output schema — fields added to PipelineState."""
    verified_claims: list[dict]       # Claims confirmed by source chunks
    flagged_claims: list[dict]        # Claims not traceable to sources

class FactCheckerAgent(BaseAgent):
    name = "fact_checker"
    version = "1.0.0"
    description = "Verifies factual claims against source chunks"
    input_schema = FactCheckerInput     # needs: chunks, dense_summary
    output_schema = FactCheckerOutput   # produces: verified_claims, flagged_claims
    dependencies = ["densifier"]        # runs after densifier

    async def execute(self, state, llm):
        # ... implementation ...

    def validate_output(self, output):
        return output.verification_ratio
```

Puis ajouter dans le registre :

```python
"my_plugins.fact_checker.FactCheckerAgent",
```

Le DAG est automatiquement mis à jour. Aucune modification du code existant.

### 25.7 Project Structure Addition

```
src/
├── pipeline/
│   ├── plugin_kit/
│   │   ├── __init__.py
│   │   ├── base_agent.py          # BaseAgent ABC
│   │   ├── registry.py            # Agent registration and discovery
│   │   ├── dag_builder.py         # Auto-construct LangGraph DAG from agents
│   │   └── models.py              # AgentOutput, AgentMetadata
```

---

## 26. RAG Integration (Optional)

### 26.1 Purpose

Deux rôles distincts :

1. **Enrichment** (during analysis) : enrichir le pipeline d'analyse en exploitant des connaissances complémentaires provenant de documents déjà analysés et de sources externes
2. **Retrieval** (post-analysis, for downstream consumers) : répondre à des requêtes en exploitant le Document Graph + Corpus Graph via un pipeline de retrieval hiérarchique

### 26.2 Dual-Store Architecture

```
┌─────────────────────────────────────┐     ┌──────────────────────────────────────┐
│         Vector Database              │     │          Graph Database               │
│    (ChromaDB / Qdrant / ArangoDB)    │     │       (Neo4j / ArangoDB)              │
│                                      │     │                                      │
│  Collections:                        │     │  Document Graphs:                    │
│  ├── chunks (decontextualized text)  │     │  ├── L1 Topics (communities)         │
│  ├── entity_profiles (L2 profiles)   │     │  ├── L2 Actors + L3 Evidence         │
│  ├── relation_profiles               │     │  └── Qualified edges + provenance    │
│  └── community_summaries (L1)        │     │                                      │
│                                      │     │  Corpus Graph:                       │
│                                      │     │  ├── C-nodes (canonical concepts)    │
│                                      │     │  ├── T-nodes (taxonomy)              │
│                                      │     │  └── X-edges (cross-doc relations)   │
└───────────────┬──────────────────────┘     └──────────────┬───────────────────────┘
                │                                            │
                └───────────────┬────────────────────────────┘
                                │
                    ┌───────────▼──────────────┐
                    │  Hierarchical Retriever   │
                    │  (rag/retriever/pipeline) │
                    └───────────┬──────────────┘
                                │
                    ┌───────────▼──────────────┐
                    │  Context Assembler        │
                    │  (structured LLM context) │
                    └──────────────────────────┘
```

### 26.3 Usage Modes

La configuration RAG repose sur deux axes indépendants :

| Axe | Variable | Responsabilité |
|-----|----------|----------------|
| **Écriture** | `CHUNK_OUTPUT_MODE` | Contrôle si les résultats sont indexés en base de données après analyse |
| **Lecture** | `RAG_ENABLED` | Contrôle si les agents consultent les bases pendant l'analyse |

Ces deux axes sont **indépendants**. On peut :
- Écrire en base sans lire (`CHUNK_OUTPUT_MODE=files_and_vectordb` + `RAG_ENABLED=false`) → constitution progressive d'un corpus
- Lire sans écrire (`CHUNK_OUTPUT_MODE=files_only` + `RAG_ENABLED=true`) → exploiter un corpus pré-existant sans l'enrichir
- Les deux (`CHUNK_OUTPUT_MODE=files_and_both_db` + `RAG_ENABLED=true`) → boucle complète d'enrichissement mutuel
- Aucun (`CHUNK_OUTPUT_MODE=files_only` + `RAG_ENABLED=false`) → pipeline standalone, fichiers uniquement

| Combinaison | Écriture DB | Lecture DB | Usage |
|-------------|:-----------:|:----------:|-------|
| `files_only` + `RAG_ENABLED=false` | ❌ | ❌ | Pipeline standalone |
| `files_and_vectordb` + `RAG_ENABLED=false` | ✅ | ❌ | Constitution de corpus |
| `files_only` + `RAG_ENABLED=true` | ❌ | ✅ | Exploitation d'un corpus existant |
| `files_and_both_db` + `RAG_ENABLED=true` | ✅ | ✅ | Enrichissement mutuel complet |

### 26.4 Integration Points in Pipeline (Enrichment)

| Agent | RAG Value | Usage | Justification |
|-------|:---------:|-------|---------------|
| **Decontextualizer** | ✅ Élevée | Retrieves entity definitions from graph DB to resolve ambiguous references | Résoudre "l'entreprise" → chercher dans le graph DB quelle entité est dominante dans des documents similaires. Résoudre des acronymes via un corpus de définitions stocké |
| **Summarizer** | ⚠️ Opt-in | Retrieves related summaries from previously analyzed documents for context | Utile seulement si on analyse un corpus cohérent (ex: tous les rapports d'une même entreprise). Risque de biais si le RAG injecte du contexte d'un autre document |
| **Densifier** | ❌ Non | N/A | Le densifier condense le résumé Refine déjà produit. Processus purement interne — le RAG n'apporte rien |
| **Concept Extractor** | ✅ Élevée | Checks if extracted entities exist in Corpus Graph C-nodes, reuses canonical names. Enriches triplet extraction with domain knowledge | Normalisation des noms d'entités, enrichissement des relations, détection de concepts liés déjà connus |
| **Reference Extractor** | ✅ Élevée | Resolves citations against known documents in the vector DB | Résoudre des citations contre des documents déjà analysés — "voir rapport XYZ" → lien concret vers un document_id existant |
| **Synthesizer** | ✅ Élevée | Enriches final synthesis with cross-document connections found in Corpus Graph | "Ce document complète l'analyse X sur le thème Y", "Les conclusions divergent du rapport Z" |
| **Critic** | ⚠️ Opt-in | Verifies factual claims against known corpus | Risque de faux positifs si le corpus contient des infos contradictoires |

**Default `RAG_ENRICH_AGENTS`** : `decontextualizer,concept_extractor,reference_extractor,synthesizer`

Le summarizer et le critic doivent être ajoutés explicitement si souhaité.

#### 26.4.1 Enrichment Injection Mechanism

**Architecture :** L'enrichissement RAG est piloté par l'orchestrateur, pas par les agents eux-mêmes. Cela préserve la testabilité des agents (aucune dépendance directe sur les stores).

**Flux :**

1. L'orchestrateur vérifie si `RAG_ENABLED=true` et si l'agent courant est dans `RAG_ENRICH_AGENTS`
2. Si oui, il appelle `rag/enricher.py` avec le contexte pertinent (chunks, entités en cours, query dérivée du contenu)
3. `enricher.py` interroge les stores disponibles (vector DB, graph DB) via le retrieval pipeline (§26.6)
4. `enricher.py` retourne un `RAGContext` (défini dans `rag/models.py`, voir §26.6.1)
5. L'orchestrateur injecte ce `RAGContext` dans `PipelineState.rag_context`
6. L'agent lit `state.rag_context.assembled_text` et l'incorpore dans son prompt via un bloc `## Corpus Context` conditionnel

**Contrat agent :** Chaque agent enrichissable DOIT vérifier `state.rag_context is not None` avant d'inclure le contexte dans son prompt. Si `rag_context` est `None`, l'agent fonctionne en mode standalone (aucune différence de comportement).

```python
# In pipeline/orchestrator.py — before calling each agent
if settings.RAG_ENABLED and agent.name in settings.RAG_ENRICH_AGENTS:
    rag_context = await enricher.build_context(
        agent_name=agent.name,
        state=state,
        vector_store=vector_store,   # May be None
        graph_store=graph_store,     # May be None
    )
    # enricher.build_context() internally calls assemble_context() (§26.6.4)
    # to produce rag_context.assembled_text — the pre-formatted prompt block.
    state.rag_context = rag_context
else:
    state.rag_context = None
```

> **Note :** Le `rag_context` est recalculé pour CHAQUE agent (le contenu pertinent diffère selon l'agent). Le decontextualizer cherchera des définitions d'entités, le concept_extractor cherchera des canonical names existants, etc.

### 26.5 Post-Analysis Indexing (`rag/indexer.py`)

Après chaque analyse réussie (étape 4 — Finalization), si `CHUNK_OUTPUT_MODE` inclut une base de données :

**Vector DB** (`files_and_vectordb` ou `files_and_both_db`) — 4 collections indexées :

| Collection | Content | Embedding Source |
|------------|---------|------------------|
| `chunks` | Decontextualized chunk content + context_summary + global_summary | chunk.content |
| `entity_profiles` | L2 entity profiles (textual, see §13.11) | profile_text |
| `relation_profiles` | Key relation profiles (textual, see §13.11) | profile_text |
| `community_summaries` | L1 community summaries (all levels) | summary text |

**Graph DB** (`files_and_graphdb` ou `files_and_both_db`) :
- Import Document Graph (L1/L2/L3 + qualified edges + metadata) into graph DB
- Si `CONSOLIDATOR_ENABLED=true` : exécuter Pass 1 (Linking) pour merger dans Corpus Graph

### 26.6 Hierarchical Retrieval Pipeline (`rag/retriever/`)

#### 26.6.1 Architecture

**Shared retrieval models** (`rag/models.py`) :

```python
class SearchResult(BaseModel):
    """Single result from a vector or graph store query."""
    source_type: Literal["chunk", "entity_profile", "relation_profile", "community_summary"]
    source_id: str                    # chunk_id, canonical_name, or community_id
    content: str                      # Retrieved text content
    score: float                      # Relevance score (0.0-1.0)
    metadata: dict[str, Any]          # Source-specific metadata (document_id, layer, etc.)

class RAGContext(BaseModel):
    """Assembled RAG context injected into agent prompts during enrichment.
    Built by rag/enricher.py from available stores. Passed to agents via PipelineState."""
    assembled_text: str               # Pre-formatted context block for prompt injection (output of assemble_context())
    community_summaries: list[str]    # Top-K community summary texts (Level 1) — for traceability
    entity_profiles: list[str]        # Top-K entity profile texts (Level 2) — for traceability
    chunk_excerpts: list[str]         # Top-K chunk texts (Level 3 fallback) — for traceability
    corpus_context: CorpusContext | None  # Cross-document knowledge (if CONSOLIDATOR_ENABLED)
    contradictions: list[str]         # Contradiction descriptions (if any)
    total_token_count: int            # Total tokens in assembled_text
    search_results: list[SearchResult]  # Raw results before assembly (for traceability)

class RetrievalPlan(BaseModel):
    """Plan generated by query_classifier for orchestrating retrieval levels."""
    query_type: Literal["conceptual", "factual", "relational", "exploratory"]
    levels_to_query: list[Literal["community", "entity", "chunk", "corpus"]]
    estimated_token_cost: int

class CorpusContext(BaseModel):
    """Cross-document knowledge retrieved from Corpus Graph."""
    cnodes: list[CNode]               # Relevant canonical concepts
    tnodes: list[TNode]               # Relevant taxonomy nodes
    xedges: list[XEdge]               # Cross-document relations
    source_document_count: int        # Number of documents contributing
```

> **Note :** `Contradiction` est défini dans `consolidator/models.py` (produit par Pass 5) et importé par `rag/retriever/context_assembler.py` quand des contradictions sont détectées.

```python
# consolidator/models.py
class Contradiction(BaseModel):
    """Conflicting claims detected in Corpus Graph."""
    contradiction_id: str
    edge_a_subject: str
    edge_a_predicate: str
    edge_a_object: str
    edge_b_subject: str
    edge_b_predicate: str
    edge_b_object: str
    conflict_type: Literal["value", "temporal", "negation"]
    source_documents_a: list[str]     # document_ids supporting edge_a
    source_documents_b: list[str]     # document_ids supporting edge_b
```

Le retrieval s'effectue en **3 niveaux hiérarchiques avec pruning** à chaque niveau. Chaque niveau est un module séparé dans `rag/retriever/`.

```
Query
  │
  ▼
[query_classifier.py] → Classify query type
  │
  ├─── LEVEL 1: Community Retrieval (community_retriever.py)
  │    │  Vector search on community_summaries collection
  │    │  → Top-K community summaries (ranked by relevance)
  │    │  → PRUNE: discard communities with score < threshold
  │    │
  ├─── LEVEL 2: Entity Retrieval (entity_retriever.py)
  │    │  Vector search on entity_profiles collection
  │    │  + PPR scoring on Document/Corpus Graph (ppr_scorer.py)
  │    │  → Top-K entity profiles + relation profiles
  │    │  → PRUNE: discard entities with composite score < threshold
  │    │
  ├─── LEVEL 3: Chunk Retrieval (chunk_retriever.py)
  │    │  Vector search on chunks collection
  │    │  ONLY if Level 2 confidence < CHUNK_FALLBACK_THRESHOLD
  │    │  → Top-K source chunks (evidence fallback)
  │    │
  ├─── CROSS-DOC: Corpus Retrieval (corpus_retriever.py)
  │    │  If CONSOLIDATOR_ENABLED: lookup C-nodes + T-nodes + X-edges
  │    │  → Cross-document knowledge context
  │    │
  └─── [context_assembler.py] → Assemble final LLM context
```

#### 26.6.2 Query Classification (`query_classifier.py`)

Module **pur** : classifie la query pour adapter la stratégie de retrieval.

| Query Type | Description | Strategy |
|------------|-------------|----------|
| `conceptual` | "Quels sont les enjeux du CSMS ?" | Community summaries first, broad entity profiles |
| `factual` | "Quel est le budget du projet X ?" | Entity profiles + L3 evidence, narrow |
| `relational` | "Comment ISO 21434 et UN R155 interagissent ?" | PPR between entities, X-edges from Corpus Graph |
| `exploratory` | "Résumez tout ce que vous savez sur OPmobility" | All levels, high breadth |

#### 26.6.3 PPR Scoring (`ppr_scorer.py`)

Module **pur** : implémente Personalized PageRank sur le knowledge graph. Utilise `nx.pagerank` (accéléré transparentement via `nx-cugraph` si disponible — voir §33).

```python
def ppr_score(graph: nx.Graph, seed_entities: list[str],
              alpha: float = 0.15, max_iter: int = 100) -> dict[str, float]:
    """
    Personalized PageRank from seed entities.
    
    1. Extract entities from query (via entity recognition)
    2. Set seed nodes (teleport probability)
    3. Propagate importance through graph edges
    4. Return scored nodes (captures relational paths)
    
    Complements composite scoring (§13.9.8) by capturing
    graph-structural relevance that static metrics miss.
    """
```

**Intégration avec le scoring composite :**

```
final_score = α × composite_score(confidence, salience, freshness, corroboration)
            + (1-α) × ppr_score
```

`α` configurable via `RAG_COMPOSITE_WEIGHT` (default: 0.3 → 30% composite score, 70% PPR).

#### 26.6.4 Context Assembly (`context_assembler.py`)

Module **pur** : assemble le contexte final structuré pour le LLM. Appelé en interne par `rag/enricher.py` — les agents ne l'appellent jamais directement.

```python
def assemble_context(communities: list[CommunitySummary],
                     entities: list[EntityProfile],
                     relations: list[RelationProfile],
                     chunks: list[Chunk],
                     corpus_knowledge: CorpusContext | None,
                     contradictions: list[Contradiction] | None,
                     token_budget: int) -> str:
    """
    Assemble structured context for LLM prompt.
    Prioritizes by relevance score, fits within token_budget.
    
    Called by rag/enricher.py which:
    1. Queries stores to get typed objects (CommunitySummary, EntityProfile, etc.)
    2. Calls this function to produce assembled_text (str)
    3. Extracts .summary/.profile_text fields for RAGContext traceability fields (list[str])
    4. Builds RAGContext with both assembled_text and traceability lists
    """
```

**Structure du contexte assemblé :**

```
## Document Context
[Top community summaries — broad thematic framing]

## Cross-Document Knowledge
[C-nodes from N documents, taxonomy paths, consolidated attributes, X-edges]
(Only if CONSOLIDATOR_ENABLED and corpus_knowledge available)

## Relevant Entities
[L2 Actor profiles + L3 attributes + temporal scopes + source refs]

## Evidence
[Top-K chunks scored by relevance — only if Level 2 confidence < threshold]

## Contradictions
[If conflicting claims detected, signal both versions with source refs]
```

#### 26.6.5 Configuration

| Env Var | Default | Description |
|---------|---------|-------------|
| `RAG_RETRIEVAL_TOP_K_COMMUNITIES` | `5` | Max community summaries retrieved |
| `RAG_RETRIEVAL_TOP_K_ENTITIES` | `20` | Max entity profiles retrieved |
| `RAG_RETRIEVAL_TOP_K_CHUNKS` | `10` | Max chunks retrieved (fallback) |
| `RAG_CHUNK_FALLBACK_THRESHOLD` | `0.6` | Entity confidence below which chunks are retrieved |
| `RAG_COMPOSITE_WEIGHT` | `0.3` | Weight of composite score in formula: α×composite + (1-α)×PPR |
| `RAG_PPR_ALPHA` | `0.15` | PPR teleport probability |
| `RAG_CONTEXT_TOKEN_BUDGET` | `4000` | Max tokens for assembled context |
| `RAG_INCLUDE_CORPUS_GRAPH` | `true` | Include C-nodes/T-nodes/X-edges in context (if CONSOLIDATOR_ENABLED) |

### 26.7 Project Structure

See section 3 — `rag/` module with:
- `retriever/` — hierarchical retrieval pipeline (query_classifier, community/entity/chunk/corpus retrievers, ppr_scorer, context_assembler, pipeline orchestrator)
- `vector_store/` — adapter-based vector store implementations
- `graph_store/` — adapter-based graph store implementations
- `embeddings/` — adapter-based embedding providers

---

## 27. LLM Adapter Architecture

### 27.1 Purpose

Abstraire le provider LLM derrière une interface uniforme. Chaque provider (Anthropic, OpenAI, Google, Ollama) implémente le même contrat, permettant de changer de provider sans toucher au code des agents.

### 27.2 Interface — `llm/base_client.py`

```python
class BaseLLMClient(ABC):
    """Unified interface for all LLM providers."""

    @abstractmethod
    async def complete(
        self,
        messages: list[Message],
        system: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        response_format: type[BaseModel] | None = None,
    ) -> LLMResponse:
        """Text completion."""

    # Implementation note: When response_format is provided with a Pydantic BaseModel,
    # adapters should use provider-native structured outputs when available:
    # - Anthropic: output_config with json_schema + constrained decoding (guaranteed schema compliance)
    # - OpenAI: response_format with json_schema (structured outputs)
    # - Google: response_schema in generation_config
    # - Ollama: format="json" (best-effort, no schema guarantee)
    # Constrained decoding eliminates JSON parse errors and retry logic.

    @abstractmethod
    async def complete_with_vision(
        self,
        messages: list[Message],
        images: list[ImageInput],
        system: str | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Vision-enabled completion (images + text)."""

    @property
    @abstractmethod
    def supports_vision(self) -> bool:
        """Whether this provider/model supports image inputs."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Provider identifier (anthropic, openai, google, ollama)."""
```

### 27.3 LLM Types — `llm/models.py`

Types Pydantic spécifiques à l'interface LLM, utilisés par `BaseLLMClient` et ses implémentations :

```python
class Message(BaseModel):
    """Single message in a conversation."""
    role: Literal["user", "assistant", "system"]
    content: str

class ImageInput(BaseModel):
    """Image payload for vision-enabled completions."""
    data: bytes                       # Raw image bytes
    media_type: str                   # MIME type (image/png, image/jpeg, etc.)
    source_id: str | None = None      # Optional reference to source image (img_001)

class LLMResponse(BaseModel):
    """Normalized response from any LLM provider."""
    content: str                      # Generated text
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int            # 0 if not supported
    cache_write_tokens: int           # 0 if not supported
    model: str                        # Actual model used
    provider: str                     # Provider name
    latency_ms: int
    raw_response: Any                 # Original provider response (for debugging)
```

### 27.4 Adapter Implementations

| Adapter | File | SDK | Vision Support |
|---------|------|-----|----------------|
| Anthropic | `anthropic_adapter.py` | `anthropic` | ✅ Claude Vision |
| OpenAI | `openai_adapter.py` | `openai` | ✅ GPT-4 Vision |
| Google | `google_adapter.py` | `google-generativeai` | ✅ Gemini Vision |
| Ollama | `ollama_adapter.py` | `ollama` | ⚠️ Model-dependent |

### 27.5 Factory — `llm/client_factory.py`

```python
def create_llm_client(provider: str, model: str, **kwargs) -> BaseLLMClient:
    """Instantiate the correct adapter from provider name."""
```

Le factory est appelé par l'orchestrateur pour instancier le bon client selon la configuration per-agent (section 17.3).

### 27.6 Adding a New Provider

1. Créer `llm/adapters/my_provider_adapter.py` implémentant `BaseLLMClient`
2. Enregistrer dans le factory
3. Utiliser via `.env` : `LLM_SUMMARIZER=my_provider:my_model`

Aucune modification du code des agents ou de l'orchestrateur.

---

## 28. Chunk Decontextualization

### 28.1 Problem

Quand un document est découpé en chunks, chaque chunk perd le contexte global. Des pronoms ("il", "elle"), des références implicites ("l'entreprise", "ce protocole", "la norme mentionnée"), ou des acronymes sans définition deviennent ambigus.

Exemples :
- *"Il a décidé de restructurer l'équipe"* → Qui est "il" ?
- *"L'entreprise a augmenté son CA de 15%"* → Quelle entreprise ?
- *"Conformément à la norme, le processus doit..."* → Quelle norme ?

### 28.2 Solution : Decontextualization Agent

Un agent LLM dédié (`decontextualizer`) traite chaque chunk **après** le chunking et **avant** le pipeline d'analyse. Il reçoit :
- Le chunk courant
- Le contexte global (titre du document, table des matières, résumé des premiers paragraphes)
- Les N chunks précédents (fenêtre glissante)

Et produit une version enrichie du chunk où toutes les références ambiguës sont résolues inline.

### 28.3 Example

**Chunk original :**
```
Il a décidé de restructurer l'équipe produit. L'entreprise prévoit
une croissance de 15% grâce à cette réorganisation.
```

**Chunk décontextualisé :**
```
Marc Dupont (CEO d'Acme Corp) a décidé de restructurer l'équipe produit
d'Acme Corp. Acme Corp prévoit une croissance de 15% grâce à cette
réorganisation de l'équipe produit.
```

### 28.4 Pipeline Position

```
2. CHUNKING + INTERLEAVED SUMMARIZATION/DECONTEXTUALIZATION PHASE
    ├── 2a. Chunking (structural/semantic)
    ├── 2b. Chunk validation
    ├── 2c. INTERLEAVED LOOP — for each chunk N:
    │       ├── 2c-i.  Decontextualize(chunk_N, refine_summary_{N-1}, refs, window)
    │       └── 2c-ii. Refine(decontextualized_chunk_N) → refine_summary_N
    │                  → store as chunk_N.context_summary
    ├── 2d. Densifier → dense_summary → inject as global_summary in ALL chunks
    ├── 2e. Write chunk files
    └── 2f. VectorDB indexation (if configured)
```

### 28.5 Output Structure Impact

```
├── 02_chunks/
│   ├── refine_summary.txt          # Final Refine summary (after all chunks)
│   ├── dense_summary.txt           # Condensed summary (= global_summary)
│   ├── chunk_001.json              # Includes context_summary + global_summary + decontextualized content
│   ├── chunk_001_original.txt      # Original version (preserved for CLI diff)
│   ├── chunk_002.json
│   ├── chunk_002_original.txt
│   └── chunks_index.json           # Includes decontextualization metadata
```

### 28.6 Configuration

| Env Var | Default | Description |
|---------|---------|-------------|
| `DECONTEXTUALIZATION_ENABLED` | `true` | Enable/disable the decontextualization step |
| `LLM_DECONTEXTUALIZER` | (default LLM) | Provider:model for this agent |
| `DECONTEXTUALIZER_TOOL_USE` | `auto` | `auto` \| `always` \| `never` — controls chunk_lookup tool availability |
| `DECONTEXTUALIZER_TOOL_CONFIDENCE_THRESHOLD` | `0.7` | Below this per-chunk confidence, tool is activated (auto mode) |

### 28.7 Decontextualizer Tool-Use — `chunk_lookup`

Lorsqu'activé (`DECONTEXTUALIZER_TOOL_USE=auto` ou `always`), le decontextualizer dispose d'un outil `chunk_lookup` lui permettant de rechercher des informations complémentaires dans les chunks déjà traités.

#### Modes

| Mode | Comportement |
|------|-------------|
| `never` | Aucun tool. Le decontextualizer utilise uniquement : dense_summary + references + fenêtre glissante. Latence minimale, coût minimal. |
| `auto` | Le decontextualizer produit d'abord sa décontextualisation **sans** tool. L'orchestrateur évalue la `resolution_confidence` du chunk. Si < `DECONTEXTUALIZER_TOOL_CONFIDENCE_THRESHOLD` (défaut: 0.7), le chunk est **re-traité** avec le tool activé. Seuls les chunks problématiques engendrent un surcoût. |
| `always` | Le tool est disponible à chaque chunk. Le LLM décide lui-même quand l'utiliser. Plus coûteux mais qualité maximale. |

#### Tool Definition

```python
@tool
def chunk_lookup(query: str, max_results: int = 3) -> list[dict]:
    """Search previously processed chunks for contextual information.
    Use when you cannot resolve an ambiguous reference (pronoun, acronym,
    definite article) from the provided context (summary + preceding chunks).
    
    Args:
        query: Natural language query describing what you're looking for
               (e.g., "who is the CEO mentioned in section 2", "what does CSMS stand for")
        max_results: Number of relevant chunks to return (1-5)
    
    Returns:
        List of {chunk_id, content_excerpt, relevance_score}
    """
```

L'implémentation utilise une recherche par similarité d'embeddings sur les chunks déjà traités (index en mémoire, pas de DB externe nécessaire). En mode `auto`, le coût additionnel est typiquement de 5-15% de tokens supplémentaires (seuls les chunks avec ambiguïtés non-résolues déclenchent le tool).

### 28.8 Impact on Downstream Agents

Les agents suivants travaillent sur les chunks décontextualisés. Bénéfices :
- **Concept Extractor** : extraction d'entités nommées correctes, pas de pronoms dans les triplets
- **RAG** : les chunks indexés sont auto-suffisants, portent `context_summary` (position narrative) + `global_summary` (vision d'ensemble). La recherche par similarité est plus précise et le reranking peut exploiter les deux niveaux de contexte.
- **Synthesizer** : les thèmes sont identifiés à partir d'entités canoniques, pas d'ambiguïtés

> **Note :** Le Summarizer Refine travaille sur les chunks **décontextualisés** (dans la boucle entrelacée, le Refine s'exécute après le decontextualizer pour chaque chunk). Il bénéficie donc de la résolution des références, contrairement à l'architecture pré-v2.0 où il travaillait sur des chunks bruts.

---

## 29. Embedding Configuration

### 29.1 Purpose

Les embeddings sont utilisés pour le chunking sémantique, le fingerprinting (MinHash), et l'indexation RAG. Le choix du modèle d'embedding impacte directement la qualité de ces traitements.

### 29.2 Interface — `rag/embeddings/base_embedder.py`

```python
class BaseEmbedder(ABC):
    """Unified interface for all embedding providers."""

    @abstractmethod
    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts into vectors."""

    @abstractmethod
    async def embed_query(self, query: str) -> list[float]:
        """Embed a single query (may use different instruction than documents)."""

    @property
    @abstractmethod
    def dimensions(self) -> int:
        """Output vector dimensions."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Provider identifier."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Model identifier."""
```

### 29.3 Available Adapters

| Adapter | File | Model Examples | Local/Remote |
|---------|------|---------------|-------------|
| Anthropic Voyage | `anthropic_embedder.py` | `voyage-3`, `voyage-3-lite` | Remote |
| OpenAI | `openai_embedder.py` | `text-embedding-3-small`, `text-embedding-3-large` | Remote |
| Sentence Transformers | `sentence_tf_embedder.py` | `all-MiniLM-L6-v2`, `multilingual-e5-large` | Local |
| Ollama | `ollama_embedder.py` | `nomic-embed-text`, `mxbai-embed-large` | Local |

### 29.4 Configuration (via `.env`)

```dotenv
EMBEDDING_PROVIDER=anthropic
EMBEDDING_MODEL=voyage-3
EMBEDDING_DIMENSIONS=1024
```

### 29.5 Usage Points

| Component | Usage |
|-----------|-------|
| `chunking/semantic_chunker.py` | Embedding-based semantic segmentation |
| `cache/fingerprint.py` | Level 4 — MinHash on embeddings |
| `rag/indexer.py` | Index chunks and summaries into vector DB |
| `rag/enricher.py` | Query vector DB with embedded queries |

### 29.6 Adding a New Embedding Provider

1. Créer `rag/embeddings/my_embedder.py` implémentant `BaseEmbedder`
2. Enregistrer dans `rag/embeddings/factory.py`
3. Utiliser via `.env` : `EMBEDDING_PROVIDER=my_provider`

---

## 30. Additional Adapter Interfaces

Toutes les interfaces ci-dessous suivent le même pattern : une classe abstraite (ABC), une factory, et des implémentations concrètes interchangeables via configuration `.env`.

### 30.1 BaseExtractor — `extraction/base_extractor.py`

Interface commune pour tous les extracteurs de documents.

```python
class BaseExtractor(ABC):
    """Unified interface for document format extractors."""

    @property
    @abstractmethod
    def supported_extensions(self) -> list[str]:
        """File extensions this extractor handles (e.g., ['.pdf'])."""

    @abstractmethod
    async def extract(self, content: bytes | str | Path) -> ExtractionResult:
        """Extract text, images, and tables from document."""

    @property
    def requires_vision(self) -> bool:
        """Whether this extractor needs LLM Vision (e.g., image_input_extractor)."""
        return False
```

**Factory** : `extraction/extractor_factory.py` — sélectionne l'extracteur à partir du format détecté ou déclaré.

**Ajout d'un nouveau format** (ex: HTML, LaTeX) :
1. Créer `extraction/html_extractor.py` implémentant `BaseExtractor`
2. Enregistrer dans la factory
3. Ajouter l'extension dans `BATCH_SCAN_FORMATS` si nécessaire

### 30.2 BaseChunker — `chunking/base_chunker.py`

Interface commune pour toutes les stratégies de chunking.

```python
class BaseChunker(ABC):
    """Unified interface for chunking strategies."""

    @property
    @abstractmethod
    def strategy_name(self) -> str:
        """Strategy identifier (e.g., 'structural', 'semantic')."""

    @abstractmethod
    async def chunk(
        self,
        text: str,
        structure: DocumentStructure | None = None,
        target_size: int = 2000,
        overlap: int = 0,
    ) -> list[Chunk]:
        """Split text into chunks respecting atomic blocks.
        Default overlap=0 aligns with CHUNK_OVERLAP .env default.
        Factory passes actual CHUNK_OVERLAP setting value."""
```

**Factory** : `chunking/chunker_factory.py` — instancie le chunker à partir de `CHUNKING_STRATEGY`.

**Ajout d'une nouvelle stratégie** (ex: hybride, par paragraphe, spécialisé code/dialogue) :
1. Créer `chunking/my_chunker.py` implémentant `BaseChunker`
2. Enregistrer dans la factory
3. Utiliser via `.env` : `CHUNKING_STRATEGY=my_strategy`

### 30.3 BaseOutputWriter — `storage/base_output_writer.py`

Interface commune pour l'écriture des fichiers de sortie.

```python
class BaseOutputWriter(ABC):
    """Unified interface for output storage backends."""

    @abstractmethod
    async def write(self, path: str, content: bytes | str) -> None:
        """Write content to the given path."""

    @abstractmethod
    async def read(self, path: str) -> bytes:
        """Read content from the given path."""

    @abstractmethod
    async def exists(self, path: str) -> bool:
        """Check if path exists."""

    @abstractmethod
    async def copy(self, src: str, dst: str) -> None:
        """Copy file or directory (used for run carries)."""

    @abstractmethod
    async def create_symlink(self, target: str, link: str) -> None:
        """Create symbolic link (or equivalent for remote storage)."""

    @abstractmethod
    async def list_dir(self, path: str) -> list[str]:
        """List directory contents."""
```

**Implémentations** :
| Adapter | File | Usage |
|---------|------|-------|
| Local filesystem | `local_writer.py` | Défaut — écriture locale |
| S3-compatible | `s3_writer.py` | Déploiement cloud (AWS S3, MinIO, etc.) |

**Configuration** : `OUTPUT_WRITER=local` ou `OUTPUT_WRITER=s3`

### 30.4 BaseCacheStore — `cache/base_cache_store.py`

Interface commune pour le backend de cache de fingerprints.

```python
class BaseCacheStore(ABC):
    """Unified interface for cache storage backends."""

    @abstractmethod
    async def get(self, key: str) -> CacheEntry | None:
        """Retrieve cache entry by fingerprint key."""

    @abstractmethod
    async def put(self, key: str, entry: CacheEntry) -> None:
        """Store cache entry."""

    @abstractmethod
    async def delete(self, key: str) -> None:
        """Remove cache entry."""

    @abstractmethod
    async def lookup_fingerprint(self, fingerprint: DocumentFingerprint) -> CacheLookupResult:
        """Multi-level fingerprint lookup (exact → content → simhash → minhash → constellation)."""

    @abstractmethod
    async def list_entries(self) -> list[CacheEntry]:
        """List all cached entries (for batch scan dedup)."""
```

**Implémentations** :
| Adapter | File | Usage |
|---------|------|-------|
| JSON files | `json_cache_store.py` | Défaut — simple, no dependency |
| SQLite | `sqlite_cache_store.py` | Performance avec beaucoup de documents |
| Redis | `redis_cache_store.py` | Déploiement distribué, multi-instance |
| ArangoDB | `arangodb_cache_store.py` | Multi-model DB, coherent with graph/vector stores |

**Configuration** : `CACHE_BACKEND=json` (défaut), `sqlite`, `redis`, ou `arangodb`

> **JSON-always :** Chaque backend implémente en plus l'écriture d'un `cache_entry.json` dans la structure documentaire (`00_metadata/`). Ce fichier est la source de vérité pour reconstruire les bases de données depuis l'arborescence de fichiers.

### 30.5 BaseGraphExporter — `graph/base_graph_exporter.py`

Interface commune pour l'export du knowledge graph.

```python
class BaseGraphExporter(ABC):
    """Unified interface for knowledge graph export formats."""

    @property
    @abstractmethod
    def format_name(self) -> str:
        """Export format identifier (e.g., 'json', 'graphml')."""

    @property
    @abstractmethod
    def file_extension(self) -> str:
        """Output file extension (e.g., '.json', '.graphml')."""

    @abstractmethod
    async def export(self, graph: nx.Graph, output_path: str) -> str:
        """Export graph to file, return path to exported file."""
```

**Implémentations** :
| Adapter | File | Extension | Usage |
|---------|------|-----------|-------|
| JSON | `json_exporter.py` | `.json` | NetworkX JSON (défaut, programmatic access) |
| GraphML | `graphml_exporter.py` | `.graphml` | Standard interchange format |
| GEXF | `gexf_exporter.py` | `.gexf` | Gephi visualization |
| Cypher | `cypher_exporter.py` | `.cypher` | Neo4j direct import script |

**Configuration** : `GRAPH_EXPORT_FORMATS=graphml` — liste séparée par virgules. Tous les formats listés sont exportés en parallèle dans `04_synthesis/`. Le format `json` est **toujours** généré quel que soit ce paramètre (JSON-always principle).

### 30.6 BaseVectorStore — `rag/vector_store/base_vector_store.py`

Interface commune pour tous les backends de vector store.

```python
class BaseVectorStore(ABC):
    """Unified interface for vector store backends."""

    @abstractmethod
    async def upsert(self, collection: str, ids: list[str],
                     embeddings: list[list[float]], documents: list[str],
                     metadatas: list[dict] | None = None) -> None:
        """Insert or update vectors with associated documents and metadata."""

    @abstractmethod
    async def query(self, collection: str, query_embedding: list[float],
                    top_k: int = 10, filter: dict | None = None) -> list[SearchResult]:
        """Query vectors by similarity. Returns ranked SearchResult list."""

    @abstractmethod
    async def delete(self, collection: str, ids: list[str]) -> None:
        """Delete vectors by ID."""

    @abstractmethod
    async def create_collection(self, collection: str, dimensions: int) -> None:
        """Create a named collection with specified vector dimensions."""

    @abstractmethod
    async def collection_exists(self, collection: str) -> bool:
        """Check if a collection exists."""

    @abstractmethod
    async def count(self, collection: str) -> int:
        """Return number of vectors in a collection."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Provider identifier (chromadb, qdrant, arangodb)."""
```

**Implémentations** :
| Adapter | File | Usage |
|---------|------|-------|
| ChromaDB | `chromadb_adapter.py` | Embedded local vector DB (défaut) |
| Qdrant | `qdrant_adapter.py` | High-performance vector DB (local ou cloud) |
| ArangoDB | `arangodb_adapter.py` | Multi-model: vector + graph dans le même DB |

**Configuration** : `VECTOR_DB_TYPE=chromadb` (ou `qdrant`, `arangodb`, `none`)

### 30.7 BaseGraphStore — `rag/graph_store/base_graph_store.py`

Interface commune pour tous les backends de graph store.

```python
class BaseGraphStore(ABC):
    """Unified interface for graph store backends.
    Used by: rag/indexer.py (Document Graph import),
    consolidator/* (Corpus Graph CRUD), rag/retriever/* (query)."""

    # --- Node CRUD ---
    @abstractmethod
    async def upsert_node(self, node_id: str, properties: dict) -> None:
        """Insert or update a node (merge by node_id)."""

    @abstractmethod
    async def get_node(self, node_id: str) -> dict | None:
        """Retrieve a node by ID. Returns properties dict or None."""

    @abstractmethod
    async def delete_node(self, node_id: str) -> None:
        """Delete a node and all its edges."""

    # --- Edge CRUD ---
    @abstractmethod
    async def upsert_edge(self, source_id: str, relation_type: str,
                          target_id: str, properties: dict) -> None:
        """Insert or update an edge (merge by source+relation+target)."""

    @abstractmethod
    async def get_edges(self, node_id: str,
                        direction: Literal["in", "out", "both"] = "both",
                        relation_type: str | None = None) -> list[dict]:
        """Get edges connected to a node, optionally filtered by type and direction."""

    @abstractmethod
    async def delete_edges(self, source_id: str, relation_type: str | None = None,
                           target_id: str | None = None) -> int:
        """Delete edges matching criteria. Returns count deleted."""

    # --- Query ---
    @abstractmethod
    async def query_neighbors(self, node_id: str, depth: int = 1,
                              relation_types: list[str] | None = None) -> dict:
        """Traverse graph from node_id up to depth hops. Returns subgraph as dict."""

    @abstractmethod
    async def query_by_properties(self, label: str | None = None,
                                  filters: dict | None = None,
                                  limit: int = 100) -> list[dict]:
        """Find nodes matching property filters."""

    # --- Bulk operations ---
    @abstractmethod
    async def import_graph(self, nodes: list[dict], edges: list[dict]) -> None:
        """Bulk import nodes and edges (used for Document Graph import)."""

    @abstractmethod
    async def node_count(self) -> int:
        """Return total number of nodes."""

    @abstractmethod
    async def edge_count(self) -> int:
        """Return total number of edges."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Provider identifier (neo4j, arangodb)."""
```

**Implémentations** :
| Adapter | File | Usage |
|---------|------|-------|
| Neo4j | `neo4j_adapter.py` | Graph DB natif, Cypher queries |
| ArangoDB | `arangodb_adapter.py` | Multi-model: graph + document + vector |

**Configuration** : `GRAPH_DB_TYPE=neo4j` (ou `arangodb`, `none`)

### 30.8 Adapter Pattern Summary

| Adapter | Interface | Factory | Location | Configured By |
|---------|-----------|---------|----------|---------------|
| **LLM** | `BaseLLMClient` | `client_factory.py` | `llm/` | `LLM_DEFAULT_PROVIDER`, `LLM_{AGENT}` |
| **Embedding** | `BaseEmbedder` | `factory.py` | `rag/embeddings/` | `EMBEDDING_PROVIDER` |
| **Extractor** | `BaseExtractor` | `extractor_factory.py` | `extraction/` | Auto-detected from format |
| **Chunker** | `BaseChunker` | `chunker_factory.py` | `chunking/` | `CHUNKING_STRATEGY` |
| **Output Writer** | `BaseOutputWriter` | `writer_factory.py` | `storage/` | `OUTPUT_WRITER` |
| **Cache Store** | `BaseCacheStore` | `cache_factory.py` | `cache/` | `CACHE_BACKEND` |
| **Graph Exporter** | `BaseGraphExporter` | `exporter_factory.py` | `graph/` | `GRAPH_EXPORT_FORMATS` |
| **Vector Store** | `BaseVectorStore` | `factory.py` | `rag/vector_store/` | `VECTOR_DB_TYPE` |
| **Graph Store** | `BaseGraphStore` | `factory.py` | `rag/graph_store/` | `GRAPH_DB_TYPE` |
| **Agent** | `BaseAgent` | `registry.py` | `pipeline/plugin_kit/` | `config/agents.py` |

Tout ajout d'implémentation suit le même processus en 3 étapes : créer le fichier, enregistrer dans la factory, configurer via `.env`.

---

## 31. Testing Strategy (Planned)

> **Status : à implémenter ultérieurement.**

### 31.1 Planned Approach

| Layer | Strategy |
|-------|----------|
| **Unit tests** | Chaque agent testé isolément avec des inputs/outputs mockés |
| **Integration tests** | Pipeline complet sur un jeu de documents de référence (golden dataset) |
| **Regression tests** | Comparer les outputs de deux runs (diff entre run_manifests + output_hashes) pour détecter les dégradations après changement de prompt, code ou modèle |
| **Golden dataset** | Set de 5-10 documents couvrant les types supportés (article court, livre long, doc multilingue, doc avec images/tableaux) avec résultats attendus validés manuellement |

### 31.2 Idempotence Guarantee

Le système de cache par fingerprinting (section 8) garantit que :
- Un même fichier soumis deux fois → cache hit exact (Level 1), pipeline non ré-exécuté
- Un fichier identique en contenu mais format différent → cache hit content (Level 2), pipeline non ré-exécuté
- Le résultat retourné est toujours celui du cache en cas de match, sans re-traitement

---

## 32. Versioning

- File: `version.py`
- Format: Semantic Versioning (`MAJOR.MINOR.PATCH`)
- Incremented at each release
- Referenced in output metadata and `run_manifest.json` for traceability

---

## 33. GPU Acceleration Strategy

### 33.1 Design Principle

Toutes les opérations compute-intensive sont conçues pour fonctionner en **mode CPU par défaut**, avec une accélération GPU **transparente et optionnelle** — sans aucun changement de code applicatif. L'activation GPU se fait uniquement par configuration d'environnement ou installation de packages optionnels.

### 33.2 Acceleration Points

| Component | CPU Path | GPU Path | Speedup (ref.) | Activation |
|-----------|----------|----------|:---------------:|------------|
| **Leiden community detection** | `leidenalg` + `python-igraph` | `nx-cugraph` backend (NVIDIA RAPIDS cuGraph) | ~47× (H100, 3.8M nodes) | `NETWORKX_BACKEND=cugraph` env var |
| **Personalized PageRank** | `nx.pagerank()` | `nx-cugraph` backend | ~30× | Same env var — transparent |
| **Graph traversals** (neighbor queries, BFS) | `NetworkX` | `nx-cugraph` backend | ~10-20× | Same env var — transparent |
| **Entity embedding clustering** | `sklearn.cluster.AgglomerativeClustering` | `cuml.cluster.AgglomerativeClustering` | ~15× | `GPU_CLUSTERING_BACKEND=cuml` |
| **Cosine similarity matrix** | `sklearn.metrics.pairwise.cosine_similarity` | `cupy` / `torch.nn.functional.cosine_similarity` | ~20× | `GPU_SIMILARITY_BACKEND=cupy` or `torch` |
| **Embedding computation** | Remote API (Voyage, OpenAI) / CPU `sentence-transformers` | GPU `sentence-transformers` (auto-detects CUDA) | ~5-10× (local only) | Automatic — `sentence-transformers` uses GPU if available |

### 33.3 NetworkX Backend Strategy — `nx-cugraph`

L'approche clé est le **nx-cugraph backend** (NVIDIA RAPIDS). Il intercepte les appels NetworkX standard et les exécute sur GPU sans changement de code :

```python
# Application code (inchangé, fonctionne CPU et GPU) :
import networkx as nx

graph = nx.Graph()
# ... build graph ...
communities = nx.community.louvain_communities(graph, resolution=1.0, seed=42)
pagerank = nx.pagerank(graph, personalization=seeds, alpha=0.15)
```

```bash
# Activation GPU — uniquement via environnement :
pip install nx-cugraph-cu12   # Install RAPIDS backend
export NETWORKX_BACKEND=cugraph  # Route all supported algorithms to GPU
```

**Algorithmes accélérés par nx-cugraph** (utilisés dans le projet) :
- `leiden` (community detection) — §13.10, §13.15.4
- `pagerank` / `personalized_pagerank` — §26.6.3
- `connected_components` — utilisé par `community_integrator.py`
- `shortest_path` / BFS — utilisé par `query_neighbors` dans les graph stores

**Fallback automatique :** Si un algorithme n'est pas supporté par le backend GPU, NetworkX exécute automatiquement la version CPU sans erreur.

### 33.4 Clustering GPU Strategy — `cuML`

Pour l'entity normalization (§13.3), le clustering agglomératif sur les embeddings peut être accéléré via cuML :

```python
# entity_normalizer.py — pattern d'abstraction :
def _get_clustering_backend():
    """Select clustering implementation based on config."""
    if settings.GPU_CLUSTERING_BACKEND == "cuml":
        from cuml.cluster import AgglomerativeClustering
    else:
        from sklearn.cluster import AgglomerativeClustering
    return AgglomerativeClustering

# Usage (identique CPU/GPU) :
clustering = _get_clustering_backend()(
    n_clusters=None,
    distance_threshold=1.0 - settings.ENTITY_SIMILARITY_THRESHOLD,
    metric="cosine",
    linkage="average"
)
labels = clustering.fit_predict(embeddings_matrix)
```

### 33.5 Similarity GPU Strategy

Pour le calcul de matrices de similarité cosinus (entity normalization, smart merge consolidator) :

```python
# shared utility — core/similarity.py :
def cosine_similarity_matrix(embeddings: np.ndarray) -> np.ndarray:
    """Compute pairwise cosine similarity. GPU-accelerated if configured."""
    if settings.GPU_SIMILARITY_BACKEND == "cupy":
        import cupy as cp
        gpu_emb = cp.asarray(embeddings)
        norms = cp.linalg.norm(gpu_emb, axis=1, keepdims=True)
        return cp.asnumpy((gpu_emb @ gpu_emb.T) / (norms @ norms.T))
    elif settings.GPU_SIMILARITY_BACKEND == "torch":
        import torch
        t = torch.tensor(embeddings, device="cuda")
        return torch.nn.functional.cosine_similarity(
            t.unsqueeze(0), t.unsqueeze(1), dim=2
        ).cpu().numpy()
    else:
        from sklearn.metrics.pairwise import cosine_similarity
        return cosine_similarity(embeddings)
```

### 33.6 Configuration

```dotenv
# === GPU Acceleration (§33) — all optional, CPU fallback if unset ===
# NETWORKX_BACKEND=cugraph        # Route NetworkX graph algorithms to GPU (requires nx-cugraph-cu12)
# GPU_CLUSTERING_BACKEND=sklearn   # sklearn (CPU, default) | cuml (GPU, requires cuml)
# GPU_SIMILARITY_BACKEND=sklearn   # sklearn (CPU, default) | cupy | torch (GPU)
```

### 33.7 Project Structure Addition

```
src/
├── core/
│   ├── models.py
│   └── similarity.py              # GPU-aware cosine similarity utility (§33.5)
```

### 33.8 Impact on Code Generation

- **Aucun changement de code applicatif** : les modules `community_detector.py`, `ppr_scorer.py`, `entity_normalizer.py` utilisent les API standard (NetworkX, sklearn). L'accélération GPU est activée par configuration.
- **Un seul utilitaire GPU-aware** : `core/similarity.py` encapsule le choix CPU/GPU pour la similarité cosinus.
- **Les interfaces ABC ne changent pas** : BaseEmbedder, BaseVectorStore, BaseGraphStore restent identiques.
- **Tests** : tous les tests passent en mode CPU (pas de dépendance GPU pour le CI). Les tests GPU sont optionnels et marqués `@pytest.mark.gpu`.

---

## 34. Makefile

Le `Makefile` à la racine du projet fournit les commandes standard pour le développement, les tests et le CI.

```makefile
# === ayExtractor Makefile ===

PYTHON ?= python3
PYTEST ?= pytest
SRC_DIR = src
TEST_DIR = tests

.PHONY: help install test test-unit test-integration test-gpu lint format typecheck clean

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

install:  ## Install project in editable mode with dev dependencies
	pip install -e ".[dev]" --break-system-packages

test: test-unit test-integration  ## Run all tests (unit + integration)

test-unit:  ## Run unit tests only (no external deps required)
	$(PYTEST) $(TEST_DIR)/unit/ -v --tb=short

test-integration:  ## Run integration tests (requires LLM API keys + optional DBs)
	$(PYTEST) $(TEST_DIR)/integration/ -v --tb=short

test-gpu:  ## Run GPU-accelerated tests (requires NVIDIA GPU + RAPIDS)
	$(PYTEST) $(TEST_DIR)/ -v --tb=short -m gpu

test-coverage:  ## Run unit tests with coverage report
	$(PYTEST) $(TEST_DIR)/unit/ --cov=$(SRC_DIR) --cov-report=term-missing --cov-report=html

lint:  ## Run linters (ruff)
	ruff check $(SRC_DIR) $(TEST_DIR)

format:  ## Auto-format code (ruff)
	ruff format $(SRC_DIR) $(TEST_DIR)

typecheck:  ## Run type checker (mypy)
	mypy $(SRC_DIR)

clean:  ## Remove build artifacts, caches, coverage
	rm -rf build/ dist/ *.egg-info .pytest_cache .mypy_cache htmlcov/ .coverage
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
```

---

## 35. Testing Strategy

### 35.1 Principle — Systematic Test Pairing

Chaque fichier source dans `src/` a un fichier test correspondant dans `tests/unit/` qui miroire la structure des répertoires source.

**Mapping :** `src/{module}/{file}.py` → `tests/unit/{module}/test_{file}.py`

Exemples :
- `src/core/models.py` → `tests/unit/core/test_models.py`
- `src/graph/builder.py` → `tests/unit/graph/test_builder.py`
- `src/graph/layers/community_detector.py` → `tests/unit/graph/layers/test_community_detector.py`

**Exceptions** (pas de test unitaire dédié) : fichiers `__init__.py`, `version.py` (testé via `test_models.py`).

### 35.2 Unit Tests (`tests/unit/`)

- **Pas de dépendances externes** : aucun appel LLM, réseau, ou base de données
- **Mocks systématiques** : `BaseLLMClient`, `BaseEmbedder`, `BaseVectorStore`, `BaseGraphStore` sont mockés via `conftest.py`
- **Fixtures partagées** : chunks de référence, triplets sample, graphe minimal dans `tests/conftest.py`
- **Marquage** : modules `[PURE]` testés sans mocks, modules `[LLM-DEPENDENT]` testés avec mock LLM

### 35.3 Integration Tests (`tests/integration/`)

- **Requièrent des clés API** et éventuellement des services (DB, vector store)
- **Scénarios end-to-end** : PDF → pipeline complet → AnalysisResult
- **Marqueurs pytest** : `@pytest.mark.integration`, `@pytest.mark.gpu`, `@pytest.mark.slow`
- **Skip automatique** si les clés API ou services ne sont pas disponibles

### 35.4 Test Generation Rule

Lors de la génération de code, chaque step produit systématiquement :
1. Les fichiers source (`src/...`)
2. Les fichiers test unitaire correspondants (`tests/unit/...`)
3. Mise à jour de `tests/conftest.py` si de nouvelles fixtures sont nécessaires

---
---

# ANNEXES

---

## Annexe A — Code Generation Order & Development Plan

> Cette annexe décrit la stratégie technique de génération du code et le plan de développement opérationnel. Elle ne fait pas partie des spécifications fonctionnelles.

### A.1 Layered Dependency Strategy

L'ordre de génération du code suit un graphe de dépendances strict. Chaque couche ne dépend que des couches précédentes.

```
Layer 0 ─── Foundations (zero inter-module dependencies)
   │
Layer 1 ─── Abstractions (interfaces only, no implementation)
   │
Layer 2 ─── Adapters (concrete implementations of interfaces)
   │
Layer 3 ─── Extraction + Chunking (depends on L0 + L1 for LLM Vision)
   │
Layer 4 ─── Agents + Graph (depends on L0-L3)
   │
Layer 5 ─── Orchestration (assembles everything)
   │
Layer 6 ─── Integration (facade, batch, CLI)
```

### A.2 Detailed Layer Contents

**Layer 0 — Foundations** (aucune dépendance inter-modules)

| Module | Files | Testable in isolation |
|--------|-------|:---------------------:|
| `core/models.py` | Shared Pydantic models (incl. QualifiedTriplet, SourceProvenance, TemporalScope, SourceMetadata) | ✅ |
| `core/similarity.py` | GPU-aware cosine similarity utility (§33.5) | ✅ |
| `config/settings.py` | .env loading via pydantic-settings | ✅ |
| `config/agents.py` | Agent registry configuration | ✅ |
| `logging/` | Logger factory, context, handlers | ✅ |
| `version.py` | Version constant | ✅ |
| `api/models.py` | DocumentInput, Metadata, AnalysisResult, ConfigOverrides | ✅ |
| `cache/models.py` | DocumentFingerprint, CacheEntry, CacheLookupResult | ✅ |
| `tracking/models.py` | LLMCallRecord, AgentStats, SessionStats, GlobalStats, TypeStats, CumulativeAgentStats, ModelStats, DailyStats, ModelPricing | ✅ |
| `llm/models.py` | Message, ImageInput, LLMResponse | ✅ |
| `rag/models.py` | RAGContext, SearchResult, RetrievalPlan, CorpusContext | ✅ |
| `batch/models.py` | BatchResult, ScanEntry | ✅ |
| `storage/models.py` | RunManifest, StepManifest | ✅ |
| `pipeline/plugin_kit/models.py` | AgentOutput, AgentMetadata | ✅ |
| `graph/layers/models.py` | Community, CommunityHierarchy, CommunitySummary | ✅ |
| `graph/profiles/models.py` | EntityProfile, RelationProfile | ✅ |
| `consolidator/models.py` | CNode, TNode, XEdge, Contradiction, ConsolidationReport, LinkingReport, ClusteringReport, InferenceReport, DecayReport, ContradictionReport, PassResult | ✅ |

**Layer 1 — Abstractions** (interfaces uniquement)

| Module | Files |
|--------|-------|
| `llm/base_client.py` | BaseLLMClient ABC |
| `llm/config.py` | Per-agent LLM routing logic |
| `extraction/base_extractor.py` | BaseExtractor ABC |
| `chunking/base_chunker.py` | BaseChunker ABC |
| `storage/base_output_writer.py` | BaseOutputWriter ABC |
| `cache/base_cache_store.py` | BaseCacheStore ABC |
| `graph/base_graph_exporter.py` | BaseGraphExporter ABC |
| `rag/embeddings/base_embedder.py` | BaseEmbedder ABC |
| `rag/vector_store/base_vector_store.py` | BaseVectorStore ABC |
| `rag/graph_store/base_graph_store.py` | BaseGraphStore ABC |
| `pipeline/plugin_kit/base_agent.py` | BaseAgent ABC |

**Layer 2 — Adapters** (implémentations concrètes, testables unitairement avec mocks)

| Module | Files |
|--------|-------|
| `llm/adapters/` | anthropic, openai, google, ollama + `client_factory.py` |
| `llm/retry.py` | Retry policy |
| `extraction/` | pdf, epub, docx, md, txt, image_input extractors + `extractor_factory.py` |
| `chunking/` | structural, semantic chunkers + `chunker_factory.py` + `chunk_validator.py` |
| `storage/` | local_writer, s3_writer + `writer_factory.py` |
| `cache/` | json, sqlite, redis cache stores + `cache_factory.py` + `fingerprint.py` |
| `graph/` | json, graphml, gexf, cypher exporters + `exporter_factory.py` + `taxonomy.py` (relation taxonomy) |
| `rag/embeddings/` | anthropic, openai, sentence_tf, ollama embedders + factory |
| `rag/vector_store/` | chromadb, qdrant adapters + factory |
| `rag/graph_store/` | neo4j, arangodb adapters + factory |

**Layer 3 — Extraction Pipeline** (dépend L0 + L1 pour LLM Vision)

| Module | Files |
|--------|-------|
| `extraction/image_analyzer.py` | Image analysis via LLM Vision |
| `extraction/table_extractor.py` | Table extraction |
| `extraction/content_merger.py` | Text + image + table merging |
| `extraction/language_detector.py` | Language detection (doc + chunk) |
| `extraction/structure_detector.py` | Structure detection |
| `llm/token_budget.py` | Token budget estimation |

**Layer 4 — Agents + Graph + Consolidator** (dépend L0-L3)

| Module | Files |
|--------|-------|
| `pipeline/agents/decontextualizer.py` | Pre-DAG chunk disambiguation |
| `pipeline/agents/summarizer.py` | Refine summarization |
| `pipeline/agents/densifier.py` | Chain of Density |
| `pipeline/agents/concept_extractor.py` | Qualified triplet extraction |
| `pipeline/agents/reference_extractor.py` | Citation/reference extraction |
| `graph/merger.py` + `entity_normalizer.py` + `relation_normalizer.py` + `triplet_consolidator.py` | Triplet consolidation pipeline (3 passes, incl. qualifiers) |
| `graph/builder.py` + `reference_linker.py` | Knowledge graph construction (L2+L3) from consolidated triplets |
| `graph/layers/layer_classifier.py` | L2/L3 layer assignment |
| `graph/layers/community_detector.py` | Hierarchical Leiden community detection → CommunityHierarchy |
| `graph/layers/community_integrator.py` | Inject L1 community nodes + encompasses edges into graph |
| `pipeline/agents/community_summarizer.py` | LLM-generated community summaries |
| `pipeline/agents/profile_generator.py` | LLM-generated entity/relation profiles |
| `graph/profiles/profile_embedder.py` | Profile embedding computation |
| `pipeline/agents/synthesizer.py` | Final synthesis (uses community summaries + profiles) |
| `pipeline/agents/critic.py` | Optional validation |
| `consolidator/entity_linker.py` | Pass 1 — Document→Corpus Graph linking |
| `consolidator/community_clusterer.py` | Pass 2 — Corpus Graph Leiden clustering → T-nodes |
| `consolidator/inference_engine.py` | Pass 3 — Transitive relation inference |
| `consolidator/decay_manager.py` | Pass 4 — Staleness recalculation + pruning |
| `consolidator/contradiction_detector.py` | Pass 5 — Conflicting claims detection |
| `consolidator/orchestrator.py` | Consolidation pass scheduling |
| `rag/retriever/query_classifier.py` | Query type classification |
| `rag/retriever/community_retriever.py` | Level 1 retrieval — community summaries |
| `rag/retriever/entity_retriever.py` | Level 2 retrieval — entity/relation profiles |
| `rag/retriever/chunk_retriever.py` | Level 3 retrieval — source chunks (fallback) |
| `rag/retriever/corpus_retriever.py` | Cross-document retrieval — C-nodes/T-nodes/X-edges |
| `rag/retriever/ppr_scorer.py` | Personalized PageRank scoring |
| `rag/retriever/context_assembler.py` | Final LLM context assembly |
| `rag/retriever/pipeline.py` | Hierarchical retrieval orchestration |
| `pipeline/prompts/` | All prompt templates (incl. community_summarizer, profile_generator) |

**Layer 5 — Orchestration** (assemble tout)

| Module | Files |
|--------|-------|
| `pipeline/state.py` | ExtractionContext + PipelineState (incl. communities, profiles) |
| `pipeline/plugin_kit/registry.py` | Agent registration |
| `pipeline/plugin_kit/dag_builder.py` | DAG construction from agents |
| `pipeline/orchestrator.py` | LangGraph workflow |
| `storage/reader.py` + `layout.py` + `run_manager.py` | Storage management |
| `tracking/` | call_logger, agent_tracker, session_tracker, stats_aggregator, cost_calculator, exporter |
| `rag/enricher.py` + `rag/indexer.py` | RAG read/write (chunks + profiles + relation profiles + communities indexing) |

**Layer 6 — Integration** (point d'entrée)

| Module | Files |
|--------|-------|
| `api/facade.py` | Public API |
| `batch/scanner.py` + `batch/dedup.py` | Batch processing |
| `main.py` | CLI entry point |

### A.3 Generation Principles

1. Chaque fichier est générable indépendamment au sein de sa couche — les dépendances se font uniquement vers les couches inférieures
2. Les tests unitaires peuvent être écrits dès la Layer 0 — les Pydantic models sont testables immédiatement
3. Les adapters (Layer 2) sont testables avec des mocks des interfaces de Layer 1
4. L'intégration (Layer 6) est le dernier fichier généré — elle ne fait que brancher les composants
5. Chaque layer peut être livrée et validée avant de passer à la suivante

### A.4 Development Plan — Phases

Le développement est découpé en 6 phases. Chaque phase correspond à un livrable fonctionnel testable.

#### Phase 1 — Socle technique (Layer 0 + Layer 1)

**Objectif :** Infrastructure de base compilable, tous les contrats d'interface définis.

**Livrables :**
- `Makefile` (§34) + `pyproject.toml` + `tests/conftest.py`
- Tous les modèles Pydantic (`core/models.py`, `api/models.py`, `cache/models.py`, `tracking/models.py`, `llm/models.py`, `rag/models.py`, `batch/models.py`, `storage/models.py`, `pipeline/plugin_kit/models.py`, `graph/layers/models.py`, `graph/profiles/models.py`, `consolidator/models.py`)
- Utilitaire GPU-aware (`core/similarity.py` — §33.5)
- Configuration `.env` + `config/settings.py` + `config/agents.py`
- Système de logging structuré (`logging/`)
- `version.py`
- Toutes les interfaces ABC (11 au total — voir Layer 1)

**Critères de validation :**
- `import ayextractor` sans erreur (via `pip install -e .`)
- `make test-unit` passe (tous les modèles Pydantic valident et sérialisent correctement)
- Les ABC sont importables et non instanciables (TypeError attendu)
- La configuration charge un `.env` de test sans erreur

**Estimation :** ~25 fichiers, ~1500 lignes de code

#### Phase 2 — Adapters et extraction (Layer 2 + Layer 3)

**Objectif :** Le système peut extraire du contenu de tous les formats supportés et communiquer avec les LLM.

**Sous-phase 2a — LLM Adapters (prioritaire)**
- Au minimum 1 adapter LLM fonctionnel (Anthropic recommandé)
- Factory + retry policy
- Tests : appel réel à l'API avec un prompt minimal

**Sous-phase 2b — Extraction pipeline**
- Au minimum 2 extracteurs (PDF + Markdown recommandés)
- Language detector, structure detector, content merger
- Table extractor, image analyzer (avec LLM Vision via adapter Phase 2a)
- Tests : extraction d'un PDF de référence, vérification du texte enrichi

**Sous-phase 2c — Storage et cache**
- Local writer + JSON cache store (backends par défaut uniquement)
- Fingerprint computation (niveaux 1-3 minimum)
- Token budget estimation
- Tests : écriture/lecture d'un run, cache hit/miss

**Sous-phase 2d — Chunking**
- Structural chunker + chunk validator
- Tests : chunking d'un texte extrait, validation des blocs atomiques IMAGE/TABLE

**Critères de validation :**
- Pipeline partiel fonctionnel : document PDF → extraction → texte enrichi → chunks → fichiers sur disque
- Cache : soumettre le même document 2 fois → cache hit au 2e passage
- LLM : appel réussi via adapter + token tracking fonctionnel

**Estimation :** ~40 fichiers, ~4000 lignes de code

#### Phase 3 — Agents d'analyse (Layer 4)

**Objectif :** Tous les agents produisent des résultats exploitables sur des chunks.

**Sous-phase 3a — Agents core (séquentiels)**
- Decontextualizer, Summarizer, Densifier
- Prompts EN
- Tests : exécution sur 5 chunks de référence, vérification manuelle des outputs

**Sous-phase 3b — Agents graphe (parallélisables)**
- Concept Extractor (qualified triplets), Reference Extractor
- Knowledge graph builder (L2+L3) + merger + reference linker
- Layer classifier (L2/L3)
- Tests : extraction de triplets qualifiés, construction du graphe L2+L3, export JSON

**Sous-phase 3c — Community detection + Integration + Profiles**
- Community detector (Leiden, pur) → CommunityHierarchy
- Community integrator (injecte L1 dans graphe) → graph.json complet
- Community summarizer agent (LLM) → community_summaries.json
- Profile generator agent (LLM) → entity_profiles.json, relation_profiles.json
- Profile embedder → embeddings pour vector DB
- Tests : communautés détectées, L1 nodes intégrés, résumés générés, profils cohérents

**Sous-phase 3d — Agents finaux**
- Synthesizer (uses community summaries + profiles), Critic (optionnel)
- Tests : synthèse à partir d'un résumé + graphe + communautés de référence

**Sous-phase 3e — Consolidator**
- 5 passes consolidation (entity_linker, community_clusterer, inference_engine, decay_manager, contradiction_detector)
- Consolidator orchestrator
- Tests : merge Document→Corpus, Leiden corpus, inference transitive, decay, contradiction detection

**Critères de validation :**
- Chaque agent produit un output conforme à son `output_schema`
- Les scores de confiance sont calculés et dans [0.0, 1.0]
- Le knowledge graph est valide (pas de nœuds orphelins, pas de cycles dans les relations)

**Estimation :** ~40 fichiers, ~5 500 lignes de code (dont prompts)

#### Phase 4 — Orchestration (Layer 5)

**Objectif :** Le pipeline complet s'exécute de bout en bout sur un document.

**Livrables :**
- PipelineState, ExtractionContext, PluginKit registry, DAG builder
- Orchestrateur LangGraph complet (incl. community detection + profile generation steps)
- Storage complet (run_manager, reader, layout)
- Tracking complet (tous les niveaux)
- RAG enricher + indexer (chunks + profiles + community summaries indexing)
- RAG hierarchical retriever pipeline (query_classifier, community/entity/chunk/corpus retrievers, PPR scorer, context_assembler)

**Critères de validation :**
- Pipeline end-to-end : `facade.analyze(pdf, metadata)` → AnalysisResult complet
- Run management : créer un run, le reprendre avec `resume_from_step`
- Tracking : `execution_stats.json` produit avec données réalistes
- `run_manifest.json` complet et cohérent

**Estimation :** ~20 fichiers, ~3 500 lignes de code

#### Phase 5 — Intégration et batch (Layer 6)

**Objectif :** Le système est utilisable en production avec CLI et batch.

**Livrables :**
- `api/facade.py` complet
- CLI (`main.py`) avec commandes : `analyze`, `batch`, `stats`
- Batch scanner + dedup

**Critères de validation :**
- `python -m ayextractor analyze document.pdf --output ./results` fonctionne
- `python -m ayextractor batch ./documents/ --output ./results` traite N documents
- Les documents déjà traités sont skippés (cache hit)

**Estimation :** ~5 fichiers, ~800 lignes de code

#### Phase 6 — Extensions optionnelles

**Objectif :** Compléter les adapters non-critiques et les backends alternatifs.

**Livrables (par priorité) :**
1. Adapters LLM restants (OpenAI, Google, Ollama)
2. Extracteurs restants (EPUB, DOCX, TXT, Image-as-input)
3. Semantic chunker
4. Cache backends (SQLite, Redis)
5. Output writer S3
6. Graph exporters (GraphML, GEXF, Cypher)
7. Embedding adapters (OpenAI, Sentence Transformers, Ollama)
8. Vector/Graph DB adapters (ChromaDB, Qdrant, Neo4j, ArangoDB)
9. RAG enrichissement complet (lecture DB pendant analyse)

**Critères de validation :** Chaque adapter passe les mêmes tests que son équivalent de Phase 2 (substituabilité via factory).

**Estimation :** ~35 fichiers, ~4000 lignes de code

### A.5 Summary

| Phase | Layers | Src files | Test files | Lignes (est.) | Prérequis |
|-------|:------:|:---------:|:----------:|:-------------:|-----------|
| 1 — Socle | L0 + L1 | ~32 | ~15 | ~3 000 | Aucun |
| 2 — Adapters & extraction | L2 + L3 | ~22 | ~12 | ~5 000 | Phase 1 |
| 3 — Agents + Graph + Consolidator | L4 | ~35 | ~25 | ~7 500 | Phase 2 |
| 4 — Orchestration + RAG Retriever | L5 | ~20 | ~12 | ~5 000 | Phase 3 |
| 5 — Intégration | L6 | ~5 | ~6 | ~1 200 | Phase 4 |
| 6 — Extensions | L2 (compléments) | ~23 | ~15 | ~5 500 | Phase 2 (indépendant de 3-5) |
| **Total** | | **~137** | **~85** | **~27 200** | |

> **Notes :**
> - Chaque fichier source a un fichier test unitaire correspondant (§35). Les tests d'intégration sont comptés dans Phase 5.
> - Les prompts sont English-only (12 fichiers `.txt`, pas de variantes FR).
> - Le `Makefile` et `pyproject.toml` sont livrés en Phase 1.
> - La Phase 6 est indépendante des Phases 3-5 et peut être développée en parallèle à partir de la Phase 2 complétée.

### A.6 Critical Path

```
Phase 1 → Phase 2a → Phase 2b → Phase 2c/2d → Phase 3a → Phase 3b → Phase 3c → Phase 3d → Phase 3e → Phase 4 → Phase 5
                                      ↓
                                  Phase 6 (parallélisable)
```

Le chemin critique passe par : socle → adapter LLM → extraction PDF → chunking → summarizer → concept extractor → community detection → profiles → synthesizer → consolidator → orchestrateur → facade. Tout le reste peut être développé en parallèle une fois les fondations posées.