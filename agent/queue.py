"""Token queue for the clinic agent (v1.1 module).

The in-clinic half of Clinic OS: patients get numbered tokens, reception
calls the next token, the waiting-room board shows "now serving", and the
owner gets end-of-day counts. Deliberately independent of agent/core.py so
v1 behaviour stays untouched; server.py can wire both together.

State is a simple JSON file so a small clinic can run it on one machine.
"""
import json
import os
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUEUE_FILE = os.environ.get("QUEUE_FILE", os.path.join(BASE, "data", "queue.json"))


class TokenQueue:
    def __init__(self, path: str = QUEUE_FILE):
        self.path = path
        self.state = self._load()

    def _load(self) -> dict:
        today = time.strftime("%Y-%m-%d")
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as f:
                s = json.load(f)
            if s.get("date") == today:
                return s
        return {"date": today, "next_number": 1, "now_serving": None,
                "waiting": [], "served": []}

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2)

    def issue(self, patient_name: str, service: str, source: str = "whatsapp") -> dict:
        n = self.state["next_number"]
        token = {"token": f"T-{n:02d}", "number": n, "patient": patient_name,
                 "service": service, "source": source,
                 "issued_at": time.strftime("%H:%M")}
        self.state["next_number"] = n + 1
        self.state["waiting"].append(token)
        self._save()
        return token

    def call_next(self) -> dict | None:
        if self.state["now_serving"]:
            self.state["served"].append(self.state["now_serving"])
            self.state["now_serving"] = None
        if self.state["waiting"]:
            self.state["now_serving"] = self.state["waiting"].pop(0)
            self._save()
            return self.state["now_serving"]
        self._save()
        return None

    def complete_current(self) -> dict | None:
        cur = self.state["now_serving"]
        if cur:
            self.state["served"].append(cur)
            self.state["now_serving"] = None
            self._save()
        return cur

    def status(self) -> dict:
        return {"date": self.state["date"],
                "now_serving": self.state["now_serving"],
                "waiting": list(self.state["waiting"]),
                "waiting_count": len(self.state["waiting"]),
                "served_count": len(self.state["served"]),
                "total_today": self.state["next_number"] - 1}
