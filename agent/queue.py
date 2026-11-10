"""Token queues for Clinic OS v3 — one queue per doctor, plus the Lab.

Flow rules (from the v3 spec):
- A token starts as "not arrived" when issued remotely (WhatsApp) and as
  "arrived" for walk-ins and appointment check-ins.
- "Call next" only calls patients marked arrived, so a remote token can
  never block the line.
- Postpone moves a waiting patient to the end of their queue (tagged).
- Not arrived removes the patient and counts a no-show.
- Finishing a visit (Visit done, or Call next moving on) records the
  service fee toward the day's earnings, per doctor and in total.

State is a JSON file so a small clinic can run it on one machine; the
numbering is global across queues and resets each day.
"""
import datetime
import json
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUEUE_FILE = os.environ.get("QUEUE_FILE", os.path.join(BASE, "data", "queue.json"))

DEFAULT_DURATION_MIN = 15
# Provider (doctor / department) ids come from the client config, so the
# same code serves a solo GP, a dental clinic, or a hospital with
# departments. This is only the fallback for the sample clinic.
DEFAULT_PROVIDERS = ["gp", "dental", "skin", "lab"]

# 14 days of clearly-labelled sample history so the demo's peak charts are
# not empty. Real deployments accumulate real counts on top of / instead of
# this seed (set SEED_SAMPLE_HISTORY = False in production).
SEED_SAMPLE_HISTORY = True
_SAMPLE_HOURS = {9: 6, 10: 11, 11: 14, 12: 9, 13: 3, 15: 8, 16: 12, 17: 17, 18: 21, 19: 15, 20: 7}
_SAMPLE_DAYS = {"Mon": 38, "Tue": 34, "Wed": 41, "Thu": 36, "Fri": 29, "Sat": 44}


def display_name(full: str) -> str:
    """Privacy-safe name for the waiting-room TV: first name + last initial."""
    parts = (full or "").split()
    if len(parts) >= 2:
        return f"{parts[0]} {parts[-1][0]}."
    return full or ""


class TokenQueue:
    def __init__(self, path: str = QUEUE_FILE, services: list | None = None,
                 now_fn=None, providers: list | None = None):
        self.path = path
        self.now_fn = now_fn or datetime.datetime.now
        self.services = {s["name"]: s for s in (services or [])}
        self.providers = providers or DEFAULT_PROVIDERS
        self.state = self._load()
        self._roll_day()

    # ---------- persistence ----------
    def _empty(self) -> dict:
        today = self.now_fn().strftime("%Y-%m-%d")
        return {"date": today, "next_number": 1,
                "queues": {p: {"now_serving": None, "waiting": []}
                           for p in self.providers},
                "served": [], "noshows": 0,
                "counts": {"online_bookings": 0, "whatsapp_tokens": 0,
                           "walkins": 0},
                "alerts": [],
                "history": ({"hours": dict(_SAMPLE_HOURS),
                             "days": dict(_SAMPLE_DAYS), "sample": True}
                            if SEED_SAMPLE_HISTORY else
                            {"hours": {}, "days": {}, "sample": False})}

    def _load(self) -> dict:
        if os.path.exists(self.path):
            try:
                with open(self.path, encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return self._empty()

    def _save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.state, f, ensure_ascii=False, indent=1)

    def _roll_day(self):
        today = self.now_fn().strftime("%Y-%m-%d")
        if self.state.get("date") != today:
            self.state = self._empty()
            self._save()

    def reset(self):
        """Demo helper: wipe today's state back to a fresh day."""
        self.state = self._empty()
        self._save()
        return self.status()

    # ---------- helpers ----------
    def _svc(self, service: str) -> dict:
        return self.services.get(service, {})

    def _find(self, provider: str, token: str):
        q = self.state["queues"][provider]
        for i, t in enumerate(q["waiting"]):
            if t["token"] == token:
                return q, i
        return q, -1

    def _finish_current(self, provider: str):
        q = self.state["queues"][provider]
        cur = q["now_serving"]
        if cur:
            cur["finished_at"] = self.now_fn().strftime("%H:%M")
            self.state["served"].append(cur)
            hour = self.now_fn().hour
            day = self.now_fn().strftime("%a")
            h = self.state["history"]
            h["hours"][str(hour)] = h["hours"].get(str(hour), 0) + 1
            h["days"][day] = h["days"].get(day, 0) + 1
            q["now_serving"] = None

    # ---------- issuing ----------
    def issue(self, provider: str, patient_name: str, service: str,
              source: str = "walkin", arrived: bool = True,
              tag: str | None = None) -> dict:
        self._roll_day()
        svc = self._svc(service)
        n = self.state["next_number"]
        self.state["next_number"] = n + 1
        token = {"token": f"T-{n:02d}", "number": n, "patient": patient_name,
                 "patient_display": display_name(patient_name),
                 "service": service, "provider": provider,
                 "source": source, "arrived": arrived, "tag": tag,
                 "fee": svc.get("fee", 0),
                 "duration_min": svc.get("duration_min", DEFAULT_DURATION_MIN),
                 "issued_at": self.now_fn().strftime("%H:%M")}
        self.state["queues"][provider]["waiting"].append(token)
        if source == "walkin":
            self.state["counts"]["walkins"] += 1
        elif source == "whatsapp":
            self.state["counts"]["whatsapp_tokens"] += 1
        self._save()
        return dict(token)

    def find_patient_token(self, provider: str, patient_name: str):
        """Existing active token for this patient in this queue, if any."""
        name = (patient_name or "").strip().lower()
        q = self.state["queues"][provider]
        pool = ([q["now_serving"]] if q["now_serving"] else []) + q["waiting"]
        for t in pool:
            if t and t["patient"].strip().lower() == name:
                return dict(t)
        return None

    # ---------- reception actions ----------
    def mark_arrived(self, provider: str, token: str) -> dict:
        q, i = self._find(provider, token)
        if i >= 0:
            q["waiting"][i]["arrived"] = True
            self._save()
        return self.status()

    def postpone(self, provider: str, token: str) -> dict:
        q, i = self._find(provider, token)
        if i >= 0:
            entry = q["waiting"].pop(i)
            entry["tag"] = "Postponed"
            q["waiting"].append(entry)
            self._save()
        return self.status()

    def not_arrived(self, provider: str, token: str) -> dict:
        q, i = self._find(provider, token)
        if i >= 0:
            q["waiting"].pop(i)
            self.state["noshows"] += 1
            self._save()
        return self.status()

    def call_next(self, provider: str) -> dict:
        """Finish the current visit (if any) and call the first ARRIVED
        waiting patient. Returns status plus who was called (or None)."""
        self._finish_current(provider)
        q = self.state["queues"][provider]
        called = None
        for i, t in enumerate(q["waiting"]):
            if t["arrived"]:
                called = q["waiting"].pop(i)
                break
        q["now_serving"] = called
        self._save()
        st = self.status()
        st["called"] = called
        return st

    def visit_done(self, provider: str) -> dict:
        self._finish_current(provider)
        self._save()
        return self.status()

    # ---------- staff alerts ----------
    def add_alert(self, kind: str, text: str) -> dict:
        alert = {"id": f"A-{len(self.state['alerts']) + 1:03d}", "kind": kind,
                 "text": text, "at": self.now_fn().strftime("%H:%M"),
                 "done": False}
        self.state["alerts"].append(alert)
        self._save()
        return alert

    def alert_done(self, alert_id: str) -> dict:
        for a in self.state["alerts"]:
            if a["id"] == alert_id:
                a["done"] = True
        self._save()
        return self.status()

    def note_online_booking(self):
        self.state["counts"]["online_bookings"] += 1
        self._save()

    def note_noshow(self):
        """Count a no-show that happened outside the queues (an
        appointment marked 'not arrived' at reception)."""
        self.state["noshows"] += 1
        self._save()

    # ---------- status / dashboard ----------
    def _queue_view(self, provider: str) -> dict:
        q = self.state["queues"][provider]
        ahead = 0
        if q["now_serving"]:
            ahead += q["now_serving"].get("duration_min", DEFAULT_DURATION_MIN)
        waiting = []
        for t in q["waiting"]:
            entry = dict(t)
            entry["est_wait_min"] = ahead
            waiting.append(entry)
            ahead += t.get("duration_min", DEFAULT_DURATION_MIN)
        served = [s for s in self.state["served"]
                  if s["provider"] == provider]
        return {"now_serving": q["now_serving"], "waiting": waiting,
                "waiting_count": len(waiting),
                "arrived_count": sum(1 for t in waiting if t["arrived"]),
                "done_count": len(served),
                "earnings": sum(s.get("fee", 0) for s in served),
                "est_wait_new_min": ahead}

    def status(self) -> dict:
        self._roll_day()
        queues = {p: self._queue_view(p) for p in self.providers}
        return {
            "date": self.state["date"],
            "queues": queues,
            "total_waiting": sum(q["waiting_count"]
                                 for q in queues.values()),
            "total_done": len(self.state["served"]),
            "earnings_total": sum(s.get("fee", 0)
                                  for s in self.state["served"]),
            "earnings_by_provider": {p: queues[p]["earnings"]
                                     for p in self.providers},
            "noshows": self.state["noshows"],
            "counts": dict(self.state["counts"]),
            "alerts": list(self.state["alerts"]),
            "history": self.state["history"],
        }
