"""
Migration Runner — Apply SQL migrations against Supabase in order.

Usage:
    python -m memory.apply_migrations          # Apply all pending
    python -m memory.apply_migrations --dry    # Show what would run
    python -m memory.apply_migrations 002      # Apply specific migration
"""
import os
import sys
import glob
import logging
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "migrations")


def get_migration_files(specific: str | None = None) -> list[str]:
    """Get ordered list of migration SQL files."""
    pattern = os.path.join(MIGRATIONS_DIR, "*.sql")
    files = sorted(glob.glob(pattern))
    if specific:
        files = [f for f in files if specific in os.path.basename(f)]
    return files


def apply_migration(filepath: str, dry_run: bool = False) -> bool:
    """Apply a single migration file."""
    filename = os.path.basename(filepath)

    with open(filepath, "r", encoding="utf-8") as f:
        sql = f.read()

    if dry_run:
        log.info(f"[DRY RUN] Would apply: {filename} ({len(sql)} chars)")
        return True

    log.info(f"Applying: {filename}...")

    try:
        from memory.connection import db_connection
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                # Try to fetch results (for SELECT verification queries)
                try:
                    rows = cur.fetchall()
                    for row in rows:
                        log.info(f"  → {row}")
                except Exception:
                    pass  # No results to fetch (DDL statements)
        log.info(f"  ✅ {filename} applied successfully")
        return True
    except Exception as e:
        log.error(f"  ❌ {filename} FAILED: {e}")
        return False


def main():
    args = sys.argv[1:]
    dry_run = "--dry" in args
    specific = None
    for a in args:
        if a != "--dry" and not a.startswith("-"):
            specific = a

    files = get_migration_files(specific)
    if not files:
        log.warning("No migration files found.")
        return

    log.info(f"Found {len(files)} migration(s) to apply:")
    for f in files:
        log.info(f"  • {os.path.basename(f)}")
    print()

    success = 0
    failed = 0
    for filepath in files:
        if apply_migration(filepath, dry_run=dry_run):
            success += 1
        else:
            failed += 1
            if not dry_run:
                log.error("Stopping on first failure.")
                break

    print()
    log.info(f"Done. {success} succeeded, {failed} failed.")


if __name__ == "__main__":
    main()
