"""Pulling contact details out of CV text.

These are pure functions over text, so no database is needed. The awkward
cases all come from the same place: PDF extraction flattens a two-column
layout into lines that break in the wrong places.
"""

from __future__ import annotations

import pytest

from app.cv_parser import (
    extract_email,
    extract_github,
    extract_linkedin,
    extract_name,
    extract_phone,
    extract_text,
    parse_cv,
)


class TestEmailExtraction:
    @pytest.mark.parametrize(
        "text, expected",
        [
            ("Email: karim.hossain@outlook.com", "karim.hossain@outlook.com"),
            ("karim@gmail.com", "karim@gmail.com"),
            ("Contact | ayesha.rahman+jobs@googlemail.com | Dhaka",
             "ayesha.rahman+jobs@googlemail.com"),
            ("farhana@yahoo.co.uk", "farhana@yahoo.co.uk"),
            ("student.id@student.buet.ac.bd", "student.id@student.buet.ac.bd"),
            ("dev@my-company.io", "dev@my-company.io"),
            ("E-mail:tanvir@example.com,", "tanvir@example.com"),
        ],
    )
    def test_every_provider_shape(self, text, expected):
        assert extract_email(text) == expected

    def test_nothing_to_find(self):
        assert extract_email("Karim Hossain\nDhaka, Bangladesh") is None


class TestPhoneExtraction:
    @pytest.mark.parametrize(
        "text",
        ["Phone: 01822556677", "Mobile: +880 1822 556677", "Cell  01822-556677",
         "Tel: +8801822556677", "01822 556677"],
    )
    def test_common_layouts(self, text):
        from app.identity import phone_match_key

        assert phone_match_key(extract_phone(text)) == "822556677"

    def test_a_year_range_is_not_a_phone_number(self):
        assert extract_phone("Experience 2019 - 2021") is None


class TestProfileUrls:
    @pytest.mark.parametrize(
        "text, expected",
        [
            ("https://www.linkedin.com/in/karim-hossain-98765", "karim-hossain-98765"),
            ("linkedin.com/in/karim-hossain-98765", "karim-hossain-98765"),
            ("https://bd.linkedin.com/in/karim-hossain-98765/", "karim-hossain-98765"),
            ("LinkedIn: https://www.linkedin.com/in/karim-hossain-98765 |", "karim-hossain-98765"),
        ],
    )
    def test_linkedin_on_one_line(self, text, expected):
        from app.identity import normalize_linkedin

        assert normalize_linkedin(extract_linkedin(text)) == expected

    @pytest.mark.parametrize(
        "text, expected",
        [
            # A slug genuinely split by the column edge must be rejoined, or it
            # truncates to "abdulla" — and since a profile match auto-links,
            # two people breaking at the same prefix would become one.
            ("linkedin.com/in/abdulla\nh-al-mamun-m-sc-cs", "abdullah-al-mamun-m-sc-cs"),
            ("linkedin.com/in/karim\n-hossain-98765", "karim-hossain-98765"),
        ],
    )
    def test_a_wrapped_slug_is_rejoined(self, text, expected):
        from app.identity import normalize_linkedin

        assert normalize_linkedin(extract_linkedin(text)) == expected

    @pytest.mark.parametrize(
        "next_line",
        ["https://github.com/karim-h", "github.com/karim-h", "karim@outlook.com",
         "Skills", "EXPERIENCE", "2019 - Present", "+880 1822 556677"],
    )
    def test_the_next_line_is_not_swallowed(self, next_line):
        """A CV stacks its contact links; the line after LinkedIn is not a slug.

        Regression test. "https" and "github.com" and "karim" are all
        perfectly slug-shaped right up to the ":" or "/" or "@" that follows,
        so the URL used to absorb them and match nobody.
        """
        from app.identity import normalize_linkedin

        text = f"linkedin.com/in/karim-hossain-98765\n{next_line}"
        assert normalize_linkedin(extract_linkedin(text)) == "karim-hossain-98765"

    @pytest.mark.parametrize(
        "text, expected",
        [
            ("https://github.com/karim-h", "karim-h"),
            ("github.com/karim-h", "karim-h"),
            ("GitHub: https://www.github.com/karim-h/my-cv", "karim-h"),
        ],
    )
    def test_github(self, text, expected):
        from app.identity import normalize_github

        assert normalize_github(extract_github(text)) == expected

    def test_a_full_contact_block(self):
        """The layout almost every CV actually uses."""
        from app.identity import normalize_github, normalize_linkedin

        text = (
            "Karim Hossain\n"
            "Data Engineer | Dhaka, Bangladesh\n"
            "karim.hossain@outlook.com\n"
            "+880 1822 556677\n"
            "https://www.linkedin.com/in/karim-hossain-98765\n"
            "https://github.com/karim-h\n"
            "\n"
            "SUMMARY\n"
        )
        assert extract_name(text) == "Karim Hossain"
        assert extract_email(text) == "karim.hossain@outlook.com"
        assert normalize_linkedin(extract_linkedin(text)) == "karim-hossain-98765"
        assert normalize_github(extract_github(text)) == "karim-h"


class TestNameExtraction:
    @pytest.mark.parametrize(
        "first_line, expected",
        [
            ("Karim Hossain", "Karim Hossain"),
            ("Md. Ayesha Rahman", "Md. Ayesha Rahman"),
            ("KARIM HOSSAIN", "KARIM HOSSAIN"),
            ("Karim  Hossain", "Karim Hossain"),  # letter-spacing collapsed
        ],
    )
    def test_the_name_at_the_top(self, first_line, expected):
        assert extract_name(f"{first_line}\nData Engineer\nkarim@outlook.com") == expected

    @pytest.mark.parametrize("header", ["CURRICULUM VITAE", "Resume", "Personal Details",
                                        "CONTACT", "Profile"])
    def test_a_section_header_is_not_a_name(self, header):
        assert extract_name(f"{header}\nKarim Hossain") == "Karim Hossain"

    def test_a_line_with_digits_is_not_a_name(self):
        assert extract_name("01822556677\nKarim Hossain") == "Karim Hossain"


class TestTextExtraction:
    def test_plain_text(self):
        text, how = extract_text("cv.txt", b"Karim Hossain\nkarim@outlook.com")
        assert how == "text"
        assert "Karim Hossain" in text

    @pytest.mark.parametrize("name", ["cv.xlsx", "cv.png", "cv"])
    def test_unsupported_types_are_refused(self, name):
        with pytest.raises(ValueError):
            extract_text(name, b"data")

    def test_legacy_doc_says_what_to_do(self):
        with pytest.raises(ValueError, match="docx"):
            extract_text("cv.doc", b"data")


class TestParseCv:
    def test_the_whole_thing(self):
        result = parse_cv("cv.txt", (
            "Karim Hossain\n"
            "karim.hossain@outlook.com\n"
            "+880 1822 556677\n"
            "https://www.linkedin.com/in/karim-hossain-98765\n"
            "https://github.com/karim-h\n"
        ).encode())
        assert result["extractedVia"] == "text"
        assert result["name"] == "Karim Hossain"
        assert result["email"] == "karim.hossain@outlook.com"
        assert result["phone"]
        assert result["linkedin"] and result["github"]

    def test_a_cv_with_no_contact_details_returns_nulls(self):
        result = parse_cv("cv.txt", b"Some notes about nothing in particular.\n")
        assert result["email"] is None
        assert result["linkedin"] is None
        assert result["github"] is None
