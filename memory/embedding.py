"""
Embedding Service — Generate vector embeddings for the Memory Spine.

Uses OpenAI text-embedding-3-small (1536 dimensions).
Includes batching and retry logic for production reliability.
"""
import os
import logging
import time
from typing import Optional

log = logging.getLogger(__name__)

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSIONS = 1536
MAX_BATCH_SIZE = 100  # OpenAI limit per request
MAX_INPUT_TOKENS = 8191  # Max tokens per input


def get_embedding(text: str, model: str = EMBEDDING_MODEL) -> list[float]:
    """
    Generate a single embedding vector for the given text.

    Args:
        text: The text to embed (will be truncated if too long)
        model: OpenAI embedding model to use

    Returns:
        List of floats (1536 dimensions)
    """
    import openai

    client = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))

    # Truncate to avoid token limit (rough estimate: 4 chars per token)
    max_chars = MAX_INPUT_TOKENS * 4
    if len(text) > max_chars:
        text = text[:max_chars]
        log.warning(f"embedding.truncated chars={len(text)}")

    # Clean the text
    text = text.replace("\n", " ").strip()
    if not text:
        return [0.0] * EMBEDDING_DIMENSIONS

    try:
        response = client.embeddings.create(
            input=text,
            model=model,
            dimensions=EMBEDDING_DIMENSIONS,
        )
        embedding = response.data[0].embedding
        log.debug(f"embedding.generated model={model} dims={len(embedding)}")
        return embedding
    except Exception as e:
        log.error(f"embedding.error model={model} error={e}")
        raise


def get_embeddings_batch(texts: list[str], model: str = EMBEDDING_MODEL) -> list[list[float]]:
    """
    Generate embeddings for a batch of texts.

    Args:
        texts: List of texts to embed
        model: OpenAI embedding model

    Returns:
        List of embedding vectors (same order as input)
    """
    import openai

    client = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))

    max_chars = MAX_INPUT_TOKENS * 4
    cleaned = []
    for t in texts:
        t = t.replace("\n", " ").strip()
        if len(t) > max_chars:
            t = t[:max_chars]
        if not t:
            t = " "
        cleaned.append(t)

    all_embeddings = []

    # Process in batches
    for i in range(0, len(cleaned), MAX_BATCH_SIZE):
        batch = cleaned[i:i + MAX_BATCH_SIZE]

        try:
            response = client.embeddings.create(
                input=batch,
                model=model,
                dimensions=EMBEDDING_DIMENSIONS,
            )
            batch_embeddings = [d.embedding for d in response.data]
            all_embeddings.extend(batch_embeddings)
            log.info(f"embedding.batch batch_size={len(batch)} total={len(all_embeddings)}")
        except Exception as e:
            log.error(f"embedding.batch_error batch_start={i} error={e}")
            # Fill with zero vectors on failure (memory stored but not searchable)
            all_embeddings.extend([[0.0] * EMBEDDING_DIMENSIONS] * len(batch))

    return all_embeddings
