"""The matching ladder and the write path, against a real database.

Two questions this file exists to answer, both of which only a real database
can settle:

1. Does the right person get recognised — and, just as important, do two
   different people stay two people?
2. Does the history survive every edit, round and deletion intact?
"""

from __future__ import annotations

import pytest

from app import service, store


def record(**payload):
    """Record one interview and return the result."""
    return service.record_interview(payload)


def ayesha_2024():
    return dict(
        name="Md. Ayesha Rahman",
        email="Ayesha.Rahman+jobs@gmail.com",
        phone="01711-223344",
        linkedin="linkedin.com/in/ayesha-rahman-12345",
        github="github.com/ayesha-rahman",
        position="Backend Engineer",
        status="Rejected",
        interviewDateTime="2024-01-05T10:00:00+00:00",
    )


# ── Recognising one person ───────────────────────────────────────────
class TestTheSamePersonIsRecognised:
    @pytest.mark.parametrize(
        "second, signal",
        [
            # Each rung on its own: only ONE shared field, spelled differently.
            (dict(name="Rahman, Ayesha", email="ayesharahman@googlemail.com"), "email"),
            (dict(name="Someone Else", linkedin="https://bd.linkedin.com/in/Ayesha-Rahman-12345/en"), "linkedin"),
            (dict(name="Someone Else", github="https://www.github.com/Ayesha-Rahman/cv"), "github"),
            (dict(name="Someone Else", phone="+880 1711 223344"), "phone"),
        ],
    )
    def test_each_rung_links_a_returning_candidate(self, second, signal):
        first = record(**ayesha_2024())
        again = record(**second, position="Senior Backend Engineer",
                       interviewDateTime="2026-09-01T10:00:00+00:00")
        assert again["linkedTo"] == first["linkedTo"], f"the {signal} rung failed to link"
        assert again["candidate"]["interviewCount"] == 2

    def test_the_headline_case_end_to_end(self):
        """Ayesha applies twice, 18 months apart, spelling nothing the same."""
        first = record(**ayesha_2024())
        again = record(
            name="Rahman, Ayesha",
            email="ayesharahman@googlemail.com",
            phone="+880 1711 223344",
            position="Senior Backend Engineer",
            status="Selected",
            interviewDateTime="2026-09-01T10:00:00+00:00",
        )
        assert again["linkedTo"] == first["linkedTo"]

        profile = service.candidate_profile(first["linkedTo"])
        assert len(profile["history"]) == 2
        # Newest first, so the 2026 application leads.
        assert profile["history"][0]["position"] == "Senior Backend Engineer"
        assert profile["history"][1]["position"] == "Backend Engineer"
        # And the 2024 verdict is still there to read.
        assert profile["history"][1]["status"] == "Rejected"

    def test_identity_keys_accumulate_rather_than_overwrite(self):
        first = record(**ayesha_2024())
        record(name="Ayesha Rahman", email="ayesha.rahman@corp-example.com",
               phone="01711223344")
        candidate = store.get_candidate(first["linkedTo"])
        assert sorted(candidate["emails"]) == [
            "ayesha.rahman@corp-example.com", "ayesharahman@gmail.com"
        ]
        # The university address from 2024 still finds her.
        assert store.find_by_email("ayesharahman@gmail.com")[0]["id"] == first["linkedTo"]


# ── Keeping two people apart ─────────────────────────────────────────
class TestDifferentPeopleStayApart:
    def test_the_same_name_is_never_enough(self):
        first = record(**ayesha_2024())
        other = record(name="Ayesha Rahman", email="ayesha.r@othercompany.com",
                       phone="01744556677", position="Financial Analyst")
        assert other["linkedTo"] != first["linkedTo"]
        assert other["linkReason"] == "new candidate"
        assert store.count_candidates() == 2

    def test_a_name_match_is_surfaced_but_below_the_bar(self):
        record(**ayesha_2024())
        result = service.check(dict(name="Ayesha Rahman"))
        assert result["isReturning"] is False
        assert result["matches"], "the name match should still be shown to a human"
        assert result["matches"][0]["signal"] == "name"
        assert result["matches"][0]["certain"] is False

    def share_a_desk_phone(self):
        """Two people on one desk phone — a family or office line.

        The second one only becomes a separate record because the interviewer
        said so; a bare phone match would otherwise link them at 0.85.
        """
        first = record(name="Karim Hossain", email="karim@outlook.com",
                       phone="01822556677")
        second = service.record_interview(
            dict(name="Farhana Akter", email="farhana@yahoo.com", phone="01822556677"),
            force_new=True,
        )
        return first, second

    def test_the_interviewer_can_overrule_the_matcher(self):
        first, second = self.share_a_desk_phone()
        assert second["linkedTo"] != first["linkedTo"]
        assert store.count_candidates() == 2
        assert second["candidate"]["displayName"] == "Farhana Akter"

    def test_a_shared_phone_then_drops_below_the_auto_link_bar(self):
        # Once two candidates hold the number it is a shared line, not proof
        # of identity, so the next application asks instead of linking.
        self.share_a_desk_phone()
        result = service.check(dict(name="Third Person", phone="01822556677"))
        assert result["isReturning"] is False
        assert len(result["matches"]) == 2
        assert all(m["certain"] is False for m in result["matches"])
        assert "shared line" in result["matches"][0]["detail"]

    def test_a_third_application_on_a_shared_phone_starts_a_new_person(self):
        self.share_a_desk_phone()
        third = record(name="Tanvir Islam", phone="01822556677")
        assert third["linkReason"] == "new candidate"
        assert store.count_candidates() == 3

    def test_dot_significant_domains_are_two_people(self):
        a = record(name="K Hossain", email="k.hossain@outlook.com")
        b = record(name="K Hossain", email="khossain@outlook.com")
        assert a["linkedTo"] != b["linkedTo"]


# ── The strongest signal wins ────────────────────────────────────────
class TestConfidence:
    def test_one_candidate_matching_twice_is_reported_once(self):
        record(**ayesha_2024())
        result = service.check(dict(name="Ayesha Rahman",
                                    email="ayesharahman@gmail.com",
                                    phone="01711223344"))
        assert len(result["matches"]) == 1, "email and phone are one person, not two rows"
        assert result["matches"][0]["confidence"] == 1.0
        assert result["matches"][0]["signal"] == "email"

    def test_check_writes_nothing(self):
        record(**ayesha_2024())
        before = (store.count_candidates(), store.count_interviews())
        for _ in range(3):
            service.check(dict(name="Ayesha Rahman", email="ayesharahman@gmail.com"))
        assert (store.count_candidates(), store.count_interviews()) == before

    def test_an_interviewer_can_force_the_link(self):
        first = record(**ayesha_2024())
        forced = service.record_interview(
            dict(name="Completely Different", email="nothing@shared.com"),
            candidate_id=first["linkedTo"],
        )
        assert forced["linkedTo"] == first["linkedTo"]
        assert forced["linkReason"] == "confirmed by interviewer"

    def test_forcing_a_link_to_a_deleted_candidate_is_refused(self):
        with pytest.raises(ValueError):
            service.record_interview(dict(name="X"), candidate_id="cand_gone")

    def test_different_person_beats_even_an_identical_email(self):
        """The strongest possible signal still loses to a human's answer.

        Shared mailboxes exist (`careers@`, a couple using one address), and
        the interviewer looking at both records is the one who knows.
        """
        first = record(**ayesha_2024())
        forced = service.record_interview(
            dict(name="Ayesha Rahman", email="Ayesha.Rahman+jobs@gmail.com"),
            force_new=True,
        )
        assert forced["linkedTo"] != first["linkedTo"]
        assert store.count_candidates() == 2


# ── History ──────────────────────────────────────────────────────────
class TestHistoryIsSavedCorrectly:
    def test_every_form_field_round_trips(self):
        payload = dict(
            team="Engineering", name="Karim Hossain", interviewer="Nadim Saker",
            interviewDateTime="2026-09-03T10:00:00+00:00",
            skillSet="Spark, Airflow, dbt", education="MSc Statistics, DU",
            experience="5 years", salaryExpectation="110,000 BDT",
            reasonForLeaving="Relocating back to Dhaka.", noticePeriod="2 months",
            position="Data Engineer", note="Strong pipeline design.",
            feedback1="Round one went well.", feedback2="Round two confirmed it.",
            feedback3="Panel agreed.", status="Offer Sent",
            email="karim.hossain@outlook.com", phone="01822556677",
            linkedin="linkedin.com/in/karim-hossain-98765",
            github="github.com/karim-h",
        )
        result = record(**payload)
        saved = store.get_interview(result["interview"]["id"], result["linkedTo"])
        for key, value in payload.items():
            assert saved[key] == value, f"{key} did not survive the round trip"

    def test_the_cv_is_attached(self):
        cv = {"fileName": "karim.pdf", "storedName": "abc_karim.pdf",
              "sizeBytes": 91234, "url": "/api/cv/abc_karim.pdf"}
        result = service.record_interview(dict(name="Karim Hossain"), cv=cv)
        saved = store.get_interview(result["interview"]["id"], result["linkedTo"])
        assert saved["cv"] == cv

    def test_history_is_newest_first(self):
        first = record(**ayesha_2024())
        cid = first["linkedTo"]
        for when, position in [("2025-05-01T10:00:00+00:00", "Mid"),
                               ("2026-09-01T10:00:00+00:00", "Senior")]:
            service.record_interview(
                dict(name="Ayesha Rahman", email="ayesharahman@gmail.com",
                     position=position, interviewDateTime=when))
        history = service.candidate_profile(cid)["history"]
        assert [r["position"] for r in history] == ["Senior", "Mid", "Backend Engineer"]

    def test_the_summary_tracks_the_latest_round(self):
        first = record(**ayesha_2024())
        record(name="Ayesha Rahman", email="ayesharahman@gmail.com",
               position="Senior Backend Engineer", status="Selected",
               interviewDateTime="2026-09-01T10:00:00+00:00")
        candidate = store.get_candidate(first["linkedTo"])
        assert candidate["interviewCount"] == 2
        assert candidate["lastStatus"] == "Selected"
        assert candidate["lastPosition"] == "Senior Backend Engineer"
        assert candidate["firstInterviewAt"] == "2024-01-05T10:00:00+00:00"
        assert candidate["lastInterviewAt"] == "2026-09-01T10:00:00+00:00"


class TestLaterRoundsEditOneRecord:
    def test_a_partial_update_leaves_other_fields_alone(self):
        result = record(name="Karim Hossain", email="karim@outlook.com",
                        position="Data Engineer", feedback1="Round one fine.",
                        status="Interviewing")
        iid, cid = result["interview"]["id"], result["linkedTo"]

        service.update_interview(iid, cid, {"feedback2": "Round two confirmed."})
        service.update_interview(iid, cid, {"status": "Offer Sent"})

        saved = store.get_interview(iid, cid)
        assert saved["feedback1"] == "Round one fine."
        assert saved["feedback2"] == "Round two confirmed."
        assert saved["status"] == "Offer Sent"
        assert saved["position"] == "Data Engineer"
        # Still ONE interview: rounds edit the record, they do not add rows.
        assert store.count_interviews_for(cid) == 1

    def test_two_interviewers_editing_different_fields_both_survive(self):
        result = record(name="Karim Hossain", email="karim@outlook.com")
        iid, cid = result["interview"]["id"], result["linkedTo"]
        service.update_interview(iid, cid, {"feedback2": "Panel note."})
        service.update_interview(iid, cid, {"status": "Selected"})
        saved = store.get_interview(iid, cid)
        assert saved["feedback2"] == "Panel note."
        assert saved["status"] == "Selected"

    def test_an_unchanged_update_reports_no_change(self):
        result = record(name="Karim Hossain", status="Applied")
        iid, cid = result["interview"]["id"], result["linkedTo"]
        assert service.update_interview(iid, cid, {"status": "Applied"})["changed"] is False

    def test_a_corrected_email_keeps_matching_both_ways(self):
        result = record(name="Karim Hossain", email="karim@outlook.com")
        iid, cid = result["interview"]["id"], result["linkedTo"]
        service.update_interview(iid, cid, {"email": "karim.hossain@outlook.com"})
        assert store.find_by_email("karim.hossain@outlook.com")[0]["id"] == cid
        assert store.find_by_email("karim@outlook.com")[0]["id"] == cid

    def test_editing_a_missing_interview_is_refused(self):
        with pytest.raises(ValueError):
            service.update_interview("int_gone", "cand_gone", {"status": "X"})


class TestDeletion:
    def test_deleting_one_of_several_keeps_the_candidate(self):
        first = record(**ayesha_2024())
        cid = first["linkedTo"]
        second = record(name="Ayesha Rahman", email="ayesharahman@gmail.com",
                        position="Senior", status="Selected",
                        interviewDateTime="2026-09-01T10:00:00+00:00")
        out = service.remove_interview(second["interview"]["id"], cid)
        assert out["candidateDeleted"] is False
        assert out["remaining"] == 1
        # The summary goes back in step with what is left.
        assert store.get_candidate(cid)["lastStatus"] == "Rejected"
        assert store.get_candidate(cid)["interviewCount"] == 1

    def test_deleting_the_last_one_removes_the_candidate(self):
        result = record(**ayesha_2024())
        out = service.remove_interview(result["interview"]["id"], result["linkedTo"])
        assert out["candidateDeleted"] is True
        assert store.get_candidate(result["linkedTo"]) is None
        assert (store.count_candidates(), store.count_interviews()) == (0, 0)

    def test_deleting_a_candidate_takes_their_history(self):
        first = record(**ayesha_2024())
        record(name="Ayesha Rahman", email="ayesharahman@gmail.com", position="Senior")
        assert store.delete_candidate(first["linkedTo"]) is True
        assert store.count_interviews() == 0, "the cascade left orphans behind"

    def test_deleting_a_missing_interview_is_refused(self):
        with pytest.raises(ValueError):
            service.remove_interview("int_gone", "cand_gone")


class TestSearch:
    @pytest.fixture(autouse=True)
    def people(self):
        record(name="Ayesha Rahman", email="ayesha@gmail.com", phone="01711223344",
               position="Backend Engineer")
        record(name="Karim Hossain", email="karim.hossain@outlook.com",
               github="github.com/karim-h", position="Data Engineer")

    @pytest.mark.parametrize(
        "term, expected",
        [("ayesha", 1), ("AYESHA", 1), ("karim", 1), ("outlook", 1),
         ("engineer", 2), ("", 2), ("nobody", 0)],
    )
    def test_search(self, term, expected):
        assert len(store.list_candidates(term)) == expected

    @pytest.mark.parametrize("term", ["%", "_", "%%", "\\", "100%"])
    def test_sql_wildcards_are_taken_literally(self, term):
        # "%" must mean the character, not "match everything".
        assert store.list_candidates(term) == []


class TestStatusFilter:
    """The candidate list's status chips filter on the LATEST status."""

    @pytest.fixture(autouse=True)
    def people(self):
        record(name="Ayesha Rahman", email="ayesha@gmail.com", status="Rejected",
               position="Backend Engineer", interviewDateTime="2024-01-05T10:00:00+00:00")
        # Ayesha again, later: her latest status is now Selected.
        record(name="Ayesha Rahman", email="ayesha@gmail.com", status="Selected",
               position="Senior Backend Engineer",
               interviewDateTime="2026-09-01T10:00:00+00:00")
        record(name="Karim Hossain", email="karim@outlook.com", status="NSOC")
        record(name="Farhana Akter", email="farhana@yahoo.com", status="Internal")
        record(name="Tanvir Islam", email="tanvir@example.com", status="Internal")
        record(name="No Status Yet", email="nostatus@example.com")

    @pytest.mark.parametrize(
        "status, expected",
        [("Selected", ["Ayesha Rahman"]),
         ("NSOC", ["Karim Hossain"]),
         ("Internal", ["Farhana Akter", "Tanvir Islam"]),
         ("Joined", [])],
    )
    def test_filters_by_status(self, status, expected):
        names = sorted(c["displayName"] for c in store.list_candidates(status=status))
        assert names == expected

    def test_an_old_status_does_not_bring_someone_back(self):
        # Ayesha WAS rejected in 2024, but is Selected now.
        assert store.list_candidates(status="Rejected") == []

    def test_no_filter_means_everyone(self):
        assert len(store.list_candidates()) == 5
        assert len(store.list_candidates(status="")) == 5

    def test_status_and_search_combine(self):
        assert [c["displayName"] for c in
                store.list_candidates("farhana", status="Internal")] == ["Farhana Akter"]
        assert store.list_candidates("karim", status="Internal") == []

    def test_counts_per_status(self):
        counts = store.count_candidates_by_status()
        assert counts["Selected"] == 1
        assert counts["NSOC"] == 1
        assert counts["Internal"] == 2
        assert counts[""] == 1                 # no status yet, still counted
        assert "Rejected" not in counts        # nobody's LATEST status
        assert sum(counts.values()) == 5

    def test_counts_respect_the_search(self):
        assert store.count_candidates_by_status("farhana") == {"Internal": 1}

    def test_a_status_edit_moves_the_candidate_between_filters(self):
        karim = store.list_candidates(status="NSOC")[0]
        interview = store.list_interviews_for(karim["id"])[0]
        service.update_interview(interview["id"], karim["id"], {"status": "Internal"})
        assert store.list_candidates(status="NSOC") == []
        assert len(store.list_candidates(status="Internal")) == 3


class TestPhoneMatching:
    """Phone numbers are FOUND on the last 9 digits and LINKED on the whole number."""

    @pytest.mark.parametrize(
        "second",
        ["+88 01711-223344", "+88 1711223344", "8801711223344", "1711223344",
         "0088 01711223344",
         "\u09e6\u09e7\u09ed\u09e7\u09e7\u09e8\u09e8\u09e9\u09e9\u09ea\u09ea"],
    )
    def test_any_spelling_of_the_same_bd_number_links(self, second):
        first = record(name="Karim Hossain", email="karim@outlook.com", phone="01711-223344")
        again = record(name="Someone Else", email="other@example.com", phone=second)
        assert again["linkedTo"] == first["linkedTo"], f"{second!r} did not link"
        assert again["candidate"]["interviewCount"] == 2

    def test_a_bangla_digit_number_is_stored_in_ascii(self):
        result = record(name="Karim Hossain",
                        phone="\u09e6\u09e7\u09ed\u09e7\u09e7\u09e8\u09e8\u09e9\u09e9\u09ea\u09ea")
        assert result["candidate"]["phones"] == ["+8801711223344"]
        assert result["candidate"]["phoneKeys"] == ["711223344"]

    def test_same_last_9_digits_in_another_country_is_only_a_possible_match(self):
        """+91 97112 23344 shares its last 9 digits with 01711-223344.

        That finds the Bangladeshi record — worth a look, since it could be
        one person writing a number two ways — but it must never link two
        different people on its own.
        """
        record(name="Karim Hossain", email="karim@outlook.com", phone="01711-223344")
        result = service.check(dict(name="Ravi Kumar", phone="+91 97112 23344"))

        assert result["isReturning"] is False
        match = result["matches"][0]
        assert match["signal"] == "phone"
        assert match["confidence"] == 0.60
        assert match["certain"] is False
        assert "+8801711223344" in match["detail"]
        assert "country code differs" in match["detail"]

    def test_and_saving_it_starts_a_new_person(self):
        record(name="Karim Hossain", email="karim@outlook.com", phone="01711-223344")
        ravi = record(name="Ravi Kumar", email="ravi@example.in", phone="+91 97112 23344")
        assert ravi["linkReason"] == "new candidate"
        assert store.count_candidates() == 2

    def test_an_exact_number_still_links_when_a_foreign_tail_twin_exists(self):
        # Once the Indian number is on file too, the BD number's key finds
        # two records — but only ONE holds the exact number. That is not a
        # shared line, and the exact holder still links automatically.
        karim = record(name="Karim Hossain", email="karim@outlook.com", phone="01711-223344")
        record(name="Ravi Kumar", email="ravi@example.in", phone="+91 97112 23344")

        again = record(name="K. Hossain", phone="+88 01711 223344")
        assert again["linkedTo"] == karim["linkedTo"]

    def test_the_shared_line_guard_counts_exact_holders(self):
        # Two BD candidates on one desk phone: still a shared line, still asks.
        record(name="Karim Hossain", email="karim@outlook.com", phone="01822556677")
        service.record_interview(
            dict(name="Farhana Akter", email="farhana@yahoo.com", phone="01822556677"),
            force_new=True,
        )
        result = service.check(dict(name="Third", phone="+88 01822-556677"))
        assert result["isReturning"] is False
        assert {m["confidence"] for m in result["matches"]} == {0.50}


class TestPhoneSearch:
    """The search box finds a number typed the way the CV printed it."""

    @pytest.fixture(autouse=True)
    def people(self):
        record(name="Mahmudul Hasan", email="mahmudul@example.com", phone="+880 1955-946392")
        record(name="Karim Hossain", email="karim@outlook.com", phone="01711223344")

    @pytest.mark.parametrize(
        "typed",
        ["01955-946392", "01955 946392", "+88 01955-946392", "+880 1955 946392",
         "8801955946392", "(+88) 01955946392",
         "\u09e6\u09e7\u09ef\u09eb\u09eb\u09ef\u09ea\u09ec\u09e9\u09ef\u09e8"],
    )
    def test_any_spelling_finds_the_candidate(self, typed):
        names = [c["displayName"] for c in store.list_candidates(typed)]
        assert names == ["Mahmudul Hasan"], f"{typed!r} found {names}"

    def test_a_partial_number_still_works_as_a_substring(self):
        assert [c["displayName"] for c in store.list_candidates("1955946")] == ["Mahmudul Hasan"]

    @pytest.mark.parametrize("typed", ["mahmudul", "karim@outlook", "2024", "Engineer"])
    def test_non_phone_searches_behave_as_before(self, typed):
        # None of these is a phone number, so none gets the phone clause.
        from app.store import _phone_search_key
        assert _phone_search_key(typed) == ""

    def test_phone_search_and_status_filter_combine(self):
        assert store.list_candidates("+88 01955-946392", status="Selected") == []
        assert len(store.list_candidates("+88 01955-946392", status="")) == 1
