"""Quick smoke test for Memory Spine database connectivity."""
from dotenv import load_dotenv
load_dotenv()

from memory.connection import check_connection
import json

result = check_connection()
print(json.dumps(result, indent=2))

if result["connected"]:
    print("\n✅ Database connection successful!")
    print(f"   PostgreSQL: {result['version'][:50]}")
    print(f"   Extensions: {', '.join(result['extensions'])}")
    has_vector = "vector" in result["extensions"]
    print(f"   pgvector installed: {'✅ YES' if has_vector else '❌ NO — needs migration 002'}")
else:
    print(f"\n❌ Connection failed: {result.get('error', 'unknown')}")
