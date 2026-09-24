"""Put a handful of realistic records in, so the UI has something to show.

    python seed_demo.py           add the demo records
    python seed_demo.py --clear   remove them again

Every demo record is tagged ``demoSeed: True``, and --clear deletes exactly
those — it will not touch real interviews somebody recorded.
"""

from __future__ import annotations

import logging
import sys

from app import service, store

logging.basicConfig(level=logging.WARNING)

# (payload, how many days ago the interview was)
PEOPLE = [
    # Ayesha applies twice, two years apart, spelling everything differently
    # the second time — this is the case the whole app exists for.
    (dict(name="Md. Ayesha Rahman", email="Ayesha.Rahman+jobs@gmail.com",
          phone="01711-223344", linkedin="linkedin.com/in/ayesha-rahman-12345",
          github="github.com/ayesha-rahman", team="Engineering",
          position="Backend Engineer", interviewer="Nadim Saker",
          skillSet="Python, Django, MySQL", education="BSc CSE, BUET",
          experience="2 years", salaryExpectation="45,000 BDT",
          reasonForLeaving="Company downsized the team.", noticePeriod="Immediate",
          feedback1="Good fundamentals. Weak on system design and SQL tuning.",
          note="Worth a second look if she gains depth.", status="Rejected"), 640),
    (dict(name="Rahman, Ayesha", email="ayesharahman@googlemail.com",
          phone="+880 1711 223344", team="Engineering",
          position="Senior Backend Engineer", interviewer="Nadim Saker",
          skillSet="Python, FastAPI, Azure, PostgreSQL, Kubernetes",
          education="BSc CSE, BUET", experience="4 years",
          salaryExpectation="95,000 BDT",
          reasonForLeaving="Wants ownership of a whole service.",
          noticePeriod="1 month",
          feedback1="Completely different candidate from 2024 — designed the "
                    "rate limiter cleanly and explained the trade-offs.",
          feedback2="Panel agreed. Strong hire.", status="Selected"), 12),

    (dict(name="Karim Hossain", email="karim.hossain@outlook.com",
          phone="01822556677", github="github.com/karim-h", team="Data",
          position="Data Engineer", interviewer="Tanvir Ahmed",
          skillSet="Spark, Airflow, dbt, Snowflake", education="MSc Statistics, DU",
          experience="5 years", salaryExpectation="110,000 BDT",
          reasonForLeaving="Relocating back to Dhaka.", noticePeriod="2 months",
          feedback1="Excellent pipeline design. Slow on the SQL exercise.",
          feedback2="Second round confirmed the depth. Offer.",
          status="Offer Sent"), 21),

    (dict(name="Farhana Akter", email="farhana.akter@gmail.com",
          phone="01933445566", linkedin="linkedin.com/in/farhana-akter-dev",
          team="Product", position="Product Designer", interviewer="Sadia Islam",
          skillSet="Figma, design systems, user research",
          education="BBA, IBA", experience="3 years",
          salaryExpectation="65,000 BDT", reasonForLeaving="No growth path.",
          noticePeriod="1 month",
          feedback1="Portfolio is strong. Communicates decisions well.",
          status="Interviewing"), 5),

    (dict(name="Tanvir Islam", email="tanvir@example.com", phone="01655778899",
          team="Engineering", position="Frontend Engineer",
          interviewer="Nadim Saker", skillSet="React, TypeScript, Next.js",
          education="BSc CSE, AIUB", experience="1 year",
          salaryExpectation="40,000 BDT", reasonForLeaving="First job change.",
          noticePeriod="2 weeks",
          feedback1="Keen, but not enough production experience yet.",
          note="Revisit in a year.", status="On Hold"), 95),

    # Same name as Ayesha, different person — must NOT merge.
    (dict(name="Ayesha Rahman", email="ayesha.r@othercompany.com",
          phone="01744556677", team="Finance", position="Financial Analyst",
          interviewer="Sadia Islam", education="MBA Finance, NSU",
          experience="6 years", salaryExpectation="90,000 BDT",
          noticePeriod="1 month", skillSet="Excel, SAP, forecasting",
          feedback1="Sharp on modelling. Different Ayesha entirely.",
          status="Selected"), 30),
]


def iso_days_ago(days: int) -> str:
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")


def seed() -> None:
    print(f"Seeding {len(PEOPLE)} demo interviews into {store.settings.db_target}...\n")
    for payload, days in PEOPLE:
        body = dict(payload)
        body["interviewDateTime"] = iso_days_ago(days)
        result = service.record_interview(body)
        cand = result["candidate"]
        print(f"  {cand['displayName']:22} #{cand['interviewCount']}  "
              f"{result['linkReason']}")
        # Tag it so --clear can find it again.
        interview = store.get_interview(result["interview"]["id"], cand["id"])
        if interview:
            interview["demoSeed"] = True
            store.save_interview(interview)
        candidate = store.get_candidate(cand["id"])
        if candidate:
            candidate["demoSeed"] = True
            store.save_candidate(candidate)

    print("\nDone. Start the app and open the Candidates tab.")
    print("Ayesha Rahman should show 2 visits; the Finance Ayesha should be separate.")


def clear() -> None:
    rows = store.list_demo_candidates()
    if not rows:
        print("No demo records found.")
        return
    for candidate in rows:
        # Their interviews go with them: the foreign key cascades.
        store.delete_candidate(candidate["id"])
        print(f"  removed {candidate['displayName']}")
    print(f"\nRemoved {len(rows)} demo candidate(s).")


if __name__ == "__main__":
    try:
        if "--clear" in sys.argv:
            clear()
        else:
            seed()
    finally:
        # The pool runs worker threads; without this the script sits for a few
        # seconds at exit and complains on the way out.
        store.close()
