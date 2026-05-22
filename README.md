# Publications RTF Emitter

Generates a formatted RTF publication list from a BibTeX file, ready to paste into Word with formatting (bold, superscripts, hyperlinks) preserved.

## Files

| File | Purpose |
|------|---------|
| `pubs-emitter.py` | The script |
| `my_papers.bib` | Input BibTeX (gitignored — private) |
| `publications.rtf` | Generated output (paste into Word) |
| `doi_cache.sqlite` | DOI lookup cache (gitignored — regenerable) |
| `requirements.txt` | Python deps |

## Usage

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 pubs-emitter.py
```

Then open `publications.rtf` in Word (or paste its contents into an existing document) — formatting carries over.

## Input convention

Each BibTeX `journal` / `booktitle` field must begin with a bracketed acronym + year tag. Examples:

```
booktitle = {[ICSE'25] Proceedings of the International Conference on Software Engineering}
journal   = {[JSS'25] The Journal of Systems and Software}
journal   = {[arXiv'26] arXiv preprint arXiv:2605.10712}
```

The script parses the bracketed tag, strips the year, and looks the acronym up in the `RANKS` dict at the top of the script. Missing or unranked acronyms abort with a fatal error so you notice immediately rather than silently mis-categorizing.

## Author classification

Configured at the top of `pubs-emitter.py`:

- `ME` — your name variants → rendered **bold**
- `ADVISORS` — → `#` superscript
- `STUDENTS["G"]` / `STUDENTS["U"]` — grad / undergrad → `G` / `U` superscript
- Last author of every paper → `*` superscript

For each name in `STUDENTS`, the script auto-generates the `"Last, First"` reverse form to match however the name appears in BibTeX.

## DOI lookup

For conference and journal entries, the script queries Crossref first, then falls back to DBLP. Hits are cached in `doi_cache.sqlite` so re-runs don't re-fetch. For arXiv entries, remote lookup is skipped — the `doi` or `url` field already in the BibTeX is used directly (Google Scholar exports normally include it).

To force re-lookup of a specific paper, delete its row from the cache or delete the file entirely.

## Output structure

```
Journals
  Rank 1
    1. ...
  Rank 2
    1. ...
Conferences and Workshops
  Rank 1
    1. ...
  Workshop
    1. ...
arXiv / Preprints
  Preprint
    1. ...
```

Within each rank, citations are numbered. Author rendering:

- **Bold** if it's you
- Superscript suffix combining role markers: `G` (grad), `U` (undergrad), `#` (advisor), `*` (last author)
- DOI/URL appears as a clickable RTF hyperlink

## Logging

The script logs to stderr at INFO level by default. Set `LOG_LEVEL=DEBUG` to see DOI cache hits per paper:

```bash
LOG_LEVEL=DEBUG python3 pubs-emitter.py
```
