# Clinic OS — WhatsApp front door + live token queue (v3)

One system for clinics, hospitals and solo doctors:

- **Patient side (WhatsApp / web chat):** answers timings, fees, doctors and
  location from the clinic's own data; books appointments with real date,
  time and doctor-schedule validation (no Sundays, no past dates, no
  double bookings — the next free slot is suggested); issues instant
  tokens for walk-in services (e.g. GP, Lab); patients check in with
  their Booking ID when they arrive and only then join the queue.
- **Inside the clinic (board at `/board`):** one queue per doctor or
  department, waiting-room tiles with privacy-safe names (first name +
  initial), reception actions — Arrived, Postpone, Not arrived,
  Call next (arrived patients only), Visit done — staff alerts with a
  Done button, and an owner dashboard: earnings today (total and per
  doctor), visits, online bookings, walk-ins, no-shows, peak hours and
  peak days.
- **Safety:** emergencies get the Rescue 1122 message immediately
  (English and Roman Urdu phrases); medical-advice requests are refused
  and routed to a booking; "talk to a human" creates a real staff alert.

## One product, many clients — everything is config

All business specifics live in ONE JSON file per client in `config/`:

| File | Client shape |
|---|---|
| `config/sample_clinic.json` | Multi-doctor clinic (GP + dental + skin + lab) |
| `config/solo_gp.json` | A single-doctor practice (one queue) |
| `config/sample_hospital.json` | A small hospital (departments as queues: OPD, gynae, paeds, lab) |

Business name, tagline, city, address, phones, currency, hours (display +
machine-readable open windows), doctors/departments with their own
schedules, services with fees/durations and which queue each belongs to,
which services may issue instant tokens, FAQs and policies — all config.
**A new client = a new config file. No code changes.** See
`CLIENT_SETUP.md` for the onboarding checklist.

## Run it

```bash
python3 server.py                       # sample clinic on :8000
CLINIC_CONFIG=config/solo_gp.json python3 server.py
PORT=8000 python3 server.py             # chat at /, board at /board
```

- Chat (patient view): `http://localhost:8000/`
- Board (TV + reception + owner): `http://localhost:8000/board`
- Tests: `python3 tests/run_tests.py` (32 checks) and
  `python3 tests/test_queue.py` (15 checks) — no network, no API key.

## Going live on WhatsApp

The webhook is built in (`/webhook`). Set `WHATSAPP_VERIFY_TOKEN`,
`WHATSAPP_TOKEN` and `WHATSAPP_PHONE_NUMBER_ID` from the clinic's Meta
WhatsApp Business account, point Meta's webhook at the deployed URL,
and the same brain answers on WhatsApp. An optional Gemini layer
(`GEMINI_API_KEY`) phrases open questions, grounded strictly in the
config facts; without a key the agent stays fully deterministic.

## State and its limits (pilot honesty)

Queues and bookings are JSON files on the server's disk
(`data/`, overridable with `AGENT_DATA_DIR` / `QUEUE_FILE`). On free
hosting tiers a redeploy can reset that disk — for a paying client,
move state to a small database and a paid always-on instance; both are
one-line changes in `agent/queue.py` / `agent/core.py` storage helpers.
Peak charts seed 14 days of clearly-labelled sample history in the
demo so they are not empty; real visits add on top
(`SEED_SAMPLE_HISTORY` in `agent/queue.py`).
