# Client Setup — launching Clinic OS for a new clinic, hospital or doctor

The product is identical for every client. A launch is **one new config
file** plus **one deployment setting**. Nothing in the code changes.

## 1. Collect this from the client (15-minute call)

- Business name, tagline (one line), city, full address
- Landline + the WhatsApp number patients will message
- Currency (usually PKR)
- Opening hours per day, including breaks (e.g. Friday 1–3 PM) and
  closed days
- Every doctor / department that needs its own queue:
  name, specialty, which days, start–end time (note any day that ends
  earlier, e.g. GP on Friday)
- Every service: name, fee, typical duration in minutes, and which
  doctor/department it belongs to
- Which services may issue **instant tokens** (walk-in services like GP
  and Lab; appointment-only services like dental/skin should not)
- 3–6 FAQs (parking, reports, insurance/Sehat Card, home visits…)
- Policies worth quoting (cancellation, late arrival)

## 2. Create their config

Copy the closest sample and edit it:

- Solo doctor → `config/solo_gp.json`
- Clinic with several doctors → `config/sample_clinic.json`
- Hospital / departments → `config/sample_hospital.json`

Save as `config/<client-slug>.json`. Keep `sample_data: false` for a
real client (the demo configs use `true`). Quick check:

```bash
python3 -c "from agent import load_config; load_config('config/<client-slug>.json'); print('config OK')"
```

## 3. Run / deploy for that client

```bash
CLINIC_CONFIG=config/<client-slug>.json python3 server.py
```

On Render (or any host): one service per client, environment variable
`CLINIC_CONFIG=config/<client-slug>.json`. For a paying client use an
always-on instance — the free tier sleeps and its disk can reset,
which wipes the day's queue file.

## 4. Connect their WhatsApp

1. Client creates a Meta Business account and registers the WhatsApp
   number (or you do it with them on a call — the number must be theirs).
2. Set env vars on the service: `WHATSAPP_VERIFY_TOKEN` (you choose it),
   `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID` (from Meta).
3. In Meta's WhatsApp config, set the webhook to
   `https://<their-service>/webhook` with the same verify token and
   subscribe to `messages`.
4. Send a test message from your own phone; watch it appear in the
   board's alerts/queue as you chat.

Optional: `GEMINI_API_KEY` enables the AI phrasing layer for open
questions (grounded in the config facts only).

## 5. In the clinic

- Waiting room: open `https://<their-service>/board` on the TV browser
  (it refreshes itself every 3 seconds; names show first name + initial
  only, for privacy).
- Reception desk: same board, lower half — one panel per doctor with
  Arrived / Postpone / Not arrived / Call next / Visit done, plus the
  appointments list with one-click check-in.
- Owner: the dashboard strip on the same page (earnings, visits,
  no-shows, peak hours/days).

## 6. What never changes per client

Code, tests, safety rules (emergency handling, no medical advice),
booking validation, and the board layout. If a client asks for
something the config cannot express, that is a product feature request
— build it once, for everyone, never as a one-off fork.
