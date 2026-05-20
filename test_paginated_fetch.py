"""
End-to-end test of the new paginated get_space_pages logic.

WHAT THIS SCRIPT DOES (read-only — no DB writes, no embeddings, no sync)
------------------------------------------------------------------------
1. Looks up a user by name/email substring in the `users` table.
2. Pulls their stored Atlassian credentials (decrypted via KMS).
3. Lists Confluence spaces and finds the one matching the given name.
4. Calls confluence.get_space_pages(space_key, max_pages=None) — the
   new paginated implementation that should fetch ALL pages.
5. Reports: total pages, first 5, last 5, batch progress visible in logs.

USAGE
-----
  python test_paginated_fetch.py
  python test_paginated_fetch.py --user aman --space "digital payments"
  python test_paginated_fetch.py --user aman --space-key DP   # if you know the key
"""

import argparse
import logging
import sys

# Verbose logging so we can see the pagination loop in action.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("test_paginated_fetch")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from db_helper import get_db_connection, release_db_connection, get_user_atlassian_credentials
from psycopg2.extras import RealDictCursor
from services.confluence_service import ConfluenceService

import requests
from requests.auth import HTTPBasicAuth


def find_user(name_or_email: str) -> dict:
    """Find a user by case-insensitive substring match on name OR email."""
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, name, email, atlassian_email, atlassian_domain,
                       atlassian_linked_at
                FROM users
                WHERE LOWER(name)  LIKE %s
                   OR LOWER(email) LIKE %s
                ORDER BY atlassian_linked_at DESC NULLS LAST
                """,
                (f"%{name_or_email.lower()}%", f"%{name_or_email.lower()}%"),
            )
            rows = [dict(r) for r in cur.fetchall()]
    finally:
        release_db_connection(conn)

    if not rows:
        print(f"\nNo users found matching '{name_or_email}'.")
        sys.exit(1)

    if len(rows) > 1:
        print(f"\nMultiple users matched '{name_or_email}':")
        for i, u in enumerate(rows, 1):
            linked = "linked" if u.get("atlassian_linked_at") else "no atlassian creds"
            print(f"  [{i}] {u['name']:<25} {u['email']:<35} ({linked})")
        # Prefer the one with atlassian creds; if multiple, the most recently linked.
        with_creds = [u for u in rows if u.get("atlassian_linked_at")]
        chosen = with_creds[0] if with_creds else rows[0]
        print(f"\nAuto-selecting: {chosen['name']} ({chosen['email']})")
        return chosen

    return rows[0]


def find_space(confluence: ConfluenceService, space_name_query: str) -> dict:
    """List all spaces and return the one whose key or name matches the query."""
    print(f"\nListing all accessible Confluence spaces...")
    all_spaces = confluence.get_spaces()
    print(f"  Got {len(all_spaces)} spaces")

    q = space_name_query.lower()
    matches = [
        s for s in all_spaces
        if q in s["key"].lower() or q in s["name"].lower()
    ]

    if not matches:
        print(f"\nNo space found matching '{space_name_query}'. Available spaces:")
        for s in all_spaces[:30]:
            print(f"  - key={s['key']:<15} name={s['name']}")
        if len(all_spaces) > 30:
            print(f"  ... and {len(all_spaces) - 30} more")
        sys.exit(1)

    if len(matches) > 1:
        print(f"\nMultiple spaces matched '{space_name_query}':")
        for i, s in enumerate(matches, 1):
            print(f"  [{i}] key={s['key']:<15} name={s['name']}")
        # Take the exact name match if any, otherwise the first one.
        exact = [s for s in matches if s["name"].lower() == q]
        chosen = exact[0] if exact else matches[0]
        print(f"\nAuto-selecting: key={chosen['key']} name={chosen['name']}")
        return chosen

    return matches[0]


def v2_count_pages_in_space(domain: str, email: str, api_token: str, space_key: str) -> dict:
    """
    Use the Confluence Cloud v2 API to count pages in a space.

    v2's /spaces/{id}/pages endpoint returns ALL pages in the space, INCLUDING
    pages that live inside Confluence Folders (the newer 2024+ content type).
    The v1 /content/page endpoint we use in production does NOT return
    folder-nested pages, so comparing the two counts tells us whether folders
    are being used in the space and how many pages we're missing.

    Returns dict with:
      space_id      — numeric space ID (v2)
      v2_page_count — total pages reported by v2
      sample_titles — first 5 titles for sanity-check
    """
    base = f"https://{domain}/wiki/api/v2"
    auth = HTTPBasicAuth(email, api_token)
    headers = {"Accept": "application/json"}

    # 1. Resolve space key -> numeric id (v2 endpoints use numeric IDs)
    r = requests.get(
        f"{base}/spaces",
        params={"keys": space_key, "limit": 1},
        headers=headers, auth=auth, timeout=30,
    )
    r.raise_for_status()
    payload = r.json()
    if not payload.get("results"):
        raise RuntimeError(f"v2 API: space key '{space_key}' not found")
    space_id = payload["results"][0]["id"]
    logger.info(f"[v2] resolved space_key='{space_key}' -> space_id={space_id}")

    # 2. Page through /spaces/{id}/pages using cursor pagination (Link header)
    all_titles = []
    next_url = f"{base}/spaces/{space_id}/pages?limit=250"
    batch_num = 0
    while next_url:
        batch_num += 1
        r = requests.get(next_url, headers=headers, auth=auth, timeout=30)
        r.raise_for_status()
        body = r.json()
        results = body.get("results", [])
        all_titles.extend(p.get("title", "") for p in results)
        logger.info(f"[v2] batch {batch_num}: {len(results)} pages (running total={len(all_titles)})")

        # v2 cursor pagination: next URL is in _links.next (relative path)
        next_rel = (body.get("_links") or {}).get("next")
        if next_rel:
            # _links.next is path-relative, like "/wiki/api/v2/spaces/.../pages?cursor=..."
            next_url = f"https://{domain}{next_rel}" if next_rel.startswith("/") else next_rel
        else:
            next_url = None

    return {
        "space_id": space_id,
        "v2_page_count": len(all_titles),
        "sample_titles": all_titles[:5],
    }


def run_test(user_query: str, space_query: str = None, space_key: str = None):
    # 1. User + credentials
    print("=" * 70)
    print("STEP 1 — Look up user and decrypt Atlassian credentials")
    print("=" * 70)
    user = find_user(user_query)
    print(f"  user_id: {user['id']}")
    print(f"  name:    {user['name']}")
    print(f"  email:   {user['email']}")
    print(f"  linked:  {user.get('atlassian_linked_at')}")

    creds = get_user_atlassian_credentials(user["id"])
    if not creds or not creds.get("atlassian_api_token"):
        print(f"\nERROR: user '{user['name']}' has no usable Atlassian credentials.")
        sys.exit(1)
    print(f"  atlassian_domain: {creds['atlassian_domain']}")
    print(f"  atlassian_email:  {creds['atlassian_email']}")
    print(f"  api_token:        {'*' * 8}{creds['atlassian_api_token'][-4:]} (decrypted)")

    # 2. Confluence client
    domain = creds["atlassian_domain"].replace("https://", "").replace("http://", "")
    confluence = ConfluenceService(
        domain=domain,
        email=creds["atlassian_email"],
        api_token=creds["atlassian_api_token"],
    )

    # 3. Resolve space key
    print("\n" + "=" * 70)
    print("STEP 2 — Resolve target space")
    print("=" * 70)
    if space_key:
        chosen_key = space_key
        chosen_name = "(from --space-key)"
    else:
        space = find_space(confluence, space_query)
        chosen_key = space["key"]
        chosen_name = space["name"]
    print(f"  Using space_key = '{chosen_key}'  (name='{chosen_name}')")

    # 4. Run the new paginated fetch
    print("\n" + "=" * 70)
    print("STEP 3 — Fetch ALL pages via NEW paginated get_space_pages")
    print("=" * 70)
    print("  (watch the [get_space_pages] log lines — each = one paginated batch)\n")

    pages = confluence.get_space_pages(chosen_key, max_pages=None)

    # 5. Report
    print("\n" + "=" * 70)
    print("RESULTS")
    print("=" * 70)
    print(f"  Total pages fetched: {len(pages)}")
    if pages:
        print(f"\n  First 5 pages:")
        for p in pages[:5]:
            print(f"    - id={p.get('id'):<12} title={p.get('title')}")
        if len(pages) > 10:
            print(f"\n  Last 5 pages:")
            for p in pages[-5:]:
                print(f"    - id={p.get('id'):<12} title={p.get('title')}")

    # 6. Cross-check against v2 API to detect folder-nested pages we'd be missing
    print()
    print("=" * 70)
    print("STEP 4 -- Cross-check against Confluence v2 API")
    print("=" * 70)
    print("  v2 /spaces/{id}/pages returns pages INCLUDING those nested inside")
    print("  Confluence 'Folders' (the newer 2024+ content type). The v1 endpoint")
    print("  we use in production does NOT return folder-nested pages.")
    print()
    try:
        v2 = v2_count_pages_in_space(domain, creds["atlassian_email"], creds["atlassian_api_token"], chosen_key)
        v1_count = len(pages)
        v2_count = v2["v2_page_count"]
        delta = v2_count - v1_count

        print(f"  Our sync's get_space_pages():    {v1_count} pages")
        print(f"  Independent v2 control fetch:    {v2_count} pages")
        print(f"  delta (pages our sync misses):   {delta}")
        print()

        if delta == 0:
            print("  VERDICT: counts match -> our sync fetches 100% of the space's pages,")
            print("  including any folder-nested content. No gap.")
        elif delta > 0:
            pct = (delta / v2_count) * 100 if v2_count else 0
            print(f"  VERDICT: control reports {delta} MORE pages ({pct:.1f}% of total).")
            print(f"  Our sync is missing those pages -- investigate get_space_pages logic.")
        else:
            print(f"  Unexpected: our sync has MORE pages than v2 control (delta={delta}).")
            print(f"  Likely a permissions or content-type scope difference. Investigate.")
    except Exception as e:
        print(f"  v2 cross-check FAILED: {type(e).__name__}: {e}")
        print(f"  Skip if you're not authorised for v2 API access; main v1 result still valid.")

    print()
    print("INTERPRETATION")
    print("-" * 70)
    print("  Compare the 'Total pages fetched' number above against Confluence UI:")
    print(f"    - Open https://{domain}/wiki/spaces/{chosen_key} in a browser")
    print( "    - Go to 'Space settings -> Content' (or 'Pages')")
    print( "    - The page count there should now equal the number printed above.")
    print()
    print("  In the logs above you should see multiple '[get_space_pages] ... fetched batch")
    print("  of 100' lines for any space larger than 100 pages -- that's the pagination")
    print("  loop in action. The OLD code only ever made one HTTP call.")
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(description="Test new paginated get_space_pages")
    parser.add_argument("--user", default="aman",
                        help="Username, email, or substring to look up in users table (default: aman)")
    parser.add_argument("--space", default="digital payments",
                        help="Space name or substring to match (default: 'digital payments')")
    parser.add_argument("--space-key", default=None,
                        help="Skip space lookup and use this exact space key (e.g. 'DP')")
    args = parser.parse_args()
    run_test(args.user, args.space, args.space_key)


if __name__ == "__main__":
    main()
