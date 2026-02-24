#!/usr/bin/env python3
"""
SEC EDGAR 10-K Section Extractor
=================================
Extracts Item 1 (Business), Item 1A (Risk Factors), and Item 7 (MD&A) from
the latest 10-K annual filing for any US public company ticker.

Outputs:
  - Individual text files: Item1.txt, Item1A.txt, Item7.txt
  - Structured JSON:       output.json
  - Excel workbook:        output.xlsx
  - Console DataFrame preview

Usage:
    python extractor.py                     # defaults to AAPL
    python extractor.py --ticker MSFT
    python extractor.py --ticker AAPL --output-dir ./output

Dependencies (all free / open-source):
    pip install requests beautifulsoup4 lxml pandas openpyxl
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup, NavigableString

# ---------------------------------------------------------------------------
# SEC EDGAR requires a proper User-Agent header identifying the caller.
# Using a generic but valid format: "CompanyName AdminEmail"
# ---------------------------------------------------------------------------
HEADERS = {
    "User-Agent": "AlphaNumeriQ-10K-Extractor admin@example.com",
    "Accept-Encoding": "gzip, deflate",
}

# ---------------------------------------------------------------------------
# Rate-limiting: SEC asks for <= 10 requests/second.  We wait between calls.
# ---------------------------------------------------------------------------
REQUEST_DELAY = 0.15  # seconds between HTTP requests


def _get(url: str) -> requests.Response:
    """HTTP GET with proper headers and rate-limiting."""
    time.sleep(REQUEST_DELAY)
    resp = requests.get(url, headers=HEADERS, timeout=60)
    resp.raise_for_status()
    return resp


# ============================= STEP 1 =====================================
# Resolve a stock ticker to a CIK (Central Index Key) that EDGAR uses.
# We use the official company_tickers.json maintained by SEC.
# ==========================================================================

def resolve_ticker_to_cik(ticker: str) -> str:
    """
    Resolve a ticker symbol (e.g. 'AAPL') to a zero-padded
    10-digit CIK string using the SEC company tickers JSON.
    """
    print(f"[1/6] Resolving ticker '{ticker}' to CIK ...")
    url = "https://www.sec.gov/files/company_tickers.json"
    data = _get(url).json()

    ticker_upper = ticker.upper()
    for entry in data.values():
        if entry.get("ticker", "").upper() == ticker_upper:
            cik = str(entry["cik_str"]).zfill(10)
            print(f"       CIK = {cik}  ({entry.get('title', '')})")
            return cik

    raise ValueError(
        f"Ticker '{ticker}' not found in SEC EDGAR company tickers."
    )


# ============================= STEP 2 =====================================
# Find the latest 10-K filing for the given CIK using the SEC submissions
# API and return the URL of the primary HTML document.
# ==========================================================================

def find_latest_10k_url(cik: str) -> str:
    """
    Query SEC EDGAR submissions API for the given CIK,
    find the most recent 10-K filing, and return the URL of
    its primary HTML document.
    """
    print(f"[2/6] Finding latest 10-K filing ...")
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    data = _get(url).json()

    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accession_numbers = recent.get("accessionNumber", [])
    primary_docs = recent.get("primaryDocument", [])

    # Find the first 10-K (not 10-K/A amendment)
    for i, form in enumerate(forms):
        if form == "10-K":
            acc_no = accession_numbers[i].replace("-", "")
            primary_doc = primary_docs[i]
            filing_url = (
                f"https://www.sec.gov/Archives/edgar/data/"
                f"{cik.lstrip('0')}/{acc_no}/{primary_doc}"
            )
            print(f"       Found 10-K: {accession_numbers[i]}")
            print(f"       Document:   {primary_doc}")
            return filing_url

    raise ValueError(f"No 10-K filing found for CIK {cik}.")


# ============================= STEP 3 =====================================
# Download the 10-K HTML document and parse it with BeautifulSoup.
# ==========================================================================

def download_filing_html(url: str) -> BeautifulSoup:
    """Download the 10-K HTML filing and return a BeautifulSoup object."""
    print(f"[3/6] Downloading 10-K filing HTML ...")
    resp = _get(url)
    soup = BeautifulSoup(resp.content, "lxml")
    print(f"       Downloaded ({len(resp.content):,} bytes)")
    return soup


# ============================= STEP 4 =====================================
# Extract Item 1, Item 1A, and Item 7 from the parsed HTML.
#
# ASSUMPTIONS (documented per task instructions):
#
# 1. Modern EDGAR filings (inline XBRL / iXBRL) use <span> and other inline
#    elements for section headings. Older filings may use <b>, <font>, <p>.
#    We look at all text-bearing elements.
#
# 2. The Table of Contents has links (<a> tags with href="#...") that
#    reference items. We skip these by ignoring <a> elements that are
#    hyperlinks (have an href attribute).
#
# 3. Section headings typically look like:
#      "Item 1. Business"
#      "Item 1A. Risk Factors"
#      "ITEM 7. MANAGEMENT'S DISCUSSION AND ANALYSIS ..."
#    We match with regex: "Item\s*1\." / "Item\s*1A" / "Item\s*7\."
#
# 4. A section runs from its heading until the next Item heading.
#    Specifically:
#      Item 1 ends when Item 1A starts
#      Item 1A ends when Item 1B or Item 2 starts
#      Item 7 ends when Item 7A or Item 8 starts
#
# 5. We extract plain text with basic whitespace normalisation.
#    Perfect formatting cleanup is not the goal.
# ==========================================================================

# Section definitions: (display_name, start_regex, end_regex)
# The start regex matches the section heading text.
# The end regex matches the heading of the NEXT section (used as boundary).
SECTION_DEFS = [
    (
        "Item 1 - Business",
        # "Item 1." but NOT "Item 1A" or "Item 10" etc.
        re.compile(r"item\s+1\.?\s", re.IGNORECASE),
        # Ends at Item 1A or Item 1B or Item 2
        re.compile(r"item\s+1\s*A\.?\s|item\s+2\.?\s", re.IGNORECASE),
    ),
    (
        "Item 1A - Risk Factors",
        re.compile(r"item\s+1\s*A\.?\s", re.IGNORECASE),
        # Ends at Item 1B or Item 2
        re.compile(r"item\s+1\s*B\.?\s|item\s+2\.?\s", re.IGNORECASE),
    ),
    (
        "Item 7 - MD&A",
        # "Item 7." but NOT "Item 7A"
        re.compile(r"item\s+7\.?\s", re.IGNORECASE),
        # Ends at Item 7A or Item 8
        re.compile(r"item\s+7\s*A\.?\s|item\s+8\.?\s", re.IGNORECASE),
    ),
]

# File names for text output
FILENAME_MAP = {
    "Item 1 - Business": "Item1.txt",
    "Item 1A - Risk Factors": "Item1A.txt",
    "Item 7 - MD&A": "Item7.txt",
}


def _is_toc_link(tag) -> bool:
    """
    Returns True if the tag is (or is inside) a Table-of-Contents hyperlink.
    TOC entries are <a> elements with an href pointing to an anchor (#...).
    Actual section headings are typically <span>, <b>, <p>, or <div> (not <a>).
    """
    # Check the tag itself and its ancestors for <a href="#...">
    node = tag
    for _ in range(5):
        if node is None:
            break
        if node.name == "a" and node.get("href", "").startswith("#"):
            return True
        node = node.parent
    return False


def _find_section_heading(soup: BeautifulSoup, pattern) -> "Tag | None":
    """
    Find the actual section heading element (not a TOC link) that matches
    the given regex pattern.

    Strategy: walk all text nodes, match against the pattern, skip TOC links,
    and prefer the element that is bold (font-weight 700) or that has a short
    heading-like text (not a paragraph mentioning the item in passing).
    """
    candidates = []

    for text_node in soup.find_all(string=True):
        text = text_node.strip()
        if not text:
            continue

        norm = re.sub(r"\s+", " ", text)
        if not pattern.search(norm):
            continue
        if len(norm) > 200:
            # Too long to be a heading — it's body text that mentions the item
            continue

        parent = text_node.parent
        if parent is None:
            continue

        # Skip TOC links
        if _is_toc_link(parent):
            continue

        # Score candidates: prefer bold (font-weight: 700) and shorter text
        is_bold = False
        style = parent.get("style", "")
        if "font-weight:700" in style or "font-weight: 700" in style:
            is_bold = True
        if parent.name in ("b", "strong"):
            is_bold = True

        candidates.append((parent, len(norm), is_bold))

    if not candidates:
        return None

    # Priority: bold headings first, then shortest text
    candidates.sort(key=lambda x: (not x[2], x[1]))
    return candidates[0][0]


def _extract_text_between(soup, start_tag, end_pattern) -> str:
    """
    Collect all visible text from start_tag (inclusive) until we encounter
    a non-TOC element whose text matches end_pattern.

    We iterate through all subsequent elements in document order.
    """
    texts = []
    collecting = True

    # Get all elements after start_tag in document order
    for element in start_tag.find_all_next(string=True):
        if not collecting:
            break

        parent = element.parent
        if parent is None:
            continue

        # Check if this element's text signals the next section
        text = element.strip()
        if not text:
            continue

        norm = re.sub(r"\s+", " ", text)

        # Check if we've reached the end boundary
        if end_pattern.search(norm) and len(norm) < 200:
            # Make sure it's not a TOC link (those appear before our section)
            if not _is_toc_link(parent):
                # Also verify it's a heading-like element, not body text
                # mentioning the next item in passing
                style = parent.get("style", "")
                is_bold = (
                    "font-weight:700" in style
                    or "font-weight: 700" in style
                    or parent.name in ("b", "strong")
                )
                # If it's bold or short (< 100 chars), treat as heading boundary
                if is_bold or len(norm) < 100:
                    break

        texts.append(text)

    result = "\n".join(texts)
    # Basic cleanup: collapse 3+ blank lines to 2
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


def extract_sections(soup: BeautifulSoup) -> dict:
    """
    Extract the three target sections from the 10-K filing.
    Returns a dict mapping section display names to their plain-text content.
    """
    print(f"[4/6] Extracting sections ...")
    sections = {}

    for section_name, start_pat, end_pat in SECTION_DEFS:
        start_tag = _find_section_heading(soup, start_pat)
        if start_tag is None:
            print(f"       WARNING: Could not find heading for '{section_name}'")
            sections[section_name] = ""
            continue

        heading_text = start_tag.get_text(separator=" ", strip=True)
        print(f"       Found heading: '{heading_text[:80]}'")

        content = _extract_text_between(soup, start_tag, end_pat)
        sections[section_name] = content

        chars = len(content)
        preview = content[:80].replace("\n", " ")
        print(f"       {section_name}: {chars:,} chars")

    return sections


# ============================= STEP 5 & 6 =================================
# Save extracted sections in multiple formats: .txt, .json, .xlsx
# and print a DataFrame preview to the console.
# ==========================================================================

def save_text_files(sections: dict, output_dir: Path):
    """Save each section as an individual plain-text file."""
    print(f"[5/6] Saving output files ...")
    for section_name, content in sections.items():
        fname = FILENAME_MAP.get(section_name, f"{section_name}.txt")
        path = output_dir / fname
        path.write_text(content, encoding="utf-8")
        print(f"       Saved: {path.name} ({len(content):,} chars)")


def save_json(sections: dict, output_dir: Path):
    """Save all sections as a single structured JSON file."""
    path = output_dir / "output.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sections, f, indent=2, ensure_ascii=False)
    print(f"       Saved: {path.name}")


def save_excel(sections: dict, output_dir: Path):
    """
    Save sections to output.xlsx with two columns:
      Column A - Section Name
      Column B - Extracted Content
    """
    rows = [{"Section Name": k, "Extracted Content": v} for k, v in sections.items()]
    df = pd.DataFrame(rows)
    path = output_dir / "output.xlsx"
    df.to_excel(path, index=False, sheet_name="10-K Sections")
    print(f"       Saved: {path.name}")


def print_dataframe_preview(sections: dict):
    """Print a DataFrame to console with truncated content preview."""
    print(f"\n[6/6] DataFrame Preview:")
    print("-" * 100)
    rows = []
    for name, content in sections.items():
        preview = (content[:150].replace("\n", " ") + " ...") if len(content) > 150 else content
        rows.append({"Section Name": name, "Content Preview": preview})

    df = pd.DataFrame(rows)
    pd.set_option("display.max_colwidth", 90)
    pd.set_option("display.width", 140)
    print(df.to_string(index=False))
    print("-" * 100)
    print()


# ============================= MAIN ========================================

def main():
    parser = argparse.ArgumentParser(
        description="Extract sections from the latest 10-K filing on SEC EDGAR."
    )
    parser.add_argument(
        "--ticker",
        default="AAPL",
        help="US stock ticker symbol (default: AAPL)",
    )
    parser.add_argument(
        "--output-dir",
        default=".",
        help="Directory to save output files (default: current dir)",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"  SEC EDGAR 10-K Section Extractor")
    print(f"  Ticker: {args.ticker.upper()}")
    print(f"{'='*60}\n")

    try:
        # Step 1: Resolve ticker to CIK
        cik = resolve_ticker_to_cik(args.ticker)

        # Step 2: Find the latest 10-K filing URL
        filing_url = find_latest_10k_url(cik)

        # Step 3: Download and parse the HTML
        soup = download_filing_html(filing_url)

        # Step 4: Extract the three sections
        sections = extract_sections(soup)

        # Validate that we got meaningful content
        empty = [k for k, v in sections.items() if len(v) < 100]
        if empty:
            print(f"\n  WARNING: these sections have very little content:")
            for s in empty:
                print(f"   - {s} ({len(sections[s])} chars)")
            print("   The filing HTML structure may differ from expected format.\n")

        # Step 5: Save output files
        save_text_files(sections, output_dir)
        save_json(sections, output_dir)
        save_excel(sections, output_dir)

        # Step 6: Print DataFrame preview
        print_dataframe_preview(sections)

        print("Done! All output files saved to:", output_dir.resolve())

    except requests.HTTPError as e:
        print(f"\nHTTP error: {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"\nError: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"\nUnexpected error: {e}", file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
