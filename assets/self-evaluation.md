# B. SELF-EVALUATION

Plain-text statements for the Purdue tenure-packet B section. Each
section is a markdown `## B.X …` heading followed by free-form prose;
paragraphs are separated by blank lines. Anywhere in the prose you can
use `@bibkey` / `@id` / `@C.X.Y` cross-references — they're resolved at
build time the same way they are in YAML prose fields (grant
descriptions, key-work impacts, etc.). Soft word limits per Purdue
template are checked at build and warn if exceeded.

Drop this entire intro block (everything above the first `## B.1`)
once you've populated the sections — the parser ignores it but it's
visible in your editor as authoring guidance.

## B.1 Summary of achievements

*In 1,000 words or less, describe the most significant accomplishments
in discovery, learning, and engagement (written in the third person).
Where appropriate, cite references to relevant section(s) of this
document.*

**About the candidate**

Dr. James C. Davis is a software engineering scholar whose research and teaching develops and promotes evidence-based methods, tools, and frameworks that improve the reliability and security of modern software systems. His research has been funded by $#TOTAL_EXTERNAL_FUNDING in external funding (sources include the US NSF, Cisco, Rolls Royce, Google, and OpenAI). He has received $#TOTAL_EXTERNAL_FUNDING_AS_PI as PI. He received the NSF CAREER award in 2026. He has received four best paper awards, four distinguished reviewer awards, and Purdue’s highest award for early-career teaching. His students have received honors including the Qualcomm Innovation Fellowship, the Chateaubriand Fellowship, the NSF GRFP, the DOD NDSEG, and the NSF CSGrad4US fellowship.

**Accomplishments in discovery**

Dr. Davis’s research begins from observed failures in real engineering practice, studies those failures systematically, and translates the resulting insights into practical interventions for developers, infrastructure maintainers, and software-producing organizations. His work is organized around three major discovery areas: regular expression engineering, software infrastructure security, and reuse of pre-trained deep neural network models.

Across these discovery areas, Davis has built a broad and productive research record. His work spans software engineering, cybersecurity, and systems, with publications at Tier-1 venues in software engineering (e.g., ICSE, FSE), cybersecurity (e.g., IEEE S&P, USENIX Security), and computer systems (e.g., WWW, EuroSys, USENIX ATC, VLDB), as well as machine learning (e.g., ICML, AAAI). His record includes #NUM_PEER_REVIEWED_WORKS peer-reviewed works, #NUM_WORKS_AT_PURDUE at Purdue, including #NUM_TIER_1 Tier-1 and #NUM_TIER_2 Tier-2 conference or journal papers. Of these papers, #NUM_STUDENT_LED were led by his students, including #NUM_STUDENT_LED_TIER_1 Tier-1 papers. His record also includes #NUM_PATENTS U.S. patents with IBM, and one provisional patent from his time at Purdue.  

*Regular expressions:* In regular expression engineering, Davis has helped establish Regular Expression Denial of Service (ReDoS) as a broad software engineering and security problem. His work characterized how real regexes and regex engines depart from textbook assumptions (@davis2019aren), showed that ReDoS is widespread across mainstream engines and real software projects (@davis2018impact), and developed practical defenses at both the engine and application levels (@davis2021using, @hassan2023improving). He contributed the first characterization of ReDoS-inducing behavior due to backreferences (@liu2026regular). This research direction has produced two distinguished paper awards (@davis2018impact, @michael2019regexes), changes in major programming languages (e.g., Ruby and C#-Dotnet), and has been supported by an NSF SaTC Small award on which Davis served as PI (@nsf-satc-regex-2022).

*Engineering with pre-trained models:* Davis was among the first scholars to frame pre-trained deep learning model reuse as a software engineering problem. His group created and analyzed early datasets of model reuse (@jiang2023ptmtorrent, @jiang2024peatmoss), studied how engineers exchange and adapt pre-trained models (@jiang2023empirical, @jiang2022empirical, @jiang2024challenges), characterized failures in model re-engineering and model conversion (@jajal2024interoperability), developed theories of model reuse (@davis2023reusing, @yasmin2025software), and developed automated techniques for detecting metadata and naming inconsistencies (@jiang2025see). This work treats models as reusable software components that must be selected, adapted, exchanged, tested, and deployed systematically. This research direction has produced has helped define an emerging software engineering research area around model reuse and AI supply chains, and has received financial support including from Cisco (@cisco-ptm-2022) and the US NSF (CAREER-@nsf-career-ptm-2026, SaTC-Medium-@nsf-aigis-2026).

*Software infrastructure security:* Davis has advanced work on memory safety in embedded software and software provenance through signing. On memory safety, his group developed a systematic validation approach for embedded network stacks (@amusuo2023systematically) and formulated "unit proofing" as a pragmatic approach to bounded model checking for memory safety (@amusuo2025unit, @amusuo2026unit, @amusuo2026autosoup). This work exposed defects in major open-source real-time operating systems and led to changes in AWS's engineering process for AWS-FreeRTOS. On software signing, Davis's group provided a foundational view of software supply chain security (@okafor2022sok), measured signing adoption and quality (@schorlemmer2024signing, @schorlemmer2025establishing), then studied the organizational, usability, and deployment factors that shape adoption of signing technologies (@kalu2025industry, @kalu2026johnny, @kalu2026longitudinal). This work has been supported by funding from the US NSF (@pose-2022), Google (@google-signing-2023), Rolls-Royce (@embedded-fuzz-rr-2023, @embedded-fuzz-rr-2024, @embedded-fuzz-rr-2025), Cisco (@sigstore-cisco-2022), and OpenAI (@autoup-openai-2025).

**Accomplishments in learning**

Davis has led the development of a software engineering concentration within his department. He created a two-course sequence and overhauled an existing laboratory course (@C.18), and has been the course lead when these courses are offered with a Purdue-Indianapolis section. His materials have been referenced and used at several other institutions.

Davis also provides extensive integration of undergraduates into his research group, including #NUM_PAPERS_WITH_UNDERGRADUATE_COAUTHORS papers with undergraduates as lead or co-authors. He has published several research papers on engineering education (@ozkan2020expectations, @davis2022exploring, @joshi2024introducing, @tanay2024exploratory, @ozkan2024fostering), and received a best paper award at ASEE 2024 (@joshi2024introducing). His educational research has been supported by the NSF (@nsf-rfe-prompt-eng-2025), Intel (@intel-laptops-gift-2026), and internal awards (@ece-curric-reform-2021, @provost-genai-ed-2023).

Davis's teaching excellence has been recognized by many awards, most notably the Purdue-wide award for 2026 Excellence in Early Career Teaching.

**Accomplishments in engagement**

Davis's engagement activities extend his research into professional leadership, community service, and institutional service. In the research community, he organized the ICSE 2025 Software Mentoring Workshop and served as PI on the associated NSF student travel grant. He has served on #NUM_TIER_1_CONFERENCE_PROGRAM_COMMITTEES program committees for Tier-1 conferences, and has been recognized four times as a Distinguished Reviewer. He has also reviewed for leading journals and served three times as an NSF panelist. He is co-chair of the first Workshop on Research Software Supply Chain Security at IEEE eScience 2026 and the organizer of the "Do More With Less" workshop on Generative AI at ASEE 2026. Davis has given more than #NUM_INVITED_TALKS_ROUNDDOWN_NEAREST_TEN talks across academic, industry, and government venues, including at Dagstuhl, Carnegie Mellon University, Rolls-Royce, and Argonne National Laboratory.

At Purdue, Davis has contributed to the department through curriculum, accreditation, faculty hiring, and research-community building. He served as ABET self-study lead for the B.S. in Computer Engineering, served on faculty search committees, hosted a Purdue Engineering Distinguished Lecture Series speaker, organized a CAREER writing group, and launched a Software Systems Reading Group with participation from multiple research groups. He has given talks on the effective use of generative AI in engineering, research, and day-to-day work (cf. @SOFTWARE-DocAble, @SOFTWARE-PurdueTenureTemplateGenerator).

These activities reflect a consistent pattern: Davis builds structures that help Purdue University and his discipline strengthen software engineering research, education, mentoring, and professional practice.

## B.2 Impact of accomplishments

*In 250 words or less, describe impacts of the above noted achievements
to the discipline(s) and society (written in the third person). Where
appropriate, cite references to relevant section(s) of the document.*

(Replace this italic placeholder with the impact statement.)

TESTING TESTING

## B.3 Vision

*In 500 words or less, provide a statement of vision of future
activities and their potential impact (written in the third person).*

(Replace this italic placeholder with the vision statement.)

## B.4 Candidate comments on any external events or issues that have impacted their productivity

Dr. Davis's family welcomed a child in 2022. This affected his
fundraising as PI and reduced his publication productivity.

## B.5 Professional COVID-19 Impact Statement (optional)

N/A.
