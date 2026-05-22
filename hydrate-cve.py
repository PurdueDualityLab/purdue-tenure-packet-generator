#!/usr/bin/env python3
"""Hydrate a list of CVE IDs into BibTeX @misc entries via NIST NVD.

Output goes to stdout; redirect to a file and append to your bib.

Input format (stdin or --input FILE):
  - Plain CVE IDs (one per line or comma-separated), OR
  - Lines of the form '<org>: CVE-..., CVE-...' to attach an org label per CVE.

Examples:
  echo 'CVE-2024-38373' | python3 hydrate-cve.py
  python3 hydrate-cve.py --input cve-list.txt > /tmp/cves.bib

Environment:
  NVD_API_KEY  Optional; raises rate limit ~10x.
               Get one at https://nvd.nist.gov/developers/request-an-api-key
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time

import requests


NVD_BASE = "https://services.nvd.nist.gov/rest/json/cves/2.0"
# NVD public rate: 5 requests per rolling 30 s; with key: 50 per 30 s.
DELAY_PUBLIC_S = 6.5
DELAY_AUTHED_S = 0.7

CVE_RE = re.compile(r"CVE-\d{4}-\d+", re.IGNORECASE)

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stderr,
)
log = logging.getLogger("hydrate-cve")


def parse_input(text: str) -> list[tuple[str, str]]:
    """Parse input. Returns list of (organization, cve_id) tuples in input order.

    Strips trailing parenthesized counts like 'FreeRTOS (2):'.
    """
    pairs: list[tuple[str, str]] = []
    for raw in text.splitlines():
        line = raw.strip().rstrip(".")
        if not line:
            continue
        # Strip "(2)" style counts.
        line = re.sub(r"\s*\(\d+\)", "", line)
        # Split on first colon if the prefix isn't itself a CVE ID.
        if ":" in line and not CVE_RE.match(line):
            org, rest = line.split(":", 1)
            org = org.strip()
        else:
            org, rest = "", line
        for cve in CVE_RE.findall(rest):
            pairs.append((org, cve.upper()))
    return pairs


def fetch_cve(cve_id: str, api_key: str) -> dict | None:
    """Fetch one CVE from NVD. Returns the inner `cve` dict, or None on miss/error."""
    headers = {"apiKey": api_key} if api_key else {}
    try:
        resp = requests.get(NVD_BASE, params={"cveId": cve_id}, headers=headers, timeout=10)
        if resp.status_code != 200:
            log.warning("NVD HTTP %d for %s", resp.status_code, cve_id)
            return None
        vulns = resp.json().get("vulnerabilities") or []
        if vulns:
            return vulns[0].get("cve")
        log.warning("NVD has no record for %s", cve_id)
    except Exception as e:
        log.warning("NVD query failed for %s: %s", cve_id, e)
    return None


def first_sentence(text: str) -> str:
    """Crude first-sentence extractor for the bib title."""
    if not text:
        return ""
    return text.split(". ")[0].rstrip(".")


def render_bib(cve_id: str, org: str, cve_data: dict, me: str) -> str:
    """Emit a single bib @misc entry. User reviews + edits title afterward."""
    desc_full = next(
        (x["value"] for x in (cve_data.get("descriptions") or []) if x.get("lang") == "en"),
        "",
    )
    title = first_sentence(desc_full) or cve_id
    published = (cve_data.get("published") or "")[:10]
    year = published[:4] if published else ""

    # Bib key: davis<year>cve<digits>
    digits = re.sub(r"\D", "", cve_id)
    key = f"davis{year}cve{digits}"

    # Strip braces in title (BibTeX-significant); keep punctuation otherwise.
    title_bib = title.replace("{", "").replace("}", "").replace("\\", "")

    lines = [
        f"@misc{{{key},",
        f"  title = {{{title_bib}}},",
        f"  author = {{{me}}},",
        f"  year = {{{year}}},",
        f"  note = {{{cve_id}}},",
    ]
    if org:
        lines.append(f"  organization = {{{org}}},")
    lines.append(f"  url = {{https://nvd.nist.gov/vuln/detail/{cve_id}}},")
    lines.append("}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Hydrate a list of CVE IDs into BibTeX @misc entries via NIST NVD. "
            "Output goes to stdout; redirect to a file and append to your bib."
        ),
        epilog=(
            "Input format: plain CVE IDs, or lines of the form\n"
            "  '<organization>: CVE-..., CVE-..., ...'\n"
            "to attach an organization label per CVE.\n\n"
            "Environment:\n"
            "  NVD_API_KEY  Optional; raises rate limit ~10x. Get one at\n"
            "               https://nvd.nist.gov/developers/request-an-api-key"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--input",
        metavar="PATH",
        help="Path to input file (default: read stdin).",
    )
    parser.add_argument(
        "--me",
        default="Davis, James C",
        help="Author name to put in the bib entries (default: 'Davis, James C').",
    )
    args = parser.parse_args(argv)

    text = open(args.input).read() if args.input else sys.stdin.read()
    pairs = parse_input(text)
    log.info("Parsed %d CVE IDs", len(pairs))
    if not pairs:
        log.error("No CVE IDs found in input.")
        sys.exit(1)

    api_key = os.environ.get("NVD_API_KEY", "")
    delay = DELAY_AUTHED_S if api_key else DELAY_PUBLIC_S
    if not api_key:
        log.info(
            "No NVD_API_KEY set; using public rate (~5/30s = %.1fs between calls)", delay,
        )

    entries: list[str] = []
    for i, (org, cve_id) in enumerate(pairs):
        log.info(
            "[%d/%d] Fetching %s%s", i + 1, len(pairs), cve_id, f" ({org})" if org else "",
        )
        cve_data = fetch_cve(cve_id, api_key)
        if cve_data:
            entries.append(render_bib(cve_id, org, cve_data, args.me))
        if i < len(pairs) - 1:
            time.sleep(delay)

    print("\n\n".join(entries))
    log.info("Emitted %d bib entries", len(entries))


if __name__ == "__main__":
    main()
