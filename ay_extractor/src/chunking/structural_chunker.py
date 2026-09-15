# src/chunking/structural_chunker.py — v2
"""Structure-aware chunking strategy.

Cuts at document SECTION boundaries first (a chunk never straddles into an
unrelated section), then size-packs WITHIN the structure. Small sibling
subsections that share the same immediate parent are merged up to the target
size (option B) so a deeply-headed document does not explode into hundreds of
tiny chunks. Each chunk carries a RELIABLE `section_path` — the longest common
ancestor chain of the sections it covers, derived from the heading hierarchy,
not a substring guess.

Atomic IMAGE_CONTENT / TABLE_CONTENT blocks stay intact (spec §7.3). When the
document has no detected structure, falls back to paragraph-packing (the legacy
behaviour — no regression). See spec §4.1 step 2a.
"""

from __future__ import annotations

import hashlib
import re

from ayextractor.chunking.base_chunker import BaseChunker
from ayextractor.config.settings import Settings
from ayextractor.core.models import Chunk, ChunkSourceSection, DocumentStructure, Section

# Pattern to match atomic blocks
_ATOMIC_BLOCK = re.compile(
    r"<<<(?:IMAGE_CONTENT|TABLE_CONTENT)\b.*?<<<END_(?:IMAGE_CONTENT|TABLE_CONTENT)>>>",
    re.DOTALL,
)

# Pattern to extract block IDs
_BLOCK_ID = re.compile(r'id="([^"]+)"')
_BLOCK_TYPE = re.compile(r"<<<(IMAGE_CONTENT|TABLE_CONTENT)")


class StructuralChunker(BaseChunker):
    """Chunk text along the document's structural boundaries."""

    def __init__(self, settings: Settings | None = None):
        self._target = 2000 if settings is None else settings.chunk_target_size
        self._overlap = 0 if settings is None else settings.chunk_overlap

    @property
    def strategy_name(self) -> str:
        return "structural"

    async def chunk(
        self,
        text: str,
        structure: DocumentStructure | None = None,
        source_file: str = "unknown",
    ) -> list[Chunk]:
        """Split text into chunks respecting structure and atomic blocks."""
        if not text.strip():
            return []

        # (section_path, content) pairs — structure-aware when we have sections,
        # else the legacy paragraph-packing fallback (path empty).
        if structure is not None and structure.sections:
            raw_chunks = self._chunk_by_structure(text, structure)
        else:
            segments = self._split_preserving_atomic(text)
            raw_chunks = [([], merged) for merged in self._merge_segments(segments)]

        # Build Chunk objects with running byte offsets + atomic-block detection.
        chunks: list[Chunk] = []
        offset = 0
        for i, (section_path, content) in enumerate(raw_chunks):
            chunk = self._build_chunk(i, content, section_path, source_file, offset)
            chunks.append(chunk)
            offset = chunk.byte_offset_end

        # Link chunks
        for i, chunk in enumerate(chunks):
            if i > 0:
                chunk.preceding_chunk_id = chunks[i - 1].id
            if i < len(chunks) - 1:
                chunk.following_chunk_id = chunks[i + 1].id

        return chunks

    # ------------------------------------------------------------------
    # Structure-aware path
    # ------------------------------------------------------------------

    def _chunk_by_structure(
        self, text: str, structure: DocumentStructure
    ) -> list[tuple[list[str], str]]:
        """Slice the text per section, then greedily pack sibling sections
        (same immediate parent) up to the target size (option B)."""
        sections = structure.sections
        paths = self._compute_paths(sections)

        # Linearised units in document order : (ancestor_path, direct_body).
        # The preamble before the first heading is its own path-less unit.
        units: list[tuple[list[str], str]] = []
        preamble = text[: sections[0].start_position]
        if preamble.strip():
            units.append(([], preamble.strip()))
        for sec, path in zip(sections, paths, strict=True):
            body = text[sec.start_position : sec.end_position].strip()
            if body:
                units.append((path, body))

        out: list[tuple[list[str], str]] = []
        buf_parts: list[str] = []
        buf_paths: list[list[str]] = []
        buf_parent: list[str] | None = None
        buf_len = 0

        def flush() -> None:
            nonlocal buf_parts, buf_paths, buf_parent, buf_len
            if buf_parts:
                out.append((_longest_common_prefix(buf_paths), "\n\n".join(buf_parts)))
            buf_parts, buf_paths, buf_parent, buf_len = [], [], None, 0

        for path, body in units:
            parent = path[:-1]
            # A section larger than the target is chunked on its own (paragraph
            # packing within the section — never merged with siblings).
            if len(body) > self._target:
                flush()
                for seg in self._merge_segments(self._split_preserving_atomic(body)):
                    out.append((path, seg))
                continue
            # Flush when the parent changes (would cross a sibling group) or the
            # buffer would overflow ; otherwise extend it.
            if buf_parts and (parent != buf_parent or buf_len + len(body) > self._target):
                flush()
            if not buf_parts:
                buf_parent = parent
            buf_parts.append(body)
            buf_paths.append(path)
            buf_len += len(body)

        flush()
        return out

    @staticmethod
    def _compute_paths(sections: list[Section]) -> list[list[str]]:
        """Ancestor chain (titles, root→leaf incl. self) for each section,
        from the heading-level hierarchy."""
        paths: list[list[str]] = []
        stack: list[Section] = []
        for sec in sections:
            while stack and stack[-1].level >= sec.level:
                stack.pop()
            paths.append([s.title for s in stack] + [sec.title])
            stack.append(sec)
        return paths

    # ------------------------------------------------------------------
    # Chunk construction
    # ------------------------------------------------------------------

    def _build_chunk(
        self,
        position: int,
        content: str,
        section_path: list[str],
        source_file: str,
        offset: int,
    ) -> Chunk:
        embedded_images: list[str] = []
        embedded_tables: list[str] = []
        for block_match in _ATOMIC_BLOCK.finditer(content):
            block_text = block_match.group()
            id_match = _BLOCK_ID.search(block_text)
            type_match = _BLOCK_TYPE.search(block_text)
            if id_match and type_match:
                bid = id_match.group(1)
                if type_match.group(1) == "IMAGE_CONTENT":
                    embedded_images.append(bid)
                else:
                    embedded_tables.append(bid)

        content_type = "mixed" if embedded_images or embedded_tables else "text"
        fp = hashlib.sha256(content.encode()).hexdigest()[:16]
        end_offset = offset + len(content.encode())
        sources = [
            ChunkSourceSection(title=title, level=depth + 1)
            for depth, title in enumerate(section_path)
        ]
        return Chunk(
            id=f"chunk_{position:03d}",
            position=position,
            content=content,
            content_type=content_type,
            embedded_images=embedded_images,
            embedded_tables=embedded_tables,
            source_file=source_file,
            source_sections=sources,
            byte_offset_start=offset,
            byte_offset_end=end_offset,
            char_count=len(content),
            word_count=len(content.split()),
            token_count_est=len(content) // 4,
            fingerprint=fp,
        )

    # ------------------------------------------------------------------
    # Fallback (no structure) — legacy paragraph packing
    # ------------------------------------------------------------------

    def _split_preserving_atomic(self, text: str) -> list[str]:
        """Split text into segments, keeping atomic blocks intact."""
        segments: list[str] = []
        last_end = 0

        for match in _ATOMIC_BLOCK.finditer(text):
            # Add text before block (split by paragraphs)
            before = text[last_end : match.start()]
            if before.strip():
                for para in re.split(r"\n{2,}", before):
                    if para.strip():
                        segments.append(para.strip())
            # Add atomic block as single segment
            segments.append(match.group())
            last_end = match.end()

        # Remaining text after last block
        remaining = text[last_end:]
        if remaining.strip():
            for para in re.split(r"\n{2,}", remaining):
                if para.strip():
                    segments.append(para.strip())

        return segments

    def _merge_segments(self, segments: list[str]) -> list[str]:
        """Merge segments into chunks of approximately target size."""
        if not segments:
            return []

        chunks: list[str] = []
        current_parts: list[str] = []
        current_len = 0

        for seg in segments:
            seg_len = len(seg)
            if current_len + seg_len > self._target and current_parts:
                chunks.append("\n\n".join(current_parts))
                # Overlap: keep last part if overlap > 0
                if self._overlap > 0 and current_parts:
                    last = current_parts[-1]
                    current_parts = [last] if len(last) <= self._overlap else []
                    current_len = len(last) if current_parts else 0
                else:
                    current_parts = []
                    current_len = 0
            current_parts.append(seg)
            current_len += seg_len

        if current_parts:
            chunks.append("\n\n".join(current_parts))

        return chunks


def _longest_common_prefix(paths: list[list[str]]) -> list[str]:
    """Longest shared ancestor chain across `paths`. For a single section this
    is its full path ; for merged siblings (same parent, distinct leaves) it is
    the parent path — the option-B labelling."""
    if not paths:
        return []
    common = list(paths[0])
    for path in paths[1:]:
        limit = min(len(common), len(path))
        i = 0
        while i < limit and common[i] == path[i]:
            i += 1
        common = common[:i]
        if not common:
            break
    return common
