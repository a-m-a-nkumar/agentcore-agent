"""
Fetch all content from a Confluence space and dump to a local folder.
Uses Atlassian credentials stored in the database.

Usage:
    python fetch_confluence_space.py --space_key sdlc_brd
    python fetch_confluence_space.py --space_key sdlc_brd --user_id <UUID>
    python fetch_confluence_space.py --space_key sdlc_brd --output_dir ./confluence_dump
"""

import argparse
import json
import os
import re
import logging
from pathlib import Path

from db_helper import get_user_atlassian_credentials
from services.confluence_service import ConfluenceService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DEFAULT_USER_ID = "572dcacd-9440-447f-8677-576fdfe24e5b"  # Kumar, Aman


def strip_html(text: str) -> str:
    """Remove HTML/Confluence XML tags and normalize whitespace."""
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s{2,}', ' ', text)
    return text.strip()


def fetch_space(space_key: str, user_id: str, output_dir: str):
    # 1. Get credentials from DB
    print(f"\n{'=' * 60}")
    print(f"  Fetching Confluence space: {space_key}")
    print(f"{'=' * 60}")

    print(f"\nLoading Atlassian credentials for user: {user_id}")
    creds = get_user_atlassian_credentials(user_id)
    if not creds:
        print(f"No Atlassian credentials found for user {user_id}")
        return

    domain = creds['atlassian_domain'].replace('https://', '').replace('http://', '')
    print(f"Domain: {domain}")
    print(f"Email:  {creds['atlassian_email']}")

    # 2. Initialize Confluence client
    confluence = ConfluenceService(
        domain=domain,
        email=creds['atlassian_email'],
        api_token=creds['atlassian_api_token']
    )

    # 3. Fetch all pages in space
    print(f"\nFetching pages from space '{space_key}'...")
    pages = confluence.get_space_pages(space_key, limit=1000)

    if not pages:
        print(f"No pages found in space '{space_key}'.")
        print("Check that the space key is correct (case-sensitive).")
        return

    print(f"Found {len(pages)} pages\n")

    # 4. Create output directory
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    # 5. Fetch full content for each page
    all_pages = []
    for i, page in enumerate(pages, 1):
        page_id = page.get('id')
        title = page.get('title', 'Untitled')
        print(f"  [{i}/{len(pages)}] Fetching: {title}")

        try:
            full_page = confluence.get_page_content(page_id)
            if not full_page:
                print(f"    -> Could not fetch content, skipping")
                continue

            raw_html = full_page.get('content', '')
            plain_text = strip_html(raw_html)

            page_data = {
                'id': page_id,
                'title': title,
                'version': full_page.get('version', 1),
                'html_length': len(raw_html),
                'text_length': len(plain_text),
                'content_html': raw_html,
                'content_text': plain_text,
            }
            all_pages.append(page_data)

            # Save individual page as text file
            safe_title = re.sub(r'[^\w\s-]', '', title).strip().replace(' ', '_')[:80]
            txt_file = out_path / f"{safe_title}.txt"
            with open(txt_file, 'w', encoding='utf-8') as f:
                f.write(f"Title: {title}\n")
                f.write(f"Page ID: {page_id}\n")
                f.write(f"Version: {full_page.get('version', '?')}\n")
                f.write(f"{'=' * 60}\n\n")
                f.write(plain_text)

            print(f"    -> {len(plain_text)} chars of text content")

        except Exception as e:
            print(f"    -> ERROR: {e}")

    # 6. Save summary JSON
    summary_file = out_path / "_summary.json"
    summary = {
        'space_key': space_key,
        'total_pages': len(all_pages),
        'pages': [
            {
                'id': p['id'],
                'title': p['title'],
                'version': p['version'],
                'html_length': p['html_length'],
                'text_length': p['text_length'],
            }
            for p in all_pages
        ]
    }
    with open(summary_file, 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2)

    # 7. Print summary
    total_text = sum(p['text_length'] for p in all_pages)
    print(f"\n{'=' * 60}")
    print(f"  DONE")
    print(f"{'=' * 60}")
    print(f"  Pages fetched:  {len(all_pages)}")
    print(f"  Total text:     {total_text:,} chars (~{total_text // 4:,} tokens)")
    print(f"  Output folder:  {out_path.resolve()}")
    print(f"  Summary file:   {summary_file.name}")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch all content from a Confluence space")
    parser.add_argument("--space_key", default="sdlc_brd", help="Confluence space key (default: sdlc_brd)")
    parser.add_argument("--user_id", default=DEFAULT_USER_ID, help="User ID for Atlassian credentials")
    parser.add_argument("--output_dir", default="./confluence_dump", help="Output directory (default: ./confluence_dump)")
    args = parser.parse_args()

    fetch_space(args.space_key, args.user_id, args.output_dir)
