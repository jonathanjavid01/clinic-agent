"""Clinic WhatsApp agent - core brain, v3.

Design rules:
- Everything business-specific comes from ONE config file (config/*.json).
  A new client = a new config file. No code changes.
- Deterministic first: hours, fees, location, doctors, booking and safety
  rules are answered by code from the config - never invented by a model.
- The LLM (agent/llm.py) only phrases open question answers, grounded in
  the same config facts, and only when an API key is configured.
- Guardrails: emergencies and medical-advice requests are never answered
  by the bot; emergencies get the rescue number, advice requests get a
  booking offer, anything else unclear goes to a human.

v3 changes (per the Clinic OS review):
- Whole-word intent matching (typing "address" no longer lists doctors).
- Booking confirmation checks NO first and requires a whole-word YES.
- Services are picked by number or a clear keyword, never by one letter.
- Dates and times are parsed and validated: no past dates, no Sundays,
  no Friday 1-3 PM break, and each doctor's own days/hours are enforced.
- A taken slot is blocked and the next free slot is suggested.
- A booking for today no longer joins the queue by itself: the patient
  gets a Booking ID and sends "Check in <ID>" on arrival to get a token.
- Instant tokens are GP and Lab only; dental/skin start the booking flow.
- Tokens are refused while the clinic is closed; a duplicate request
  returns the patient's existing token instead of issuing a second one.
- "Talk to a human" alerts reception for real (staff alert with a Done
  button on the board) instead of only printing phone numbers.
"""
import datetime
import json
import os
import re
import uuid

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("AGENT_DATA_DIR", os.path.join(BASE, "data"))
BOOKINGS_FILE = os.path.join(DATA_DIR, "bookings.json")
LOG_FILE = os.path.join(DATA_DIR, "conversations.jsonl")

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DAY_NAMES = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
             "friday": 4, "saturday": 5, "sunday": 6,
             "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5,
             "sun": 6}
MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
     "nov", "dec"])}

GREETINGS = {"hi", "hello", "aoa", "salam", "assalamualaikum", "hey"}
EMERGENCY = [
    "chest pain", "dil mein dard", "heart attack", "heart problem",
    "cant breathe", "can't breathe", "saans nahi", "saans ruk",
    "breathing problem", "bleeding", "khoon ruk nahi raha", "khoon beh",
    "unconscious", "behosh", "fracture", "haddi toot", "gir gaya",
    "gir gayi", "poison", "zehar kha", "snake bite", "saanp ne kata",
    "electric shock", "current laga", "fits", "daura pad", "accident",
    "severe pain", "blood pressure high", "bp high", "bp low",
]
ADVICE = ["diagnose", "diagnosis", "prescription", "prescribe", "dawai",
          "medicine for", "treatment for", "bimari", "what should i take",
          "konsi dawa"]
YES_WORDS = {"yes", "haan", "han", "ji", "ok", "confirm", "confirmed",
             "theek", "bilkul", "yep"}
NO_WORDS = {"no", "nahi", "nhi", "cancel", "nope", "ruk", "band"}


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def words(text: str) -> set:
    return set(re.findall(r"[a-z']+", text.lower()))


def has_any(text: str, options) -> bool:
    return bool(words(text) & set(options))


def _hm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def fmt_time(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    suf = "AM" if h < 12 else "PM"
    return f"{h % 12 or 12}:{m:02d} {suf}"


def parse_date(text: str, today: datetime.date):
    """Return a date or None. Understands today/tomorrow, weekday names,
    '14 nov', '14 november' and '14/11'."""
    t = " " + re.sub(r"[^a-z0-9/ ]", " ", text.lower()).strip() + " "
    if " today " in t or t.strip() == "aaj":
        return today
    if " tomorrow " in t or " kal " in t:
        return today + datetime.timedelta(days=1)
    m = re.search(r"\b(\d{1,2})[/-](\d{1,2})\b", t)
    if m:
        try:
            return datetime.date(today.year, int(m.group(2)),
                                 int(m.group(1)))
        except ValueError:
            return None
    m = re.search(r"\b(\d{1,2})\s*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct"
                  r"|nov|dec)", t)
    if m:
        try:
            return datetime.date(today.year, MONTHS[m.group(2)],
                                 int(m.group(1)))
        except ValueError:
            return None
    for name, idx in DAY_NAMES.items():
        if re.search(rf"\b{name}\b", t):
            delta = (idx - today.weekday()) % 7
            return today + datetime.timedelta(days=delta)
    return None


def parse_time(text: str):
    """Return minutes since midnight or None."""
    t = text.lower().strip()
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", t)
    if m:
        h, mi, suf = int(m.group(1)), int(m.group(2) or 0), m.group(3)
        if h == 12:
            h = 0
        if suf == "pm":
            h += 12
        return h * 60 + mi
    m = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", t)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    m = re.search(r"\b(\d{1,2})\s*(baje|o'?clock)\b", t)
    if m:
        h = int(m.group(1))
        return (h + 12 if 1 <= h <= 8 else h) * 60
    return None


def load_bookings() -> list:
    if os.path.exists(BOOKINGS_FILE):
        try:
            with open(BOOKINGS_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []
    return []


def save_bookings(bookings: list):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(BOOKINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(bookings, f, ensure_ascii=False, indent=1)


def find_booking(booking_id: str):
    for b in load_bookings():
        if b["id"].upper() == booking_id.upper():
            return b
    return None


def slot_taken(bookings, provider, date_iso, time24, ignore_id=None):
    return any(b["provider"] == provider and b["date_iso"] == date_iso
               and b["time24"] == time24 and not b.get("noshow")
               and b["id"] != ignore_id for b in bookings)


def doctor_for(cfg, provider):
    for d in cfg["doctors"]:
        if d["id"] == provider:
            return d
    return None


def schedule_window(cfg, provider, day: datetime.date):
    """(start_min, end_min) for this provider on this date, or None when
    the provider does not work that day."""
    doc = doctor_for(cfg, provider)
    if not doc:
        return None
    sch = doc["schedule"]
    label = DAYS[day.weekday()]
    if label not in sch["days"]:
        return None
    end = sch.get("fri_end", sch["end"]) if label == "Fri" else sch["end"]
    return _hm(sch["start"]), _hm(end)


def next_free_slot(cfg, bookings, provider, day: datetime.date,
                   from_min: int = 0, ignore_id=None):
    """First free 30-minute slot for a provider, scanning up to 7 days.
    Returns (date, minutes) or None."""
    for offset in range(0, 8):
        d = day + datetime.timedelta(days=offset)
        if d.weekday() == 6:  # Sunday: clinic closed
            continue
        win = schedule_window(cfg, provider, d)
        if not win:
            continue
        start, end = win
        t = max(start, from_min if offset == 0 else start)
        while t + 15 <= end:
            if not slot_taken(bookings, provider, d.isoformat(),
                              f"{t // 60:02d}:{t % 60:02d}", ignore_id):
                return d, t
            t += 30
    return None


class Agent:
    def __init__(self, config: dict, llm=None, queue=None, now_fn=None):
        self.cfg = config
        self.llm = llm
        self.queue = queue          # TokenQueue (v3), optional
        self.now_fn = now_fn or datetime.datetime.now
        self.sessions: dict[str, dict] = {}

    # ---------- basics ----------
    def now(self) -> datetime.datetime:
        return self.now_fn()

    def today(self) -> datetime.date:
        return self.now().date()

    def clinic_open_now(self) -> bool:
        n = self.now()
        label = DAYS[n.weekday()]
        mins = n.hour * 60 + n.minute
        for start, end in self.cfg.get("open_windows", {}).get(label, []):
            if _hm(start) <= mins < _hm(end):
                return True
        return False

    def _log(self, session: str, role: str, text: str):
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"session": session, "role": role,
                                "text": text,
                                "at": self.now().isoformat()},
                               ensure_ascii=False) + "\n")

    def _save_booking(self, data: dict) -> dict:
        bookings = load_bookings()
        booking = dict(data)
        booking["id"] = "BK-" + uuid.uuid4().hex[:8].upper()
        booking["business"] = self.cfg["business_name"]
        booking["created_at"] = self.now().strftime("%Y-%m-%d %H:%M")
        booking["checked_in"] = False
        booking["noshow"] = False
        bookings.append(booking)
        save_bookings(bookings)
        if self.queue:
            self.queue.note_online_booking()
        return booking

    # ---------- config answers ----------
    def services_text(self, numbered=False) -> str:
        lines = []
        for i, s in enumerate(self.cfg["services"], 1):
            fee = (f"{self.cfg.get('currency', 'PKR')} {s['fee']:,}" if s["fee"]
                   else "Free")
            note = f" | {s['note']}" if s.get("note") else ""
            prefix = f"{i}. " if numbered else "- "
            lines.append(f"{prefix}{s['name']} - {fee} "
                         f"(about {s['duration_min']} min){note}")
        return "\n".join(lines)

    def hours_text(self) -> str:
        h = self.cfg.get("hours", {})
        order = ["monday", "tuesday", "wednesday", "thursday", "friday",
                 "saturday", "sunday"]
        lines = [f"{d.capitalize()}: {h[d]}" for d in order if d in h]
        body = "; ".join(lines) if lines else str(h)
        return (f"Our hours — {body}.\n"
                f"We are {'OPEN now' if self.clinic_open_now() else 'CLOSED right now'}.")

    def doctors_text(self) -> str:
        lines = [f"- {d['name']} ({d['specialty']}): {d['timings']}"
                 for d in self.cfg["doctors"]
                 if d.get("kind", "doctor") == "doctor"]
        return "Our doctors:\n" + "\n".join(lines)

    def greeting(self) -> str:
        c = self.cfg
        return (f"Assalam-o-Alaikum! Welcome to {c['business_name']}, "
                f"{c['city']}. {c['tagline']}. I can share timings and "
                "fees, book an appointment, check you in with your "
                "Booking ID, or issue a GP/lab token so you skip the "
                "queue. How can I help you today?")

    # ---------- entry point ----------
    def reply(self, session: str, message: str) -> str:
        st = self.sessions.setdefault(session, {"step": None, "data": {}})
        answer = self._reply(session, st, message.strip())
        self._log(session, "user", message)
        self._log(session, "agent", answer)
        return answer

    def _reply(self, session: str, st: dict, msg: str) -> str:
        low = msg.lower()
        # Safety first, at any point in any flow.
        if any(p in low for p in EMERGENCY):
            st.update(step=None, data={})
            return ("This sounds like an emergency. Please call Rescue "
                    "1122 right now, or go to the nearest hospital "
                    "emergency. Do not wait for a chat reply.")
        if has_any(low, ["human", "agent", "person", "staff", "reception",
                         "insan", "banda"]) and not st.get("step"):
            return self._handoff()
        if st.get("step"):
            return self._booking_step(st, msg, low)
        if has_any(low, ["cancel"]) and not st.get("step"):
            return "No problem. Kuch aur madad chahiye to bata dein."
        return self._intent(st, msg, low)

    # ---------- intents (idle state) ----------
    def _handoff(self) -> str:
        if self.queue:
            self.queue.add_alert(
                "human",
                "A patient asked to talk to a human on WhatsApp.")
        c = self.cfg
        return ("I've alerted reception — a staff member will get back "
                "to you here shortly. For anything urgent, please call "
                f"{c['phone']} or WhatsApp "
                f"{c.get('whatsapp') or c.get('handoff_phone', '')} "
                "(reception is staffed during clinic hours).")

    def _intent(self, st: dict, msg: str, low: str) -> str:
        c = self.cfg
        w = words(low)
        m = re.search(r"\bcheck ?in\b[^A-Za-z0-9]*(BK-[0-9A-F]{8})?",
                      low, re.I)
        if "check" in w and "in" in w or re.search(r"\bcheckin\b", low):
            bid = None
            mm = re.search(r"(BK-[0-9A-Fa-f]{8})", msg)
            if mm:
                bid = mm.group(1).upper()
            return self._checkin(st, bid)
        if re.search(r"\b(BK-[0-9A-Fa-f]{8})\b", msg) and \
                ("check" in w or "arrived" in w or "aa gaya" in low):
            return self._checkin(
                st, re.search(r"(BK-[0-9A-Fa-f]{8})", msg).group(1).upper())
        if w & GREETINGS and len(w) <= 4:
            return self.greeting()
        if w & {"timing", "timings", "timing?", "hours", "khula", "band"} \
                or "what time" in low or "open" in w and "now" in w:
            return self.hours_text()
        if w & {"fee", "fees", "price", "prices", "cost", "charges",
                "charge", "rate", "rates", "kitne", "pese", "paisay"}:
            return "Our services and fees:\n" + self.services_text()
        if w & {"doctor", "doctors", "dr", "specialist", "lady"}:
            return self.doctors_text()
        if w & {"address", "location", "located", "kahan", "kidhar",
                "rasta", "direction", "directions"}:
            return (f"We are at: {c['address']}. "
                    f"Phone: {c['phone']}.")
        if self._wants_token(low):
            return self._token_start(st, low)
        if w & {"book", "booking", "appointment", "appoint", "mulaqat",
                "milna"}:
            st.update(step="service", data={})
            return ("Sure, let's book your appointment. Which service "
                    "do you need? Reply with the number:\n"
                    + self.services_text(numbered=True))
        if any(p in low for p in ADVICE):
            return ("I can't give medical advice or prescribe on chat — "
                    "only the doctor can do that after an examination. "
                    "Shall I book you an appointment? Reply 'book' and "
                    "I'll set it up.")
        for faq in c.get("faqs", []):
            if any(q in low for q in faq["q"]):
                return faq["a"]
        if self.llm:
            answer = self.llm.answer(msg, self.cfg)
            if answer:
                return answer
        return ("I want to make sure I get that right. You can ask me "
                "about timings, fees, doctors, or say 'book' for an "
                "appointment, 'token' for a GP/lab token, 'check in' "
                "with your Booking ID when you arrive, or 'human' to "
                "talk to reception.")

    def _wants_token(self, low: str) -> bool:
        return bool(words(low) & {"token"}) or "skip the queue" in low

    # ---------- token flow (GP / Lab instant tokens) ----------
    def _token_start(self, st: dict, low: str) -> str:
        instant = [s for s in self.cfg["services"]
                   if s["provider"] in self.cfg.get(
                       "instant_token_providers", [])]
        chosen = self._match_service(low, instant)
        if not chosen:
            st.update(step="token_service", data={})
            names = " or ".join(
                f"{i + 1}. {s['name']}" for i, s in enumerate(instant))
            return ("Instant tokens are for GP and Lab only (dental and "
                    "skin need an appointment). Which token do you need?\n"
                    + names)
        return self._token_name(st, chosen)

    def _token_name(self, st: dict, service: dict) -> str:
        st.update(step="token_name", data={"service": service["name"],
                                           "provider": service["provider"]})
        return f"Your name for the {service['name']} token, please?"

    def _issue_chat_token(self, st: dict, name: str) -> str:
        data = st["data"]
        provider = data["provider"]
        st.update(step=None, data={})
        if not self.clinic_open_now():
            return ("The clinic is closed right now, so I can't issue a "
                    f"token. {self.hours_text()}")
        if self.queue:
            existing = self.queue.find_patient_token(provider, name)
            if existing:
                return (f"You already have token {existing['token']} "
                        f"for {existing['service']}. Show it at "
                        "reception when you arrive and they will mark "
                        "you arrived.")
            token = self.queue.issue(provider, name, data["service"],
                                     source="whatsapp", arrived=False)
            view = self.queue.status()["queues"][provider]
            position = next(i for i, t in enumerate(view["waiting"])
                            if t["token"] == token["token"])
            ahead = position + (1 if view["now_serving"] else 0)
            est = view["waiting"][position]["est_wait_min"]
            doc = doctor_for(self.cfg, provider)
            return (
                f"Token {token['token']} issued for {name} — "
                f"{data['service']} with {doc['name']}.\n"
                f"Patients ahead of you: {ahead} "
                f"(estimated wait about {est} minutes).\n"
                "Please come to the clinic and tell reception your "
                "token number; they will mark you arrived and your "
                "turn will come in order.")
        return ("Token booking is not switched on in this demo chat — "
                "please take your token at reception.")

    def _est_text(self, provider: str, token_id: str) -> str:
        if not self.queue:
            return ""
        view = self.queue.status()["queues"][provider]
        for t in view["waiting"]:
            if t["token"] == token_id:
                return f"estimated wait about {t['est_wait_min']} minutes"
        return ""

    # ---------- check-in with Booking ID ----------
    def _checkin(self, st: dict, booking_id: str | None) -> str:
        if not booking_id:
            st.update(step="checkin_id", data={})
            return ("Welcome! Please send your Booking ID (it looks "
                    "like BK-1A2B3C4D) and I'll check you in.")
        booking = find_booking(booking_id)
        if not booking:
            return ("I couldn't find that Booking ID. Please check the "
                    "ID in your confirmation message and try again, or "
                    "ask reception to check you in.")
        if booking.get("checked_in"):
            return (f"This booking is already checked in"
                    + (f" — your token is {booking['token']}."
                       if booking.get("token") else "."))
        if booking["date_iso"] != self.today().isoformat():
            return (f"Your appointment is on {booking['date']} at "
                    f"{booking['time']}. Check-in opens on the "
                    "appointment day — see you then!")
        if not self.queue:
            return "Check-in is not switched on in this demo chat."
        token = self.queue.issue(booking["provider"],
                                 booking["patient_name"],
                                 booking["service"], source="checkin",
                                 arrived=True, tag="Appt")
        bookings = load_bookings()
        for b in bookings:
            if b["id"] == booking["id"]:
                b["checked_in"] = True
                b["token"] = token["token"]
        save_bookings(bookings)
        doc = doctor_for(self.cfg, booking["provider"])
        return (f"Checked in! Token {token['token']} for "
                f"{booking['patient_name']} — {booking['service']} "
                f"with {doc['name']}. "
                f"{self._est_text(booking['provider'], token['token'])}"
                " Please take a seat; your token will be called in "
                "order.")

    # ---------- booking state machine ----------
    SERVICE_KEYS = {
        "General Consultation (GP)": {"gp", "general", "consultation",
                                      "checkup"},
        "Dental Checkup & Cleaning": {"dental", "cleaning", "teeth",
                                      "tooth", "daant"},
        "Dental Filling": {"filling", "cavity"},
        "Root Canal": {"root", "canal"},
        "Skin Consultation": {"skin", "جلد", "derma", "rash", "pimple"},
        "Blood Tests (lab)": {"blood", "lab", "test", "tests", "report"},
    }

    def _match_service(self, low: str, pool: list):
        """A service matches by its list number or a clear keyword only —
        never by a stray single letter."""
        w = words(low)
        m = re.search(r"\b(\d)\b", low)
        if m:
            idx = int(m.group(1)) - 1
            if 0 <= idx < len(pool):
                return pool[idx]
        for s in pool:
            keys = self.SERVICE_KEYS.get(
                s["name"], {s["name"].split()[0].lower()})
            if w & keys:
                return s
        for s in pool:
            if s["name"].lower() in low:
                return s
        return None

    def _booking_step(self, st: dict, msg: str, low: str) -> str:
        step, data = st["step"], st["data"]
        w = words(low)
        if step in ("service", "date", "time", "token_service",
                    "checkin_id") and w & (NO_WORDS | {"stop", "rehne"}):
            st.update(step=None, data={})
            return ("Theek hai, cancelled. Kuch aur chahiye to bata "
                    "dein — timings, fees, ya dobara booking.")
        if step == "token_service":
            instant = [s for s in self.cfg["services"]
                       if s["provider"] in self.cfg.get(
                           "instant_token_providers", [])]
            chosen = self._match_service(low, instant)
            if not chosen:
                if w & {"dental", "skin", "daant"} or \
                        self._match_service(low, self.cfg["services"]):
                    st.update(step="service", data={})
                    return ("Dental and skin are by appointment. Let's "
                            "book instead — which service? Reply with "
                            "the number:\n"
                            + self.services_text(numbered=True))
                return ("Please reply 1 for General Consultation (GP) "
                        "or 2 for Blood Tests (lab).")
            return self._token_name(st, chosen)
        if step == "token_name":
            name = msg.strip()
            if len(re.sub(r"[^A-Za-z ]", "", name)) < 2:
                return "Please tell me the patient's full name."
            return self._issue_chat_token(st, name)
        if step == "checkin_id":
            mm = re.search(r"(BK-[0-9A-Fa-f]{8})", msg)
            st.update(step=None, data={})
            if not mm:
                return ("That doesn't look like a Booking ID. It looks "
                        "like BK-1A2B3C4D — please check and send it "
                        "again, or ask reception.")
            return self._checkin(st, mm.group(1).upper())
        if step == "service":
            chosen = self._match_service(low, self.cfg["services"])
            if not chosen:
                return ("Please pick from the list — reply with the "
                        "number (1-6) or the service name:\n"
                        + self.services_text(numbered=True))
            data["service"] = chosen["name"]
            data["provider"] = chosen["provider"]
            st["step"] = "date"
            doc = doctor_for(self.cfg, chosen["provider"])
            return (f"{chosen['name']} with {doc['name']} "
                    f"({doc['timings']}). Which date? You can say "
                    "'today', 'tomorrow', a day name, or a date like "
                    "'14 Nov'.")
        if step == "date":
            d = parse_date(low, self.today())
            if d is None:
                return ("I couldn't understand that date. Please try "
                        "'today', 'tomorrow', 'Friday', or '14 Nov'.")
            if d < self.today():
                return ("That date has already passed. Please pick "
                        "today or a coming day.")
            if d.weekday() == 6:
                return ("We are closed on Sundays. Please pick another "
                        "day (Mon-Sat).")
            win = schedule_window(self.cfg, data["provider"], d)
            if not win:
                doc = doctor_for(self.cfg, data["provider"])
                return (f"{doc['name']} is not available on "
                        f"{d.strftime('%A')}s ({doc['timings']}). "
                        "Please pick another day.")
            data["date_iso"] = d.isoformat()
            data["date"] = d.strftime("%a %d %b %Y")
            st["step"] = "time"
            return (f"And what time on {data['date']}? "
                    f"({doctor_for(self.cfg, data['provider'])['name']} "
                    f"sees patients {fmt_time(win[0])} - "
                    f"{fmt_time(win[1])})")
        if step == "time":
            t = parse_time(low)
            if t is None:
                return ("I couldn't understand that time. Please try "
                        "like '6 PM' or '5:30 PM'.")
            d = datetime.date.fromisoformat(data["date_iso"])
            win = schedule_window(self.cfg, data["provider"], d)
            if not (win[0] <= t < win[1]):
                doc = doctor_for(self.cfg, data["provider"])
                reason = ""
                if d.weekday() == 4 and 13 * 60 <= t < 15 * 60:
                    reason = " (the clinic takes a break 1-3 PM on " \
                             "Fridays)"
                return (f"{doc['name']} sees patients "
                        f"{fmt_time(win[0])} - {fmt_time(win[1])} on "
                        f"{d.strftime('%A')}s{reason}. Please pick a "
                        "time in that range.")
            time24 = f"{t // 60:02d}:{t % 60:02d}"
            bookings = load_bookings()
            if slot_taken(bookings, data["provider"], data["date_iso"],
                          time24):
                nxt = next_free_slot(self.cfg, bookings,
                                     data["provider"], d, t + 30)
                hint = (f" The next free slot is "
                        f"{nxt[0].strftime('%a %d %b')} at "
                        f"{fmt_time(nxt[1])} — you can say that date "
                        "and time instead." if nxt else "")
                return (f"Sorry, {fmt_time(t)} on {data['date']} is "
                        f"already booked.{hint} Please pick another "
                        "time.")
            data["time24"] = time24
            data["time"] = fmt_time(t)
            st["step"] = "name"
            return "And the patient's full name, please?"
        if step == "name":
            name = msg.strip()
            if len(re.sub(r"[^A-Za-z ]", "", name)) < 2:
                return "Please tell me the patient's full name."
            data["patient_name"] = name
            st["step"] = "confirm"
            return (f"Please confirm: {data['service']} on "
                    f"{data['date']} at {data['time']} for {name}. "
                    "Reply YES to confirm or NO to cancel.")
        if step == "confirm":
            if w & NO_WORDS:  # NO is checked FIRST: "no, book later"
                st.update(step=None, data={})  # must not book anything
                return ("Theek hai, I have not booked anything. Jab "
                        "ready hon, 'book' likh dein.")
            if w & YES_WORDS:
                booking = self._save_booking({
                    "patient_name": data["patient_name"],
                    "service": data["service"],
                    "provider": data["provider"],
                    "date": data["date"], "date_iso": data["date_iso"],
                    "time": data["time"], "time24": data["time24"]})
                st.update(step=None, data={})
                return (
                    f"Booked! Your Booking ID is {booking['id']}.\n"
                    f"{booking['service']} on {booking['date']} at "
                    f"{booking['time']} for {booking['patient_name']}.\n"
                    "Please arrive a little early. When you reach the "
                    f"clinic, send 'Check in {booking['id']}' here "
                    "and I'll give you your token — no need to queue "
                    "at the desk.")
            return ("Please reply YES to confirm the booking, or NO "
                    "to cancel it.")
        st.update(step=None, data={})
        return self._intent(st, msg, low)
