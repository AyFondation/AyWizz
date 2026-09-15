# tests/e2e/conftest.py — v6
"""E2E test fixtures — provider-agnostic, configured via .env.

Changelog:
    v6: Merge functional fixtures (golden files + expected output schemas)
        directly into conftest.py. Remove conftest_functional.py — pytest
        only auto-discovers files named exactly 'conftest.py'.
    v5: Add e2e_llm_settings_overrides fixture — returns Settings kwargs
        for any configured TEST_LLM_PROVIDER (ollama, openrouter, openai,
        anthropic, google). Facade tests use this instead of ollama_e2e_url.
        ollama_e2e_url kept for Ollama-specific tests only.
    v4: Align container name to ayextractor-test-ollama (unified
        docker-compose.test.yml). Remove docker-compose.e2e.yml dependency.
    v3: Replace Ollama-only fixtures with generic e2e_llm / e2e_embedder
        via llm_test_factory. Cloud providers skip Docker entirely.
        Uses dotenv_values() — never pollutes os.environ.
    v2: Read model names from env vars (OLLAMA_LLM_MODEL, OLLAMA_EMBED_MODEL).
    v1: Hardcoded Ollama model names and container discovery.

Configuration (.env):
    TEST_LLM_PROVIDER=ollama           # or openrouter, openai, anthropic, google
    TEST_LLM_MODEL=qwen2.5:3b         # model for that provider
    TEST_EMBED_PROVIDER=ollama         # embedder provider
    TEST_EMBED_MODEL=nomic-embed-text  # embedder model

For Ollama: requires docker-compose.test.yml running (make test-infra-up).
For cloud providers: requires API key in .env (no Docker needed for LLM).
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest
from dotenv import dotenv_values

from ayextractor.core.models import Chunk

# Read .env into a dict WITHOUT modifying os.environ
_dotenv = dotenv_values()


def _env(key: str, default: str = "") -> str:
    """Read from os.environ first, then .env file, then default."""
    return os.environ.get(key, "") or _dotenv.get(key, "") or default


# ── Configuration ───────────────────────────────────────────────

E2E_OLLAMA_CONTAINER = "ayextractor-test-ollama"


# ── Test document — known facts for assertion ───────────────────

E2E_DOCUMENT_TEXT = (
    "The European Union adopted the NIS2 Directive in 2022 to strengthen "
    "cybersecurity across member states. NIS2 replaces the original NIS "
    "Directive from 2016. The directive requires organizations in critical "
    "sectors such as energy, transport, and healthcare to implement "
    "cybersecurity risk management measures and report significant incidents. "
    "ENISA, the EU Agency for Cybersecurity, provides technical guidance "
    "for NIS2 implementation across all 27 member states."
)

E2E_DOCUMENT_TITLE = "NIS2 Directive Overview"
E2E_EXPECTED_ENTITIES = {"European Union", "NIS2", "ENISA"}


# ── Markers ─────────────────────────────────────────────────────

def pytest_configure(config):
    config.addinivalue_line("markers", "e2e: end-to-end tests requiring infrastructure")


# ── Ollama discovery (only used when TEST_LLM_PROVIDER=ollama) ──

def _discover_ollama_url() -> str:
    """Discover Ollama URL from env var or docker inspect."""
    url = _env("OLLAMA_BASE_URL").strip()
    if url and url != "http://localhost:11434":
        return url

    # Auto-detect via docker inspect (bridge IP for devcontainer)
    try:
        result = subprocess.run(
            [
                "docker", "inspect", "-f",
                "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
                E2E_OLLAMA_CONTAINER,
            ],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            ip = result.stdout.strip()
            return f"http://{ip}:11434"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    if url:
        return url

    pytest.skip(
        f"Ollama not reachable: set OLLAMA_BASE_URL in .env or run 'make test-infra-up'. "
        f"Container '{E2E_OLLAMA_CONTAINER}' not found via docker inspect."
    )
    return ""  # unreachable


def _wait_for_ollama(url: str, timeout: int = 120) -> None:
    """Block until Ollama API responds."""
    import urllib.request
    import urllib.error

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            req = urllib.request.Request(f"{url}/api/tags")
            with urllib.request.urlopen(req, timeout=3) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            time.sleep(2)
    pytest.fail(f"Ollama at {url} did not respond within {timeout}s")


def _wait_for_model(url: str, model: str, timeout: int = 300) -> None:
    """Block until a specific model is available (pulled by init container)."""
    import urllib.request

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            req = urllib.request.Request(f"{url}/api/tags")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode())
                names = [m.get("name", "") for m in data.get("models", [])]
                if any(model in n for n in names):
                    return
        except (OSError, json.JSONDecodeError):
            pass
        time.sleep(5)
    pytest.fail(f"Model '{model}' not available at {url} within {timeout}s")


# =====================================================================
#  SESSION-SCOPED FIXTURES — provider-agnostic
# =====================================================================

@pytest.fixture(scope="session")
def e2e_llm():
    """Provider-agnostic LLM adapter for E2E tests.

    Reads TEST_LLM_PROVIDER / TEST_LLM_MODEL from .env.
    For Ollama: discovers docker-compose container, waits for model.
    For cloud providers: creates adapter with API key from .env.
    """
    from tests.llm_test_factory import (
        create_test_llm, get_test_llm_model, provider_needs_docker,
    )

    overrides = {}
    if provider_needs_docker():
        url = _discover_ollama_url()
        _wait_for_ollama(url)
        _wait_for_model(url, get_test_llm_model())
        overrides["host"] = url

    return create_test_llm(**overrides)


@pytest.fixture(scope="session")
def e2e_embedder():
    """Provider-agnostic embedder for E2E tests."""
    from tests.llm_test_factory import (
        create_test_embedder, get_test_embed_model, embedder_needs_docker,
    )

    overrides = {}
    if embedder_needs_docker():
        url = _discover_ollama_url()
        _wait_for_ollama(url)
        _wait_for_model(url, get_test_embed_model())
        overrides["base_url"] = url

    return create_test_embedder(**overrides)


# ── Backward-compatible aliases for existing tests ──────────────

@pytest.fixture(scope="session")
def ollama_e2e_llm(e2e_llm):
    """Alias for e2e_llm — backward compatibility."""
    return e2e_llm


@pytest.fixture(scope="session")
def ollama_e2e_embedder(e2e_embedder):
    """Alias for e2e_embedder — backward compatibility."""
    return e2e_embedder


@pytest.fixture(scope="session")
def ollama_e2e_url():
    """Ollama URL — only for Ollama-specific adapter tests.

    Skips when TEST_LLM_PROVIDER != ollama.
    For provider-agnostic facade tests, use e2e_llm_settings_overrides.
    """
    from tests.llm_test_factory import provider_needs_docker

    if provider_needs_docker():
        url = _discover_ollama_url()
        _wait_for_ollama(url)
        return url
    else:
        pytest.skip("ollama_e2e_url requires TEST_LLM_PROVIDER=ollama")
        return ""  # unreachable


# =====================================================================
#  PROVIDER-AGNOSTIC SETTINGS FIXTURE — for facade / pipeline tests
# =====================================================================

@pytest.fixture(scope="session")
def e2e_llm_settings_overrides() -> dict:
    """Return Settings kwargs for the configured TEST_LLM_PROVIDER.

    Usage in tests:
        settings = Settings.model_construct(
            **e2e_llm_settings_overrides,
            output_dir=str(tmp_path / "output"),
            ...
        )

    This fixture builds the right Settings fields for any supported
    provider, so facade/pipeline tests work without hardcoding Ollama.
    """
    from tests.llm_test_factory import (
        get_test_llm_provider, get_test_llm_model,
    )

    provider = get_test_llm_provider()
    model = get_test_llm_model()

    overrides: dict = {
        "llm_default_provider": provider,
        "llm_default_model": model,
    }

    if provider == "ollama":
        url = _discover_ollama_url()
        _wait_for_ollama(url)
        _wait_for_model(url, model)
        overrides["ollama_base_url"] = url

    elif provider == "openrouter":
        api_key = _env("OPENROUTER_API_KEY")
        if not api_key:
            pytest.skip("OPENROUTER_API_KEY not set in .env")
        overrides["openrouter_api_key"] = api_key
        overrides["openrouter_base_url"] = _env(
            "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
        )
        overrides["openrouter_app_name"] = _env(
            "OPENROUTER_APP_NAME", "ayExtractor-test"
        )

    elif provider == "openai":
        api_key = _env("OPENAI_API_KEY")
        if not api_key:
            pytest.skip("OPENAI_API_KEY not set in .env")
        overrides["openai_api_key"] = api_key

    elif provider == "anthropic":
        api_key = _env("ANTHROPIC_API_KEY")
        if not api_key:
            pytest.skip("ANTHROPIC_API_KEY not set in .env")
        overrides["anthropic_api_key"] = api_key

    elif provider == "google":
        api_key = _env("GOOGLE_API_KEY")
        if not api_key:
            pytest.skip("GOOGLE_API_KEY not set in .env")
        overrides["google_api_key"] = api_key

    else:
        pytest.skip(f"Unknown TEST_LLM_PROVIDER: {provider}")

    return overrides


# ── Test data fixtures ──────────────────────────────────────────

@pytest.fixture
def e2e_chunk() -> Chunk:
    """A realistic chunk from the E2E test document."""
    return Chunk(
        id="chunk_001",
        position=0,
        content=E2E_DOCUMENT_TEXT,
        source_file="nis2_overview.txt",
        char_count=len(E2E_DOCUMENT_TEXT),
        word_count=len(E2E_DOCUMENT_TEXT.split()),
    )


@pytest.fixture
def e2e_document_title() -> str:
    return E2E_DOCUMENT_TITLE


@pytest.fixture
def e2e_expected_entities() -> set[str]:
    return E2E_EXPECTED_ENTITIES


# =====================================================================
#  FUNCTIONAL FIXTURES — golden files + expected outputs
#  (merged from conftest_functional.py — pytest only discovers conftest.py)
# =====================================================================

_E2E_DIR = Path(__file__).parent
_FIXTURES_DIR = _E2E_DIR / "fixtures"
_EXPECTED_DIR = _E2E_DIR / "expected"


# ── Golden input documents ──────────────────────────────────────

@pytest.fixture(scope="session")
def nis2_pdf_path() -> Path:
    """Path to the NIS2 test PDF (3 pages: text + table + diagram)."""
    p = _FIXTURES_DIR / "nis2_test_document.pdf"
    assert p.exists(), f"Missing fixture: {p}"
    return p


@pytest.fixture(scope="session")
def nis2_pdf_bytes(nis2_pdf_path: Path) -> bytes:
    """Raw bytes of the NIS2 test PDF."""
    return nis2_pdf_path.read_bytes()


@pytest.fixture(scope="session")
def nis2_docx_path() -> Path:
    """Path to the NIS2 test DOCX (same content as PDF)."""
    p = _FIXTURES_DIR / "nis2_test_document.docx"
    assert p.exists(), f"Missing fixture: {p}"
    return p


@pytest.fixture(scope="session")
def nis2_docx_bytes(nis2_docx_path: Path) -> bytes:
    """Raw bytes of the NIS2 test DOCX."""
    return nis2_docx_path.read_bytes()


@pytest.fixture(scope="session")
def nis2_table_png_path() -> Path:
    """Path to PNG screenshot of page 2 (table)."""
    p = _FIXTURES_DIR / "nis2_test_page2_table.png"
    assert p.exists(), f"Missing fixture: {p}"
    return p


@pytest.fixture(scope="session")
def nis2_diagram_png_path() -> Path:
    """Path to PNG screenshot of page 3 (diagram)."""
    p = _FIXTURES_DIR / "nis2_test_page3_diagram.png"
    assert p.exists(), f"Missing fixture: {p}"
    return p


# ── Expected outputs (golden assertions) ────────────────────────

@pytest.fixture(scope="session")
def expected_extraction() -> dict:
    """Expected extraction assertions (text fragments, sections, tables)."""
    p = _EXPECTED_DIR / "nis2_extraction.json"
    assert p.exists(), f"Missing expected: {p}"
    return json.loads(p.read_text())


@pytest.fixture(scope="session")
def expected_chunks() -> dict:
    """Expected chunk structural assertions."""
    p = _EXPECTED_DIR / "nis2_chunks.json"
    assert p.exists(), f"Missing expected: {p}"
    return json.loads(p.read_text())


@pytest.fixture(scope="session")
def expected_triplets() -> dict:
    """Expected triplet semantic assertions."""
    p = _EXPECTED_DIR / "nis2_triplets_schema.json"
    assert p.exists(), f"Missing expected: {p}"
    return json.loads(p.read_text())


@pytest.fixture(scope="session")
def expected_image_input() -> dict:
    """Expected assertions for image-as-document input."""
    p = _EXPECTED_DIR / "nis2_image_input.json"
    assert p.exists(), f"Missing expected: {p}"
    return json.loads(p.read_text())