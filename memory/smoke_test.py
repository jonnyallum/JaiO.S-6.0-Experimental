"""
End-to-End Smoke Test — Validates the complete Memory Spine pipeline.

Tests:
    1. Store a memory with embedding
    2. Retrieve it by ID
    3. Search for it via vector similarity
    4. Search via keyword
    5. Link two memories
    6. Get stats
"""
import sys
import json
from dotenv import load_dotenv
load_dotenv()

from memory.agent_mixin import AgentMemory
from memory.store import MemoryStore
from memory.connection import check_connection


def main():
    print("=" * 60)
    print("🧠 MEMORY SPINE — End-to-End Smoke Test")
    print("=" * 60)

    # Step 0: Connection check
    print("\n📡 Step 0: Database connectivity...")
    status = check_connection()
    if not status["connected"]:
        print(f"   ❌ FAILED: {status.get('error')}")
        sys.exit(1)
    has_vector = "vector" in status["extensions"]
    print(f"   ✅ Connected to PostgreSQL")
    print(f"   pgvector: {'✅' if has_vector else '❌'}")

    # Step 1: Store a memory
    print("\n📝 Step 1: Storing a memory...")
    mem = AgentMemory("smoke_test_agent")
    try:
        mid1 = mem.remember(
            content="PostgreSQL with pgvector outperforms Pinecone for datasets under 100M vectors with proper HNSW indexing.",
            memory_type="semantic",
            importance=0.8,
            tags=["database", "pgvector", "performance"],
            metadata={"source": "smoke_test", "benchmark": True},
        )
        print(f"   ✅ Memory 1 stored: {mid1}")
    except Exception as e:
        print(f"   ❌ FAILED: {e}")
        sys.exit(1)

    # Store a second memory for linking
    mid2 = mem.remember(
        content="Qdrant provides sub-10ms query latency for hot-path vector retrieval in Rust-based architectures.",
        memory_type="semantic",
        importance=0.7,
        tags=["database", "qdrant", "performance"],
    )
    print(f"   ✅ Memory 2 stored: {mid2}")

    # Step 2: Retrieve by ID
    print("\n🔍 Step 2: Retrieving memory by ID...")
    store = MemoryStore()
    retrieved = store.get_memory(mid1)
    if retrieved:
        print(f"   ✅ Retrieved: '{retrieved.content[:80]}...'")
        print(f"   Type: {retrieved.memory_type} | Status: {retrieved.status} | Importance: {retrieved.importance}")
    else:
        print("   ❌ FAILED: Memory not found")
        sys.exit(1)

    # Step 3: Vector similarity search
    print("\n🎯 Step 3: Vector similarity search...")
    try:
        results = mem.recall("best vector database for performance", k=3, threshold=0.3)
        print(f"   ✅ Found {len(results)} results:")
        for r in results:
            sim = f"{r.similarity*100:.1f}%" if r.similarity else "?"
            print(f"      [{sim}] {r.content[:70]}...")
    except Exception as e:
        print(f"   ❌ FAILED: {e}")

    # Step 4: Keyword search
    print("\n📖 Step 4: Keyword search...")
    try:
        kw_results = mem.recall_keyword("pgvector performance", k=3)
        print(f"   ✅ Found {len(kw_results)} keyword results")
        for r in kw_results:
            print(f"      {r.content[:70]}...")
    except Exception as e:
        print(f"   ❌ FAILED: {e}")

    # Step 5: Link memories
    print("\n🔗 Step 5: Linking memories...")
    try:
        link_id = mem.link(mid1, mid2, link_type="associates", weight=0.9)
        print(f"   ✅ Link created: {link_id}")
    except Exception as e:
        print(f"   ❌ FAILED: {e}")

    # Step 6: Reflect (formatted context)
    print("\n💭 Step 6: Reflect (memory context for prompts)...")
    try:
        context = mem.reflect("vector database architecture")
        print(f"   ✅ Reflection generated ({len(context)} chars)")
        print(f"   Preview: {context[:200]}...")
    except Exception as e:
        print(f"   ❌ FAILED: {e}")

    # Step 7: Stats
    print("\n📊 Step 7: Memory statistics...")
    try:
        stats = mem.stats()
        print(f"   ✅ Stats: {json.dumps(stats, indent=6)}")
    except Exception as e:
        print(f"   ❌ FAILED: {e}")

    # Cleanup: soft-delete test memories
    print("\n🧹 Step 8: Cleanup (soft-delete test memories)...")
    try:
        from memory.connection import db_connection
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE memories SET deleted_at = NOW() WHERE agent_id = 'smoke_test_agent'"
                )
                print(f"   ✅ Cleaned up {cur.rowcount} test memories")
    except Exception as e:
        print(f"   ⚠️ Cleanup failed (non-critical): {e}")

    print("\n" + "=" * 60)
    print("🎉 MEMORY SPINE SMOKE TEST COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()
