"""Rules corrected after a review of every ad dropped between 11 and 17 November 2026.

Each class is one rule that dropped ads it was not meant to drop. The titles,
locations and sentences are the ones found in those ads.
"""
import pytest

import config
import main
import scorer
import scrapers

ENGLISH = (
    "We are an international team and our working language is English. "
    "In this role the student works with data, builds dashboards and supports the analytics team. "
    "Enrolment at a university and an interest in Python and SQL are what the role needs. "
)


def _j(title="Working Student Data Analytics", description=ENGLISH * 2, location="Düsseldorf, North Rhine-Westphalia, Germany", **kw):
    base = {"id": "x", "title": title, "company": "Acme", "location": location,
            "url": "https://example.com/jobs/1", "source": "linkedin", "posted_at": "",
            "description": description}
    base.update(kw)
    return base


def _formed(job):
    job["_form"] = main._job_form(job)
    return job


# -- where the work is done: the title counts ----------------------------------
class TestRemoteAndHybridInTheTitle:
    @pytest.mark.parametrize("title,location,where", [
        ("Data Scientist - R, Statistics & Modelling - Hybrid - Frankfurt", "Frankfurt am Main, Hesse, Germany", "HYBRID"),
        ("Computer Vision Engineer (m/f/d) | Berlin, Hybrid", "Berlin, Berlin, Germany", "HYBRID"),
        ("Data Analysis Engineer (w/m/d) I Remote möglich", "Munich, Bavaria, Germany", "REMOTE"),
        ("Data Analyst (m/w/d) - Banking & Digital Solutions mit Homeoffice-Option", "Wiesbaden, Hesse, Germany", "HYBRID"),
        ("[Remote] Werkstudent:in Data Analytics", "Hamburg", "HYBRID"),
        ("BI / Data Analytics Consultant | 100 % Home-Office möglich", "Rheine, North Rhine-Westphalia, Germany", "HYBRID"),
        ("AI Engineer (m/w/d) - remote & deutschlandweit", "Berlin, Germany", "REMOTE"),
    ])
    def test_the_title_says_where(self, title, location, where):
        j = main._tag_where(_formed(_j(title, "Wir suchen Verstärkung für unser Team. " * 6, location)))
        assert j["_where"] == where
        assert main._is_in_focus_area(j)

    @pytest.mark.parametrize("title", ["Remote Sensing Data Scientist", "Remote-Sensing Data Scientist",
                                       "Remote Control Software Engineer", "Remote Monitoring Engineer"])
    def test_remote_as_a_field_or_a_product_is_not_a_workplace(self, title):
        j = main._tag_where(_formed(_j(title, "On-site work in our lab. " * 6, "Munich, Bavaria, Germany")))
        assert j["_where"] == "ON-SITE" and not main._is_in_focus_area(j)

    def test_an_on_site_title_outside_nrw_still_drops(self):
        j = main._tag_where(_formed(_j("Data Scientist", "Work in our office. " * 6, "Munich, Bavaria, Germany")))
        assert j["_where"] == "ON-SITE" and not main._is_in_focus_area(j)

    def test_the_ruhr_region_is_nrw(self):
        j = main._tag_where(_formed(_j("AI Engineer (m/w/d)", "Work in our office. " * 6, "Ruhr Region")))
        assert j["_nrw"] and main._is_in_focus_area(j)


# -- a degree someone is studying for is not a finished one ---------------------
class TestStudyingForAMasters:
    @pytest.mark.parametrize("sentence", [
        "Enrolled student pursuing a Master's degree in computer science.",
        "Currently pursuing a master's or PhD degree in a related field.",
        "Current enrolment in a Master's program in computer science or AI.",
        "Completed Bachelor's degree and currently pursuing a Master's degree.",
        "You are currently pursuing an advanced master's degree.",
        "Pursuing Masters degree in electrical engineering or computer science.",
        "You are working towards a Master's degree in data science.",
    ])
    def test_the_filter_keeps_it(self, sentence):
        assert main._no_masters_required(_j("Intern - Software Engineer, AI Platform", f"{ENGLISH}{sentence} {ENGLISH}"))

    @pytest.mark.parametrize("sentence", [
        "A completed Master's degree in computer science is required.",
        "You hold a PhD in machine learning.",
        "Abgeschlossener Masterabschluss in Informatik.",
        # "pursuing" and "enrollment" in ordinary ad copy say nothing about studies
        "A Master's degree is required. Candidates pursuing excellence are welcome.",
        "PhD required, we are pursuing ambitious goals in research.",
        "Master's degree required. Our enrollment process is quick.",
        "Master's degree required. Automatic enrolment in our pension programme.",
    ])
    def test_a_finished_degree_still_drops(self, sentence):
        assert not main._no_masters_required(_j("AI Engineer", f"{ENGLISH}{sentence} {ENGLISH}"))

    def test_the_check_before_the_model_keeps_it_too(self):
        j = _j("Research Engineer AI", f"{ENGLISH}Minimum qualifications: currently pursuing a Master's degree. {ENGLISH}")
        assert scorer._hard_disqualify(j)[2] != "masters_required"

    @pytest.mark.parametrize("sentence", [
        "We are looking for a Master’s student with strong software engineering skills.",
        "The role is particularly suited to students considering a future PhD.",
        "Bachelor's degree with experience gained through internships, academic projects, coursework, "
        "or practical training, or Master's degree in a relevant technical field.",
        "MSc in computer science or a related field.",
    ])
    def test_in_an_internship_a_degree_is_the_one_being_studied_for(self, sentence):
        j = _formed(_j("Intern - Software Engineer, Spatial AI Agent Platform", f"{ENGLISH}{sentence} {ENGLISH}"))
        assert j["_form"] == "internship" and main._no_masters_required(j)

    def test_unless_the_ad_says_it_has_to_be_finished(self):
        j = _formed(_j("Research Intern", f"{ENGLISH}A completed Master's degree is required. {ENGLISH}"))
        assert j["_form"] == "internship" and not main._no_masters_required(j)

    def test_a_full_time_ad_is_read_as_before(self):
        j = _formed(_j("AI Engineer", f"{ENGLISH}MSc in computer science or a related field. {ENGLISH}"))
        assert j["_form"] == "full-time" and not main._no_masters_required(j)


class TestYearsInSomeoneElsesBiography:
    def test_a_founders_biography_is_no_requirement(self):
        j = _j("LLM Engineer - Post-Training (Remote - Germany)",
               f"{ENGLISH}Our founders. Jane Doe [CEO] • 10+ years in tech executive leadership • "
               f"co-founded two companies. John Roe [Co-Founder & CMO] • 8+ years in customer acquisition "
               f"and e-commerce growth. {ENGLISH}")
        assert main._no_experience_overload(j)

    def test_the_role_as_a_heading_above_the_first_bullet(self):
        j = _j("LLM Engineer - Post-Training (Remote - Germany)",
               f"{ENGLISH}Founders.\nJane Doe [CEO]\n• 10+ years in tech executive leadership\n"
               f"• co-founded two companies\nJohn Roe [Co-Founder & CMO]\n• 8+ years in customer acquisition\n{ENGLISH}")
        assert main._no_experience_overload(j)

    def test_a_plain_bullet_with_years_still_drops(self):
        j = _j("LLM Engineer", f"{ENGLISH}Your profile • 10+ years in backend development • Python. {ENGLISH}")
        assert not main._no_experience_overload(j)

    @pytest.mark.parametrize("text", [
        "Requirements:\n- Work closely with our CTO\n- 5+ years of experience in Python",
        "- Advise the CEO\n- 5+ years of Python development",
        "Reporting to the CTO | 4+ years in ML engineering",
        "Requirements: CEO, 5+ years of experience in AI products",
        "Experience in leadership (e.g. as CTO) - 5+ years of experience in Python",
    ])
    def test_a_role_named_without_brackets_is_no_biography(self, text):
        assert not main._no_experience_overload(_j("LLM Engineer", f"{ENGLISH}{text}\n{ENGLISH}"))


# -- remote, with Germany inside the named region --------------------------------
class TestRemoteRegionsThatIncludeGermany:
    D = "Fully remote role in our engineering team. Python services, English-speaking."

    @pytest.mark.parametrize("location", [
        "Remote, Europe,  USA", "Remote, Argentina,  Europe,  USA", "Remote, EMEA,  LATAM,  Canada,  USA",
        "Remote, APAC,  EMEA,  LATAM,  Canada", "Remote - EMEA", "Remote, EU", "Remote, Worldwide",
    ])
    def test_the_location_itself_covers_germany(self, location):
        disq, why, _c = scorer._hard_disqualify(_j("Junior Software Engineer", self.D, location))
        assert not disq, (location, why)

    @pytest.mark.parametrize("location", ["Remote, USA", "Remote, LATAM", "Remote, India", "Remote, Canada",
                                          "Remote - Anywhere in the United States", "Remote, Anywhere in the US",
                                          "Remote, North America"])
    def test_a_region_without_germany_still_drops(self, location):
        disq, _w, category = scorer._hard_disqualify(_j("Junior Software Engineer", self.D, location))
        assert disq and category == "location", location

    def test_locked_out_wording_still_wins(self):
        disq, _w, category = scorer._hard_disqualify(
            _j("Junior Software Engineer", self.D + " US only, must be based in the US.", "Remote, Europe, USA"))
        assert disq and category == "location"

    @pytest.mark.parametrize("title", ["Werkstudent Data Engineering & Backend (w/m/div.)",
                                       "Praktikum Data Science", "Data Engineer (m/w/d)"])
    def test_a_title_written_for_the_german_market_places_a_remote_job(self, title):
        disq, why, _c = scorer._hard_disqualify(_j(title, self.D, "Remote"))
        assert not disq, (title, why)

    def test_such_a_title_does_not_move_a_foreign_city(self):
        disq, _w, category = scorer._hard_disqualify(_j("Praktikum Data Science", ENGLISH * 2, "Zürich, Switzerland"))
        assert disq and category == "location"


# -- the wrong-domain words have to describe the role ----------------------------
class TestWrongDomain:
    COMPANY = ("We are a leading medical device company with customers in radiology and oncology. "
               "Our products range from embedded systems to cloud services. ")

    @pytest.mark.parametrize("title", [
        "Data Scientist (m/f/d)", "Intern - Machine Learning 12 months (m/f/d)",
        "Internship - Statistical Methodology, Applied Statistics", "Quality & Data Intern (m/f/d)",
        "Internship Software Engineering - AI Debugging Tools",
    ])
    def test_a_data_or_ml_role_at_such_an_employer_goes_to_the_model(self, title):
        assert scorer._hard_disqualify(_j(title, self.COMPANY + ENGLISH * 2))[2] != "wrong_domain"

    @pytest.mark.parametrize("title", ["Embedded Software Engineer", "FPGA Developer", "Firmware Engineer (m/w/d)",
                                       "Hardware Design Engineer", "Software Engineer (Embedded)",
                                       "Embedded C++ Developer", "Softwareentwickler Embedded (m/w/d)",
                                       "Entwicklungsingenieur Hardware", "Software Engineer, Medical Devices",
                                       "ML Engineer (FPGA)"])
    def test_a_title_in_the_wrong_domain_still_drops(self, title):
        assert scorer._hard_disqualify(_j(title, ENGLISH * 2))[2] == "wrong_domain"

    @pytest.mark.parametrize("title", ["Software Engineer", "Software Developer C++", "Graduate Software Engineer"])
    def test_a_plain_software_title_still_drops_on_embedded_text(self, title):
        text = "You write firmware for a microcontroller and work with FPGA boards. " + ENGLISH
        assert scorer._hard_disqualify(_j(title, text))[2] == "wrong_domain"

    def test_a_title_that_names_no_such_work_still_drops_on_the_text(self):
        j = _j("Working Student (m/f/d)", "You develop firmware on a microcontroller and work with FPGA boards. " + ENGLISH)
        assert scorer._hard_disqualify(j)[2] == "wrong_domain"


# -- full-time titles that name tech work ----------------------------------------
class TestFullTimeTitles:
    @pytest.mark.parametrize("title", [
        "Forward Deployed Engineer (all genders)", "LLMOps Engineer für CI / CD & Inference Services (m/w/d)",
        "GIS & Database Engineer (m/w/d)", "Business-Intelligence Consultant (m/w/d)",
        "(Junior) Business Analyst:in Energiedatenmanagement (w/m/d) - Remote", "Robot Learning Engineer, Entry-Level",
        "Junior Technology Analyst",
        "Trainee Informationstechnologie mit Schwerpunkt Anwendungsbetrieb und Azure",
        "Trainee IT – International Graduate Program (w/m/d)", "Trainee - Central IT (m/f/d)",
        "Robotics Software Engineer (Junior / Mid-Level)",
        "Softwareentwickler IPS / Integrated Product Support (m/w/d)",
    ])
    def test_kept(self, title):
        j = _formed(_j(title))
        assert j["_form"] == "full-time" and main._fulltime_title_ok(j), title

    @pytest.mark.parametrize("title", [
        "IT Sales Manager", "IT Helpdesk Agent", "Make it happen: Sales Trainee", "Account Executive",
        "Data Engineer (Mid-Level)", "Software Engineer II", "Customer Support Specialist", "Office Manager",
        # words that only look like tech work
        "Stammdatenpflege Specialist", "Lagerdaten Sachbearbeiter", "Kundendatenpflege (m/w/d)",
        "Database Marketing Manager", "Product Engineer Mechanical", "Robot Welding Technician",
        "Robot Operator", "Field Ops Engineer", "Facility Ops Engineer", "Web Analyst SEO",
        "Junior IT Administrator", "Entry Level IT Technician", "Entwickler Vertrieb",
        "Kundendatenanalyse Sachbearbeiter", "Stammdatenmanagement Specialist",
        # a higher level stays a higher level
        "Associate Data Scientist (Mid-Level)", "Associate Software Engineer II",
    ])
    def test_still_dropped(self, title):
        j = _formed(_j(title))
        assert not main._fulltime_title_ok(j), title

    def test_soft_hyphens_do_not_hide_a_title(self):
        soft = "­"
        j = scrapers.job(f"Wis{soft}sen{soft}schaft{soft}li{soft}che:r Soft{soft}wa{soft}re{soft}ent{soft}wick{soft}ler:in",
                         "TIB", "Hannover, Germany", "https://example.com/jobs/2", "XING", ENGLISH)
        assert j["title"] == "Wissenschaftliche:r Softwareentwickler:in"
        assert main._fulltime_title_ok(_formed(j))


# -- student titles are never senior, degree lists are no doctorate ---------------
class TestStudentAndGraduateTitles:
    @pytest.mark.parametrize("title", [
        "Working student - PMT Architect - Software Engineering", "Werkstudent (m/w/d) Assistenz Head of Data",
        "Praktikant Lead Generation Analytics", "Student Assistant to the Director of AI Research",
    ])
    def test_a_student_title_passes_the_senior_filter(self, title):
        assert main._not_fulltime_senior(_formed(_j(title)))
        assert scorer._hard_disqualify(_j(title))[2] != "senior_title"

    @pytest.mark.parametrize("title", ["Software Architect", "Lead Data Scientist", "Head of Data", "Senior Data Engineer"])
    def test_a_senior_title_still_drops(self, title):
        assert not main._not_fulltime_senior(_formed(_j(title)))

    @pytest.mark.parametrize("title", [
        "Graduate Software Engineer - STEM Graduate (MSc/PhD)", "Data Scientist (BSc, MSc or PhD)",
        "Research Engineer AI (Master/PhD)",
    ])
    def test_a_list_of_accepted_degrees_is_no_doctorate(self, title):
        assert main._is_eligible_form(_j(title))

    @pytest.mark.parametrize("title", ["PhD Student Machine Learning", "Doctoral researcher (m/f/d)", "PhD position in NLP",
                                       "MSc/PhD Thesis: Computer Vision", "MSc/PhD Position Computer Vision",
                                       "Master or PhD Student - Computer Vision", "MA or PhD Fellow",
                                       "MSc, PhD Candidate Data Science"])
    def test_a_doctorate_or_thesis_still_drops(self, title):
        assert not main._is_eligible_form(_j(title))


# -- the ad text of a LinkedIn page with a pay range ------------------------------
class TestLinkedInPageWithAPayRange:
    AD = ("Airteam is a Berlin-based startup with an AI-powered cloud platform. As a computer vision engineer "
          "you train and evaluate models for roof detection, work with Python and PyTorch and ship them to "
          "production with the platform team. We work hybrid from Berlin and speak English in the team. " * 2)
    PAGE = (
        "<html><body><main>"
        "<section class='core-section-container my-3 compensation compensation--above-description'>"
        "<h2>Airteam provided pay range</h2><p>This range is provided by Airteam. Your actual pay will be based "
        "on your skills and experience — talk with your recruiter to learn more.</p>"
        "<h3>Base pay range</h3><div class='salary compensation__salary'>€60,000.00/yr - €80,000.00/yr</div>"
        "<p>Additional compensation types: annual bonus. Direct message the job poster from Airteam.</p></section>"
        "<section class='core-section-container my-3 description'><div class='description__text description__text--rich'>"
        "<section class='show-more-less-html'><div class='show-more-less-html__markup'>{ad}</div></section>"
        "</div></section></main></body></html>"
    )

    class _Resp:
        status_code = 200
        headers = {"content-type": "text/html"}

        def __init__(self, text):
            self.text = text

    def test_the_ad_is_read_not_the_pay_note(self, monkeypatch):
        page = self.PAGE.format(ad=self.AD)
        monkeypatch.setattr(scrapers.requests, "get", lambda url, **kw: self._Resp(page))
        text = scrapers._fetch_full_description("https://www.linkedin.com/jobs/view/4489316540")
        assert "computer vision engineer" in text and "PyTorch" in text
        assert "provided pay range" not in text

    def test_a_page_whose_only_description_block_is_the_pay_note_does_not_return_it(self, monkeypatch):
        page = ("<html><body><div class='wrap'>"
                "<section class='compensation compensation--above-description'><p>"
                + "This range is provided by Acme. Your actual pay will be based on your skills and experience. " * 4
                + "</p></section><article><p>" + self.AD + "</p></article></div></body></html>")
        monkeypatch.setattr(scrapers.requests, "get", lambda url, **kw: self._Resp(page))
        text = scrapers._fetch_full_description("https://example.org/jobs/7")
        assert "PyTorch" in text and "This range is provided" not in text
