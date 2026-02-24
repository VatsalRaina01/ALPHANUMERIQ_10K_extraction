# SEC EDGAR 10-K Section Extractor

A Python script that automatically retrieves the latest 10-K annual filing from SEC EDGAR for any US public company and extracts key sections into structured output formats.

## Extracted Sections

- **Item 1** — Business
- **Item 1A** — Risk Factors
- **Item 7** — Management's Discussion & Analysis (MD&A)

## Quick Start

### Prerequisites

```bash
pip install requests beautifulsoup4 lxml pandas openpyxl
```

### Usage

```bash
# Default: Apple Inc. (AAPL)
python extractor.py

# Any US public company
python extractor.py --ticker MSFT

# Custom output directory
python extractor.py --ticker GOOGL --output-dir ./output
```

## Output Files

| File | Format | Description |
|---|---|---|
| `Item1.txt` | Plain text | Item 1 — Business |
| `Item1A.txt` | Plain text | Item 1A — Risk Factors |
| `Item7.txt` | Plain text | Item 7 — MD&A |
| `output.json` | JSON | All sections with section names as keys |
| `output.xlsx` | Excel | Two columns: Section Name, Extracted Content |

A DataFrame preview with truncated content is also printed to the console.

## How It Works

1. **Ticker → CIK** — Resolves the ticker symbol to SEC's Central Index Key using `company_tickers.json`
2. **CIK → Filing URL** — Queries the EDGAR submissions API to locate the latest 10-K
3. **Download HTML** — Fetches the full filing document with proper SEC-required User-Agent headers
4. **Extract Sections** — Uses regex-based heading detection on parsed HTML (BeautifulSoup), skipping Table of Contents links and preferring bold heading elements
5. **Save Outputs** — Generates `.txt`, `.json`, and `.xlsx` files

## Assumptions & Design Decisions

- Section headings are identified via regex matching on visible text content of HTML elements (e.g., `Item 1.`, `Item 1A.`, `Item 7.`)
- Table of Contents entries (anchor links with `href="#..."`) are skipped; only the actual body heading is used as the section start
- A section boundary ends when the next Item heading is encountered (e.g., Item 1 ends at Item 1A; Item 7 ends at Item 7A or Item 8)
- Bold elements (`font-weight: 700`) are preferred when selecting headings
- Plain text is extracted with basic whitespace normalization; perfect formatting cleanup is not the goal

## Libraries Used

All free and open-source:

- `requests` — HTTP requests to SEC EDGAR
- `beautifulsoup4` + `lxml` — HTML parsing
- `pandas` — DataFrame display
- `openpyxl` — Excel export
