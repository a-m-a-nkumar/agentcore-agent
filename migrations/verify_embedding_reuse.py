"""
Verify Embedding Reuse — Hybrid Dedup Diagnostic
=================================================
Checks whether two projects that share the same Confluence space / Jira project
actually reuse the same embedding vectors instead of calling Bedrock twice.

Logic:
  - Find (source_type, source_id, chunk_index) groups that appear in MORE THAN ONE project
  - For each such group, compare whether the embedding vectors are bit-for-bit identical
  - If identical  → reuse worked ✅
  - If different  → Bedrock was called separately ❌
"""

import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()


def run():
    print("🔄 Connecting to database...")
    conn = psycopg2.connect(
        host=os.getenv("DATABASE_HOST"),
        port=os.getenv("DATABASE_PORT", "5432"),
        database=os.getenv("DATABASE_NAME"),
        user=os.getenv("DATABASE_USER"),
        password=os.getenv("DATABASE_PASSWORD"),
    )
    print("✅ Connected!\n")
    cursor = conn.cursor()

    # ── Step 1: Find all (source_type, source_id, chunk_index) that appear in 2+ projects ──
    cursor.execute("""
        SELECT source_type, source_id, chunk_index, COUNT(DISTINCT project_id) AS project_count
        FROM document_embeddings
        GROUP BY source_type, source_id, chunk_index
        HAVING COUNT(DISTINCT project_id) > 1
        ORDER BY source_type, source_id, chunk_index;
    """)
    shared_chunks = cursor.fetchall()

    if not shared_chunks:
        print("ℹ️  No shared chunks found across projects.")
        print("   Either only one project exists, or the same space hasn't been synced by two projects yet.")
        conn.close()
        return

    print(f"📋 Found {len(shared_chunks)} chunk(s) shared across multiple projects:\n")
    print(f"  {'source_type':<15} {'source_id':<20} {'chunk':<8} {'projects'}")
    print("  " + "-" * 60)
    for row in shared_chunks:
        print(f"  {row[0]:<15} {row[1]:<20} {row[2]:<8} {row[3]}")

    print()

    # ── Step 2: For each shared chunk, compare embeddings across projects ──
    reused_count = 0
    bedrock_called_count = 0

    for (source_type, source_id, chunk_index, _) in shared_chunks:
        cursor.execute("""
            SELECT project_id, embedding::text
            FROM document_embeddings
            WHERE source_type = %s AND source_id = %s AND chunk_index = %s
            ORDER BY created_at ASC;
        """, (source_type, source_id, chunk_index))
        rows = cursor.fetchall()

        # Compare all embedding strings — identical strings mean reuse worked
        embeddings = [r[1] for r in rows]
        project_ids = [str(r[0])[:8] + "..." for r in rows]  # truncate for readability

        all_same = len(set(embeddings)) == 1

        status = "✅ REUSED" if all_same else "❌ BEDROCK CALLED AGAIN"
        if all_same:
            reused_count += 1
        else:
            bedrock_called_count += 1

        print(f"  [{source_type}] {source_id} chunk {chunk_index}: {status}")
        for pid, emb in zip(project_ids, embeddings):
            # Show just first 40 chars of embedding string for comparison
            print(f"    project {pid}  →  {emb[:60]}...")

    print()
    print("=" * 60)
    print(f"✅ Reused (Bedrock skipped):   {reused_count}")
    print(f"❌ Called Bedrock again:       {bedrock_called_count}")

    if bedrock_called_count == 0 and reused_count > 0:
        print("\n🎉 Perfect! Hybrid dedup is working correctly.")
        print("   Bedrock was NOT called for duplicate content across projects.")
    elif reused_count == 0 and bedrock_called_count > 0:
        print("\n⚠️  Dedup did NOT work — Bedrock was called for all shared chunks.")
        print("   Check find_existing_embedding() and the index on (source_type, source_id, chunk_index).")
    else:
        print("\n⚠️  Mixed results — dedup worked for some chunks but not all.")

    print("=" * 60)

    cursor.close()
    conn.close()
    print("\n🔌 Connection closed.")


if __name__ == "__main__":
    run()
