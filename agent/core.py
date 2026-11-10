"""Clinic WhatsApp agent - core brain.

Design rules (v1):
- Everything business-specific comes from ONE config file (config/*.json).
  A new client = a new config file. No code changes.
- Deterministic first: hours, fees, location, doctors, booking and safety
  rules are answered by code from the config - never invented by a model.
- The LLM (agent/llm.py) only phrases open question answers, grounded in
  the same config facts, and only when an API key is configured.
- Guardrails: emergencies and medical-advice requests are never answered
  clinically; they are escalated to a human with the clinic's phone number.
- Every exchange is appended to a JSONL conversation log; bookings are
  appended to a bookings file. Both paths are configurable by environment.
"""
import json
import os
import re
import time
import uuid

from . import llm

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOOKINGS_FILE = os.environ.get("BOOKINGS_FILE", os.path.join(BASE, "data", "bookings.json"))
LOG_FILE = os.environ.get("LOG_FILE", os.path.join(BASE, "data", "conversations.jsonl"))

EMERGENCY_WORDS = [
    "chest pain", "heart attack", "stroke", "unconscious", "not breathing",
    "breathing problem", "heavy bleeding", "bleeding a lot", "accident",
    "seizure", "overdose", "suicide", "sans nahi", "khoon beh", "behosh",
    "dil ka dora", "emergency",
]
MEDICAL_ADVICE_PATTERNS = [
    r"what (medicine|tablet|drug|antibiotic)", r"which (medicine|tablet|drug)",
    r"prescribe", r"diagnos", r"treatment for", r"cure for",
    r"konsi dawa", r"kaunsi dawa", r"dawai bata", r"ilaaj bata",
    r"should i take", r"dose of", r"dosage",
]
HANDOFF_WORDS = [
    "human", "real person", "staff", "receptionist", "call me", "agent",
    "complaint", "shikayat", "manager", "someone real", "baat karwa",
]
BOOKING_WORDS = [
    "book", "appointment", "appoint", "buking", "booking", "schedule a",
    "appointment lena", "appointment chahiye", "time lena", "milna hai doctor",
]
GREETINGS = ["hello", "hi", "hey", "salam", "assalam", "aoa", "aoa."]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().strip())


def _tokens(text: str) -> set:
    return set(re.findall(r"[a-z0-9]+", _norm(text)))


class Agent:
    def __init__(self, config_path: str):
        with open(config_path, encoding="utf-8") as f:
            self.cfg = json.load(f)
        self.sessions: dict[str, dict] = {}
        # Build searchable fact chunks from the config.
        self.facts: list[tuple[set, str]] = []
        for faq in self.cfg.get("faqs", []):
            self.facts.append((_tokens(faq["q"]), faq["a"]))
        for svc in self.cfg.get("services", []):
            fee = "Free" if svc["fee_pkr"] == 0 else f"PKR {svc['fee_pkr']:,}"
            txt = f"{svc['name']}: fee {fee}, about {svc['duration_min']} minutes."
            if svc.get("note"):
                txt += " " + svc["note"]
            self.facts.append((_tokens(svc["name"]), txt))
        for doc in self.cfg.get("doctors", []):
            txt = (f"{doc['name']} is our {doc['role']}. Available {doc['days']}, "
                   f"{doc['timings']}.")
            self.facts.append((_tokens(doc["name"] + " " + doc["role"]), txt))

    # ---------- helpers ----------
    def _log(self, session_id: str, role: str, text: str) -> None:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "session": session_id,
                                "role": role, "text": text}) + "\n")

    def _save_booking(self, b: dict) -> None:
        os.makedirs(os.path.dirname(BOOKINGS_FILE), exist_ok=True)
        data = []
        if os.path.exists(BOOKINGS_FILE):
            with open(BOOKINGS_FILE, encoding="utf-8") as f:
                data = json.load(f)
        b["id"] = "BK-" + uuid.uuid4().hex[:8].upper()
        b["created_at"] = time.strftime("%Y-%m-%d %H:%M")
        data.append(b)
        with open(BOOKINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return b

    def _hours_text(self) -> str:
        h = self.cfg["hours"]
        lines = [f"{day.capitalize()}: {h[day]}" for day in
                 ["monday", "tuesday", "wednesday", "thursday", "friday",
                  "saturday", "sunday"]]
        return "Our timings are:\n" + "\n".join(lines)

    def _services_text(self) -> str:
        out = ["Our services and fees:"]
        for svc in self.cfg["services"]:
            fee = "Free" if svc["fee_pkr"] == 0 else f"PKR {svc['fee_pkr']:,}"
            out.append(f"- {svc['name']} - {fee}")
        out.append("Would you like to book an appointment? Just say 'book appointment'.")
        return "\n".join(out)

    def _doctors_text(self) -> str:
        out = ["Our doctors:"]
        for d in self.cfg["doctors"]:
            out.append(f"- {d['name']} ({d['role']}) - {d['days']}, {d['timings']}")
        return "\n".join(out)

    _STOP = {"do", "you", "the", "a", "an", "is", "there", "for", "my", "i",
             "to", "of", "on", "in", "it", "what", "which", "how", "can", "we",
             "are", "your", "me", "have", "has", "with", "ke", "liye", "hai",
             "hain", "ka", "ki", "ko", "se", "ne", "bhi", "aur"}

    def _retrieve(self, text: str) -> str | None:
        q = _tokens(text) - self._STOP
        best, score = None, 0
        for tokens, answer in self.facts:
            s = len(q & (tokens - self._STOP))
            if s > score:
                best, score = answer, s
        return best if score >= 2 else None

    def _grounded_llm(self, text: str) -> str | None:
        if not llm.available():
            return None
        facts = json.dumps(self.cfg, indent=1)[:6000]
        system = (
            f"You are the WhatsApp assistant for {self.cfg['business_name']} in "
            f"{self.cfg['city']}. Answer ONLY using the business facts below. "
            "If the facts do not contain the answer, say you will connect them "
            "with reception and give the phone number. Never give medical "
            "advice or diagnoses. Keep replies short and friendly.\n\nFACTS:\n" + facts
        )
        return llm.answer(system, text)

    # ---------- booking state machine ----------
    def _booking_step(self, s: dict, text: str) -> str:
        step = s["step"]
        slots = s["slots"]
        if step == "service":
            slots["service"] = text
            s["step"] = "date"
            return f"Great - {text}. Which date would you like? (e.g. tomorrow, or 14 Nov)"
        if step == "date":
            slots["date"] = text
            s["step"] = "time"
            return "And what time suits you? (e.g. 6 PM)"
        if step == "time":
            slots["time"] = text
            s["step"] = "name"
            return "Almost done - may I have the patient's full name?"
        if step == "name":
            slots["patient_name"] = text
            s["step"] = "confirm"
            return (f"Please confirm your appointment:\n"
                    f"- Service: {slots['service']}\n"
                    f"- Date: {slots['date']}\n"
                    f"- Time: {slots['time']}\n"
                    f"- Patient: {slots['patient_name']}\n"
                    "Reply YES to confirm, or NO to start over.")
        if step == "confirm":
            if _norm(text).startswith(("y", "yes", "confirm", "ok", "jee", "han")):
                b = self._save_booking(dict(slots, business=self.cfg["business_name"]))
                s["step"] = "idle"
                s["slots"] = {}
                return (f"Booked! Your appointment ID is {b['id']}.\n"
                        f"{b['service']} on {b['date']} at {b['time']} for {b['patient_name']}.\n"
                        f"Please arrive 10 minutes early. To reschedule, call us on "
                        f"{self.cfg['phone']}.")
            s["step"] = "idle"
            s["slots"] = {}
            return "No problem, I have cancelled that. Say 'book appointment' whenever you are ready."
        s["step"] = "idle"
        return "Let me help with something else."

    # ---------- main entry ----------
    def reply(self, session_id: str, text: str) -> str:
        self._log(session_id, "user", text)
        s = self.sessions.setdefault(session_id, {"step": "idle", "slots": {}})
        t = _norm(text)
        cfg = self.cfg

        # 1) Safety first - emergencies.
        if any(w in t for w in EMERGENCY_WORDS):
            ans = (f"This sounds urgent. Please go to the nearest hospital emergency "
                   f"or call an ambulance (1122 in Punjab) right now. You can also "
                   f"call us on {cfg['phone']}. I am also flagging this chat to our staff.")
            s["needs_human"] = True
            return self._finish(session_id, ans)

        # 2) Medical advice guardrail.
        if any(re.search(p, t) for p in MEDICAL_ADVICE_PATTERNS):
            ans = (f"I am the clinic's booking assistant, so I can't advise on medicines "
                   f"or treatment - only a doctor who examines you can do that. I can "
                   f"book you a consultation, or connect you with reception on "
                   f"{cfg['phone']}. Would you like an appointment?")
            return self._finish(session_id, ans)

        # 3) Human handoff.
        if any(w in t for w in HANDOFF_WORDS):
            s["needs_human"] = True
            ans = (f"Of course - I am passing this chat to our reception team. "
                   f"For anything urgent, please call {cfg['phone']} "
                   f"(WhatsApp {cfg['handoff_phone']}). Someone will reply here shortly.")
            return self._finish(session_id, ans)

        # 4) Mid-booking: continue the flow unless they cancel.
        if s["step"] != "idle":
            if t in ("cancel", "stop", "rehne do", "cancel kar do"):
                s["step"] = "idle"
                s["slots"] = {}
                return self._finish(session_id, "Booking cancelled. How else can I help?")
            return self._finish(session_id, self._booking_step(s, text))

        # 5) Start booking.
        if any(w in t for w in BOOKING_WORDS):
            s["step"] = "service"
            return self._finish(session_id,
                "I can book that for you. Which service do you need?\n" +
                "\n".join(f"- {svc['name']}" for svc in cfg["services"]))

        # 6) Direct factual intents from the config.
        if any(w in t for w in ["timing", "timings", "hours", "open", "close", "kab khul", "waqt"]):
            return self._finish(session_id, self._hours_text())
        if any(w in t for w in ["fee", "fees", "price", "charges", "cost", "rate", "kitne", "pese"]):
            return self._finish(session_id, self._services_text())
        if any(w in t for w in ["doctor", "doctors", "dr.", "specialist"]):
            return self._finish(session_id, self._doctors_text())
        if any(w in t for w in ["address", "location", "where", "pata", "kahan", "direction"]):
            return self._finish(session_id,
                f"We are at: {cfg['address']}. Phone: {cfg['phone']}.")
        if t.rstrip(".!") in GREETINGS or len(t) <= 3:
            return self._finish(session_id,
                f"Hello, and welcome to {cfg['business_name']} - {cfg['tagline']}. "
                "I can share timings, fees and doctor details, or book an appointment. "
                "How can I help?")

        # 7) Knowledge retrieval, then grounded LLM, then honest fallback.
        hit = self._retrieve(text)
        if hit:
            return self._finish(session_id, hit)
        llm_ans = self._grounded_llm(text)
        if llm_ans:
            return self._finish(session_id, llm_ans)
        return self._finish(session_id,
            f"I want to give you the right answer rather than guess. Our reception team "
            f"can help on {cfg['phone']} (WhatsApp {cfg['whatsapp']}). Meanwhile I can "
            "share timings, fees, doctor details, or book an appointment - just ask.")

    def _finish(self, session_id: str, ans: str) -> str:
        self._log(session_id, "assistant", ans)
        return ans
