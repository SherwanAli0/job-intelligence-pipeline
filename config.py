#
#  config.py  -  edit this file to customise
#

from datetime import date as _date

# -- One-time catch-up window -------------------------------------------------
# The owner did not job-hunt at all during the pivot week (and for most of it
# the filters were mis-tuned anyway), so Sunday 2026-08-16's scheduled runs
# sweep the last 7 days of postings ONCE. From Monday 2026-08-17 the cap
# self-reverts to 24 hours - no second deploy, nothing to remember to undo.
# Every scraper that takes a time-range parameter (JobSpy hours_old,
# LinkedIn f_TPR, Arbeitsagentur veroeffentlichtseit) derives it from here,
# so the window applies at the SOURCE too, not only at the filter.
CATCHUP_UNTIL = _date(2026, 10, 23)         # exclusive: one-off recovery run started late on 2026-10-21 UTC (= 22 Oct Berlin) after the full-time rule (seen_jobs reset, everything ever emailed blocked). Fargate's clock is UTC and the scrape crosses midnight, so the window covers UTC 21 and 22 Oct; normal again from the 23rd
_CATCHUP_HOURS = 168                        # 7 days
_NORMAL_HOURS = 24


def is_catchup(today=None) -> bool:
    """True on a catch-up day (before CATCHUP_UNTIL). Besides the wider
    freshness window, a catch-up run re-evaluates employer boards without an
    age cap and blocks everything ever emailed - see main._is_fresh_enough
    and main._ever_shown_keys. `today` is injectable for tests."""
    return (today or _date.today()) < CATCHUP_UNTIL


def max_posting_age_hours(today=None) -> int:
    """SOURCE window in hours: what scrapers ask JobSpy, LinkedIn and the
    Arbeitsagentur for. `today` is injectable for tests."""
    return _CATCHUP_HOURS if is_catchup(today) else _NORMAL_HOURS


# FILTER cap. The pipeline runs once a day, so a 24h filter cap equals the
# run interval with no slack: a posting published just after its source was
# scraped yesterday is just over 24h old at today's filter step, and a missed
# run lost a whole day. 48h covers both. Repeats stay blocked by seen_jobs and
# the emailed-key memory, so a wider window cannot resend anything.
_FILTER_HOURS = 48


def max_filter_age_hours(today=None) -> int:
    """Freshness cap applied by main._is_fresh_enough. `today` is injectable."""
    return _CATCHUP_HOURS if is_catchup(today) else _FILTER_HOURS


# Private scoring facts are required at runtime; no candidate CV is embedded.
# Select private S3 or JOBHUNTER_PROFILE_FILE. See aws/private-profiles.md.
from profile_loader import FACT_NAMES, ProfilesUnavailable, load_profiles as _load_private_profiles
_loaded_profiles = _load_private_profiles()
CV_PROFILE_AI = _loaded_profiles.values["AI"]
CV_PROFILE_ML = _loaded_profiles.values["ML"]
CV_PROFILE_DS = _loaded_profiles.values["DS"]
_CV_SHARED = _loaded_profiles.values["shared"]
CV_PROFILE = CV_PROFILE_AI
SCORER_PROMPT_CONTEXT = _loaded_profiles.prompt_context
if set(SCORER_PROMPT_CONTEXT) != {"intro", "work_match"}:
    raise ProfilesUnavailable("Private scoring prompt context is required")
# Candidate facts used in scoring prompts and rule messages (see profile_loader.FACT_NAMES).
# The private object must contain them before this code is deployed.
CANDIDATE_FACTS = _loaded_profiles.candidate_facts
if set(CANDIDATE_FACTS) != FACT_NAMES:
    raise ProfilesUnavailable("Private candidate facts are required")

# -- Search queries sent to job boards -----------------------------------------
# BALANCED across four equal tracks: AI · ML · Data Scientist · Data Analyst.
# JobSpy ROTATES halves per run (morning run = first half, afternoon run =
# second half - see _jobspy_active_queries), so every query gets coverage
# every day. The first 16 are interleaved 4×4 (AI, ML, DS, DA, repeat) so any
# prefix stays balanced. Post-scrape filters drop the off-targets.
SEARCH_QUERIES = [
    # Student and entry-level search mode; full-time
    # entry-level queries were added at the end of this list on 2026-10-14.
    # Balanced across the four tracks, German and English phrasings.
    "Werkstudent Data Science",
    "Werkstudent Machine Learning",
    "Werkstudent Künstliche Intelligenz",
    "Werkstudent Data Analyst",
    "Werkstudent AI",
    "Werkstudent Datenanalyse",
    "Werkstudent Python",
    "Werkstudent Business Intelligence",
    "Werkstudent Data Engineering",
    "Working Student Data Science",
    "Working Student Machine Learning",
    "Working Student AI",
    "Working Student Data Analytics",
    "Werkstudent Softwareentwicklung KI",
    "studentische Hilfskraft Data Science",
    "studentische Hilfskraft Machine Learning",
    "Werkstudent LLM",
    "Werkstudent NLP",
    "Werkstudent Computer Vision",
    "Werkstudent Deep Learning",
    "Werkstudent SQL",
    "Werkstudent Tableau",
    "Working Student GenAI",
    "Werkstudent MLOps",
    # Internships (added 2026-08-16) - both languages, all four tracks
    "Praktikum Data Science",
    "Praktikum Machine Learning",
    "Praktikum Künstliche Intelligenz",
    "Praktikum Datenanalyse",
    "Praktikum Informatik",
    "Pflichtpraktikum Informatik",
    "Praxissemester Data Science",
    "Internship Data Science",
    "Internship Machine Learning",
    "Internship Artificial Intelligence",
    "Data Science Intern",
    "Machine Learning Intern",
    # Part-time regular roles (added 2026-09-03)
    "Teilzeit Data",
    "Teilzeit Informatik",
    "Teilzeit Softwareentwicklung",
    "Teilzeit Data Analyst",
    "Part-time Data Analyst",
    "Part-time Software Developer",
    # Regular full-time roles (added 2026-10-14, owner: "add the full time
    # jobs as well"). Entry level, English-confirmed; the pre-pivot query set
    # trimmed to what fits one daily JobSpy run. NRW and remote anchors because
    # on-site is only wanted in NRW.
    "Junior AI Engineer Germany",
    "Junior Machine Learning Engineer Germany",
    "Junior Data Scientist Germany",
    "Junior Data Analyst Germany",
    "Junior Data Engineer Germany",
    "Entry Level Data Scientist Germany",
    "Entry Level Data Analyst Germany",
    "Graduate Machine Learning Engineer Germany",
    "Graduate Data Analyst Germany",
    "Junior Python Developer Germany English",
    "Applied AI Engineer Germany",
    "LLM Engineer Germany",
    "Data Analyst NRW English",
    "AI Engineer NRW English",
    "Data Scientist Düsseldorf English",
    "Data Scientist Köln English",
    "Remote Data Scientist Germany",
    "Remote Machine Learning Engineer Germany",
    "Remote Data Analyst Germany English",
    "Business Intelligence Analyst Germany English",
]

LOCATION = "Germany"

# Minimum Claude score (0-100) to include a job in the digest.
# 45 is the cutoff for "borderline worth looking at" - anything below is a
# weak/long-shot match. The notifier tiers the digest into bands:
#   70-100 → "Apply now"  (Band A)
#   55-69  → "Worth a look"  (Band B)
#   45-54  → "Long shots"  (Band C - collapsed at the bottom)
# Pre-screened jobs (Werkstudent, fluent-German, 5+ years exp, senior title,
# etc.) get score=0 and are excluded by the >=45 cutoff.
MIN_SCORE = 45

# Per-band caps for email size (no more global MAX_RESULTS truncation that
# silently hid borderline matches).
BAND_A_MAX = 20   # high-confidence - always show
BAND_B_MAX = 20
BAND_C_MAX = 15

# Legacy alias retained for any code still importing MAX_RESULTS.
MAX_RESULTS = BAND_A_MAX + BAND_B_MAX + BAND_C_MAX

# -- Companies with Greenhouse JSON API (free, no scraping needed) --------------
# Add more slugs from: boards-api.greenhouse.io/v1/boards/{SLUG}/jobs
# Verified: each slug returned 200 OK with > 0 jobs at time of addition.
# Location filter (_is_attendable_from_germany) drops anything that isn't
# Germany on-site / hybrid / EU-wide remote, so US-only roles auto-drop.
GREENHOUSE_SLUGS = [
    # -- Bonn commute belt (added from the 2026-08-15 regional sweep) ---------
    # Every slug below was verified live: HTTP 200 AND real postings AND the
    # right company. That last check matters - boards-api "metro" returns 200
    # with genuine jobs belonging to Unity One, a security firm in Idaho, not
    # METRO Düsseldorf, so it is deliberately NOT here.
    "trivago",         # 10 jobs, Düsseldorf - incl. Data Scientist, AI Search & Ranking
    "dept",            # 233 jobs, 5 in the Köln area
    "mindsquareag",    # 54 jobs - remote Pflichtpraktikum/Praxisphase IT Consulting
    "forvismazars",    # 187 jobs - Praktikum/Werkstudent Interne Revision & IT, Düsseldorf
    "thoughtworks",    # 40 jobs - Cologne office, English; watch for student roles
    "valtech",         # 143 jobs, 6 in the Köln area
    # -- German tech / e-commerce (original list)
    # NOTE (health sweep): zalando, deepl, deliveryhero, cognigy, biontech,
    # mangopay, personio all 404'd on Greenhouse (migrated ATS). Relocated
    # where possible: deepl -> ASHBY_SLUGS, deliveryhero -> SMARTRECRUITERS_SLUGS,
    # flixbus -> "flix" (slug renamed), personio already in PERSONIO_SLUGS.
    # zalando/biontech/cognigy/mangopay not on any free ATS at sweep time.
    "n26", "sumup",
    "celonis", "flix", "zattoo", "razor-group",
    # -- German / EU AI-first startups (verified)
    "parloa",          # 58 jobs, conversational AI agents
    "helsing",         # 105 jobs, defence AI
    "blackforestlabs", # 12 jobs, image generation (FLUX)
    "traderepublic",   # fintech with AI/data work
    "raisin",          # 49 jobs, Berlin fintech
    # -- Top AI/ML labs hiring EU-remote
    "anthropic",       # 397 jobs
    "scaleai",         # 180 jobs
    "togetherai",      # 54 jobs
    "snorkelai",       # 48 jobs
    "stabilityai",     # 12 jobs
    "labelbox",        # 10 jobs
    # -- Data & infra giants with EU hiring
    "databricks",      # 785 jobs (huge ML hiring)
    "mongodb",         # 415 jobs
    "datadog",         # 406 jobs
    "elastic",         # 152 jobs, Berlin office
    "grafanalabs",     # 152 jobs
    "cloudflare",      # 144 jobs
    "fastly",          # 67 jobs
    "newrelic",        # 78 jobs
    "sumologic",       # 47 jobs
    "honeycomb",       # 12 jobs
    "planetscale",     # 5 jobs
    "vercel",          # 78 jobs
    # -- Berlin / EU consumer & SaaS using Greenhouse
    "contentful",      # 102 jobs, Berlin tech
    "scout24",         # 37 jobs, Berlin
    "freenow",         # 63 jobs, mobility (Berlin/Hamburg)
    "wolt",            # 286 jobs, EU food delivery
    "konux",           # 1 job, Munich industrial AI
    "adahealth",       # 2 jobs, Berlin health AI
    "isaraerospace",   # 96 jobs, Munich rocket startup with heavy ML
    # -- International ML-heavy companies (will hit our EU filter)
    "stripe",          # 478 jobs
    "hellofresh",      # 396 jobs (Berlin HQ - moved from LEVER_SLUGS, more jobs here)
    "okta",            # 370 jobs
    "verkada",         # 301 jobs
    "lucidmotors",     # 261 jobs
    "remotecom",       # 253 jobs
    "coreweave",       # 248 jobs, GPU cloud - direct AI/ML fit
    "adyen",           # 248 jobs
    "brex",            # 237 jobs
    "airbnb",          # 234 jobs
    "xai",             # 221 jobs, Elon's AI
    "doctolib",        # 213 jobs, EU health
    "gitlab",          # 173 jobs (remote-first)
    "intercom",        # 168 jobs
    "block",           # 168 jobs (Square's parent)
    "pinterest",       # 162 jobs
    "twilio",          # 156 jobs
    "reddit",          # 153 jobs
    "fivetran",        # 136 jobs
    "asana",           # 136 jobs
    "robinhood",       # 134 jobs
    "lyft",            # 125 jobs
    "smartsheet",      # 124 jobs
    "instacart",       # 124 jobs
    "postman",         # 118 jobs
    "flexport",        # 97 jobs
    "psiquantum",      # 85 jobs, quantum
    "dialpad",         # 82 jobs
    "tripadvisor",     # 79 jobs
    "discord",         # 77 jobs
    "gusto",           # 74 jobs
    "monzo",           # 68 jobs, UK neobank
    "amplitude",       # 66 jobs
    "getyourguide",    # 65 jobs, Berlin
    "mercury",         # 56 jobs, fintech
    "carta",           # 56 jobs
    "cabify",          # 56 jobs
    "tailscale",       # 50 jobs
    "launchdarkly",    # 49 jobs
    "autoscout24",     # 41 jobs, Berlin
    "immoscout24",     # 39 jobs, Berlin
    "bird",            # 37 jobs
    "lucidsoftware",   # 37 jobs
    "project44",       # 37 jobs, logistics ML
    "mixpanel",        # 37 jobs
    "calendly",        # 21 jobs
    "skyscanner",      # 20 jobs
    "stockx",          # 20 jobs
    "tomorrow",        # 16 jobs, Berlin neobank
    "dominodatalab",   # 13 jobs
    "wunderkind",      # 12 jobs
    "wallapop",        # 11 jobs
    "bitwarden",       # 8 jobs
    "buynomics",       # 8 jobs, Cologne pricing AI
    "watershed",       # 8 jobs, climate tech
    "trivago",         # 7 jobs, Düsseldorf
    "inflectionai",    # 6 jobs
    "public",          # 5 jobs
    "warp",            # 5 jobs, AI terminal
    "lattice",         # 3 jobs
    "mercari",         # 3 jobs
    "solarisbank",     # 3 jobs, Berlin BaaS
    "netlify",         # 2 jobs
    "kayak",           # 1 job
    "lottoland",       # 1 job
    "medium",          # 1 job
    # -- Discovery sweep batch (verified live) --
    "tripactions",     # Navan - 194 jobs
    "clickhouse",      # ClickHouse - 175 jobs
    "tide",            # Tide - 121 jobs, UK neobank
    "coinbase",        # Coinbase - 71 jobs
    "dataiku",         # Dataiku - 59 jobs
    "algolia",         # Algolia - 44 jobs
    "bitpanda",        # Bitpanda - 43 jobs, Vienna crypto
    "cognite",         # Cognite - 43 jobs, industrial AI
    "auterion",        # Auterion - 41 jobs, drone OS
    "feedzai",         # Feedzai - 39 jobs, fraud ML
    "collibra",        # Collibra - 39 jobs, data governance
    "gocardless",      # GoCardless - 36 jobs
    "avimedical",      # Avi Medical - 34 jobs, Munich health
    "kinexon",         # Kinexon - 25 jobs, Munich sensors/ML
    "dkbcodefactory",  # DKB Code Factory - 21 jobs, Berlin banking tech
    "moonfare",        # Moonfare - 13 jobs, Berlin fintech
    # mangopay removed - 404 on Greenhouse (moved ATS, not on any free one)
    "truelayer",       # TrueLayer - 9 jobs
    "bondora",         # Bondora - 8 jobs
    "copperco",        # Copper - 7 jobs
    "clarityai",       # Clarity AI - 6 jobs, sustainability data
    "carbonfuture",    # Carbonfuture - 3 jobs, Freiburg climate
    # -- E-commerce sweep batch (verified live) --
    "catawiki",        # Catawiki - 59 jobs, marketplace
    "gostudent",       # GoStudent - 29 jobs, Vienna edtech
    "flaconi",         # Flaconi - 21 jobs, Berlin beauty e-com
    "refurbed",        # Refurbed - 17 jobs, Vienna circular
    "gymshark",        # Gymshark - 14 jobs
    "commercetools",   # Commercetools - 13 jobs, Munich commerce API
    "grover",          # Grover - 5 jobs, Berlin tech rental
    "airup",           # Air up - 5 jobs, Munich D2C
    "spryker",         # Spryker - 2 jobs, Berlin commerce
]

# -- Companies with Lever JSON API (free, no scraping needed) ------------------
# Verified: each slug returned 200 OK with > 0 jobs at time of addition.
# NOTE: hellofresh moved to GREENHOUSE_SLUGS (396 jobs there vs Lever).
LEVER_SLUGS = [
    # nuri removed - 404 (Nuri/Bitwala ceased operations)
    "mistral",         # 161 jobs, top AI lab - Paris, EU-hires
    "qonto",           # 35 jobs, French fintech, EU remote
    "moonpay",         # 24 jobs, fintech / crypto
    "neon",            # 14 jobs, Postgres-as-a-service
    "trustyou",        # 5 jobs, Munich travel tech
    # -- Discovery sweep batch (verified live) --
    "agicap",          # Agicap - 49 jobs
    "trustly",         # Trustly - 41 jobs, payments
    "alice-bob",       # Alice and Bob - 26 jobs, quantum (Paris)
    "doctrine",        # Doctrine - 20 jobs, legal AI
    "anybotics",       # ANYbotics - 12 jobs, robotics (Zurich)
    "younited",        # Younited - 8 jobs
    # -- E-commerce sweep batch (verified live) --
    "emma-sleep",      # Emma - 75 jobs, Frankfurt mattresses
    "finn",            # Finn - 41 jobs, Munich car subscription
    "veo",             # Veo - 23 jobs
    "vestiairecollective", # Vestiaire Collective - 12 jobs
    "factor",          # Factor - 11 jobs, meal kits
]

# -- Companies with Personio public XML feeds ----------------------------------
# Personio is the German Mittelstand's HR system. Each company exposes
# {slug}.jobs.personio.de/xml as a public feed (no auth, full descriptions).
# These are companies that 404 on Greenhouse - Personio catches them.
# Verified: each returned 200 OK with > 0 positions.
PERSONIO_SLUGS = [
    # -- Bonn commute belt (2026-08-15 regional sweep, all verified live) -----
    # Personio is honest about bad slugs: a nonexistent one 307-redirects
    # rather than returning an empty 200, so a live 200 with <position> rows
    # is real proof.
    "auxmoney-gmbh",       # 15, Düsseldorf fintech - runs Werkstudent Data Analytics
    "sipgate",             # 7, Düsseldorf - telephony/cloud, student roles
    "metro-markets-gmbh",  # 5, Düsseldorf - METRO's marketplace tech arm
    # Bonn itself - small boards, but they are the employers a Bonn student can
    # cycle to, and none of them syndicate reliably to the aggregators.
    "comma-soft",          # 20, Bonn - GenAI / MLOps / data platform consultancy
    "aktion-mensch",       # 8, Bonn - 4 live Werkstudent roles incl. IT Service Desk
    "high-office-it-gmbh", # 19, Bonn - IT services, several Werkstudent
    "haeger-consulting",   # 14, Bonn Bad Godesberg - data engineering
    "linkbroker",          # 3, Bonn-Ramersdorf - Werkstudent in Development/IT
    "eitco-gmbh",          # 1, Bonn - IT consultancy
    "i22",                 # 1, Bonn - digital agency
    "envelio",             # 13, Köln - energy-grid AI
    # Köln
    # NOTE: Personio slugs "dumont" and "circuly" return HTTP 200 with jobs and
    # look like the Köln companies of those names. They are blank-name demo
    # boards (no office set / "Pioneers Club"), so they are NOT here.
    "gesis",               # 12, Köln + Mannheim - Leibniz institute, 4 HiWi posts
    "appsfactory-gmbh",    # 25, Köln office - Werkstudent roles
    "areto",               # 7, Köln - data/BI consultancy (Databricks, Snowflake)
    "vytal",               # 2, Köln
    "foodforecast",        # 2, Köln - AI food-waste startup
    "effzeh",              # 14, 1. FC Köln - Werkstudent Digitale Projekte, analysis internships
    "trusteq-gmbh",        # 21, Köln office - AI Developer Intern, AI Strategy Intern (some ask C1)
    "maibornwolff",        # 47, Bonn office - software/AI consultancy
    "1komma5grad",     # 352 positions, solar/energy AI
    "westwing",        # 61, e-commerce
    "vivid",           # 19, Berlin fintech
    "merantix",        # 19, Berlin AI venture studio
    "pitch",           # 10, presentation SaaS
    "verivox",         # 9, comparison platform
    "deepset",         # 6, RAG / search AI
    "horizn-studios",  # 5, travel goods
    "snocks",          # 5
    "circula",         # 4, expense management
    "celonis",         # 3, process mining (also on Greenhouse)
    "wandelbots",      # 3, Dresden industrial robotics
    "candis",          # 3, fintech
    "audibene",        # 2, healthtech
    "limehome",        # 2, hospitality
    "finway",          # 2, fintech
    "penta",           # 1, banking
    "smava",           # 2, loan platform
    "personio",        # 1
    "ada",             # 1
    # -- Discovery sweep batch (verified live) --
    "360t",            # 360T - 23, Frankfurt FX trading
    "vantis",          # Vantis - 23
    "neoshare",        # Neoshare - 18
    "eqs-group",       # EQS Group - 18
    "bloomwell",       # Bloomwell - 13, health
    "centogene",       # Centogene - 12, genomics
    "planetafoods",    # Planet A Foods - 12, Munich foodtech
    "ottonova",        # ottonova - 11, Munich insurtech
    "data4life",       # Data4Life - 11, health data
    "carbmee",         # Carbmee - 11, climate
    "idealworks",      # Idealworks - 11, BMW robotics spinout
    "gridx",           # GridX - 10, energy
    "vodeno",          # Vodeno - 8
    "apheris",         # Apheris - 7, federated ML
    "tanso",           # Tanso - 6, climate
    "topi",            # Topi - 5, Berlin fintech
    "finoa",           # Finoa - 5, Berlin crypto custody
    "xempus",          # Xempus - 5
    "bonify",          # Bonify - 5, Berlin fintech
    "friendsurance",   # Friendsurance - 5
    "cellumation",     # Cellumation - 5, Bremen robotics
    "floy",            # Floy - 4, Munich medical AI
    "clark",           # Clark - 3, insurtech
    "finanzguru",      # Finanzguru - 3
    "payhawk",         # Payhawk - 3
    "tangany",         # Tangany - 3
    "hepster",         # hepster - 3
    "sevdesk",         # sevDesk - 3
    "crypto-finance",  # Crypto Finance - 3
    "finlex",          # Finlex - 3
    "temedica",        # Temedica - 3, Munich health
    "mediaire",        # Mediaire - 3, Berlin medical AI
    "climedo",         # Climedo - 3, health
    "dermanostic",     # Dermanostic - 3
    "vivira",          # Vivira - 3, digital therapeutics
    "infiniteroots",   # Infinite Roots - 3, Hamburg biotech
    "project-eaden",   # Project Eaden - 3, foodtech
    "iqm",             # IQM Quantum - 3
    "banxware",        # Banxware - 2
    "cashlink",        # Cashlink - 2
    "samedi",          # Samedi - 2, health
    "doctorly",        # Doctorly - 2, health
    "filics",          # Filics - 2, robotics
    "hypofriend",      # Hypofriend - 1
    "vara",            # Vara - 1, Berlin medical AI
    "kipu-quantum",    # Kipu Quantum - 1, Berlin quantum
    # -- E-commerce sweep batch (verified live) --
    "urbansportsclub", # Urban Sports Club - 38, Berlin fitness
    "tonies",          # Tonies - 13, audio for kids
    "everphone",       # Everphone - 9, Berlin device rental
    "connox",          # Connox - 4, Hannover home design
    "selfmade",        # Selfmade - 3
    "bbg",             # Berlin Brands Group - 3, e-com aggregator
    "cabify",          # Cabify - 3, mobility
    "momox",           # Momox - 2, Berlin recommerce
    "trbo",            # trbo - 1, Munich e-com personalization
    # -- Germany-wide English sweep 2026-09-07 (verified live) --
    "knime",           # KNIME - 10, Berlin/Konstanz data-science platform, English ads
]

# -- Workday CXS tenants (modern JSON API) -------------------------------------
# Each tuple: (tenant_slug, workday_region, site_name)
# Discovered manually by HTTP-testing - these all return 200 OK with jobs.
# The scraper pre-filters titles to AI/ML/data-relevant before fetching the
# full description per posting (cap 30 per tenant for sane runtime).
#
# NOTE: I tested ~50 candidate German DAX companies (Mercedes-Benz, BMW, VW,
# Audi, Porsche, Bayer, BASF, Henkel, Allianz, Deutsche Bank, Commerzbank,
# Lufthansa, Vodafone, Deutsche Telekom). NONE of them returned data via
# Workday - they either use SuccessFactors or their own bespoke ATS. The
# entries below are companies that DO work and have major German offices.
WORKDAY_CXS_TENANTS = [
    # (tenant, region, site_name) - total raw jobs at verification time
    ("nvidia",      "wd5",  "NVIDIAExternalCareerSite"),   # 2000  jobs - Munich office
    ("abbott",      "wd5",  "abbottcareers"),              # 2000  jobs - Wiesbaden
    ("citi",        "wd5",  "2"),                          # 2000  jobs - Frankfurt
    ("astrazeneca", "wd3",  "Careers"),                    # 1585  jobs - Wedel/Hamburg
    ("sanofi",      "wd3",  "SanofiCareers"),              # 1473  jobs - Frankfurt
    ("salesforce",  "wd12", "External_Career_Site"),       # 1441  jobs - Munich/Berlin
    ("adobe",       "wd5",  "external_experienced"),       # 1193  jobs - Hamburg/Munich
    ("philips",     "wd3",  "jobs-and-careers"),           # 1026  jobs - Hamburg/Aachen
    # Bonn-belt employers found in the 2026-09 sector sweep (verified live).
    ("debeka",      "wd3",  "Karriere"),                   # Koblenz - 13 Werkstudent/Praktikant
    ("creditreform","wd103","Verband_Creditreform"),       # Neuss - small, Werkstudent roles
    ("accenture",   "wd103","AccentureCareers", "", True),  # 2000 -> 243 DE with the country facet (2026-09-07)          # 15 Werkstudent; Düsseldorf 121 / Köln 12 roles
    ("kone",        "wd3",  "Careers"),                    # 1000  jobs - Hannover area
    ("novartis",    "wd3",  "Novartis_Careers"),           # 760   jobs - Nuremberg
    ("intel",       "wd1",  "External"),                   # 727   jobs - Munich
    ("gsk",         "wd5",  "GSKCareers"),                 # 708   jobs - Munich
    ("autodesk",    "wd1",  "Ext"),                        # 648   jobs - Munich
    ("pfizer",      "wd1",  "PfizerCareers"),              # 493   jobs - Berlin
    ("workday",     "wd5",  "Workday"),                    # 322   jobs - Munich
    # -- Germany-wide English sweep 2026-09-07 (verified live). The optional
    # 4th element is a Workday search string: these tenants hold thousands of
    # postings, and without it the 200-offset walk would see a random slice.
    # A 5th element True applies Workday's Germany country facet (tenants
    # that reject it with HTTP 400 fall back to the plain walk).
    ("stryker",     "wd1",  "StrykerCareers", "working student"),        # 78 hits (facet: 400) - Freiburg medtech internships in English
    ("harman",      "wd3",  "HARMAN",         "working student", True),  # 24 -> 9 DE hits - Garching/Karlsbad
    ("ag",          "wd3",  "Airbus",         "working student", True),  # 327 -> 114 DE hits - Hamburg/Manching/Stade
]

# -- Companies on SmartRecruiters (enterprise ATS) -----------------------------
# Used by Bosch, Continental, and many other industrial/consultancy giants.
# Public API at api.smartrecruiters.com/v1/companies/{slug}/postings, with
# country=de filter so we only get Germany-eligible jobs.
# The scraper pre-filters to AI/ML/data-relevant titles since Bosch alone has
# 4641 active jobs (across all functions). Only relevant ones reach Claude.
SMARTRECRUITERS_SLUGS = [
    # WARNING for anyone adding to this list: SmartRecruiters answers HTTP 200
    # with {"totalFound": 0} for slugs that do not exist, so a 200 alone proves
    # nothing. Henkel, Uniper, Ceconomy and MediaMarktSaturn were all rejected
    # on that basis during the 2026-08-15 sweep. Assert totalFound > 0.
    "METROMAKRO",      # 1613 jobs - METRO/METRO.digital, Düsseldorf tech org
    # Köln - the densest Werkstudent employers found in the whole sweep.
    "BarmeniaGothaerAG",        # 425 jobs, 17 Werkstudent (11 Köln) incl. KI-Automatisierung
    "StrerSECoKGaAStrerGruppe", # 235 - Ströer, incl. Werkstudent CRM-Datenanalyse Köln
    "Bertelsmann-Jobs",         # 909 - Bertelsmann/RTL Deutschland, Köln Picassoplatz
    "JobTeaser",                # 7 - Köln Friesenplatz
    # SmartRecruiters ids are CASE-SENSITIVE. "RedcarePharmacy" (CamelCase) is a
    # test board full of fake "Test Hire Sync" rows - never add it.
    "redcare-pharmacy",         # 154 DE jobs, 11 student in Cologne (Marketplace Technical Integration...)
    "SopraSteria1",             # 256 DE jobs - Werkstudent roles, many "bundesweit"
    "BoschGroup",      # 4641 total jobs, ~947 in Germany
    "deliveryhero",    # 1087 jobs - relocated from Greenhouse (404 there)
    "Continental",     # 1188 jobs
    "RolandBerger",    # 213 jobs, top European consultancy
    "Visa",            # 20 jobs
    "ContinentalAG",   # 2 jobs (different listing)
    # -- Discovery sweep batch (verified live) --
    "wise",            # Wise - 350 jobs
    "brainlab",        # Brainlab - 91 jobs, Munich medical tech
    "devexperts",      # Devexperts - 36 jobs, trading tech
    "ecovadis",        # EcoVadis - 32 jobs, sustainability ratings
    "kaiahealth",      # Kaia Health - 2 jobs
    "kadmos",          # Kadmos - 1 job
    # -- E-commerce sweep batch (verified live) --
    "home24",          # Home24 - 9 jobs, furniture e-com
    "picnic",          # Picnic - 2 jobs, online grocery
    # -- Germany-wide English sweep 2026-09-07 (verified live, country=de) --
    "Vattenfall",      # Vattenfall - 72 DE jobs; Working Student Data Engineer Berlin, Werkstudent IT Hamburg
]

# -- Major German companies - direct career page scraping ----------------------
# Restored per "attempt everything, tolerate errors" principle. Many of these
# are JavaScript-rendered SPAs so the generic HTML scraper often pulls only
# nav links rather than real job titles. That's expected and accepted; the
# scraper attempts each URL on every run and swallows failures cleanly.
# type: "workday" | "successfactors" | "generic"
COMPANY_PAGES = [
    {"name": "Siemens",            "url": "https://jobs.siemens.com/careers?location=Germany&search=data+science",                          "type": "generic"},
    # SAP retired 2026-10-04: this page returned 13 rows a run until
    # 2026-09-21 and nothing since (it is a SuccessFactors site whose markup
    # changed), and its query was full-time "data scientist" roles anyway.
    # The source name is in main._RETIRED_SOURCES so its alarm stays quiet.
    {"name": "BMW Group",          "url": "https://www.bmwgroup.jobs/de/en/jobfinder.html?search=data+science",                               "type": "generic"},
    {"name": "Bosch",              "url": "https://careers.bosch.com/en/jobs/?q=data+scientist&location=Germany",                              "type": "generic"},
    {"name": "Continental",        "url": "https://jobs.continental.com/en/search/?q=data+scientist&location=Germany",                         "type": "generic"},
    {"name": "Infineon",           "url": "https://www.infineon.com/cms/en/careers/job-opportunities/?search=data+scientist",                  "type": "generic"},
    {"name": "Deutsche Telekom",   "url": "https://careers.telekom.com/jobs?q=data+scientist&location=Germany",                                "type": "generic"},
    {"name": "E.ON",               "url": "https://careers.eon.com/jobs?q=data+scientist&location=Germany",                                    "type": "generic"},
    {"name": "TUI",                "url": "https://careers.tuigroup.com/jobs?q=data+scientist&location=Germany",                               "type": "generic"},
    {"name": "CHECK24",            "url": "https://careers.check24.de/?s=data",                                                                "type": "generic"},
    {"name": "Volkswagen",         "url": "https://www.volkswagenag.com/en/group/careers/job-portal.html",                                     "type": "generic"},
    {"name": "Allianz",            "url": "https://careers.allianz.com/search?q=data+scientist&country=DE",                                    "type": "generic"},
    {"name": "Munich Re",          "url": "https://careers.munichre.com/search?q=data+science&country=DE",                                     "type": "generic"},
    {"name": "Siemens Healthineers","url":"https://www.siemens-healthineers.com/en-de/careers/open-positions?q=data+science",                  "type": "generic"},
    {"name": "Siemens Advanta",    "url": "https://jobs.siemens.com/careers?location=Germany&search=analytics+AI",                             "type": "generic"},
]

# -- Ashby ATS slugs (https://api.ashbyhq.com/posting-api/job-board/{slug}) ----
# Slug = the final path segment of a company's jobs.ashbyhq.com/{slug} board.
# Verified: each returned 200 OK with > 0 jobs at time of addition.
ASHBY_SLUGS = [
    "deepl",         # 24 jobs - relocated from Greenhouse (404 there), Cologne LLM/MT
    "sosafe",        # 24 jobs, 15 in Cologne - security-awareness scale-up, Köln HQ
    "hrs",           # 20 jobs - HRS Group, Cologne office + Germany-remote, English
    "ramp",          # 121 jobs, fintech
    "deepgram",      # 61 jobs, speech AI - direct AI/ML fit
    "perplexity",    # 59 jobs, frontier AI lab
    "supabase",      # 43 jobs, dev infra
    "speak",         # 40 jobs, language learning AI
    "dust",          # 25 jobs, agent platform (Paris)
    "linear",        # 23 jobs, dev tools (Berlin office)
    "browserbase",   # 7 jobs, browser-as-a-service for agents
    "letta",         # 5 jobs, AI memory
    "weaviate",      # 4 jobs, vector DB
    # -- Discovery sweep batch (verified live) --
    "harvey",          # Harvey - 253 jobs, legal AI
    "legora",          # Legora - 191 jobs, legal AI
    "elevenlabs",      # ElevenLabs - 155 jobs, voice AI
    "cohere",          # Cohere - 123 jobs, LLM lab
    "plaid",           # Plaid - 106 jobs, fintech infra
    "cursor",          # Cursor (Anysphere) - 95 jobs, AI code editor
    "lovable",         # Lovable - 76 jobs, AI app builder
    "faculty",         # Faculty - 73 jobs, AI consultancy
    "avelios-medical", # Avelios Medical - 51 jobs, Munich health
    "encord",          # Encord - 47 jobs, data labeling for ML
    "elliptic",        # Elliptic - 43 jobs, crypto analytics
    "mollie",          # Mollie - 41 jobs, payments
    "thought-machine", # Thought Machine - 39 jobs, banking core
    "camunda",         # Camunda - 39 jobs, Berlin process automation
    "moss",            # Moss - 36 jobs, Berlin fintech
    "upvest",          # Upvest - 35 jobs, Berlin investment API
    "pleo",            # Pleo - 33 jobs, fintech
    "pliant",          # Pliant - 32 jobs, Berlin fintech
    "hcompany",        # H Company - 29 jobs, Paris agentic AI
    "blacksemiconductor", # Black Semiconductor - 25 jobs, Aachen chips
    "axelera",         # Axelera AI - 19 jobs, AI hardware
    "deepjudge",       # DeepJudge - 19 jobs, legal AI search
    "kestra",          # Kestra - 18 jobs, data orchestration
    "hawk",            # Hawk AI - 17 jobs, Munich AML/fraud ML
    "lemon-markets",   # lemon.markets - 16 jobs, Berlin trading API
    "nelly",           # Nelly Solutions - 15 jobs, health
    "langdock",        # Langdock - 15 jobs, Berlin enterprise LLM
    "e2b",             # E2B - 13 jobs, AI agent infra
    "klim",            # Klim - 11 jobs, regenerative ag
    "cradlebio",       # Cradle Bio - 11 jobs, protein ML
    "langfuse",        # Langfuse - 11 jobs, LLM observability (Berlin)
    "knowunity",       # Knowunity - 11 jobs, Berlin edtech AI
    "ledger",          # Ledger - 9 jobs, crypto
    "alephalpha",      # Aleph Alpha - 9 jobs, Heidelberg LLM lab
    "swan",            # Swan - 8 jobs, embedded banking
    "sweep",           # Sweep - 7 jobs, climate
    "codesphere",      # Codesphere - 7 jobs, Karlsruhe dev platform
    "deeploy",         # Deeploy - 7 jobs, ML monitoring
    "lightdash",       # Lightdash - 6 jobs, BI / analytics
    "sylvera",         # Sylvera - 4 jobs, carbon data
    "ceezer",          # Ceezer - 4 jobs, carbon
    "bitvavo",         # Bitvavo - 3 jobs, crypto exchange
    "ostrom",          # Ostrom - 3 jobs, Berlin energy
    "billie",          # Billie - 1 job, Berlin BNPL
    # -- E-commerce sweep batch (verified live) --
    "preply",          # Preply - 143 jobs, language learning
    "rohlik",          # Rohlik - 51 jobs, grocery (Prague/DE)
    "crisp",           # Crisp - 29 jobs, grocery
    "backmarket",      # Back Market - 24 jobs, refurbished
    "forto",           # Forto - 17 jobs, Berlin logistics
    "real",            # Real - 14 jobs, retail
    "babbel",          # Babbel - 4 jobs, Berlin edtech
    "flink",           # Flink - 3 jobs, Berlin grocery
    "choco",           # Choco - 2 jobs, Berlin food supply
    "sellerx",         # SellerX - 1 job, Berlin aggregator
    # -- Germany-wide English sweep 2026-09-07 (verified live) --
    "bettermile",      # Bettermile - 6 jobs, Berlin; Working Student User Operations & Insights
]

# -- Recruitee ATS slugs (https://{slug}.recruitee.com/api/offers) -------------
RECRUITEE_SLUGS = [
    "benutagmbh",    # 14 jobs, Bonn - 5 Werkstudent roles live
    "currenta",      # 40, Leverkusen Chempark - 2 Werkstudent live
    "trustedshops",  # 24, Köln
    "inovex",        # 20, Köln office - runs "Werkstudent:in Data & AI"
    "unu",           # United Nations University (UNU-EHS Bonn) - English, intern roles
    "limehome",      # 16 jobs, Munich hospitality
    "personio",      # 1 job
    # -- Discovery sweep batch (verified live) --
    "bunq",          # bunq - 27 jobs, neobank
    "sequra",        # SeQura - 18 jobs, BNPL
    "payflows",      # Payflows - 10 jobs
    "climeworks",    # Climeworks - 10 jobs, carbon capture (Zurich)
    "ethonai",       # EthonAI - 9 jobs, manufacturing AI (Zurich)
    "ginmon",        # Ginmon - 7 jobs, Frankfurt roboadvisor
    "constellr",     # Constellr - 4 jobs, space/climate data
    "flower",        # Flower Labs - 4 jobs, federated LLM (GmbH)
    # -- E-commerce sweep batch (verified live) --
    "channable",     # Channable - 17 jobs, feed management
    "shopwareag",    # Shopware - 13 jobs, e-com platform
    "everdrop",      # Everdrop - 11 jobs, Munich D2C
    "rebuy",         # Rebuy - 7 jobs, Berlin recommerce
    "lillydoo",      # Lillydoo - 3 jobs
]
