#!/usr/bin/env python3

import sqlite3
import requests
import re
import urllib.parse
from collections import defaultdict
import bibtexparser
import sys

BIB_FILE = "my_papers.bib"
OUT_FILE = "publications.rtf"
DB_FILE = "doi_cache.sqlite"

# ==========================================
# CONFIGURATION: AUTHOR LISTS
# ==========================================
ME = ["Davis, James C", "Davis, J.", "Davis, James"]
ADVISORS = ["Lee, Dongyoon"]

# You can tinker with these lists. The script will automatically generate
# reverse formats (e.g., "Kalu, Kelechi G.") before inserting into the DB.
STUDENTS = {
    "G": [ # PhDs, MScs, and Alumni (Grad)
        "Paschal C. Amusuo", "Dharun Anand", "Kelechi G. Kalu", "Purvish Jajal", 
        "Nick Eliopoulos", "Berk Çakar", "Huiyun Peng", "Daniel Lugo", "Drew Rozema",
        "Sofia Okorafor", "Chinenye Okafor", "Tanmay Singla", "Parth V. Patil", "Ishgair",
        "Wenxin Jiang", "Taylor Schorlemmer", "Jason Jones", "William Maxam", "Trey Maxam",
        "Geoffrey Cramer", "Ricardo Calvo"
    ],
    "U": [ # Undergraduates and Alumni (Undergrad)
        "Charlie Sale", "Arav Tewari", "Taylor Le Lievre", "Nathaniel Bielanski", 
        "Owen Cochell", "Ethan Burmane", "Sophie Chen", "Mohammed Ahmed", 
        "Mohammed Sameh", "Mingyu Kim", "Heesoo Kim", "Zhongwei Xu", 
        "Matthew Campbell", "Kyle Robinson", "Ananya Singh", "Evan Williams", 
        "David Li", "Zach Ghera", "Allen Liu", "Feny Patel", "Efe Barlas", 
        "Xin Du", "Diego Montes", "Naveen Vivek", "Anirudh Vegesana", "Vishnu Banna",
	"Jiashen Kuo", "Luke Chigges", "Kyung Ko", "Joseph Woo", "Anusha Sarraf",
	"Bhavesh Pareek", "Erik Kocinare"


# TODO: I am on item 43 from website, "Discrepancies"
# Once we are working on the full bib, need to add an assert that every student appears in at least one paper.

    ]
}

# ==========================================
# CONFIGURATION: VENUE RANKS
# ==========================================
RANKS = {
    # Rank 1
    "EMSE": "Rank 1", "ICSE": "Rank 1", "FSE": "Rank 1", "ESEC/FSE": "Rank 1",
    "ASE": "Rank 1", "ISSTA": "Rank 1", "JSS": "Rank 1",
    "S&P": "Rank 1", "USENIX Security": "Rank 1", "CCS": "Rank 1", "NDSS": "Rank 1",
    "WWW": "Rank 1", "EuroSys": "Rank 1", "USENIX ATC": "Rank 1", "VLDB": "Rank 1",
    "AAAI": "Rank 1", "ICLR": "Rank 1", "WACV": "Rank 1", "CVPR": "Rank 1",
    "EJEE": "Rank 1",

    # Rank 2
    "SANER": "Rank 2", "MSR": "Rank 2", "ESEM": "Rank 2",
    "AsiaCCS": "Rank 2", "EuroS&P": "Rank 2",
    "ICSOC": "Rank 2", "ISLPED": "Rank 2", "CVPR-Findings": "Rank 2",
    "Frontiers": "Rank 2", "JIEE": "Rank 2",

    # Rank 3
    "ASEE": "Rank 3",

    # Workshops
    "SERP4IoT": "Workshop", "ASE-Demos": "Workshop", "MSR-MiningChallenge": "Workshop",
    "FSE-Artifacts": "Workshop", "JAWs": "Workshop", "EuroSec": "Workshop",
    "SCORED": "Workshop", "SIGMOD-Demos": "Workshop", "DSN-Disrupt": "Workshop",
    "LCTES-WIP": "Workshop", "ACIEE": "Workshop", "ICSE-DREE": "Workshop",

    # Magazines
    "IEEE Design&Test": "Magazine", "Computer": "Magazine"
}

# ==========================================
# INITIALIZATION & DATABASE SETUP
# ==========================================
# Pre-process student names to catch both "First Last" and "Last, First"
PROCESSED_STUDENTS = {}
for s_type, names in STUDENTS.items():
    for name in names:
        PROCESSED_STUDENTS[name.lower()] = s_type
        if " " in name:
            parts = name.split()
            last_name = parts[-1]
            first_names = " ".join(parts[:-1])
            reversed_name = f"{last_name}, {first_names}"
            PROCESSED_STUDENTS[reversed_name.lower()] = s_type

# Keep SQLite ONLY for the DOI Cache
conn = sqlite3.connect(DB_FILE)
cur = conn.cursor()
cur.execute('''CREATE TABLE IF NOT EXISTS doi_cache 
               (title TEXT PRIMARY KEY, doi_or_url TEXT)''')
conn.commit()

# ==========================================
# DATABASE SETUP & POPULATION
# ==========================================
conn = sqlite3.connect(DB_FILE)
cur = conn.cursor()
cur.execute('''CREATE TABLE IF NOT EXISTS doi_cache 
               (title TEXT PRIMARY KEY, doi_or_url TEXT)''')
cur.execute('''CREATE TABLE IF NOT EXISTS ranks 
               (acronym TEXT PRIMARY KEY, rank TEXT)''')

# Reset and populate the students table every run
cur.execute('''DROP TABLE IF EXISTS students''')
cur.execute('''CREATE TABLE students (name TEXT PRIMARY KEY, type TEXT)''')

for s_type, names in STUDENTS.items():
    for name in names:
        # Insert "First Last"
        cur.execute("INSERT OR IGNORE INTO students (name, type) VALUES (?, ?)", (name, s_type))
        
        # Auto-generate and insert "Last, First M." variation for BibTeX matching
        if " " in name:
            parts = name.split()
            last_name = parts[-1]
            first_names = " ".join(parts[:-1])
            reversed_name = f"{last_name}, {first_names}"
            cur.execute("INSERT OR IGNORE INTO students (name, type) VALUES (?, ?)", (reversed_name, s_type))

conn.commit()

# ==========================================
# HELPER FUNCTIONS
# ==========================================
def is_me_or_advisor(bib_name, person_list):
    return any(p.lower() in bib_name.lower() for p in person_list)

def get_student_type(bib_name):
    """Queries the SQLite DB to check if the author is a known student."""
    cur.execute("SELECT name, type FROM students")
    for db_name, s_type in cur.fetchall():
        # Match exact string inclusion (case-insensitive) to prevent "Li" from matching "Lie"
        if db_name.lower() in bib_name.lower():
            return s_type
    return ""

def format_author_name(bib_name, is_last):
    """Converts 'Last, First Middle' -> 'F.M. Last' and applies RTF tags."""
    if ',' in bib_name:
        parts = [p.strip() for p in bib_name.split(',')]
        last = parts[0]
        firsts = parts[1].split() if len(parts) > 1 else []
    else:
        parts = bib_name.split()
        last = parts[-1]
        firsts = parts[:-1]
    
    initials = ".".join([f[0].upper() for f in firsts]) + "." if firsts else ""
    formatted_name = f"{initials} {last}".strip()

    # Apply RTF formatting
    is_me = is_me_or_advisor(bib_name, ME)
    if is_me:
        formatted_name = f"\\b {formatted_name}\\b0"
        
    superscripts = ""
    
    # Check DB for student status
    student_type = get_student_type(bib_name)
    if student_type:
        superscripts += student_type
        
    # Check Advisors
    if is_me_or_advisor(bib_name, ADVISORS): 
        superscripts += "#"
        
    # Final author asterisk
    if is_last: 
        superscripts += "*"
    
    if superscripts:
        formatted_name += f"\\super {superscripts}\\nosupersub"
        
    return formatted_name

def fetch_doi_or_url(title, authors):
    """Query Crossref first, fallback to DBLP for USENIX/others."""
    cur.execute("SELECT doi_or_url FROM doi_cache WHERE title=?", (title.lower(),))
    row = cur.fetchone()
    if row:
        return row[0]
        
    print(f"Fetching DOI for: {title[:50]}...")
    
    # 1. Try Crossref
    try:
        query = urllib.parse.quote(f"{title} {authors.split(' and ')[0]}")
        url = f"https://api.crossref.org/works?query={query}&select=DOI,title&rows=1"
        resp = requests.get(url, timeout=5).json()
        items = resp.get('message', {}).get('items', [])
        if items:
            link = f"https://doi.org/{items[0]['DOI']}"
            cur.execute("INSERT INTO doi_cache VALUES (?, ?)", (title.lower(), link))
            conn.commit()
            return link
    except Exception as e:
        pass

    # 2. Try DBLP
    try:
        q_title = urllib.parse.quote(title)
        url = f"https://dblp.org/search/publ/api?q={q_title}&format=json&h=1"
        resp = requests.get(url, timeout=5).json()
        hits = resp.get('result', {}).get('hits', {}).get('hit', [])
        if hits:
            link = hits[0]['info'].get('ee', '')
            if link:
                cur.execute("INSERT INTO doi_cache VALUES (?, ?)", (title.lower(), link))
                conn.commit()
                return link
    except Exception:
        pass

    # 3. Fallback
    cur.execute("INSERT INTO doi_cache VALUES (?, ?)", (title.lower(), ""))
    conn.commit()
    return ""

def get_venue_rank(acronym, title):
    """Returns the rank from the dictionary, or triggers a fatal error if missing."""
    if not acronym:
        print(f"\n[FATAL ERROR] Missing [ACRONYM] tag in venue field.")
        print(f"-> Paper: '{title}'")
        sys.exit(1)
    
    # Look for exact or case-insensitive match
    for known_acronym, rank in RANKS.items():
        if acronym.upper() == known_acronym.upper():
            return rank
            
    print(f"\n[FATAL ERROR] Unranked venue encountered: '{acronym}'")
    print(f"-> Paper: '{title}'")
    print("Please add it to the RANKS dictionary at the top of the script to continue.")
    sys.exit(1)

def escape_rtf(text):
    """Escapes curly braces and backslashes for raw RTF."""
    return text.replace('\\', '\\\\').replace('{', '\\{').replace('}', '\\}')

# ==========================================
# MAIN PROCESSING LOOP
# ==========================================
def main():
    with open(BIB_FILE, 'r', encoding='utf-8') as f:
        bib_database = bibtexparser.load(f)

    # Structure: dict[Category][Rank] = [citations...]
    publications = {
        "Journals": defaultdict(list),
        "Conferences and Workshops": defaultdict(list),
        "arXiv / Preprints": defaultdict(list)
    }

    for entry in bib_database.entries:
        title = entry.get('title', '').replace('{', '').replace('}', '').replace('\n', ' ')
        raw_authors = entry.get('author', '')
        year = entry.get('year', 'In Press')
        
        # 1. Determine Category and extract Venue details
        category = "Unknown"
        venue_str = ""
        
        if 'journal' in entry:
            venue_str = entry['journal'].replace('\n', ' ')
            category = "arXiv / Preprints" if "arxiv" in venue_str.lower() else "Journals"
        elif 'booktitle' in entry:
            venue_str = entry['booktitle'].replace('\n', ' ')
            category = "Conferences and Workshops"
        elif 'eprint' in entry:
            venue_str = "arXiv"
            category = "arXiv / Preprints"

        # 2. Parse Acronym from venue string "[ACRONYM'YY]"
        acronym = None
        # Match anything inside the first pair of brackets
        match = re.search(r'\[(.*?)\]', venue_str)
        if match:
            # Extracts everything inside (e.g., "USENIX Security'26" or "JSS 2025")
            raw_acronym = match.group(1).strip()
            
            # Safely strip trailing quotes, backticks, spaces, and the 2-4 digit year
            acronym = re.sub(r'[\'’`\s]+\d{2,4}$', '', raw_acronym).strip()
            
            # Remove the bracketed tag from the final string
            venue_str = venue_str.replace(match.group(0), '').strip()
            venue_str = re.sub(r'^[,:\s]+', '', venue_str)

        # Pass the title into the updated get_venue_rank function
        rank = get_venue_rank(acronym, title) if category != "arXiv / Preprints" else "Preprint"

        # 3. Format Authors
        author_list = raw_authors.split(' and ')
        formatted_authors = []
        for i, auth in enumerate(author_list):
            is_last = (i == len(author_list) - 1)
            formatted_authors.append(format_author_name(auth.strip(), is_last))
        
        authors_final = ", ".join(formatted_authors)

         # 4. Fetch DOI / Link
        if category == "arXiv / Preprints":
            # Bypass external APIs to prevent aggressive/wrong fuzzy matching.
            # Just pull it if Google Scholar already provided it in the .bib file.
            if 'doi' in entry:
                link = f"https://doi.org/{entry['doi'].replace('https://doi.org/', '')}"
            elif 'url' in entry:
                link = entry['url']
            else:
                link = ""
        else:
            link = fetch_doi_or_url(title, raw_authors)

        # 5. Build Citation String
        vol_issue = entry.get('volume', '')
        if 'number' in entry: vol_issue += f"({entry['number']})"
        pages = entry.get('pages', '')
        
        details = []
        if vol_issue: details.append(vol_issue)
        if pages: details.append(pages)
        details_str = ", ".join(details)
        if details_str: details_str = f", {details_str}"

        citation = f"{authors_final}, \"{escape_rtf(title)},\" {escape_rtf(venue_str)}{details_str} ({year})."
        if link:
            citation += f" {{\\field{{\\*\\fldinst HYPERLINK \"{link}\"}}{{\\fldrslt {link}}}}}"

        publications[category][rank].append(citation)

    # ==========================================
    # GENERATE RTF FILE
    # ==========================================
    print("\nGenerating RTF file...")
    with open(OUT_FILE, 'w', encoding='utf-8') as out:
        out.write(r"{\rtf1\ansi\ansicpg1252\deff0{\fonttbl{\f0\froman\fcharset0 Times New Roman;}}")
        out.write(r"{\colortbl;\red0\green0\blue255;}")
        
        for category, ranks in publications.items():
            if not ranks: continue
            
            out.write(f"\\par\\b\\fs28 {category}\\b0\\fs24\\par\\par\n")
            
            # Sort ranks (Custom sort so Rank 1 > Rank 2 > Rank 3 > Workshop > Unranked)
            for rank in sorted(ranks.keys()):
                out.write(f"\\b {rank}\\b0\\par\n")
                
                for idx, cit in enumerate(ranks[rank], 1):
                    out.write(f"{idx}. {cit}\\par\\par\n")
        out.write("}")
        
    print(f"Success! Check {OUT_FILE}")

if __name__ == "__main__":
    main()

