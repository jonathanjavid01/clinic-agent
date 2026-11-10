#!/usr/bin/env python3
"""Tests for the v3 per-doctor token queues. Run: python3 tests/test_queue.py"""
import datetime
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent.queue import TokenQueue, display_name  # noqa: E402
from agent import load_config                    # noqa: E402

CFG = load_config(os.path.join(os.path.dirname(__file__), "..",
                               "config", "sample_clinic.json"))
NOW = [datetime.datetime(2026, 11, 16, 10, 0)]  # Monday 10 AM
PASS = []


def check(name, cond):
    PASS.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name)


path = os.path.join(tempfile.mkdtemp(), "queue.json")
q = TokenQueue(path=path, services=CFG["services"],
               providers=[d["id"] for d in CFG["doctors"]],
               now_fn=lambda: NOW[0])

a = q.issue("gp", "Ahmed Raza", "General Consultation (GP)",
            source="whatsapp", arrived=False)
b = q.issue("dental", "Fatima Noor", "Dental Checkup & Cleaning",
            source="walkin", arrived=True)
check("numbering is global across queues", (a["token"], b["token"]) == ("T-01", "T-02"))
st = q.status()
check("queues are separate per doctor",
      st["queues"]["gp"]["waiting_count"] == 1
      and st["queues"]["dental"]["waiting_count"] == 1)

c = q.issue("gp", "Imran Sheikh", "General Consultation (GP)",
            source="walkin", arrived=True)
st = q.call_next("gp")
check("call next skips the not-arrived remote token",
      st["called"]["patient"] == "Imran Sheikh")
st = q.status()
check("remote token still waiting after skip",
      st["queues"]["gp"]["waiting"][0]["token"] == "T-01")

q.mark_arrived("gp", "T-01")
st = q.call_next("gp")
check("finishing a visit tallies the fee (GP 800)",
      st["queues"]["gp"]["earnings"] == 800 and st["called"]["token"] == "T-01")
check("earnings total matches", q.status()["earnings_total"] == 800)

d = q.issue("gp", "Post Pone", "General Consultation (GP)",
            source="walkin", arrived=True)
e = q.issue("gp", "Last One", "General Consultation (GP)",
            source="walkin", arrived=True)
q.postpone("gp", d["token"])
w = q.status()["queues"]["gp"]["waiting"]
check("postpone moves to the end with a tag",
      w[-1]["token"] == d["token"] and w[-1]["tag"] == "Postponed")

q.not_arrived("gp", e["token"])
st = q.status()
check("not arrived removes and counts a no-show",
      st["noshows"] == 1
      and all(t["token"] != e["token"]
              for t in st["queues"]["gp"]["waiting"]))

check("duplicate lookup finds the patient's token",
      q.find_patient_token("gp", "Post Pone")["token"] == d["token"])
check("TV privacy name is first name + initial",
      display_name("Ahmed Raza") == "Ahmed R.")
w = q.status()["queues"]["gp"]["waiting"]
check("wait estimates grow down the queue",
      all(w[i]["est_wait_min"] <= w[i + 1]["est_wait_min"]
          for i in range(len(w) - 1)))

al = q.add_alert("human", "A patient asked to talk to a human.")
check("alert recorded with a timestamp", al["at"] == "10:00")
q.alert_done(al["id"])
check("alert can be marked done",
      q.status()["alerts"][0]["done"] is True)

q2 = TokenQueue(path=path, services=CFG["services"],
                providers=[d["id"] for d in CFG["doctors"]],
                now_fn=lambda: NOW[0])
check("state survives a restart (same file)",
      q2.status()["noshows"] == 1)
NOW[0] = datetime.datetime(2026, 11, 17, 9, 0)
check("a new day resets the queues and numbering",
      q2.status()["date"] == "2026-11-17"
      and q2.status()["total_waiting"] == 0)

failed = [n for n, ok in PASS if not ok]
print(f"\n{len(PASS) - len(failed)}/{len(PASS)} passed")
sys.exit(1 if failed else 0)
