"""
RAG retrieval using PostgreSQL + pgvector.

Embeds knowledge sources and destination descriptions at startup,
then provides cosine similarity search for user queries.
"""

import json
import logging
from typing import Optional
from sqlalchemy import create_engine, text
from app.config import settings

logger = logging.getLogger(__name__)

# Will be initialized on startup
_gemini_client = None


def _get_gemini_client():
    """Lazy-load the Gemini client."""
    global _gemini_client
    if _gemini_client is None:
        from google import genai
        _gemini_client = genai.Client(api_key=settings.gemini_api_key)
    return _gemini_client


def generate_embedding(text_content: str) -> list[float]:
    """Generate an embedding vector for the given text using Gemini."""
    client = _get_gemini_client()
    result = client.models.embed_content(
        model=settings.gemini_embedding_model,
        contents=text_content,
    )
    return result.embeddings[0].values


def build_embeddings():
    """Build embeddings for all knowledge sources and destinations. Skips if already built."""
    sync_url = settings.sync_database_url
    engine = create_engine(sync_url)

    with engine.begin() as conn:
        # Check if embeddings already exist
        count = conn.execute(text("SELECT COUNT(*) FROM embeddings")).scalar()
        if count > 0:
            logger.info(f"Embeddings already built ({count} entries), skipping.")
            engine.dispose()
            return

        logger.info("Building embeddings...")

        # ── Embed knowledge sources ──
        rows = conn.execute(text("SELECT id, destination_country_code, title, text FROM knowledge_sources")).fetchall()
        for row in rows:
            content = f"Destination: {row[1]}. {row[2]}. {row[3]}"
            try:
                embedding = generate_embedding(content)
                conn.execute(
                    text("""
                        INSERT INTO embeddings (source_type, source_id, content, embedding)
                        VALUES ('knowledge', :source_id, :content, :embedding)
                    """),
                    {
                        "source_id": row[0],
                        "content": content,
                        "embedding": str(embedding),
                    },
                )
            except Exception as e:
                logger.error(f"Failed to embed knowledge source {row[0]}: {e}")

        # ── Embed destinations ──
        rows = conn.execute(
            text("SELECT id, country_code, country_name, interests FROM destinations")
        ).fetchall()
        for row in rows:
            interests = row[3] if isinstance(row[3], list) else []
            content = f"Destination: {row[2]} ({row[1]}). Interests: {', '.join(interests)}."
            try:
                embedding = generate_embedding(content)
                conn.execute(
                    text("""
                        INSERT INTO embeddings (source_type, source_id, content, embedding)
                        VALUES ('destination', :source_id, :content, :embedding)
                    """),
                    {
                        "source_id": row[0],
                        "content": content,
                        "embedding": str(embedding),
                    },
                )
            except Exception as e:
                logger.error(f"Failed to embed destination {row[0]}: {e}")

        # ── Embed visa SKUs ──
        rows = conn.execute(
            text("""
                SELECT id, sku_code, country_name, purpose, processing_speed,
                       processing_time_days, base_price_amount, base_price_currency,
                       validity_days, stay_days
                FROM visa_skus
            """)
        ).fetchall()
        for row in rows:
            content = (
                f"Visa SKU: {row[1]} for {row[2]}. "
                f"Purpose: {row[3]}. Speed: {row[4]}. "
                f"Processing: {row[5]} days. Price: {row[7]} {row[6]}. "
                f"Validity: {row[8]} days. Stay: {row[9]} days."
            )
            try:
                embedding = generate_embedding(content)
                conn.execute(
                    text("""
                        INSERT INTO embeddings (source_type, source_id, content, embedding)
                        VALUES ('sku', :source_id, :content, :embedding)
                    """),
                    {
                        "source_id": row[0],
                        "content": content,
                        "embedding": str(embedding),
                    },
                )
            except Exception as e:
                logger.error(f"Failed to embed SKU {row[0]}: {e}")

        logger.info("Embeddings built successfully!")

    engine.dispose()


def search_similar(query: str, top_k: int = 5, source_type: Optional[str] = None) -> list[dict]:
    """
    Search for similar content using cosine similarity.

    Args:
        query: user question text
        top_k: number of results to return
        source_type: optional filter ('knowledge', 'destination', 'sku')

    Returns:
        List of dicts with source_type, source_id, content, and similarity score
    """
    try:
        query_embedding = generate_embedding(query)
    except Exception as e:
        logger.error(f"Failed to generate query embedding: {e}")
        return []

    sync_url = settings.sync_database_url
    engine = create_engine(sync_url)
    query_vec_str = str(query_embedding)

    results = []
    with engine.connect() as conn:
        if source_type:
            rows = conn.execute(
                text("""
                    SELECT source_type, source_id, content,
                           1 - (embedding <=> CAST(:query_vec AS vector)) AS similarity
                    FROM embeddings
                    WHERE source_type = :src_type
                    ORDER BY embedding <=> CAST(:query_vec AS vector)
                    LIMIT :k
                """),
                {
                    "query_vec": query_vec_str,
                    "src_type": source_type,
                    "k": top_k,
                },
            ).fetchall()
        else:
            rows = conn.execute(
                text("""
                    SELECT source_type, source_id, content,
                           1 - (embedding <=> CAST(:query_vec AS vector)) AS similarity
                    FROM embeddings
                    ORDER BY embedding <=> CAST(:query_vec AS vector)
                    LIMIT :k
                """),
                {
                    "query_vec": query_vec_str,
                    "k": top_k,
                },
            ).fetchall()

        for row in rows:
            results.append({
                "source_type": row[0],
                "source_id": row[1],
                "content": row[2],
                "similarity": float(row[3]) if row[3] else 0.0,
            })

    engine.dispose()
    return results

