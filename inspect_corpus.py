"""
One-off corpus inspection — characterize the Digital Payments vector DB content
so we can design recency-sensitive eval queries grounded in actual data.

What it prints:
  1. Total counts (pages, embeddings, age stats)
  2. Date distribution (how many pages per age bucket)
  3. Most recent pages (last 30d, 90d) — candidates for "fresh" content tests
  4. Topic clusters where multiple pages span very different ages
     (where recency CAN have an effect)
  5. Topics that exist ONLY in old content (where recency cannot help)
"""

import os, sys, re
from collections import defaultdict
from datetime import datetime, timezone

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("ERROR: psycopg2 not installed")
    sys.exit(1)

try:
    from dotenv import load_dotenv; load_dotenv()
except ImportError:
    pass


PROJECT_ID = "280e0adb-9dc4-4556-8c9f-c1521fc29ab4"


def get_conn():
    return psycopg2.connect(
        host=os.getenv("DATABASE_HOST"),
        port=os.getenv("DATABASE_PORT", "5432"),
        database=os.getenv("DATABASE_NAME", "postgres"),
        user=os.getenv("DATABASE_USER", "postgres"),
        password=os.getenv("DATABASE_PASSWORD", ""),
        connect_timeout=10,
    )


def section(title):
    print("\n" + "=" * 80)
    print(f"  {title}")
    print("=" * 80)


def main():
    conn = get_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    # 1. Totals
    section("1. Corpus totals")
    cur.execute("""
        SELECT
          COUNT(*) AS embeddings,
          COUNT(DISTINCT source_id) AS unique_pages,
          COUNT(*) FILTER (WHERE source_updated_at IS NULL) AS null_ts,
          MIN(source_updated_at) AS oldest,
          MAX(source_updated_at) AS newest
        FROM document_embeddings
        WHERE project_id = %s AND source_type = 'confluence'
    """, (PROJECT_ID,))
    r = cur.fetchone()
    print(f"  embeddings:       {r['embeddings']}")
    print(f"  unique pages:     {r['unique_pages']}")
    print(f"  null source_ts:   {r['null_ts']}")
    print(f"  oldest:           {r['oldest']}")
    print(f"  newest:           {r['newest']}")

    # 2. Age distribution by bucket
    section("2. Page age distribution (unique pages, by source_updated_at)")
    cur.execute("""
        WITH pages AS (
          SELECT DISTINCT ON (source_id) source_id, title, source_updated_at
          FROM document_embeddings
          WHERE project_id = %s AND source_type = 'confluence'
          ORDER BY source_id, chunk_index
        )
        SELECT
          COUNT(*) FILTER (WHERE source_updated_at > NOW() - INTERVAL '30 days')   AS last_30d,
          COUNT(*) FILTER (WHERE source_updated_at > NOW() - INTERVAL '90 days')   AS last_90d,
          COUNT(*) FILTER (WHERE source_updated_at > NOW() - INTERVAL '180 days')  AS last_180d,
          COUNT(*) FILTER (WHERE source_updated_at > NOW() - INTERVAL '1 year')    AS last_1y,
          COUNT(*) FILTER (WHERE source_updated_at > NOW() - INTERVAL '2 years')   AS last_2y,
          COUNT(*) FILTER (WHERE source_updated_at > NOW() - INTERVAL '5 years')   AS last_5y,
          COUNT(*) FILTER (WHERE source_updated_at <= NOW() - INTERVAL '5 years')  AS older_than_5y,
          COUNT(*)                                                                  AS total
        FROM pages
    """, (PROJECT_ID,))
    r = cur.fetchone()
    total = r["total"] or 1
    for b in ("last_30d", "last_90d", "last_180d", "last_1y", "last_2y", "last_5y", "older_than_5y"):
        n = r[b]
        bar = "#" * int(50 * n / total)
        print(f"  {b:<16}  {n:>5}  {bar}")
    print(f"  {'TOTAL':<16}  {total:>5}")

    # 3. Most recent pages — fresh content candidates
    section("3. Most recent pages (top 25 — these are 'fresh' candidates)")
    cur.execute("""
        SELECT DISTINCT ON (source_id) source_id, title, source_updated_at,
                                       EXTRACT(EPOCH FROM (NOW() - source_updated_at))/86400 AS age_days
        FROM document_embeddings
        WHERE project_id = %s AND source_type = 'confluence'
          AND source_updated_at IS NOT NULL
        ORDER BY source_id, chunk_index
        LIMIT 999999
    """, (PROJECT_ID,))
    pages = list(cur.fetchall())
    # sort in python so DISTINCT ON works first
    pages.sort(key=lambda p: p["source_updated_at"] or datetime(1900, 1, 1, tzinfo=timezone.utc), reverse=True)
    for p in pages[:25]:
        age = int(p["age_days"]) if p["age_days"] is not None else "?"
        print(f"  {str(age) + 'd':>6}  {p['title'][:75]}  ({p['source_id']})")

    # 4. Topic clusters spanning ages — where recency CAN help
    # Group pages by the first 3 lowercase words of the title (cheap topic clustering).
    section("4. Topic clusters where pages span >2 years (recency-sensitive topics)")
    by_topic = defaultdict(list)
    stop = {"the", "a", "an", "of", "and", "in", "on", "for", "to", "with",
            "is", "what", "how", "do", "i", "you", "we", "are"}
    for p in pages:
        title = (p["title"] or "").lower()
        words = [w for w in re.findall(r"[a-z0-9]+", title) if w not in stop and len(w) >= 3]
        if len(words) < 2:
            continue
        key = " ".join(words[:3])
        by_topic[key].append((p["age_days"], p["title"], p["source_id"]))

    # Filter to topics with >= 3 pages where age range >= 2 years
    cross_age_topics = []
    for topic, lst in by_topic.items():
        if len(lst) < 3:
            continue
        ages = [a for a, *_ in lst if a is not None]
        if not ages:
            continue
        age_range = max(ages) - min(ages)
        if age_range >= 730 and min(ages) <= 365:
            cross_age_topics.append((age_range, topic, lst))

    cross_age_topics.sort(reverse=True)
    print(f"  Found {len(cross_age_topics)} cross-age topic clusters")
    for age_range, topic, lst in cross_age_topics[:15]:
        lst_sorted = sorted(lst, key=lambda x: x[0] or 9999)
        print(f"\n  TOPIC: '{topic}'  (age range {int(age_range)}d, {len(lst)} pages)")
        for age, title, sid in lst_sorted[:5]:
            print(f"     {int(age):>5}d  {title[:65]}")
        if len(lst) > 5:
            print(f"     ... and {len(lst) - 5} more")

    # 5. Topics ONLY in old content — recency cannot help
    section("5. Sample topics with NO recent content (recency irrelevant)")
    old_only_topics = []
    for topic, lst in by_topic.items():
        if len(lst) < 3:
            continue
        ages = [a for a, *_ in lst if a is not None]
        if not ages:
            continue
        if min(ages) > 1000:  # newest version of this topic is >2.7 years old
            old_only_topics.append((min(ages), topic, lst))
    old_only_topics.sort()
    for newest_age, topic, lst in old_only_topics[:6]:
        print(f"\n  TOPIC: '{topic}'  (newest {int(newest_age)}d old, {len(lst)} pages)")
        for age, title, sid in sorted(lst, key=lambda x: x[0] or 9999)[:3]:
            print(f"     {int(age):>5}d  {title[:65]}")

    # 6. Chunks per page distribution — diagnoses the "same page 4 times in top-K" issue
    section("6. Chunks-per-page distribution (informs source-dedup strategy)")
    cur.execute("""
        SELECT chunks, COUNT(*) AS n_pages
        FROM (
            SELECT source_id, COUNT(*) AS chunks
            FROM document_embeddings
            WHERE project_id = %s AND source_type = 'confluence'
            GROUP BY source_id
        ) g
        GROUP BY chunks
        ORDER BY chunks
    """, (PROJECT_ID,))
    rows = cur.fetchall()
    cumul = 0
    for r in rows:
        cumul += r["n_pages"]
        if r["chunks"] <= 15 or r["chunks"] % 5 == 0:
            print(f"  {r['chunks']:>3} chunks/page  -> {r['n_pages']:>5} pages")
    cur.execute("""
        SELECT MAX(chunks) AS max_chunks, AVG(chunks)::int AS avg_chunks
        FROM (
            SELECT source_id, COUNT(*) AS chunks
            FROM document_embeddings
            WHERE project_id = %s AND source_type = 'confluence'
            GROUP BY source_id
        ) g
    """, (PROJECT_ID,))
    r = cur.fetchone()
    print(f"\n  max chunks/page: {r['max_chunks']}, avg chunks/page: {r['avg_chunks']}")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
