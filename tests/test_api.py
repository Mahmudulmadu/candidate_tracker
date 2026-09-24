"""The HTTP surface, driven the way web/app.js drives it.

`test_matching.py` covers the service layer directly. This file exists for the
things that can only go wrong in between — request shapes, status codes, and
in particular the difference between a field that is ABSENT and one that is
explicitly null, which is invisible to a service-layer test and was how the
"different person" button came to be ignored.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import store
from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def save(client, **payload):
    response = client.post("/api/interviews", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


class TestHealthAndMeta:
    def test_health(self, client):
        body = client.get("/api/health").json()
        assert body["ok"] is True
        assert body["database"] and body["endpoint"]

    def test_meta_serves_the_form_choices(self, client):
        body = client.get("/api/meta").json()
        assert "Selected" in body["statuses"]
        assert 0 < body["autoLinkMinConfidence"] <= 1
        assert body["phoneRegion"]

    def test_the_ui_is_served(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert "<!doctype html" in response.text.lower()


class TestRecordingAnInterview:
    def test_a_name_is_required(self, client):
        assert client.post("/api/interviews", json={"email": "a@b.com"}).status_code == 400
        assert client.post("/api/interviews", json={"name": "   "}).status_code == 400

    def test_absent_candidate_id_lets_the_matcher_decide(self, client):
        first = save(client, name="Karim Hossain", email="karim@outlook.com")
        again = save(client, name="Karim Hossain", email="karim@outlook.com")
        assert again["linkedTo"] == first["linkedTo"]

    def test_explicit_null_forces_a_new_person(self, client):
        """The 'Different person' button, over the wire.

        Regression test. `candidateId: null` used to be indistinguishable from
        the key being absent, so the matcher ran anyway and filed the second
        person's interview in the first person's history, under their name.
        """
        first = save(client, name="Karim Hossain", email="karim@outlook.com",
                     phone="01822556677")
        second = save(client, name="Farhana Akter", email="farhana@yahoo.com",
                      phone="01822556677", candidateId=None)

        assert second["linkedTo"] != first["linkedTo"]
        assert second["candidate"]["displayName"] == "Farhana Akter"
        assert client.get("/api/dashboard").json()["candidates"] == 2

    def test_a_candidate_id_files_it_against_that_person(self, client):
        first = save(client, name="Karim Hossain", email="karim@outlook.com")
        second = save(client, name="Totally Different", email="other@example.com",
                      candidateId=first["linkedTo"])
        assert second["linkedTo"] == first["linkedTo"]
        assert second["linkReason"] == "confirmed by interviewer"

    def test_a_stale_candidate_id_is_a_400(self, client):
        assert client.post(
            "/api/interviews", json={"name": "X", "candidateId": "cand_gone"}
        ).status_code == 400


class TestCheck:
    def test_check_reports_a_returning_candidate_with_history(self, client):
        save(client, name="Md. Ayesha Rahman", email="Ayesha.Rahman+jobs@gmail.com",
             phone="01711-223344", position="Backend Engineer", status="Rejected")
        body = client.post("/api/check", json={
            "name": "Rahman, Ayesha", "email": "ayesharahman@googlemail.com",
        }).json()

        assert body["isReturning"] is True
        assert body["hasStrongSignal"] is True
        assert body["bestMatch"]["signal"] == "email"
        assert body["bestMatch"]["confidence"] == 1.0
        assert body["history"][0]["status"] == "Rejected"
        assert body["normalized"]["email"] == "ayesharahman@gmail.com"

    def test_check_on_an_empty_form_is_harmless(self, client):
        body = client.post("/api/check", json={}).json()
        assert body["matches"] == []
        assert body["hasStrongSignal"] is False

    def test_a_name_only_check_never_claims_a_returning_candidate(self, client):
        save(client, name="Ayesha Rahman", email="ayesha@gmail.com")
        body = client.post("/api/check", json={"name": "Ayesha Rahman"}).json()
        assert body["isReturning"] is False
        assert body["matches"][0]["certain"] is False


class TestEditingAndDeleting:
    def test_patch_needs_a_candidate_id(self, client):
        result = save(client, name="Karim Hossain")
        response = client.patch(f"/api/interviews/{result['interview']['id']}",
                                json={"status": "Selected"})
        assert response.status_code == 400

    def test_patch_updates_only_what_was_sent(self, client):
        result = save(client, name="Karim Hossain", position="Data Engineer",
                      feedback1="Round one fine.")
        iid, cid = result["interview"]["id"], result["linkedTo"]
        body = client.patch(f"/api/interviews/{iid}",
                            json={"candidateId": cid, "feedback2": "Round two."}).json()
        assert body["changed"] is True
        assert body["interview"]["feedback1"] == "Round one fine."
        assert body["interview"]["position"] == "Data Engineer"

    def test_delete_removes_the_candidate_when_it_was_their_last(self, client):
        result = save(client, name="Karim Hossain")
        body = client.delete(f"/api/interviews/{result['interview']['id']}",
                             params={"candidateId": result["linkedTo"]}).json()
        assert body["candidateDeleted"] is True
        assert client.get(f"/api/candidates/{result['linkedTo']}").status_code == 404

    def test_delete_of_a_missing_interview_is_a_400(self, client):
        assert client.delete("/api/interviews/int_gone",
                             params={"candidateId": "cand_gone"}).status_code == 400


class TestListing:
    def test_search_and_detail(self, client):
        result = save(client, name="Karim Hossain", email="karim.hossain@outlook.com",
                      position="Data Engineer")
        assert len(client.get("/api/candidates").json()["candidates"]) == 1
        assert len(client.get("/api/candidates",
                              params={"search": "karim"}).json()["candidates"]) == 1
        assert len(client.get("/api/candidates",
                              params={"search": "nobody"}).json()["candidates"]) == 0

        detail = client.get(f"/api/candidates/{result['linkedTo']}").json()
        assert detail["candidate"]["displayName"] == "Karim Hossain"
        assert len(detail["history"]) == 1

    def test_an_unknown_candidate_is_a_404(self, client):
        assert client.get("/api/candidates/cand_gone").status_code == 404

    def test_the_limit_is_clamped(self, client):
        # A hand-edited URL must not be able to ask for the whole table.
        assert client.get("/api/candidates", params={"limit": 99999}).status_code == 200
        assert client.get("/api/candidates", params={"limit": -5}).status_code == 200


class TestCvUpload:
    def test_a_text_cv_is_parsed_and_checked(self, client):
        cv = (
            "Karim Hossain\n"
            "Email: karim.hossain@outlook.com\n"
            "Phone: 01822556677\n"
            "https://www.linkedin.com/in/karim-hossain-98765\n"
            "https://github.com/karim-h\n"
        ).encode()
        body = client.post("/api/cv/parse", files={"file": ("cv.txt", cv, "text/plain")}).json()
        assert body["name"] == "Karim Hossain"
        assert body["email"] == "karim.hossain@outlook.com"
        assert body["linkedin"].endswith("karim-hossain-98765")
        assert body["github"].endswith("karim-h")
        assert body["cv"]["fileName"] == "cv.txt"
        assert body["check"]["matches"] == []  # nobody in the database yet

    def test_an_uploaded_cv_can_be_downloaded_again(self, client):
        body = client.post("/api/cv/parse",
                           files={"file": ("cv.txt", b"Karim Hossain\nx@y.com\n", "text/plain")}).json()
        assert client.get(body["cv"]["url"]).status_code == 200

    def test_an_empty_file_is_refused(self, client):
        assert client.post("/api/cv/parse",
                           files={"file": ("cv.txt", b"", "text/plain")}).status_code == 400

    def test_an_unsupported_type_is_refused(self, client):
        assert client.post("/api/cv/parse",
                           files={"file": ("cv.xyz", b"data", "application/x")}).status_code == 400

    @pytest.mark.parametrize("name", ["../../../etc/passwd", "..\\..\\secret.txt", "a/b/c.txt"])
    def test_a_download_cannot_escape_the_cv_folder(self, client, name):
        assert client.get(f"/api/cv/{name}").status_code in (404, 400)


class TestUploadedCvsAreIsolated:
    def test_two_uploads_of_the_same_filename_do_not_collide(self, client):
        first = client.post("/api/cv/parse",
                            files={"file": ("resume.txt", b"Karim Hossain\na@b.com", "text/plain")}).json()
        second = client.post("/api/cv/parse",
                             files={"file": ("resume.txt", b"Farhana Akter\nc@d.com", "text/plain")}).json()
        assert first["cv"]["storedName"] != second["cv"]["storedName"]
        assert "Farhana" in client.get(second["cv"]["url"]).text
