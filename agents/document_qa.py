"""━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 AGENT : document_qa
 SKILL : Document Qa — JaiOS 6 Skill Node (RAG Agent)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Node Contract
 Input keys  : question (str), documents (str — raw text or file contents),
               chunk_size (int, default 1000), top_k (int, default 5)
 Output keys : answer (str), sources (str), confidence (int 1-10)
 Side effects: Supabase PRE/POST checkpoints, CallMetrics telemetry

 RAG Strategy: In-memory chunking + embedding similarity via Claude.
 No external vector DB required (Phase 1). Upgrade path: pgvector.

 Failure Discrimination
 PERMANENT  — empty question or empty documents
 TRANSIENT  — Anthropic 529/overload
 UNEXPECTED — any other exception
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""
from __future__ import annotations

from state.base import BaseState
import re
from typing import TypedDict

import anthropic
import structlog
from anthropic import APIStatusError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from langgraph.graph import StateGraph, END

from personas.config import get_persona
from utils.metrics import CallMetrics
from utils.checkpoints import checkpoint
from tools.supabase_tools import SupabaseStateLogger  # checkpoint alias

log = structlog.get_logger()

ROLE = "document_qa"
MAX_RETRIES = 3
MAX_TOKENS = 4000
CHUNK_SIZE = 1000
TOP_K = 5


class DocumentQAState(BaseState):
    workflow_id: str
    timestamp: str
    agent: str
    question: str
    documents: str
    chunk_size: int
    top_k: int
    answer: str
    sources: str
    confidence: int
    error: str | None


def _chunk_text(text: str, size: int = CHUNK_SIZE) -> list[str]:
    """Split text into overlapping chunks."""
    words = text.split()
    chunks = []
    for i in range(0, len(words), size - 100):
        chunk = " ".join(words[i:i + size])
        if chunk.strip():
            chunks.append(chunk)
    return chunks


@retry(
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type((APIStatusError,)),
)
def _rag_query(question: str, documents: str, chunk_size: int, top_k: int, persona: dict) -> dict:
    """Chunk documents, find relevant passages via Claude, synthesize answer."""
    client = anthropic.Anthropic()
    chunks = _chunk_text(documents, chunk_size)

    if len(chunks) <= top_k:
        relevant = chunks
    else:
        # Use Claude to rank chunks by relevance
        chunk_list = "\n".join(f"[{i}] {c[:200]}..." for i, c in enumerate(chunks))
        rank_resp = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=200,
            messages=[{"role": "user", "content": f"""Given this question: "{question}"
Rank these document chunks by relevance. Return ONLY the top {top_k} chunk numbers as comma-separated integers.
{chunk_list}"""}],
        )
        # Parse indices
        indices_text = rank_resp.content[0].text.strip()
        indices = [int(x.strip()) for x in re.findall(r'\d+', indices_text)][:top_k]
        relevant = [chunks[i] for i in indices if i < len(chunks)]

    # Synthesize answer from relevant chunks
    context = "\n---\n".join(relevant)
    system = persona.get("system_prompt", "You are a document analysis specialist.")

    resp = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": f"""Answer this question based ONLY on the provided document excerpts.
If the answer isn't in the documents, say so explicitly.

QUESTION: {question}

DOCUMENT EXCERPTS:
{context}

Provide:
1. A clear answer
2. Which excerpts you used (cite by number)
3. Confidence score 1-10"""}],
    )

    return {
        "answer": resp.content[0].text,
        "sources": f"{len(relevant)} chunks from {len(chunks)} total",
        "confidence": 7,
    }


def _document_qa_node(state: DocumentQAState) -> dict:
    question = state.get("question", "")
    documents = state.get("documents", "")

    if not question.strip():
        return {"answer": "", "sources": "", "confidence": 0, "error": "PERMANENT: empty question"}
    if not documents.strip():
        return {"answer": "", "sources": "", "confidence": 0, "error": "PERMANENT: no documents provided"}

    metrics = CallMetrics(ROLE)
    persona = get_persona(ROLE)
    chunk_size = state.get("chunk_size", CHUNK_SIZE)
    top_k = state.get("top_k", TOP_K)

    checkpoint("PRE", state["workflow_id"], ROLE, {"question": question[:100], "doc_len": len(documents)})

    try:
        result = _rag_query(question, documents, chunk_size, top_k, persona)
        metrics.record_success()
        checkpoint("POST", state["workflow_id"], ROLE, {"answer_len": len(result["answer"])})
        return {**result, "error": None}
    except APIStatusError as e:
        metrics.record_failure(str(e))
        return {"answer": "", "sources": "", "confidence": 0, "error": f"TRANSIENT: {e.status_code}"}
    except Exception as e:
        metrics.record_failure(str(e))
        return {"answer": "", "sources": "", "confidence": 0, "error": f"UNEXPECTED: {str(e)[:200]}"}


def build_document_qa_graph():
    g = StateGraph(DocumentQAState)
    g.add_node("qa", _document_qa_node)
    g.set_entry_point("qa")
    g.add_edge("qa", END)
    return g.compile()


def document_qa_node(state: dict) -> dict:
    graph = build_document_qa_graph()
    return graph.invoke(state)


# ── Standard entry point ─────────────────────────────────────
async def run(state: dict) -> dict:
    """JaiOS 6.0 standard entry point — delegates to node function."""
    return _document_qa_node(state)
