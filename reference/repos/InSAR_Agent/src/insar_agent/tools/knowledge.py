"""InSAR knowledge base - document storage and search

Stores InSAR-related documentation (MintPy, ISCE, GAMMA, HyP3, SAR theory) as
JSON documents. Provides keyword-based search for retrieval-augmented responses.
"""

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / 'data' / 'knowledge'


@dataclass
class KnowledgeResult:
    query: str
    results: list[dict] = field(default_factory=list)
    total_found: int = 0
    warnings: list[str] = field(default_factory=list)


def _tokenize(text: str) -> list[str]:
    """Chinese-aware tokenization: split on CJK chars individually, Latin words by spaces."""
    tokens = []
    for char in text:
        if '\u4e00' <= char <= '\u9fff' or '\u3400' <= char <= '\u4dbf':
            tokens.append(char)
        elif char.isalnum() or char == '_':
            tokens.append(char)
        else:
            tokens.append(' ')
    merged = ''.join(tokens)
    return [t.lower() for t in merged.split() if len(t) > 1]


def _load_documents() -> list[dict]:
    docs = []
    if not _KNOWLEDGE_DIR.is_dir():
        return docs
    for fpath in sorted(_KNOWLEDGE_DIR.glob('*.json')):
        try:
            with open(fpath, 'r', encoding='utf-8') as f:
                doc = json.load(f)
                if isinstance(doc, list):
                    docs.extend(doc)
                else:
                    docs.append(doc)
        except Exception:
            pass
    return docs


def search_knowledge(query: str, top_k: int = 3) -> KnowledgeResult:
    """Search the InSAR knowledge base for documents matching the query.

    Performs keyword-based full-text search across all stored documents.
    Returns the most relevant documents ranked by match score.

    Args:
        query: Search query (natural language)
        top_k: Number of results to return (default 3)

    Returns:
        KnowledgeResult with ranked document chunks
    """
    warnings = []
    docs = _load_documents()
    if not docs:
        return KnowledgeResult(
            query=query,
            warnings=['Knowledge base is empty. Run build_knowledge to populate it.'],
        )

    query_tokens = _tokenize(query)
    if not query_tokens:
        return KnowledgeResult(query=query)

    scored = []
    for doc in docs:
        title = doc.get('title', '')
        content = doc.get('content', '')
        tags = ' '.join(doc.get('tags', []))
        search_text = f'{title} {tags} {content}'

        search_lower = search_text.lower()
        score = 0
        for token in query_tokens:
            score += search_lower.count(token)
        if score > 0:
            # Chunk content to around 800 chars around the best match
            snippet = content
            if len(content) > 800:
                best_pos = 0
                best_cnt = 0
                token_set = set(query_tokens)
                for i in range(0, len(content) - 200, 100):
                    window = content[i:i + 800].lower()
                    cnt = sum(window.count(t) for t in token_set)
                    if cnt > best_cnt:
                        best_cnt = cnt
                        best_pos = i
                start = max(0, best_pos - 100)
                snippet = content[start:start + 800]
                if start > 0:
                    snippet = '...' + snippet
                if start + 800 < len(content):
                    snippet = snippet + '...'

            scored.append({
                'title': title,
                'source': doc.get('source', ''),
                'category': doc.get('category', ''),
                'snippet': snippet,
                'score': score,
            })

    scored.sort(key=lambda x: -x['score'])
    top = scored[:top_k]

    return KnowledgeResult(
        query=query,
        results=top,
        total_found=len(scored),
        warnings=warnings,
    )
