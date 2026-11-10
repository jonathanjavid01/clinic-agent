# Clinic WhatsApp Agent — v1

A config-driven AI receptionist for clinics and similar appointment businesses.
It answers patient questions on WhatsApp (timings, fees, doctors, location,
FAQs), books appointments step by step, refuses medical-advice requests, and
hands off to a human whenever needed. It understands English and basic
Roman Urdu.

**The product idea:** everything about a business lives in ONE JSON config
file (`config/`). Selling to a new clinic = copying the config, changing the
facts, connecting their WhatsApp number. No code changes per client.

## Run it (no installs — Python 3 only)

```bash
python3 server.py            # test chat at http://localhost:8000/
python3 tests/run_tests.py   # 14 automated checks
```

Optional: set `GEMINI_API_KEY` in the environment and open questions are
also answered by Gemini, grounded strictly in the config facts. Without a
key the built-in brain answers everything deterministically — it never
guesses: unknown questions get an honest "reception will help" reply.

## Files

- `agent/core.py` — the brain: intents, booking state machine, guardrails,
  retrieval, conversation logging, booking store.
- `agent/llm.py` — optional Gemini layer (key from environment only).
- `server.py` — test-chat web page **and** the real WhatsApp Cloud API
  webhook (`/webhook`, Meta verification + Graph API sending).
- `config/sample_clinic.json` — the sample business. Copy it per client.
- `data/bookings.json`, `data/conversations.jsonl` — created at runtime
  (owner can read every booking and every word the agent said).

## Safety rules built in

- Emergency keywords (chest pain, unconscious, heavy bleeding, …) → urgent
  message with ambulance number + chat flagged to staff.
- Medicine/treatment questions → polite refusal, offer a consultation.
- "Talk to a human" / complaints → immediate handoff message with the
  clinic's phone number, chat flagged.
- The agent only states facts from the client's own config.

## Going live for a real client (checklist)

1. Copy `config/sample_clinic.json`, fill in the client's real facts.
2. Create a Meta app + WhatsApp Business number (client's own, or yours as
   reseller) → set `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`,
   `WHATSAPP_VERIFY_TOKEN`.
3. Host `server.py` anywhere that runs Python (Render/Railway/a small VPS),
   point the Meta webhook at `https://<host>/webhook`.
4. Set `GEMINI_API_KEY` (client's or yours) if LLM answers are wanted.
5. Suggested pricing: one-time setup fee + monthly retainer per client;
   you keep the config, they keep their number and their data.

## Known v1 limits (honest)

- Dates/times are stored as the patient typed them ("tomorrow", "6 PM") —
  v2 should parse them into real calendar slots with availability checks.
- One conversation language mix (English + Roman Urdu keywords); full Urdu
  script support is a v2 item.
- Appointment reminders (day-before WhatsApp message) need a scheduler —
  planned for v2; Meta template-message approval is required for those.
