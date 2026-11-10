#!/usr/bin/env python3
"""Clinic OS server v3 (pure Python standard library, no installs).

Surfaces on one port:
1. Patient chat at /            - the WhatsApp front door, in a browser.
2. Clinic board at /board       - waiting-room TV tiles per doctor,
   reception desk (arrived / postpone / not arrived / call next /
   visit done), appointments with check-in actions, staff alerts and
   the owner dashboard.
3. WhatsApp Cloud API webhook at /webhook (Meta verification + replies
   through the Graph API when WHATSAPP_TOKEN etc. are set).

Everything business-specific comes from ONE config file chosen by the
CLINIC_CONFIG environment variable (default config/sample_clinic.json):
a new clinic, hospital or solo doctor = a new config file, no code
changes. See CLIENT_SETUP.md.

Run:  python3 server.py         (PORT env var, default 8000)
"""
import datetime
import json
import os
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from agent import Agent, load_config
from agent import core
from agent.queue import TokenQueue

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.environ.get(
    "CLINIC_CONFIG", os.path.join(BASE, "config", "sample_clinic.json"))
CONFIG = load_config(CONFIG_PATH)
PROVIDERS = [d["id"] for d in CONFIG["doctors"]]
queue = TokenQueue(services=CONFIG.get("services", []), providers=PROVIDERS)

_llm = None
try:
    from agent.llm import GeminiLLM
    _llm = GeminiLLM()
    if not _llm.available():
        _llm = None
except Exception:
    _llm = None
agent = Agent(CONFIG, llm=_llm, queue=queue)

VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN", "")
WA_TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
WA_PHONE_ID = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")


def meta() -> dict:
    return {
        "business_name": CONFIG["business_name"],
        "tagline": CONFIG.get("tagline", ""),
        "city": CONFIG.get("city", ""),
        "address": CONFIG.get("address", ""),
        "phone": CONFIG.get("phone", ""),
        "whatsapp": CONFIG.get("whatsapp") or CONFIG.get("handoff_phone", ""),
        "currency": CONFIG.get("currency", "PKR"),
        "sample_data": bool(CONFIG.get("sample_data")),
        "providers": [{"id": d["id"], "name": d["name"],
                       "specialty": d.get("specialty", ""),
                       "kind": d.get("kind", "doctor"),
                       "timings": d.get("timings", "")}
                      for d in CONFIG["doctors"]],
        "services": CONFIG.get("services", []),
        "hours": CONFIG.get("hours", {}),
        "open_windows": CONFIG.get("open_windows", {}),
        "instant_token_providers": CONFIG.get("instant_token_providers", []),
    }


def wa_send(to: str, text: str):
    if not (WA_TOKEN and WA_PHONE_ID):
        print(f"[whatsapp not configured] -> {to}: {text}")
        return
    body = json.dumps({"messaging_product": "whatsapp", "to": to,
                       "type": "text", "text": {"body": text}}).encode()
    req = urllib.request.Request(
        f"https://graph.facebook.com/v21.0/{WA_PHONE_ID}/messages",
        data=body, headers={"Authorization": f"Bearer {WA_TOKEN}",
                            "Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=15)
    except Exception as exc:  # keep serving even if Meta hiccups
        print("WhatsApp send failed:", exc)


CHAT_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__NAME__ - WhatsApp agent</title><style>
body{font-family:system-ui,Arial,sans-serif;background:#ece5dd;margin:0;display:flex;justify-content:center}
.phone{width:min(430px,100vw);height:100dvh;background:#ece5dd;display:flex;flex-direction:column}
header{background:#075e54;color:#fff;padding:12px 16px}
header b{font-size:17px} header div{font-size:12px;opacity:.85}
#log{flex:1;overflow-y:auto;padding:12px}
.msg{max-width:82%;padding:8px 11px;border-radius:10px;margin:5px 0;white-space:pre-wrap;line-height:1.45;font-size:15px}
.me{background:#dcf8c6;margin-left:auto}.bot{background:#fff}
.chips{display:flex;gap:6px;flex-wrap:wrap;padding:6px 10px;background:#f0ebe3}
.chips button{border:1px solid #075e54;color:#075e54;background:#fff;border-radius:16px;padding:6px 11px;font-size:13px;cursor:pointer}
form{display:flex;gap:8px;padding:10px;background:#f0f0f0}
input{flex:1;padding:11px;border:1px solid #ccc;border-radius:20px;font-size:15px}
form button{background:#075e54;color:#fff;border:0;border-radius:20px;padding:0 18px;font-size:15px;cursor:pointer}
</style></head><body><div class="phone">
<header><b>__NAME__</b><div>online - replies instantly (demo chat)</div></header>
<div id="log" aria-live="polite"></div>
<div class="chips">
<button onclick="quick('What are your timings?')">Timings</button>
<button onclick="quick('What are your fees?')">Fees</button>
<button onclick="quick('book')">Book appointment</button>
<button onclick="quick('token')">Get a token</button>
<button onclick="quick('talk to a human')">Talk to a human</button>
</div>
<form id="f"><input id="i" placeholder="Type a message..." autocomplete="off">
<button>Send</button></form></div>
<script>
const log=document.getElementById('log'),inp=document.getElementById('i');
const sid='web-'+Math.random().toString(36).slice(2);
function add(t,cls){const d=document.createElement('div');d.className='msg '+cls;d.textContent=t;log.appendChild(d);log.scrollTop=1e6}
async function send(text){add(text,'me');const r=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session:sid,message:text})});const j=await r.json();add(j.reply,'bot')}
function quick(t){send(t)}
document.getElementById('f').onsubmit=e=>{e.preventDefault();const v=inp.value.trim();if(v){inp.value='';send(v)}};
send('hi');
</script></div></body></html>"""

BOARD_PAGE = r"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Clinic Board</title><style>
body{font-family:system-ui,Arial,sans-serif;background:#0e2a24;color:#eafff8;margin:0;padding:18px}
h1{margin:0 0 2px;font-size:24px} .sub{opacity:.75;margin-bottom:12px}
.badge{display:inline-block;padding:3px 10px;border-radius:12px;font-size:13px;font-weight:700}
.open{background:#14532d;color:#bbf7d0}.closed{background:#7f1d1d;color:#fecaca}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px;margin-bottom:14px}
.tile{background:#123c32;border-radius:14px;padding:14px}
.tile h3{margin:0 0 4px;font-size:16px}.tile .now{font-size:44px;font-weight:800;line-height:1.05}
.tile .who{font-size:17px;min-height:22px}
.panels{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:12px}
.card{background:#f4fbf8;color:#122;border-radius:14px;padding:14px}
.card h3{margin:0 0 2px}.meta{font-size:13px;color:#456;margin-bottom:8px}
.row{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:6px 0;border-top:1px solid #dde}
.chip{font-size:11px;padding:2px 7px;border-radius:9px;background:#e2e8f0;color:#234}
.arr{background:#bbf7d0}.notyet{background:#fde68a}.tag{background:#ddd6fe}
button{background:#0b6b4f;color:#fff;border:0;border-radius:8px;padding:7px 11px;cursor:pointer;font-size:13px}
button.alt{background:#e2e8f0;color:#123}button.warn{background:#b91c1c}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin-bottom:14px}
.kpi{background:#123c32;border-radius:12px;padding:10px}.kpi b{font-size:22px;display:block}
.bar{display:flex;align-items:flex-end;gap:3px;height:70px;margin-top:6px}
.bar div{flex:1;background:#2dd4bf;border-radius:3px 3px 0 0;min-height:3px}
.barlab{display:flex;gap:3px;font-size:10px;opacity:.75}.barlab span{flex:1;text-align:center}
input,select{padding:8px;border:1px solid #bbb;border-radius:8px;font-size:14px}
.small{font-size:12px;opacity:.8}
</style></head><body>
<h1 id="biz">Clinic Board</h1>
<div class="sub"><span id="openbadge" class="badge open">...</span>
<span id="dateline"></span> - one queue per doctor; "Call next" picks the first ARRIVED patient.
<button class="alt" style="float:right" onclick="resetDemo()">Reset demo</button></div>

<div class="grid" id="tiles"></div>
<div class="kpis" id="kpis"></div>
<div class="panels">
 <div class="card"><h3>Owner dashboard</h3><div class="meta">Peak hours (visits finished per hour)</div>
 <div class="bar" id="peakHours"></div><div class="barlab" id="peakHoursLab"></div>
 <div class="meta" style="margin-top:8px">Peak days</div>
 <div class="bar" id="peakDays"></div><div class="barlab" id="peakDaysLab"></div>
 <div class="small" id="histnote"></div></div>
 <div class="card"><h3>Staff alerts</h3><div id="alerts"></div></div>
 <div class="card"><h3>Appointments today</h3><div id="appts"></div>
 <div class="meta" style="margin-top:10px">Patients can also chat on the home page; a booking joins the queue only when the patient checks in with its Booking ID.</div></div>
</div>
<h2 style="margin:16px 0 8px">Reception desk</h2>
<div class="panels" id="desk"></div>
<div class="card" style="margin-top:12px"><h3>Walk-in</h3>
<input id="wname" placeholder="Patient name">
<select id="wsvc"></select>
<button onclick="walkin()">Add walk-in (arrived)</button></div>

<script>
let META=null;
const $=id=>document.getElementById(id);
async function J(u,o){const r=await fetch(u,o);return r.json()}
async function act(u,body){await J(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});refresh()}
function openNow(){const n=new Date(),days=['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];const w=(META.open_windows||{})[days[n.getDay()]]||[];const m=n.getHours()*60+n.getMinutes();return w.some(([a,b])=>{const A=+a.split(':')[0]*60+ +a.split(':')[1],B=+b.split(':')[0]*60+ +b.split(':')[1];return m>=A&&m<B})}
async function refresh(){
 const q=await J('/api/queue');const bk=await J('/api/bookings');
 $('dateline').textContent=' - '+q.date;
 const ob=$('openbadge');const on=openNow();ob.textContent=on?'OPEN now':'CLOSED now';ob.className='badge '+(on?'open':'closed');
 $('tiles').innerHTML=META.providers.map(p=>{const v=q.queues[p.id]||{now_serving:null,waiting_count:0,arrived_count:0};const ns=v.now_serving;
  return `<div class="tile"><h3>${p.name}</h3><div class="now">${ns?ns.token:'--'}</div><div class="who">${ns?ns.patient_display+' - '+ns.service:'Free'}</div><div>${v.waiting_count} waiting - ${v.arrived_count} arrived</div></div>`}).join('');
 const cur=META.currency;
 $('kpis').innerHTML=[['Earnings today',cur+' '+q.earnings_total.toLocaleString()],['Visits done',q.total_done],['Online bookings',q.counts.online_bookings],['WhatsApp tokens',q.counts.whatsapp_tokens],['Walk-ins',q.counts.walkins],['Waiting now',q.total_waiting],['No-shows',q.noshows]].map(([k,v])=>`<div class="kpi">${k}<b>${v}</b></div>`).join('');
 const hrs=Object.entries(q.history.hours).sort((a,b)=>a[0]-b[0]);const mx=Math.max(1,...hrs.map(x=>+x[1]));
 $('peakHours').innerHTML=hrs.map(([h,c])=>`<div style="height:${Math.round(c/mx*100)}%" title="${h}:00 - ${c}"></div>`).join('');
 $('peakHoursLab').innerHTML=hrs.map(([h])=>`<span>${h}</span>`).join('');
 const days=['Mon','Tue','Wed','Thu','Fri','Sat'];const mxd=Math.max(1,...days.map(d=>q.history.days[d]||0));
 $('peakDays').innerHTML=days.map(d=>`<div style="height:${Math.round((q.history.days[d]||0)/mxd*100)}%"></div>`).join('');
 $('peakDaysLab').innerHTML=days.map(d=>`<span>${d} ${q.history.days[d]||0}</span>`).join('');
 $('histnote').textContent=q.history.sample?'Peak charts include 14 days of sample history (demo). Real visits add on top.':'';
 $('alerts').innerHTML=q.alerts.filter(a=>!a.done).length?q.alerts.filter(a=>!a.done).map(a=>`<div class="row"><span>${a.at} - ${a.text}</span><button onclick="act('/api/alerts/done',{id:'${a.id}'})">Done</button></div>`).join(''):'<p class="small">No open alerts</p>';
 const today=q.date;
 $('appts').innerHTML=bk.filter(b=>b.date_iso===today).length?bk.filter(b=>b.date_iso===today).map(b=>{let status=b.noshow?'<span class="chip notyet">NO-SHOW</span>':b.checked_in?`<span class="chip arr">CHECKED IN ${b.token||''}</span>`:'<span class="chip">BOOKED</span>';
  let btns=b.checked_in||b.noshow?'':`<button onclick="act('/api/bookings/arrived',{id:'${b.id}'})">Arrived</button> <button class="alt" onclick="act('/api/bookings/postpone',{id:'${b.id}'})">Postpone</button> <button class="warn" onclick="act('/api/bookings/notarrived',{id:'${b.id}'})">Not arrived</button>`;
  return `<div class="row"><span><b>${b.time}</b> ${b.patient_name}<br><span class="small">${b.service} - ${b.id}</span></span><span>${status}<br>${btns}</span></div>`}).join(''):'<p class="small">No appointments today yet</p>';
 $('desk').innerHTML=META.providers.map(p=>{const v=q.queues[p.id]||{now_serving:null,waiting:[],done_count:0,earnings:0};const ns=v.now_serving;
  const rows=v.waiting.map(t=>`<div class="row"><span><b>${t.token}</b> ${t.patient} ${t.tag?`<span class="chip tag">${t.tag}</span>`:''} ${t.source==='checkin'?'<span class="chip tag">Appt</span>':''}<br><span class="small">${t.service} - ${t.source} - wait about ${t.est_wait_min} min</span></span><span><span class="chip ${t.arrived?'arr':'notyet'}">${t.arrived?'ARRIVED':'NOT YET'}</span><br>
  ${t.arrived?'':`<button class="alt" onclick="act('/api/queue/arrived',{provider:'${p.id}',token:'${t.token}'})">Arrived</button> `}
  <button class="alt" onclick="act('/api/queue/postpone',{provider:'${p.id}',token:'${t.token}'})">Postpone</button>
  <button class="warn" onclick="act('/api/queue/notarrived',{provider:'${p.id}',token:'${t.token}'})">Not arrived</button></span></div>`).join('');
  return `<div class="card"><h3>${p.name}</h3><div class="meta">${p.specialty} - ${v.done_count} done - ${cur} ${v.earnings.toLocaleString()} collected</div>
  <div class="row"><span>${ns?`<b>${ns.token}</b> ${ns.patient} - ${ns.service}`:'No patient inside'}</span>${ns?`<button onclick="act('/api/queue/done',{provider:'${p.id}'})">Visit done</button>`:''}</div>
  <div style="margin:8px 0"><button onclick="act('/api/queue/callnext',{provider:'${p.id}'})">Call next arrived</button></div>
  ${rows||'<p class="small">Queue clear</p>'}</div>`}).join('');
}
async function walkin(){const name=$('wname').value.trim();if(!name)return alert('Enter the patient name');await act('/api/queue/issue',{patient:name,service:$('wsvc').value,source:'walkin',arrived:true});$('wname').value=''}
async function resetDemo(){if(confirm('Reset all demo data (queues, bookings, alerts)?'))await act('/api/reset',{})}
(async()=>{META=await J('/api/meta');$('biz').textContent=META.business_name+' - Clinic Board';document.title=META.business_name+' - Clinic Board';
 $('wsvc').innerHTML=META.services.map(s=>`<option>${s.name}</option>`).join('');refresh();setInterval(refresh,3000)})();
</script></body></html>
"""


def service_provider(service: str) -> str:
    for s in CONFIG.get("services", []):
        if s["name"] == service:
            return s["provider"]
    return PROVIDERS[0]


def update_booking(booking_id: str, **changes) -> dict | None:
    bookings = core.load_bookings()
    found = None
    for b in bookings:
        if b["id"] == booking_id:
            b.update(changes)
            found = b
    if found:
        core.save_bookings(bookings)
    return found


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return {}

    # ---------- GET ----------
    def do_GET(self):
        if self.path == "/":
            return self._send(
                200, CHAT_PAGE.replace("__NAME__", CONFIG["business_name"]))
        if self.path == "/board":
            return self._send(200, BOARD_PAGE)
        if self.path == "/api/meta":
            return self._send(200, json.dumps(meta()), "application/json")
        if self.path == "/api/queue":
            return self._send(200, json.dumps(queue.status()),
                              "application/json")
        if self.path == "/api/bookings":
            return self._send(200, json.dumps(core.load_bookings()),
                              "application/json")
        if self.path.startswith("/webhook"):
            mode = self.path
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            if (qs.get("hub.mode") == ["subscribe"] and
                    qs.get("hub.verify_token") == [VERIFY_TOKEN]):
                return self._send(200, qs["hub.challenge"][0],
                                  "text/plain")
            return self._send(403, "verification failed", "text/plain")
        return self._send(404, "not found", "text/plain")

    # ---------- POST ----------
    def do_POST(self):
        body = self._json_body()
        p = self.path
        if p == "/api/chat":
            reply = agent.reply(body.get("session", "web"),
                                body.get("message", ""))
            return self._send(200, json.dumps({"reply": reply}),
                              "application/json")
        if p == "/api/queue/issue":
            provider = service_provider(body.get("service", ""))
            token = queue.issue(provider, body.get("patient", "Walk-in"),
                                body.get("service", ""),
                                source=body.get("source", "walkin"),
                                arrived=bool(body.get("arrived", True)))
            return self._send(200, json.dumps(token), "application/json")
        if p == "/api/queue/callnext":
            return self._send(200, json.dumps(
                queue.call_next(body.get("provider", ""))),
                "application/json")
        if p == "/api/queue/done":
            return self._send(200, json.dumps(
                queue.visit_done(body.get("provider", ""))),
                "application/json")
        if p == "/api/queue/arrived":
            return self._send(200, json.dumps(queue.mark_arrived(
                body.get("provider", ""), body.get("token", ""))),
                "application/json")
        if p == "/api/queue/postpone":
            return self._send(200, json.dumps(queue.postpone(
                body.get("provider", ""), body.get("token", ""))),
                "application/json")
        if p == "/api/queue/notarrived":
            return self._send(200, json.dumps(queue.not_arrived(
                body.get("provider", ""), body.get("token", ""))),
                "application/json")
        if p == "/api/alerts/done":
            return self._send(200, json.dumps(
                queue.alert_done(body.get("id", ""))), "application/json")
        if p == "/api/bookings/arrived":
            b = core.find_booking(body.get("id", ""))
            if b and not b.get("checked_in") and not b.get("noshow"):
                token = queue.issue(b["provider"], b["patient_name"],
                                    b["service"], source="checkin",
                                    arrived=True, tag="Appt")
                update_booking(b["id"], checked_in=True,
                               token=token["token"])
            return self._send(200, json.dumps({"ok": True}),
                              "application/json")
        if p == "/api/bookings/notarrived":
            b = core.find_booking(body.get("id", ""))
            if b and not b.get("noshow"):
                update_booking(b["id"], noshow=True)
                queue.note_noshow()
            return self._send(200, json.dumps({"ok": True}),
                              "application/json")
        if p == "/api/bookings/postpone":
            b = core.find_booking(body.get("id", ""))
            if b and not b.get("checked_in") and not b.get("noshow"):
                day = datetime.date.fromisoformat(b["date_iso"])
                t = core._hm(b["time24"])
                nxt = core.next_free_slot(
                    CONFIG, core.load_bookings(), b["provider"], day,
                    t + 30, ignore_id=b["id"])
                if nxt:
                    nd, nt = nxt
                    update_booking(
                        b["id"], date_iso=nd.isoformat(),
                        date=nd.strftime("%a %d %b %Y"),
                        time24=f"{nt // 60:02d}:{nt % 60:02d}",
                        time=core.fmt_time(nt))
            return self._send(200, json.dumps({"ok": True}),
                              "application/json")
        if p == "/api/reset":
            queue.reset()
            core.save_bookings([])
            return self._send(200, json.dumps({"ok": True}),
                              "application/json")
        if p.startswith("/webhook"):
            try:
                for entry in body.get("entry", []):
                    for change in entry.get("changes", []):
                        for msg in change.get("value", {}).get(
                                "messages", []):
                            if msg.get("type") == "text":
                                reply = agent.reply(
                                    msg["from"],
                                    msg["text"]["body"])
                                wa_send(msg["from"], reply)
            except Exception as exc:
                print("webhook error:", exc)
            return self._send(200, json.dumps({"ok": True}),
                              "application/json")
        return self._send(404, "not found", "text/plain")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    print(f"{CONFIG['business_name']} - Clinic OS v3 on "
          f"http://localhost:{port}  (chat /, board /board, "
          f"config {os.path.basename(CONFIG_PATH)})")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
