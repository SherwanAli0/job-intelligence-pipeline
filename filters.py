"""
filters.py — single source of truth for Germany-eligibility term lists.

main.py (pre-scorer location filter) and scorer.py (hard disqualifier) used to
carry their own copies of these three tuples, and they drifted: scorer knew
"dach"/"german market" but not "hiring across europe"; main knew the reverse.
Result: a job could pass one stage and be killed by the other, and any tuning
fix applied to one file silently didn't apply to the other — the same failure
shape as the two historical filter regressions.

Every list below is the UNION of the two former copies. Add new signals HERE
and only here; both stages pick them up automatically.
"""

import re

# ── Title-only screen, used to decide whether a description is worth fetching ─
# LinkedIn descriptions cost one HTTP request each. Fetching all ~1,100 results
# took 420-600s and made JobSpy time out entirely, returning ZERO jobs on half
# the runs. Screening on the title first means we only spend requests on
# postings that could plausibly survive.
#
# This must stay CONSERVATIVE. It is an optimisation, not a filter: the real
# filter chain in main.py still sees every job that gets through. Anything
# dropped here is dropped without ever being read, so only patterns that are
# certain — never "probably" — belong in this list.

_RE_TITLE_HARD_SENIOR = re.compile(
    r"(?<![a-z])(expert|senior|sr\.?|lead|principal|staff|head\s+of|director|"
    r"vice\s+president|\bvp\b|chief|manager|leiter(in)?)(?![a-z])",
    re.IGNORECASE,
)
# Employment forms that are never the target. Since the Werkstudent pivot
# (owner enrolled at Uni Bonn from 10/2026), werkstudent/studentische/HiWi
# titles are the TARGET and must NOT appear here. Internships joined them
# on 2026-08-16, so praktikum/praktikant/intern came out too. Thesis work,
# Ausbildung and academia remain out of scope.
_RE_TITLE_INELIGIBLE = re.compile(
    r"\b(ausbildung|dual(es|er)|abschlussarbeit|masterarbeit|bachelorarbeit|"
    r"thesis|postdoc\w*|professor\w*)\b",
    re.IGNORECASE,
)
_RE_TITLE_JUNIOR_OK = re.compile(
    r"\b(junior|jr\.?|entry[\s-]?level|graduate|absolvent\w*|trainee|associate)\b",
    re.IGNORECASE,
)
# "(Senior)" in parentheses is German-ad convention for "senior OPTIONAL".
_RE_TITLE_OPTIONAL_SENIOR = re.compile(r"\(\s*senior\s*\)", re.IGNORECASE)


def title_is_worth_fetching(title: str) -> bool:
    """
    Should we spend an HTTP request fetching this posting's description?

    Conservative by design: when in doubt, fetch. A wrongly skipped job is
    invisible forever, whereas a wrongly fetched one just costs a request.
    """
    t = (title or "").strip()
    if not t:
        return True                      # unknown title: let the real filters decide

    if _RE_TITLE_INELIGIBLE.search(t):
        return False

    # Neutralise the optional-senior convention before the seniority test.
    t_clean = _RE_TITLE_OPTIONAL_SENIOR.sub(" ", t)
    if _RE_TITLE_HARD_SENIOR.search(t_clean):
        # "Junior Engineering Manager" and the like are rare but real.
        return bool(_RE_TITLE_JUNIOR_OK.search(t_clean))

    return True


# Germany-presence signals — these confirm a role is doable from Germany.
GERMANY_TERMS = (
    "germany", "deutschland", "berlin", "munich", "münchen", "hamburg",
    "frankfurt", "cologne", "köln", "düsseldorf", "bochum", "dortmund",
    "essen", "stuttgart", "leipzig", "nrw", "bavaria", "bayern",
    "saxony", "sachsen", "hessen", "baden-württemberg",
    # The Bonn commute belt. These were MISSING, and the omission was
    # expensive: a posting whose location field reads just "Bonn",
    # "Bonn-Rhein-Sieg", "Sankt Augustin" or "Koblenz" — which is how
    # Stellenwerk, Fraunhofer, BWI and Debeka all write it — matched no
    # Germany term at all and was hard-disqualified by the scorer as being
    # outside Germany. The pipeline's own home region was the one region it
    # could not recognise.
    "bonn", "rhein-sieg", "sankt augustin", "st. augustin", "siegburg",
    "troisdorf", "hennef", "königswinter", "bad honnef", "wachtberg",
    "meckenheim", "rheinbach", "alfter", "bornheim", "swisttal",
    "koblenz", "andernach", "remagen", "neuwied", "euskirchen",
    "leverkusen", "bergisch gladbach", "brühl", "wesseling", "hürth",
    "dormagen", "neuss", "rheinland-pfalz", "nordrhein-westfalen",
    # formerly scorer-only:
    "dach", "german market", "german office", "german team",
)

# ── German places, for LOCATION strings only ────────────────────────────────
# GERMANY_TERMS above is matched as a substring and is kept short on purpose.
# The scorer's pre-screen used it to decide whether a location is in Germany,
# and anything it did not recognise was dropped as "outside Germany" (the only
# thing that removes Warsaw, Madrid or Amsterdam, which the non-EU blocklist
# does not cover). A German town that was not on the list therefore vanished:
# measured 2026-10-14, 80 of 175 student rows from XING were dropped that way,
# among them Paderborn, Aachen, Hilden and Schwelm in NRW, and Iserlohn,
# Nürnberg and Karlsruhe. LinkedIn writes "City, Germany" and was immune,
# which is a large part of why it was the only source that reached the inbox.
#
# These names are matched as WHOLE WORDS and only against a location field,
# never against ad text, so "Copenhagen" is not Hagen and "Kielce" is not Kiel.
# Austria and Switzerland are deliberately absent.
GERMAN_STATES = (
    "baden-württemberg", "baden-wuerttemberg", "baden wuerttemberg", "bayern", "bavaria",
    "brandenburg", "hessen", "hesse", "mecklenburg-vorpommern", "mecklenburg vorpommern",
    "niedersachsen", "lower saxony", "nordrhein-westfalen", "nordrhein westfalen",
    "north rhine-westphalia", "north rhine westphalia", "nrw", "rheinland-pfalz",
    "rheinland pfalz", "rhineland-palatinate", "saarland", "sachsen", "saxony",
    "sachsen-anhalt", "saxony-anhalt", "schleswig-holstein", "schleswig holstein",
    "thüringen", "thueringen", "thuringia",
)

# NRW towns (and the larger Köln/Düsseldorf districts). main.py uses this list
# for the on-site rule ("on-site only in NRW") on LOCATION fields.
NRW_PLACES = (
    "aachen", "ahaus", "ahlen", "alsdorf", "arnsberg", "bad driburg", "bad honnef",
    "bad lippspringe", "bad oeynhausen", "bad salzuflen", "bad münstereifel",
    "baesweiler", "beckum", "bergheim", "bergisch gladbach", "bergkamen", "bielefeld",
    "bocholt", "bochum", "bonn", "borken", "bornheim", "bottrop", "brilon", "brühl",
    "bünde", "burscheid", "castrop-rauxel", "coesfeld", "datteln", "delbrück",
    "detmold", "dinslaken", "dormagen", "dorsten", "dortmund", "dülmen", "düren",
    "duisburg", "düsseldorf", "ehrenfeld", "emsdetten", "engelskirchen", "ennepetal",
    "enger", "erftstadt", "erkelenz", "erkrath", "eschweiler", "espelkamp", "essen",
    "euskirchen", "frechen", "fröndenberg", "gelsenkirchen", "gevelsberg", "gladbeck",
    "goch", "greven", "gronau", "gummersbach", "gütersloh", "haan", "hagen", "haltern am see",
    "hamm", "hattingen", "heiligenhaus", "heinsberg", "hemer", "hennef", "herford",
    "herne", "herten", "herzogenrath", "hilden", "höxter", "hückelhoven", "hürth",
    "ibbenbüren", "iserlohn", "jülich", "kaarst", "kamp-lintfort", "kerpen", "kevelaer",
    "kleve", "köln", "königswinter", "korschenbroich", "krefeld", "kreuztal",
    "langenfeld", "lemgo", "leichlingen", "leverkusen", "lindlar", "lippstadt", "lohmar",
    "löhne", "lübbecke", "lüdenscheid", "lüdinghausen", "lünen", "marl", "mechernich",
    "meckenheim", "meerbusch", "menden", "meschede", "mettmann", "minden", "moers",
    "mönchengladbach", "monheim am rhein", "mülheim an der ruhr", "münster", "nettetal",
    "netphen", "neuss", "niederkassel", "nippes", "oberhausen", "oelde", "olpe",
    "overath", "paderborn", "plettenberg", "pulheim", "radevormwald", "ratingen",
    "recklinghausen", "remscheid", "rheda-wiedenbrück", "rheinbach", "rheine",
    "rietberg", "rösrath", "salzkotten", "sankt augustin", "sassenberg", "schwelm",
    "schwerte", "selm", "siegburg", "siegen", "soest", "solingen", "sprockhövel",
    "stolberg", "swisttal", "telgte", "troisdorf", "unna", "velbert", "verl",
    "versmold", "viersen", "voerde", "waltrop", "warendorf", "warstein", "wegberg",
    "werdohl", "werl", "wermelskirchen", "wesel", "wesseling", "wiehl", "wipperfürth",
    "witten", "wülfrath", "wuppertal", "würselen", "xanten", "zülpich",
    # seen in real ads after the first run of this list (2026-10-14)
    "attendorn", "gescher", "bad berleburg", "bad laasphe", "lennestadt", "hilchenbach",
    "hückeswagen", "kürten", "eitorf", "weilerswist", "nörvenich", "kreuzau", "monschau",
    "simmerath", "langerwehe", "linnich", "jüchen", "rommerskirchen", "borgholzhausen",
    "büren", "wadersloh", "harsewinkel", "steinfurt", "ochtrup", "rees", "emmerich am rhein",
    "geldern", "straelen", "schwalmtal", "grefrath", "tönisvorst", "willich", "kempen",
    "brüggen", "niederkrüchten", "gangelt", "waldfeucht", "selfkant", "bedburg",
    "bedburg-hau", "issum", "isselburg", "kalkar", "uedem", "weeze", "wachtendonk",
)

# Other German towns that turn up in student and Mittelstand ads.
GERMAN_PLACES = NRW_PLACES + (
    "aalen", "aschaffenburg", "augsburg", "ansbach", "amberg", "baden-baden", "bamberg",
    "bayreuth", "berlin", "bad homburg", "bad kreuznach", "bad nauheim", "bad vilbel",
    "biberach", "böblingen", "braunschweig", "bremen", "bremerhaven", "bruchsal",
    "celle", "chemnitz", "cottbus", "cuxhaven", "dachau", "darmstadt", "delmenhorst",
    "dreieich", "dresden", "eisenach", "erfurt", "erlangen", "eschborn", "esslingen",
    "ettlingen", "flensburg", "frankenthal", "frankfurt", "freiburg", "freising",
    "friedrichshafen", "fulda", "fürth", "garching", "gera", "gießen", "giessen",
    "göppingen", "görlitz", "göttingen", "goettingen", "greifswald", "hamburg", "hanau",
    "hannover", "hanover", "heidelberg", "heidenheim", "heilbronn", "hildesheim",
    "ingolstadt", "jena", "kaiserslautern", "karlsruhe", "kassel", "kempten", "kiel",
    "koblenz", "konstanz", "landshut", "leipzig", "leonberg", "limburg an der lahn", "limburg a.d. lahn", "lörrach",
    "lübeck", "ludwigsburg", "ludwigshafen", "lüneburg", "magdeburg", "mainz",
    "mannheim", "marburg", "memmingen", "münchen", "munich", "muenchen", "neu-isenburg",
    "neu-ulm", "neubrandenburg", "neumünster", "norderstedt", "nürnberg", "nuremberg",
    "nuernberg", "oberursel", "offenbach", "offenburg", "oldenburg", "osnabrück",
    "osnabrueck", "passau", "pforzheim", "potsdam", "ravensburg", "regensburg",
    "reutlingen", "rosenheim", "rostock", "rüsselsheim", "saarbrücken", "saarbruecken",
    "salzgitter", "schwäbisch gmünd", "schwäbisch hall", "schweinfurt", "schwerin",
    "sindelfingen", "speyer", "stralsund", "straubing", "stuttgart", "trier",
    "tübingen", "tuebingen", "ulm", "villingen-schwenningen", "waiblingen", "weiden",
    "weinheim", "wetzlar", "wiesbaden", "wilhelmshaven", "wismar", "wolfenbüttel",
    "wolfsburg", "worms", "würzburg", "wuerzburg", "zwickau", "walldorf", "weilheim",
    "garbsen", "lingen", "emden", "nordhorn", "papenburg", "vechta", "verden",
    "stade", "buxtehude", "pinneberg", "ahrensburg", "elmshorn", "henstedt-ulzburg",
    "kornwestheim", "fellbach", "filderstadt", "ostfildern", "metzingen", "nürtingen",
    "backnang", "crailsheim", "ellwangen", "rottweil", "tuttlingen", "singen",
    "überlingen", "bad säckingen", "emmendingen", "lahr", "achern", "rastatt",
    "gaggenau", "bühl", "landau", "neustadt an der weinstraße", "pirmasens", "bingen",
    "idar-oberstein", "neuwied", "andernach", "mayen", "remagen", "bad neuenahr",
    "unterföhring", "ismaning", "garching bei münchen", "unterschleißheim", "haar",
    "ehingen", "burgwedel", "stockach", "grünwald", "oberkochen", "cloppenburg", "peine",
    "deggendorf", "coburg", "neumarkt in der oberpfalz", "oberhaching", "planegg", "martinsried", "grasbrunn", "poing", "erding", "starnberg",
    "fürstenfeldbruck", "germering", "gilching", "weßling", "oberpfaffenhofen",
    "ottobrunn", "taufkirchen", "parsdorf", "feldkirchen", "kirchheim bei münchen",
    "eching", "neufahrn", "ottersberg", "halle (saale)", "halle/saale", "halle an der saale",
    "dessau", "dessau-roßlau", "wittenberg", "stendal", "halberstadt", "wernigerode",
    "bautzen", "freiberg", "meißen", "pirna", "radebeul", "plauen", "weimar", "gotha",
    "ilmenau", "suhl", "nordhausen", "mühlhausen", "altenburg", "eberswalde",
    "frankfurt (oder)", "frankfurt am main", "bernau", "falkensee", "königs wusterhausen",
    "wildau", "teltow", "kleinmachnow", "oranienburg", "neuruppin", "brandenburg an der havel",
)

# ── v2 (2026-10-14, after the review of v1) ─────────────────────────────────
# More NRW municipalities: the regional gaps the review found (Halver, Kamen,
# Werne, Hamminkeln, Warburg, Sundern ...). Ambiguous words are left out on
# purpose (Lage, Much, Alpen, Senden, Horn, Hille ...): a location field is
# short, but "Lage" is also a German word.
NRW_PLACES_MORE = (
    "halver", "altena", "meinerzhagen", "kierspe", "schalksmühle", "schalksmuehle", "neuenrade",
    "balve", "sundern", "schmallenberg", "winterberg", "marsberg", "geseke", "erwitte", "kamen",
    "bönen", "holzwickede", "herdecke", "wetter (ruhr)", "werne", "nottuln", "rheinberg",
    "neukirchen-vluyn", "hamminkeln", "rhede", "steinhagen", "porta westfalica", "petershagen",
    "hövelhof", "oerlinghausen", "warburg", "brakel", "windeck", "waldbröl", "nümbrecht",
    "marienheide", "odenthal", "wassenberg", "übach-palenberg", "nideggen", "wilnsdorf",
    "oer-erkenschwick", "lengerich", "grevenbroich", "bergneustadt", "reichshof", "morsbach",
    "neunkirchen-seelscheid", "geilenkirchen", "olfen", "rahden", "blomberg", "dorsten",
    "ahaus", "alsdorf", "dülmen", "bad münstereifel", "heimbach", "hürtgenwald", "inden",
    "aldenhoven", "titz", "vettweiß", "merzenich", "niederzier", "schleiden", "blankenheim",
    "hellenthal", "nettersheim", "ruppichteroth", "nörvenich", "kranenburg", "kerken", "sonsbeck",
    "schermbeck", "hünxe", "raesfeld", "velen", "reken", "südlohn", "legden", "heek",
    "schöppingen", "rosendahl", "havixbeck", "billerbeck", "nordkirchen", "ascheberg", "hörstel",
    "altenberge", "wettringen", "metelen", "westerkappeln", "mettingen", "recke",
    "saerbeck", "tecklenburg", "ladbergen", "lienen", "ennigerloh", "drensteinfurt", "everswinkel",
    "beelen", "ostbevern", "herzebrock-clarholz", "schloß holte-stukenbrock", "schloss holte-stukenbrock",
    "halle (westf.)", "halle (westfalen)", "werther (westf.)", "vlotho", "spenge", "kirchlengern",
    "rödinghausen", "hiddenhausen", "beverungen", "nieheim", "marienmünster",
    "willebadessen", "borgentreich", "horn-bad meinberg", "augustdorf", "leopoldshöhe",
    "schieder-schwalenberg", "extertal", "dörentrup", "kalletal", "lügde", "hüllhorst",
    "preußisch oldendorf", "stemwede", "bad wünnenberg", "lichtenau", "borchen", "altenbeken",
    "breckerfeld", "olsberg", "eslohe", "medebach", "bestwig", "hallenberg", "nachrodt-wiblingwerde",
    "erndtebrück", "rüthen", "wickede", "möhnesee", "bad sassendorf", "lippetal",
    "ruhrgebiet", "ruhrpott", "sauerland", "münsterland", "muensterland", "niederrhein",
    "bergisches land", "ostwestfalen", "siegerland", "rhein-sieg", "rhein-erft", "rhein-kreis neuss",
    "rhein-ruhr", "rheinisch-bergischer kreis", "märkischer kreis", "ennepe-ruhr",
    "mülheim", "mulheim", "porz", "deutz", "beuel", "bad godesberg", "lindenthal", "mülheim-ruhr",
    "koeln", "duesseldorf", "dusseldorf", "muenster", "grevenbroich", "cologne", "neuss",
)

# Employer towns outside NRW that turn up in ads (the review's list plus the
# towns seen in the 2026-10-14 pool). Not needed for the pre-screen any more
# (unknown towns pass), but they count as German evidence for remote roles.
GERMAN_PLACES_MORE = (
    "herzogenaurach", "neckarsulm", "renningen", "gerlingen", "ditzingen", "weissach",
    "künzelsau", "kuenzelsau", "melsungen", "ingelheim", "burghausen", "donauwörth", "baunatal",
    "dingolfing", "neubiberg", "auerbach", "dettenhausen", "friedberg", "gollhofen",
    "grafenrheinfeld", "landsberg am lech", "manching", "nagold", "schramberg", "waldenburg",
    "windach", "winterbach", "zirndorf", "brunswick", "bundesweit", "deutschlandweit",
    "ganz deutschland", "hamburg", "rüsselsheim", "russelsheim", "kelsterbach", "mörfelden-walldorf",
    "bad homburg", "bensheim", "viernheim", "lampertheim", "worms", "alzey", "alzey-worms",
    "lutherstadt wittenberg", "wittenberg", "bad hersfeld", "alsfeld", "lauterbach", "schlüchtern",
    "gelnhausen", "büdingen", "nidderau", "karben", "bad vilbel", "maintal", "hattersheim",
    "flörsheim", "hofheim", "kronberg", "königstein", "idstein", "taunusstein", "bad schwalbach",
    "unterhaching", "ottobrunn", "aschheim", "kirchheim", "haar", "vaterstetten", "zorneding",
    "gräfelfing", "krailling", "puchheim", "olching", "karlsfeld", "hallbergmoos", "oberschleißheim",
    "pullach", "grünwald", "gruenwald", "icking", "wolfratshausen", "geretsried", "murnau",
    "garmisch-partenkirchen", "kaufbeuren", "kempten", "lindau", "isny", "wangen", "leutkirch",
    "biberach", "laupheim", "ehingen", "blaubeuren", "münsingen", "bad urach", "kirchheim unter teck",
    "weilheim an der teck", "stockach", "radolfzell", "engen", "donaueschingen", "bad dürrheim",
    "villingen", "schwenningen", "rottweil", "oberndorf", "sulz", "horb", "freudenstadt", "calw",
    "oberkochen", "coburg", "cloppenburg", "peine", "burgwedel", "deggendorf", "neumarkt in der oberpfalz",
    # compounds the whole-word match would otherwise lose
    "oberbayern", "niederbayern", "unterfranken", "oberfranken", "mittelfranken", "süddeutschland",
    "norddeutschland", "ostdeutschland", "westdeutschland", "mitteldeutschland", "nordhessen",
    "südhessen", "nordbayern", "südbayern", "ostwestfalen-lippe", "rhein-main", "rhein-neckar",
    "metropolregion rhein-neckar", "großraum münchen", "großraum stuttgart", "großraum köln",
    "großraum hamburg", "großraum berlin", "raum münchen", "raum stuttgart", "raum köln",
    # German regions whose names contain a foreign word (see _GERMAN_PRIORITY)
    "sächsische schweiz", "saechsische schweiz", "fränkische schweiz", "fraenkische schweiz",
    "holsteinische schweiz", "märkische schweiz",
)

# Places main.py and scrapers.py already treated as German, found missing here
# by tests/test_german_places.py::TestOneListForTheWholePipeline.
GERMAN_PLACES_FROM_MAIN = ('unkel', 'linz am rhein', 'monheim', 'halle', 'mecklenburg',)

GERMAN_PLACES = GERMAN_PLACES + NRW_PLACES_MORE + GERMAN_PLACES_MORE + GERMAN_PLACES_FROM_MAIN
NRW_PLACES = NRW_PLACES + NRW_PLACES_MORE

# State NAMES that are not North Rhine-Westphalia. Used to stop an NRW town
# name from counting when the location names another state ("Borken, Hessen",
# "Gronau (Leine), Niedersachsen").
OTHER_STATE_NAMES = (
    "baden-württemberg", "baden-wuerttemberg", "baden wuerttemberg", "bayern", "bavaria",
    "brandenburg", "hessen", "hesse", "mecklenburg-vorpommern", "mecklenburg vorpommern",
    "niedersachsen", "lower saxony", "rheinland-pfalz", "rheinland pfalz", "rhineland-palatinate",
    "saarland", "sachsen", "saxony", "sachsen-anhalt", "saxony-anhalt", "schleswig-holstein",
    "schleswig holstein", "thüringen", "thueringen", "thuringia",
)

# ── Evidence of a place OUTSIDE Germany ─────────────────────────────────────
# The scorer's catch-all used to drop every location it could not recognise as
# German. That removed real German towns (a finite list cannot hold ~11,000
# municipalities) and, because the same list was the only thing that said
# "German", it also had to guess. It now drops only on positive evidence that
# the place is abroad: a country, a well-known foreign city, a US / Canadian /
# Australian state code, or a foreign country code.
FOREIGN_COUNTRIES = (
    "austria", "österreich", "oesterreich", "switzerland", "schweiz", "suisse", "svizzera",
    "liechtenstein", "netherlands", "nederland", "niederlande", "holland", "belgium", "belgien",
    "belgique", "belgië", "luxembourg", "luxemburg", "france", "frankreich", "poland", "polen",
    "polska", "czechia", "czech republic", "tschechien", "slovakia", "slowakei", "hungary",
    "ungarn", "romania", "rumänien", "bulgaria", "bulgarien", "croatia", "kroatien", "slovenia",
    "slowenien", "serbia", "serbien", "bosnia", "greece", "griechenland", "turkey", "türkei",
    "türkiye", "italy", "italien", "italia", "spain", "spanien", "españa", "espana", "portugal",
    "ireland", "irland", "united kingdom", "great britain", "england", "scotland", "wales",
    "northern ireland", "denmark", "dänemark", "danmark", "sweden", "schweden", "sverige",
    "norway", "norwegen", "norge", "finland", "finnland", "suomi", "estonia", "estland", "latvia",
    "lettland", "lithuania", "litauen", "iceland", "malta", "cyprus", "zypern", "ukraine",
    "russia", "russland", "belarus", "moldova", "albania", "north macedonia", "montenegro",
    "kosovo", "israel", "egypt", "morocco", "tunisia", "south africa", "nigeria", "kenya",
    "united states", "usa", "u.s.a.", "united states of america", "america", "canada", "mexico",
    "brazil", "brasil", "argentina", "chile", "colombia", "peru", "uruguay", "india", "china",
    "japan", "south korea", "korea", "taiwan", "hong kong", "singapore", "thailand", "vietnam",
    "indonesia", "malaysia", "philippines", "pakistan", "bangladesh", "sri lanka", "australia",
    "new zealand", "united arab emirates", "uae", "saudi arabia", "qatar", "dubai", "jordan",
    "lebanon",
)
FOREIGN_REGIONS = (
    # US states (names) - codes are handled separately
    "california", "texas", "florida", "ohio", "massachusetts", "pennsylvania", "illinois",
    "michigan", "arizona", "colorado", "oregon", "nebraska", "wisconsin", "minnesota",
    "new hampshire", "new jersey", "new mexico", "north carolina", "south carolina",
    "north dakota", "south dakota", "west virginia", "rhode island", "connecticut", "maryland",
    "virginia", "tennessee", "kentucky", "louisiana", "alabama", "mississippi", "missouri",
    "oklahoma", "arkansas", "kansas", "iowa", "indiana", "utah", "nevada", "idaho", "montana",
    "wyoming", "vermont", "delaware", "hawaii", "alaska",
    # Canada / Australia / Benelux provinces
    "ontario", "quebec", "british columbia", "alberta", "manitoba", "saskatchewan",
    "new south wales", "queensland", "victoria", "tasmania",
    "flemish region", "wallonia", "carinthia", "kärnten", "steiermark", "styria", "tyrol", "tirol",
    "vorarlberg", "burgenland", "oberösterreich", "niederösterreich", "ticino", "graubünden",
)
FOREIGN_CITIES = (
    "wien", "vienna", "graz", "linz", "salzburg", "innsbruck", "klagenfurt", "villach",
    "dornbirn", "bregenz", "zürich", "zurich", "basel", "bern", "genf", "geneva", "genève",
    "lausanne", "luzern", "lucerne", "winterthur", "lugano", "st. gallen", "st gallen",
    "esch-sur-alzette", "amsterdam", "rotterdam", "utrecht", "eindhoven", "den haag", "the hague",
    "groningen", "maastricht", "tilburg", "breda", "nijmegen", "enschede", "venlo", "brussels",
    "bruxelles", "brüssel", "antwerp", "antwerpen", "ghent", "gent", "leuven", "liège", "liege",
    "charleroi", "paris", "lyon", "marseille", "toulouse", "nantes", "strasbourg", "straßburg",
    "lille", "bordeaux", "montpellier", "rennes", "grenoble", "london", "manchester",
    "birmingham", "edinburgh", "glasgow", "cambridge", "oxford", "bristol", "leeds", "liverpool",
    "dublin", "galway", "warsaw", "warszawa", "warschau", "krakow", "kraków", "wroclaw",
    "wrocław", "gdansk", "gdańsk", "poznan", "poznań", "lodz", "łódź", "katowice", "prague",
    "praha", "prag", "brno", "bratislava", "budapest", "bucharest", "cluj", "cluj-napoca",
    "timisoara", "sofia", "plovdiv", "zagreb", "ljubljana", "belgrade", "beograd", "athens",
    "thessaloniki", "istanbul", "ankara", "izmir", "madrid", "barcelona", "valencia", "seville",
    "sevilla", "bilbao", "málaga", "malaga", "lisbon", "lisboa", "porto", "rome", "roma",
    "milan", "milano", "turin", "torino", "naples", "napoli", "florence", "bologna",
    "copenhagen", "københavn", "aarhus", "odense", "stockholm", "gothenburg", "göteborg",
    "malmö", "malmo", "oslo", "helsinki", "espoo", "tampere", "tallinn", "riga", "vilnius",
    "kaunas", "reykjavik", "kyiv", "kiev", "moscow", "tel aviv", "new york", "san francisco",
    "los angeles", "seattle", "boston", "chicago", "austin", "denver", "atlanta", "miami",
    "dallas", "houston", "san diego", "toronto", "vancouver", "montreal", "ottawa", "sydney",
    "melbourne", "brisbane", "perth", "auckland", "tokyo", "bangalore", "bengaluru", "mumbai",
    "delhi", "hyderabad", "pune", "chennai", "shanghai", "beijing", "shenzhen", "seoul",
    "abu dhabi", "cairo", "lagos", "nairobi", "cape town", "johannesburg", "são paulo",
    "sao paulo", "rio de janeiro", "buenos aires", "santiago", "bogotá", "bogota", "lima",
    "mexico city",
)
# Places that are German although a foreign list holds the same word.
_GERMAN_PRIORITY = ("linz am rhein", "sächsische schweiz", "saechsische schweiz",
                    "fränkische schweiz", "fraenkische schweiz", "holsteinische schweiz",
                    "märkische schweiz", "holland-hessen")

_US_CODES = ("al ak az ar ca co ct fl ga hi ia id il in ks ky la ma md me mi mn mo ms mt nc nd "
             "ne nh nj nm nv ny oh ok or pa ri sc sd tn tx ut va vt wa wi wv wy dc").split()
# "DE" (Delaware) is deliberately not in the list: it is also Germany's code.
_US_CODE_RE = re.compile(r",\s*(?:" + "|".join(_US_CODES) + r")(?:\s+\d{5}(?:-\d{4})?)?\s*(?:,\s*(?:usa|us))?\s*$")
_CA_AU_CODE_RE = re.compile(r",\s*(?:on|qc|bc|ab|mb|nb|ns|nl|pe|sk|nsw|vic|qld|tas|act)\s*$")
_FOREIGN_CC_RE = re.compile(r",\s*(?:at|ch|nl|fr|es|it|pl|pt|ie|gb|uk|dk|se|fi|cz|hu|ro|lu|us|ca|au|nz)\s*$")
# German state codes as Indeed and some boards write them: "Hamminkeln, NW",
# "Herzogenaurach, BY", "Paderborn, NW, DE", "Köln, NW, DE, 50933".
_DE_STATE_CODE_RE = re.compile(
    # codes that are NOT an ISO country code: enough on their own
    r"(?:^|,\s*)(?:nw|hb|hh|he|rp)(?:\s*,\s*de)?(?:[\s,]*\d{5})?\s*$"
    # codes that are also a country (BE Belgium, BY Belarus, TH Thailand, SN Senegal,
    # NI Nicaragua, SL Sierra Leone, BW Botswana, BB Barbados, MV Maldives, SH Saint
    # Helena, ST Sao Tome): only with ", DE" or a 5-digit German postcode
    r"|(?:^|,\s*)(?:bw|by|be|bb|mv|ni|sl|sn|st|sh|th)\s*(?:,\s*de(?:[\s,]*\d{5})?|[\s,]+\d{5})\s*$")
_NW_CODE_RE = re.compile(r"(?:^|,\s*)nw(?:\s*,\s*de)?(?:[\s,]*\d{5})?\s*$")
_GERMANY_WORD_RE = re.compile(
    r"(?<![a-zäöüß])(?:germany|deutschland|deutschlandweit|bundesweit|bundesrepublik)(?![a-zäöüß])")

import functools

_RE_CACHE: dict = {}
# Built once: _word_re caches by id(), so it must only ever see module-level tuples.
_ALL_GERMAN_TERMS: tuple = ()


def _translit(t: str) -> list[str]:
    """The spellings of a term: as written, ae/oe/ue/ss, and umlauts stripped
    ("Düsseldorf" -> duesseldorf, dusseldorf)."""
    out = {t}
    if re.search(r"[äöüß]", t):
        out.add(t.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss"))
        out.add(t.replace("ä", "a").replace("ö", "o").replace("ü", "u").replace("ß", "ss"))
    return sorted(out)


def _word_re(terms) -> "re.Pattern":
    """Whole-word alternation of terms (letters are the boundary; a term that
    starts or ends with punctuation is matched as written on that side)."""
    key = id(terms) if isinstance(terms, tuple) else tuple(terms)
    rx = _RE_CACHE.get(key)
    if rx is None:
        flat = sorted({v for t in terms for v in _translit(t.lower())}, key=len, reverse=True)
        parts = []
        for t in flat:
            p = re.escape(t)
            if t[:1].isalpha():
                p = r"(?<![a-zäöüß])" + p
            if t[-1:].isalpha():
                p = p + r"(?![a-zäöüß])"
            parts.append(p)
        rx = re.compile("|".join(parts))
        _RE_CACHE[key] = rx
    return rx


def mentions_germany(text: str) -> bool:
    """Does free text (an ad) mention Germany, a German state or place from
    GERMANY_TERMS, as a WHOLE word? The scorer used a substring test here, so
    "essential" counted as Essen and "dachshund" as DACH, which switched the
    outside-Germany catch-all off for most English ads."""
    return bool(_word_re(GERMANY_TERMS).search((text or "").lower()))


# Separators between OFFICES in a multi-office location ("Köln; Amsterdam",
# "Berlin / Zürich", "Munich or London"). A comma is NOT one: "Maastricht,
# Limburg, Netherlands" is one hierarchical place.
_OFFICE_SPLIT = re.compile(r"\s*(?:;|\||/|&|\bor\b|\band\b|\boder\b|\bund\b)\s*")
_MAX_LOC = 300          # location fields are short; this only bounds garbage input


def _strong_foreign(loc: str) -> bool:
    """Country, region, state code or country code: evidence that attaches to
    the place it follows ("Hanover, NH", "Konstanz, Switzerland")."""
    return bool(
        _word_re(FOREIGN_COUNTRIES).search(loc)
        or _word_re(FOREIGN_REGIONS).search(loc)
        or _US_CODE_RE.search(loc) or _CA_AU_CODE_RE.search(loc) or _FOREIGN_CC_RE.search(loc))


def _has_german_signal(loc: str) -> bool:
    """A German state code, state, or town in the string (no foreign test)."""
    global _ALL_GERMAN_TERMS
    if _DE_STATE_CODE_RE.search(loc):
        return True
    if not _ALL_GERMAN_TERMS:
        _ALL_GERMAN_TERMS = tuple(GERMANY_TERMS) + GERMAN_STATES + GERMAN_PLACES
    return bool(_word_re(_ALL_GERMAN_TERMS).search(loc))


@functools.lru_cache(maxsize=8192)
def is_foreign_location(loc: str) -> bool:
    """Positive evidence that a LOCATION field is outside Germany.

    * A string that also says Germany / Deutschland is never foreign.
    * A foreign country, region or code is strong evidence: it vetoes a
      German-sounding town in the same place ("Hanover, NH"). In a
      multi-office string it only counts when no office is German on its own.
    * A foreign CITY alone is weak evidence: it never overrides a German place
      or state in the string ("Köln; Amsterdam" is German, bare "Wien" is not).
    """
    loc = (loc or "").strip().lower()[:_MAX_LOC]
    if not loc or _GERMANY_WORD_RE.search(loc):
        return False
    if any(p in loc for p in _GERMAN_PRIORITY):
        return False
    if _strong_foreign(loc):
        segs = [s for s in _OFFICE_SPLIT.split(loc) if s]
        if len(segs) > 1:
            return not any(_has_german_signal(s) and not _strong_foreign(s) for s in segs)
        return True
    if _word_re(FOREIGN_CITIES).search(loc):
        return not _has_german_signal(loc)
    return False


@functools.lru_cache(maxsize=8192)
def is_german_location(loc: str) -> bool:
    """Does a LOCATION field name Germany: the country, a German state or
    state code, or a German town from the lists - and no foreign country,
    region, city or code? Whole-word matching, location text only."""
    loc = (loc or "").strip().lower()[:_MAX_LOC]
    if not loc:
        return False
    if _GERMANY_WORD_RE.search(loc):
        return True
    if is_foreign_location(loc):
        return False
    return _has_german_signal(loc)


def is_nrw_excluded(loc: str) -> bool:
    """A location that names another German state or a foreign place, so an
    NRW town name inside it ("Borken, Hessen", "Soest, Utrecht, Netherlands")
    must not count as NRW. An explicit NRW mention wins."""
    loc = (loc or "").strip().lower()
    if re.search(r"nordrhein|north rhine|\bnrw\b", loc) or _NW_CODE_RE.search(loc):
        return False
    if is_foreign_location(loc):
        return True
    return bool(_word_re(OTHER_STATE_NAMES).search(loc)
                or (_DE_STATE_CODE_RE.search(loc) and not _NW_CODE_RE.search(loc)))


def is_nrw_code(loc: str) -> bool:
    """'Hamminkeln, NW' / 'Paderborn, NW, DE': the NRW state code."""
    return bool(_NW_CODE_RE.search((loc or "").strip().lower()))


# main matches NRW names with a plain whole-word test, so the ASCII spellings
# (Lüdenscheid -> luedenscheid / ludenscheid) must be in the tuple itself. Done
# last, once the list is final; main imports the finished tuple.
NRW_PLACES = tuple(sorted({v for t in NRW_PLACES for v in _translit(t.lower())}))


# Phrases that confirm a remote role accepts Germany-based hires.
# Required when a non-German location appears in the location field.
REMOTE_COVERS_GERMANY_SIGNALS = (
    "remote in germany", "remote from germany", "remote within germany",
    "remote (germany", "germany-remote", "remote-germany",
    "remote in eu", "remote within the eu", "remote in the european union",
    "fully remote eu", "fully remote within europe", "fully remote in europe",
    "remote in europe", "remote within europe", "remote across europe",
    "remote anywhere in europe", "europe-wide remote", "eu-wide remote",
    "remote in dach", "dach region", "remote across the dach",
    "we hire across europe", "we hire across the eu",
    "open to candidates in germany", "open to applicants in germany",
    "hiring across europe", "hiring across the eu",
    "based in germany", "based anywhere in europe", "based anywhere in the eu",
    "you can work from anywhere in europe",
    "you can work from anywhere in the eu",
    "you can work from anywhere in the european union",
    "remote across emea",
    # worldwide remote (the old substring accident kept these by luck)
    "remote worldwide", "worldwide remote", "work from anywhere in the world",
    "anywhere in the world", "remote anywhere in the world", "globally remote", "global remote",
    "location independent", "location-independent", "work from anywhere",
)

# Phrases that REVOKE Germany eligibility — even if "remote" appears, these
# lock the role outside Germany.
REMOTE_LOCKED_OUT_SIGNALS = (
    "us-based only", "us only", "united states only", "must be based in the us",
    "must reside in the us", "must be in the us", "us residents only",
    "uk only", "uk-based only", "must be based in the uk",
    "canada only", "must be based in canada",
    "latin america only", "latam only", "must be based in latam",
    "remote in latam", "remote in latin america",
    "remote in india", "india only", "must be based in india",
    "apac only", "must be based in apac",
)
