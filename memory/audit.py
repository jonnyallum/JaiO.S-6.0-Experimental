"""Inspect learnings and chatroom table schemas."""
from dotenv import load_dotenv
load_dotenv()
from memory.connection import db_connection

with db_connection() as conn:
    with conn.cursor() as cur:
        # Learnings schema
        cur.execute("""
            SELECT column_name, data_type 
            FROM information_schema.columns 
            WHERE table_name = 'learnings' 
            ORDER BY ordinal_position
        """)
        print("=== learnings columns ===")
        for r in cur.fetchall():
            print(f"  {r[0]}: {r[1]}")

        # Sample row
        cur.execute("SELECT * FROM learnings ORDER BY created_at DESC LIMIT 1")
        cols = [d[0] for d in cur.description]
        row = cur.fetchone()
        print("\n=== Sample learning ===")
        for c, v in zip(cols, row):
            val = str(v)[:120] if v else "NULL"
            print(f"  {c}: {val}")

        # Count
        cur.execute("SELECT COUNT(*) FROM learnings")
        print(f"\nTotal learnings: {cur.fetchone()[0]}")

        # Chatroom schema
        cur.execute("""
            SELECT column_name, data_type 
            FROM information_schema.columns 
            WHERE table_name = 'chatroom' 
            ORDER BY ordinal_position
        """)
        print("\n=== chatroom columns ===")
        for r in cur.fetchall():
            print(f"  {r[0]}: {r[1]}")

        cur.execute("SELECT * FROM chatroom ORDER BY created_at DESC LIMIT 1")
        cols = [d[0] for d in cur.description]
        row = cur.fetchone()
        print("\n=== Sample chatroom msg ===")
        for c, v in zip(cols, row):
            val = str(v)[:120] if v else "NULL"
            print(f"  {c}: {val}")
