"""End-to-end checks for the v3 agent brain (no network, no LLM key).

Run:  python3 tests/run_tests.py
Fixed clock: Monday 16 Nov 2026, 10:00 (clinic open) unless a test
builds its own agent with a different clock.
"""
import datetime
import os
import re
import sys
import tempfile

os.environ["AGENT_DATA_DIR"] = tempfile.mkdtemp(prefix="clinic-agent-test-")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import Agent, load_config                      # noqa: E402
from agent import core                                    # noqa: E402
from agent.queue import TokenQueue                        # noqa: E402

CFG = load_config(os.path.join(os.path.dirname(__file__), "..",
                               "config", "sample_clinic.json"))
MON_10AM = datetime.datetime(2026, 11, 16, 10, 0)
PASS = []


def make_agent(now=MON_10AM):
    q = TokenQueue(path=os.path.join(os.environ["AGENT_DATA_DIR"],
                                     f"q-{now.timestamp()}.json"),
                   services=CFG["services"],
                   providers=[d["id"] for d in CFG["doctors"]],
                   now_fn=lambda: now)
    return Agent(CFG, queue=q, now_fn=lambda: now), q


def check(name, cond):
    PASS.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name)


def say(agent, session, *msgs):
    out = ""
    for m in msgs:
        out = agent.reply(session, m)
    return out


agent, queue = make_agent()

r = say(agent, "s1", "hello")
check("greeting names the business and mentions check-in",
      "CarePoint" in r and "check you in" in r)
r = say(agent, "s1", "what are your fees")
check("fees list a service price", "Dental Checkup" in r and "2,500" in r)
r = say(agent, "s1", "what is your address")
check("'address' returns the address, not the doctor list",
      "Jaranwala" in r and "Our doctors" not in r)
r = say(agent, "s1", "who are your doctors")
check("doctors listed", "Dr. Ayesha Khan" in r)
check("GP Friday hours corrected to 1 PM", "Fri till 1 PM" in r)
r = say(agent, "s1", "what time do you open")
check("hours answer includes live OPEN badge", "OPEN now" in r)
r = say(agent, "s1", "i have severe chest pain")
check("emergency gets 1122", "1122" in r)
r = say(agent, "s1", "mujhe saans nahi aa raha")
check("roman urdu emergency gets 1122", "1122" in r)
r = say(agent, "s1", "which medicine for fever should i take")
check("medical advice refused, booking offered",
      "can't give medical advice" in r)

# --- booking happy path: dental by number, tomorrow, 6 PM ---
r = say(agent, "b1", "book", "2", "tomorrow", "6 pm", "Test Patient",
        "yes")
m = re.search(r"(BK-[0-9A-F]{8})", r)
check("booking confirmed with a Booking ID", bool(m))
check("confirmation explains check-in", bool(m) and "Check in" in r)
BID_TOMORROW = m.group(1) if m else "BK-00000000"

# --- 'No, book later' must NOT book (the 'ok' inside 'book' bug) ---
before = len(core.load_bookings())
r = say(agent, "b2", "book", "1", "today", "10:30 am", "Cancel Patient",
        "no, book later")
check("'no, book later' cancels", "not booked" in r)
check("cancel saved no booking", len(core.load_bookings()) == before)

# --- single letter must not pick a service ---
r = say(agent, "b3", "book", "d")
check("single letter re-asks with the numbered list",
      "reply with the" in r and "number" in r)
r = say(agent, "b3", "1")
check("number then picks the service", "which date" in r.lower())

# --- date validation ---
r = say(agent, "b4", "book", "1", "banana")
check("nonsense date rejected", "couldn't understand that date" in r)
r = say(agent, "b4", "22 nov")
check("Sunday rejected (closed)", "closed on Sundays" in r)
r = say(agent, "b4", "10 nov")
check("past date rejected", "already passed" in r)
r = say(agent, "b5", "book", "5", "tomorrow")
check("skin doctor unavailable on Tuesday",
      "not available on Tuesdays" in r)
r = say(agent, "b6", "book", "1", "friday", "2 pm")
check("Friday 2 PM rejected with the 1-3 PM break explained",
      "1-3 PM" in r)

# --- double booking blocked, next free slot suggested ---
say(agent, "b7", "book", "1", "today", "11 am", "First Patient", "yes")
r = say(agent, "b11", "book", "1", "today")
r = agent.reply("b11", "11 am")
check("second booking at a taken slot is refused",
      "already booked" in r and "next free slot" in r)

# --- check-in: today's booking gets a token, tomorrow's must wait ---
r = say(agent, "c1", "book", "1", "today", "12 pm", "Checkin Patient",
        "yes")
bid_today = re.search(r"(BK-[0-9A-F]{8})", r).group(1)
r = say(agent, "c1", f"check in {bid_today}")
check("check-in issues a token", "Checked in! Token T-" in r)
r = say(agent, "c1", f"check in {bid_today}")
check("second check-in says already checked in",
      "already checked in" in r)
r = say(agent, "c2", f"check in {BID_TOMORROW}")
check("tomorrow's booking cannot check in today",
      "appointment is on" in r)

# --- tokens: GP instant, duplicate returns existing, dental books ---
r = say(agent, "t1", "token")
check("token asks GP or Lab", "GP and Lab only" in r)
r = say(agent, "t1", "1", "Token Patient")
check("GP token issued", "Token T-" in r and "issued" in r)
r = say(agent, "t2", "token", "1", "Token Patient")
check("duplicate token returns the existing one",
      "already have token" in r)
r = say(agent, "t3", "token", "2", "Lab Patient")
check("lab token issued", "Token T-" in r)
r = say(agent, "t4", "i need a dental token")
r = say(agent, "t4", "dental")
check("dental token routes to booking", "by appointment" in r)

# --- handoff really alerts reception ---
r = say(agent, "h1", "talk to a human")
check("handoff says reception alerted", "alerted reception" in r)
check("staff alert recorded for the board",
      any(a["kind"] == "human" for a in queue.status()["alerts"]))

# --- tokens refused while closed (Sunday agent) ---
sun_agent, _ = make_agent(datetime.datetime(2026, 11, 15, 10, 0))
r = say(sun_agent, "x1", "token", "1", "Sunday Patient")
check("token refused while clinic is closed", "closed right now" in r)

failed = [n for n, ok in PASS if not ok]
print(f"\n{len(PASS) - len(failed)}/{len(PASS)} passed")
sys.exit(1 if failed else 0)
