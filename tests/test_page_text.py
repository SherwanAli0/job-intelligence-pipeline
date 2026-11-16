"""Page text keeps its lines, and the length cut keeps the language lines (16 November 2026).

Found while checking the German-level gate against the first production run:
LinkedIn job pages reached the pre-screen and the scorer as ONE line, cut at
3,000 characters. Of 25 real ads read again in full, four demanded fluent
German in a line the cut had removed, one hard demand was excused by the
softener of the next list item, and one ad with German as a plus was dropped.
"""
from bs4 import BeautifulSoup

import evidence
import scorer
import scrapers


def _text(html):
    return scrapers._block_text(BeautifulSoup(html, "html.parser"))


class TestBlockText:
    def test_a_list_keeps_one_line_per_item(self):
        html = "<ul><li>Excellent English and German skills</li><li>Strong analytical skills</li></ul>"
        assert _text(html) == "Excellent English and German skills\nStrong analytical skills"

    def test_inline_tags_do_not_break_a_sentence(self):
        assert _text("<p>You speak <b>fluent</b> <i>German</i> and English.</p>") == "You speak fluent German and English."

    def test_br_and_paragraphs_break_lines_and_empty_lines_go(self):
        html = "<div><p>First</p><p></p><p>Second<br>Third</p></div>"
        assert _text(html) == "First\nSecond\nThird"

    def test_neighbouring_inline_pieces_get_a_space(self):
        assert _text("<p><span>Python</span><span>SQL</span></p>") == "Python SQL"

    def test_the_gate_reads_the_items_apart(self, monkeypatch):
        monkeypatch.setitem(scorer.CANDIDATE_FACTS, "german_level", "B1")
        plus = _text("<ul><li>Sehr gute Englischkenntnisse</li><li>Deutschkenntnisse</li>"
                     "<li>Idealerweise Auslandserfahrung</li></ul>")
        hard = _text("<ul><li>Sehr gute Deutsch- und fließende Englischkenntnisse</li>"
                     "<li>Idealerweise Auslandserfahrung</li></ul>")
        assert not scorer._requires_fluent_german(plus.lower(), plus)
        assert scorer._requires_fluent_german(hard.lower(), hard)      # the next item's softener stays there


class TestTheCutKeepsLanguageLines:
    BODY = "\n".join("Line %03d about the product, the team and the work" % i for i in range(90))

    def test_short_text_is_returned_as_it_is(self):
        assert scrapers._cut_keeping_language_lines("short text", 3000) == "short text"

    def test_text_without_a_language_line_is_cut_as_before(self):
        assert scrapers._cut_keeping_language_lines(self.BODY, 3000) == self.BODY[:3000]

    def test_a_language_line_behind_the_cut_is_kept(self):
        text = self.BODY + "\nSehr gute Deutsch- und Englischkenntnisse\nWir bieten frisches Obst"
        cut = scrapers._cut_keeping_language_lines(text, 3000)
        assert len(cut) <= 3000
        assert cut.endswith("[...]\nSehr gute Deutsch- und Englischkenntnisse")
        assert "frisches Obst" not in cut
        assert evidence.required_level(cut, "german")[0] == 5

    def test_at_most_three_language_lines_are_kept(self):
        extra = ["Deutsch C1", "English B2", "Französisch von Vorteil", "Spanish is a plus"]
        cut = scrapers._cut_keeping_language_lines(self.BODY + "\n" + "\n".join(extra), 3000)
        assert cut.split("[...]\n")[1].split("\n") == extra[:3]

    def test_flattened_text_keeps_a_window_around_the_mention(self):
        text = ("word " * 650) + ("filler " * 60) + "You bring fluent German and English " + ("tail " * 80)
        cut = scrapers._cut_keeping_language_lines(text, 3000)
        assert len(cut) <= 3000
        assert "fluent German and English" in cut.split("[...]\n")[1]


class _Response:
    status_code = 200
    headers = {"content-type": "text/html"}

    def __init__(self, text):
        self.text = text


class TestFetchFullDescription:
    PAGE = ("<html><body><main><h1>Working Student Data</h1><p>" + ("We build data products. " * 150) + "</p>"
            "<ul><li>Python and SQL</li><li>Du sprichst und schreibst <b>fließend</b> Deutsch und gutes Englisch</li>"
            "<li>Team spirit</li></ul></main></body></html>")

    def test_the_language_line_at_the_end_of_a_long_page_reaches_the_gate(self, monkeypatch):
        monkeypatch.setattr(scrapers, "_guarded_get", lambda url, **kw: _Response(self.PAGE))
        monkeypatch.setitem(scorer.CANDIDATE_FACTS, "german_level", "B1")
        text = scrapers._fetch_full_description("https://example.org/jobs/working-student-data")
        assert len(text) <= 3000
        assert "Du sprichst und schreibst fließend Deutsch und gutes Englisch" in text
        assert scorer._requires_fluent_german(text.lower(), text)

    def test_list_items_arrive_on_their_own_lines(self, monkeypatch):
        page = ("<html><body><main><p>" + ("About the role. " * 20) + "</p><ul><li>English and German skills</li>"
                "<li>Strong analytical skills</li></ul></main></body></html>")
        monkeypatch.setattr(scrapers, "_guarded_get", lambda url, **kw: _Response(page))
        text = scrapers._fetch_full_description("https://example.org/jobs/analyst")
        assert "English and German skills\nStrong analytical skills" in text
