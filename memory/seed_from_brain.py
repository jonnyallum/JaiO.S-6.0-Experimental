"""
Seed Memory Spine from Shared Brain — Imports existing learnings into persistent memory.

Reads all rows from the `learnings` table, generates vector embeddings in batch,
and inserts them as `semantic` memories into the Memory Spine.

Usage:
    python -m memory.seed_from_brain               # Full seed
    python -m memory.seed_from_brain --limit 100    # Test with 100
    python -m memory.seed_from_brain --dry          # Preview only
"""
import sys
import json
import time
import logging
from dotenv import load_dotenv

load_dotenv()

from memory.connection import db_connection
from memory.embedding import get_embeddings_batch

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

BATCH_SIZE = 50  # Embedding API batch size


def fetch_learnings(limit: int | None = None) -> list[dict]:
    """Fetch all learnings from Supabase."""
    with db_connection() as conn:
        with conn.cursor() as cur:
            limit_clause = f"LIMIT {limit}" if limit else ""
            cur.execute(f"""
                SELECT id, source_agent, source_project, source_ai,
                       learning, category, tags, created_at
                FROM learnings
                ORDER BY created_at ASC
                {limit_clause}
            """)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
            return [dict(zip(cols, row)) for row in rows]


def check_already_seeded() -> int:
    """Count how many seeded memories already exist."""
    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) FROM memories 
                WHERE source_uri LIKE 'supabase://learnings/%'
                  AND deleted_at IS NULL
            """)
            return cur.fetchone()[0]


def seed_batch(learnings: list[dict], dry_run: bool = False) -> int:
    """Seed a batch of learnings into the memories table."""
    if not learnings:
        return 0

    # Prepare texts for embedding
    texts = [l["learning"] for l in learnings]

    if dry_run:
        log.info(f"  [DRY] Would embed {len(texts)} learnings")
        return len(texts)

    # Generate embeddings in batch
    log.info(f"  Generating {len(texts)} embeddings...")
    embeddings = get_embeddings_batch(texts)

    inserted = 0
    with db_connection() as conn:
        with conn.cursor() as cur:
            for learning, embedding in zip(learnings, embeddings):
                agent_id = learning.get("source_agent") or "system"
                tags = learning.get("tags") or []
                category = learning.get("category") or "general"

                metadata = {
                    "source_project": learning.get("source_project"),
                    "source_ai": learning.get("source_ai"),
                    "category": category,
                    "seeded_from": "learnings",
                    "original_id": str(learning["id"]),
                }
                # Clean None values
                metadata = {k: v for k, v in metadata.items() if v is not None}

                try:
                    cur.execute(
                        """
                        INSERT INTO memories (
                            agent_id, content, memory_type, status,
                            embedding, importance, metadata, source_uri, tags
                        ) VALUES (%s, %s, 'semantic', 'active',
                                  %s::vector, %s, %s::jsonb, %s, %s)
                        ON CONFLICT DO NOTHING
                        """,
                        (
                            agent_id,
                            learning["learning"],
                            str(embedding),
                            0.6,  # Medium-high importance for existing learnings
                            json.dumps(metadata),
                            f"supabase://learnings/{learning['id']}",
                            tags if isinstance(tags, list) else [],
                        ),
                    )
                    inserted += 1
                except Exception as e:
                    log.warning(f"  Skip {learning['id']}: {e}")

    return inserted


def main():
    args = sys.argv[1:]
    dry_run = "--dry" in args
    limit = None
    for a in args:
        if a.startswith("--limit"):
            continue
        try:
            limit = int(a)
        except ValueError:
            pass
    # Check --limit N format
    for i, a in enumerate(args):
        if a == "--limit" and i + 1 < len(args):
            try:
                limit = int(args[i + 1])
            except ValueError:
                pass

    print("=" * 60)
    print("🧠 MEMORY SPINE — Seed from Shared Brain")
    print("=" * 60)

    # Check existing
    already = check_already_seeded()
    if already > 0:
        print(f"\n⚠️  {already} learnings already seeded. Duplicates will be skipped.")

    # Fetch learnings
    print(f"\n📥 Fetching learnings{f' (limit {limit})' if limit else ''}...")
    learnings = fetch_learnings(limit)
    print(f"   Found {len(learnings)} learnings to process")

    if not learnings:
        print("   Nothing to seed.")
        return

    # Preview
    print(f"\n📝 Sample learnings:")
    for l in learnings[:3]:
        print(f"   [{l.get('source_agent', '?')}] {l['learning'][:80]}...")

    # Process in batches
    total_inserted = 0
    total_batches = (len(learnings) + BATCH_SIZE - 1) // BATCH_SIZE
    start_time = time.time()

    print(f"\n🚀 Processing {len(learnings)} learnings in {total_batches} batches...")

    for i in range(0, len(learnings), BATCH_SIZE):
        batch = learnings[i:i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        print(f"\n  Batch {batch_num}/{total_batches} ({len(batch)} items)...")

        inserted = seed_batch(batch, dry_run=dry_run)
        total_inserted += inserted

        elapsed = time.time() - start_time
        rate = total_inserted / elapsed if elapsed > 0 else 0
        print(f"  ✅ {inserted} inserted (total: {total_inserted}, {rate:.0f}/sec)")

    elapsed = round(time.time() - start_time, 1)
    print(f"\n{'=' * 60}")
    print(f"🎉 Seeding complete!")
    print(f"   Inserted: {total_inserted}/{len(learnings)}")
    print(f"   Time: {elapsed}s")
    print(f"   Rate: {total_inserted / elapsed:.0f} memories/sec" if elapsed > 0 else "")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
