/*
OUTLINE — markdown-master walker source

Phase 2+ of the markdown-master outline refactor. This file is
consumed by the walker
(`pubs_emitter.section_walker.walk_section_prose`) when
`--use-markdown-master` is set. Headings here become the rendered
packet's outline; !DIRECTIVE! lines dispatch to Python renderers
registered in `pubs_emitter.directives.DIRECTIVES`.

In Phase 2 this file contains only the C.19 Patents stanza — the
walker emits THAT chunk while the legacy emit-order block in
`rtf.write_rtf` handles every other section. The legacy emit-order
block skips C.19 when `--use-markdown-master` is set so the section
isn't double-emitted.

Phase 3 migrates more sections (each adds a stanza here + a
directive registration + a skip in the legacy emit-order block).
Phase 4 turns this file into the document's master outline —
`section-prose.md` then becomes a free-form-prose sidecar merged
into here.

Heading-depth → renderer mapping:
  #     Roman section heading      (III. / V.)
  ##    Group heading              (A. / B. / C.)
  ###   Section-level heading      (A.1 / C.19 / B.3)
  ####  Sub-section heading        (A.1.1 / C.16.2.3)

Directives accept token form [A-Z_][A-Z0-9_]* on their own line.
Tokens mid-paragraph render as literal text.

This block is editor-only — stripped before parsing along with any
other comment block in the file. Use the convention to leave
authoring guidance / template prompts / reminders alongside the
real content. Avoid nested close-comment markers inside a block —
the strip is non-greedy and would close at the first one.
*/

### C.1 Key Scholarly Publications or Patents.

!KEY_WORKS!

### C.2 Refereed journal papers.

!JOURNALS!

### C.3 Books and chapters in books.

!BOOKS_AND_CHAPTERS!

### C.4 Refereed conferences, symposium papers or other refereed reports.

!CONFERENCES_AND_WORKSHOPS!

### C.5 Other publications and products.

!OTHER_PUBLICATIONS!

### C.6 Invited external keynote/conference/symposium/colloquium/seminar presentations.

!INVITED_TALKS!

### C.7 Leadership roles in government or professional organizations.

!LEADERSHIP_ROLES!

### C.8 Appearances in media interviews and other coverage.

!MEDIA_APPEARANCES!

### C.9 Selected contributed conference/symposium presentations where the candidate was the presenter.

!CONFERENCE_PRESENTATIONS!

### C.10 Externally sponsored grants as PI, or Purdue lead on multi-institution grants.

!GRANTS_PI!

### C.11 Externally sponsored grants as Co-PI or Co-I.

!GRANTS_COPI!

### C.12 External gifts and voluntary support.

!GIFTS!

### C.13 Internal competitive grants as PI or Co-PI.

!INTERNAL_GRANTS!

### C.14 Graduate students advised.

!GRADUATE_STUDENTS!

### C.15 Mentoring of postdoctoral and visiting faculty scholars the candidate has directly supervised.

!POSTDOCS_VISITING!

### C.16 Graduate or undergraduate student mentoring activities and outcomes.

!UNDERGRAD_STUDENTS_TABLE!

#### C.16.1 Overview

#### C.16.2 Undergraduate Student Mentoring

#### C.16.2.1 Vertically Integrated Projects

#### C.16.2.2 Other Undergraduate Research Pathways.

!UNDERGRAD_PATHWAYS!

#### C.16.2.3 Undergraduate Research Products and Authorship.

!UNDERGRAD_PRODUCTS!

#### C.16.2.4 Undergraduate Awards, Fellowships, and Career Development.

!UNDERGRAD_AWARDS!

#### C.16.3 Graduate Student Mentoring

#### C.16.3.1 Thesis Advising and Research Supervision

#### C.16.3.2 Graduate Student Awards and Fellowships.

!GRADUATE_AWARDS!

### C.17 Courses taught at Purdue and elsewhere and teaching scores.

!COURSES_TAUGHT!

### C.18 Course development, within Purdue, or external short courses and workshops taught.

!COURSE_DEVELOPMENT!

### C.19 Issued U.S. and International Patents.

!PATENTS_TABLE!

### C.20 Major entrepreneurial activities.

!ENTREPRENEURIAL_ACTIVITIES!

### C.21 Technology transfer to industry practice, non-profits, or government policy.

!TECHNOLOGY_TRANSFER!

### C.22 Software products.

!SOFTWARE_PRODUCTS!

### C.23 Service to Purdue.

!UNIVERSITY_SERVICE!

### C.24 Service to the profession through professional societies.

!PROFESSION_SERVICE!

### C.25 Service to State, Nation, or International Organizations.

!NATIONAL_SERVICE!

### C.26 Other external service activities to the profession not noted above.

!OTHER_SERVICE!
