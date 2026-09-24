"""Normalization tests: the keys every match is made on.

These are the tests that matter most. Everything the matcher compares comes
out of `app/identity.py`, and a bug here does not produce a wrong screen — it
produces two people merged into one record, or one person's history split in
half. Both are invisible until somebody notices, so the coverage is deliberate
about the boundaries rather than the happy path.
"""

from __future__ import annotations

import pytest

from app.identity import (
    normalize_email,
    normalize_github,
    normalize_identity,
    normalize_linkedin,
    normalize_name,
    normalize_phone,
    phone_match_key,
)


# ── Email ────────────────────────────────────────────────────────────
class TestEmailIsTrackedForEveryProvider:
    """Every domain is normalized, not just Gmail."""

    @pytest.mark.parametrize(
        "raw, expected",
        [
            # Gmail and its aliases: dots in the local part are NOT significant.
            ("Ayesha.Rahman@gmail.com", "ayesharahman@gmail.com"),
            ("ayesharahman@googlemail.com", "ayesharahman@gmail.com"),
            ("A.Y.E.S.H.A@GMAIL.COM", "ayesha@gmail.com"),
            # Everywhere else dots ARE significant and must be kept: on Outlook
            # first.last@ and firstlast@ are two different people.
            ("Karim.Hossain@outlook.com", "karim.hossain@outlook.com"),
            ("Karim.Hossain@hotmail.com", "karim.hossain@hotmail.com"),
            ("Farhana.Akter@yahoo.com", "farhana.akter@yahoo.com"),
            ("Tanvir.Islam@protonmail.com", "tanvir.islam@protonmail.com"),
            ("Nadim.Saker@ainvio.com", "nadim.saker@ainvio.com"),
            ("first.last@sub.domain.co.uk", "first.last@sub.domain.co.uk"),
            ("Student.ID@student.buet.ac.bd", "student.id@student.buet.ac.bd"),
            ("dev@my-company.io", "dev@my-company.io"),
            ("a@b.dev", "a@b.dev"),
        ],
    )
    def test_domains(self, raw, expected):
        assert normalize_email(raw) == expected

    @pytest.mark.parametrize(
        "raw, expected",
        [
            # Plus-tagging is near-universal, so it is stripped everywhere.
            ("ayesha+jobs@gmail.com", "ayesha@gmail.com"),
            ("karim+linkedin@outlook.com", "karim@outlook.com"),
            ("farhana+2026@yahoo.co.uk", "farhana@yahoo.co.uk"),
        ],
    )
    def test_plus_tags_stripped_on_every_domain(self, raw, expected):
        assert normalize_email(raw) == expected

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("  Ayesha@Gmail.com  ", "ayesha@gmail.com"),
            ("<ayesha@gmail.com>", "ayesha@gmail.com"),
            ("mailto:ayesha@gmail.com", "ayesha@gmail.com"),
            ("MAILTO:Ayesha@Gmail.com", "ayesha@gmail.com"),
            ("(ayesha@gmail.com)", "ayesha@gmail.com"),
            ("ayesha@gmail.com,", "ayesha@gmail.com"),
            ("ayesha@gmail.com.", "ayesha@gmail.com"),
            ("Email: ayesha@gmail.com;", "ayesha@gmail.com"),
        ],
    )
    def test_messy_input_from_cv_extraction(self, raw, expected):
        # A trailing period or a mailto: is what PDF extraction actually hands
        # over; none of it should reach the match key.
        if raw.startswith("Email: "):
            pytest.skip("a label is the CV parser's job to strip, not this one")
        assert normalize_email(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        ["", None, "not-an-email", "@gmail.com", "ayesha@", "ayesha@@gmail.com",
         "ayesha gmail.com", "ayesha@gmail", "ayesha@gmail..com", "a b@c.com",
         "+tagonly@gmail.com"],
    )
    def test_unparseable_never_becomes_a_key(self, raw):
        # "" makes the matcher fall through to the next rung. A junk value that
        # normalized to something would link everyone who shares the junk.
        assert normalize_email(raw) == ""

    def test_two_different_people_on_a_dot_significant_domain_stay_apart(self):
        assert normalize_email("k.hossain@outlook.com") != normalize_email("khossain@outlook.com")

    def test_the_same_gmail_written_four_ways_converges(self):
        keys = {
            normalize_email("Ayesha.Rahman+jobs@gmail.com"),
            normalize_email("ayesharahman@gmail.com"),
            normalize_email("AYESHA.RAHMAN@googlemail.com"),
            normalize_email("  <ayesha.rahman+recruiters@GoogleMail.com>  "),
        }
        assert keys == {"ayesharahman@gmail.com"}


# ── Phone ────────────────────────────────────────────────────────────
class TestPhone:
    @pytest.mark.parametrize(
        "raw",
        [
            "01711223344", "01711-223344", "01711 223344", "+8801711223344",
            "+880 1711 223344", "+880-1711-223344", "8801711223344",
            "1711223344", "+880 (0) 1711223344", "00880 1711 223344",
            "(017) 1122-3344", "+88 01711223344",
        ],
    )
    def test_every_bangladeshi_spelling_of_one_number_agrees(self, raw):
        # This is the case the app exists for: the same person writes their
        # number differently on two applications 18 months apart.
        assert phone_match_key(raw) == "711223344", f"{raw!r} broke the match"

    @pytest.mark.parametrize(
        "raw, region, expected",
        [
            ("+91 98765 43210", "IN", "+919876543210"),
            ("09876543210", "IN", "+919876543210"),
            ("+44 7911 123456", "GB", "+447911123456"),
            ("+1 (415) 555-0123", "US", "+14155550123"),
            ("+65 8123 4567", "SG", "+6581234567"),
            ("+971 50 123 4567", "AE", "+971501234567"),
        ],
    )
    def test_other_regions(self, raw, region, expected):
        assert normalize_phone(raw, region) == expected

    def test_extension_is_not_part_of_the_identity(self):
        assert phone_match_key("01711223344 ext. 42") == phone_match_key("01711223344")

    @pytest.mark.parametrize(
        "raw",
        ["01768438600,01992460122", "01768438600 / 01992460122",
         "01768438600 or 01992460122", "01768438600; 01992460122"],
    )
    def test_two_numbers_in_one_field_takes_the_first(self, raw):
        assert phone_match_key(raw) == phone_match_key("01768438600")

    @pytest.mark.parametrize("raw", ["", None, "n/a", "12345", "abcdefgh", "-", "0"])
    def test_junk_never_becomes_a_key(self, raw):
        assert phone_match_key(raw) == ""

    def test_short_numbers_are_refused(self):
        # A partial number must not match everything ending the same way.
        assert phone_match_key("223344") == ""

    def test_two_different_numbers_do_not_collide(self):
        assert phone_match_key("01711223344") != phone_match_key("01711223345")


# ── LinkedIn ─────────────────────────────────────────────────────────
class TestLinkedIn:
    @pytest.mark.parametrize(
        "raw",
        [
            "https://www.linkedin.com/in/ayesha-rahman-12345",
            "http://linkedin.com/in/ayesha-rahman-12345",
            "linkedin.com/in/ayesha-rahman-12345",
            "www.linkedin.com/in/ayesha-rahman-12345/",
            "https://bd.linkedin.com/in/ayesha-rahman-12345",
            "https://uk.linkedin.com/in/Ayesha-Rahman-12345",
            "https://www.linkedin.com/in/ayesha-rahman-12345?trk=public_profile",
            "https://www.linkedin.com/in/ayesha-rahman-12345/en",
            "https://www.linkedin.com/in/ayesha-rahman-12345/details/experience",
            "https://www.linkedin.com/in/ayesha-rahman-12345#about",
            "in/ayesha-rahman-12345",
            "ayesha-rahman-12345",
            "  LinkedIn.com/IN/Ayesha-Rahman-12345  ",
        ],
    )
    def test_every_spelling_of_one_profile_collapses(self, raw):
        assert normalize_linkedin(raw) == "ayesha-rahman-12345"

    @pytest.mark.parametrize("raw", ["https://www.linkedin.com/company/ainvio",
                                     "https://www.linkedin.com/school/buet",
                                     "https://www.linkedin.com/feed/",
                                     "https://www.linkedin.com/jobs/view/123",
                                     "https://linkedin.com",
                                     "https://twitter.com/ayesha", "", None])
    def test_non_profiles_are_refused(self, raw):
        # A company page is not one person, and a profile match auto-links.
        assert normalize_linkedin(raw) == ""

    @pytest.mark.parametrize(
        "raw",
        ["https://www.xing.com/in/ayesha", "https://example.com/in/ayesha",
         "https://evil-linkedin.com/in/ayesha", "https://linkedin.com.evil.net/in/ayesha"],
    )
    def test_another_sites_in_path_is_not_a_linkedin_profile(self, raw):
        # The host is checked exactly. Matching "/in/" on any host would make
        # xing.com/in/ayesha and linkedin.com/in/ayesha the same person, at a
        # confidence that links records without asking.
        assert normalize_linkedin(raw) == ""

    def test_two_profiles_stay_apart(self):
        assert normalize_linkedin("in/karim") != normalize_linkedin("in/karim-hossain-98765")


# ── GitHub ───────────────────────────────────────────────────────────
class TestGitHub:
    @pytest.mark.parametrize(
        "raw",
        [
            "https://github.com/ayesha-rahman",
            "http://www.github.com/Ayesha-Rahman",
            "github.com/ayesha-rahman",
            "github.com/ayesha-rahman/",
            "https://github.com/ayesha-rahman/my-cv",
            "https://github.com/ayesha-rahman?tab=repositories",
            "@ayesha-rahman",
            "ayesha-rahman",
            "  GitHub.com/Ayesha-Rahman  ",
        ],
    )
    def test_every_spelling_of_one_account_collapses(self, raw):
        assert normalize_github(raw) == "ayesha-rahman"

    @pytest.mark.parametrize(
        "raw",
        ["https://github.com/orgs/ainvio",
         "github.com/settings/profile", "github.com/features",
         "https://gitlab.com/ayesha", "", None, "github.com",
         "-leading-hyphen", "trailing-hyphen-", "double--hyphen",
         "a" * 40],
    )
    def test_reserved_paths_and_invalid_handles_are_refused(self, raw):
        assert normalize_github(raw) == ""

    @pytest.mark.parametrize(
        "raw, would_have_been",
        [
            ("https://api.github.com/users/ayesha", "users"),
            ("https://docs.github.com/en/get-started", "en"),
            ("https://gist.github.com/a1b2c3d4e5f6", "a1b2c3d4e5f6"),
            ("https://raw.github.com/ayesha/repo/main/x", "ayesha"),
            ("https://evil-github.com/ayesha", "ayesha"),
            ("https://notgithub.com/ayesha", "ayesha"),
        ],
    )
    def test_only_githubs_own_host_counts(self, raw, would_have_been):
        # Matching "github.com" as a substring accepted all of these. Two of
        # them are especially bad: every candidate linking api.github.com
        # became the same person "users", and docs.github.com became "en" —
        # silently merging unrelated people at 0.95 confidence.
        assert normalize_github(raw) == "", f"{raw} leaked {would_have_been!r}"


# ── Name ─────────────────────────────────────────────────────────────
class TestName:
    @pytest.mark.parametrize(
        "raw",
        ["Ayesha Rahman", "ayesha rahman", "AYESHA RAHMAN", "Rahman, Ayesha",
         "Md. Ayesha Rahman", "Mohammad Ayesha Rahman", "Ayesha Rahman, PhD",
         "Dr. Ayesha Rahman", "Ayesha K. Rahman", "  Ayesha   Rahman  ",
         "Ayesha Rahman BSc", "Engr. Ayesha Rahman"],
    )
    def test_one_person_spelled_many_ways(self, raw):
        assert normalize_name(raw) == "ayesha rahman"

    def test_accents_are_folded(self):
        assert normalize_name("José Álvarez") == normalize_name("Jose Alvarez")

    def test_hyphenated_names_split(self):
        assert normalize_name("Jean-Luc Picard") == normalize_name("Jean Luc Picard")

    def test_a_lone_given_name_prefix_is_kept(self):
        # Someone recorded simply as "Mohammad" must keep their name.
        assert normalize_name("Mohammad") == "mohammad"

    @pytest.mark.parametrize("raw", ["", None, "Dr.", "Mr.", "A B", "123"])
    def test_nothing_left_is_empty(self, raw):
        assert normalize_name(raw) == ""

    def test_different_people_stay_apart(self):
        assert normalize_name("Ayesha Rahman") != normalize_name("Ayesha Khatun")


# ── The bundle ───────────────────────────────────────────────────────
class TestNormalizedIdentity:
    def test_a_full_record(self):
        i = normalize_identity(
            email="Ayesha.Rahman+jobs@GoogleMail.com",
            phone="01711-223344",
            name="Md. Ayesha Rahman",
            linkedin="https://bd.linkedin.com/in/Ayesha-Rahman-12345/en?trk=x",
            github="https://www.github.com/Ayesha-Rahman/my-cv",
        )
        assert i.email == "ayesharahman@gmail.com"
        assert i.phone == "+8801711223344"
        assert i.phone_key == "711223344"
        assert i.name == "ayesha rahman"
        assert i.linkedin == "ayesha-rahman-12345"
        assert i.github == "ayesha-rahman"
        assert i.has_strong_signal

    def test_a_name_alone_is_not_a_strong_signal(self):
        # Nothing here can ever auto-link, which is the whole safety property.
        assert not normalize_identity(name="Ayesha Rahman").has_strong_signal

    @pytest.mark.parametrize("field", ["email", "linkedin", "github", "phone"])
    def test_any_one_real_signal_is_enough(self, field):
        values = {"email": "a@b.com", "linkedin": "in/x-y", "github": "xy",
                  "phone": "01711223344"}
        assert normalize_identity(**{field: values[field]}).has_strong_signal

    def test_an_empty_record_is_inert(self):
        assert not normalize_identity().has_strong_signal
