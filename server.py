#!/usr/bin/env python3
"""Clinic WhatsApp agent - server (pure Python standard library, no installs).

Runs two surfaces on one port:
1. A browser test chat at  http://localhost:8000/          (for demos/testing)
   API: POST /api/chat  {"session": "abc", "message": "..."}
2. The real WhatsApp Cloud API webhook:
   GET  /webhook  (Meta verification, uses WHATSAPP_VERIFY_TOKEN)
   POST /webhook  (incoming messages; replies are sent through the Graph API
                   when WHATSAPP_TOKEN and WHATSAPP_PHONE_NUMBER_ID are set,
                   otherwise the reply is written to the conversation log)

Environment:
  AGENT_CONFIG       path to the business config JSON (default config/sample_clinic.json)
  PORT               default 8000
  GEMINI_API_KEY     optional - enables LLM answers for open questions
  WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_VERIFY_TOKEN  (production)
"""
import json
import os
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from agent.core import Agent
from agent.queue import TokenQueue

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.environ.get("AGENT_CONFIG", os.path.join(BASE, "config", "sample_clinic.json"))
agent = Agent(CONFIG)
queue = TokenQueue()

BOARD_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>Clinic Queue Board</title>
<style>body{font-family:system-ui;margin:0;background:#0b3d2e;color:#fff}
.wrap{max-width:900px;margin:0 auto;padding:16px}
.now{background:#062e22;border:3px solid #25D366;border-radius:14px;text-align:center;padding:10px}
.now .tok{font-size:84px;font-weight:800;color:#25D366;line-height:1}
.now .who{font-size:20px}
.cols{display:flex;gap:16px;flex-wrap:wrap;margin-top:14px}
.col{flex:1;min-width:260px;background:#ffffff;color:#111;border-radius:12px;padding:12px}
.col h3{margin:4px 0 10px}
.row{display:flex;justify-content:space-between;padding:7px 8px;border-bottom:1px solid #eee}
button{background:#25D366;border:0;padding:10px 14px;border-radius:8px;font-weight:700;cursor:pointer;margin:4px 4px 4px 0}
input,select{padding:9px;margin:4px 0;width:100%;box-sizing:border-box}
.small{color:#555;font-size:13px}</style></head><body><div class="wrap">
<h2 id="bn">Clinic Queue</h2>
<div class="now"><div class="small" style="color:#bfe9d4">NOW SERVING</div>
<div class="tok" id="tok">--</div><div class="who" id="who">No patient called yet</div></div>
<div class="cols">
<div class="col"><h3>Waiting</h3><div id="wait"></div>
<p class="small" id="counts"></p></div>
<div class="col"><h3>Reception</h3>
<button onclick="act('/api/queue/next')">Call next patient</button>
<button onclick="act('/api/queue/done')">Mark current done</button>
<h4>Add walk-in</h4>
<input id="wn" placeholder="Patient name">
<input id="ws" placeholder="Service (e.g. General Consultation (GP))">
<button onclick="walkin()">Issue token</button>
<p class="small">Patients can also chat with the agent on the home page; bookings made there are saved by the agent.</p>
</div></div></div>
<script>
async function load(){const r=await fetch('/api/queue');const q=await r.json();
document.getElementById('tok').textContent=q.now_serving?q.now_serving.token:'--';
document.getElementById('who').textContent=q.now_serving?(q.now_serving.patient+' - '+q.now_serving.service):'No patient called yet';
document.getElementById('wait').innerHTML=q.waiting.map(w=>'<div class="row"><span><b>'+w.token+'</b> '+w.patient+'</span><span>'+w.service+'</span></div>').join('')||'<p class="small">Nobody waiting</p>';
document.getElementById('counts').textContent='Waiting: '+q.waiting_count+' | Served today: '+q.served_count+' | Total tokens: '+q.total_today;}
async function act(p){await fetch(p,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});load();}
async function walkin(){const n=document.getElementById('wn').value.trim(),s=document.getElementById('ws').value.trim();
if(!n||!s)return alert('Name and service needed');
await fetch('/api/queue/issue',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({patient:n,service:s,source:'walk-in'})});
document.getElementById('wn').value='';document.getElementById('ws').value='';load();}
load();setInterval(load,3000);
</script></body></html>"""

CHAT_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>Clinic Agent - test chat</title>
<style>body{font-family:system-ui;max-width:640px;margin:24px auto;padding:0 12px}
#log{border:1px solid #ddd;border-radius:10px;padding:12px;height:55vh;overflow:auto}
.u{text-align:right;margin:8px}.u span{background:#dcf8c6;padding:8px 12px;border-radius:12px;display:inline-block}
.a{margin:8px}.a span{background:#f1f0f0;padding:8px 12px;border-radius:12px;display:inline-block;white-space:pre-wrap}
form{display:flex;gap:8px;margin-top:10px}input{flex:1;padding:10px}button{padding:10px 16px}</style>
</head><body><h2 id="t">Clinic Agent - test chat</h2><div id="log"></div>
<form id="f"><input id="m" placeholder="Type as a patient..." autocomplete="off"><button>Send</button></form>
<script>
const log=document.getElementById('log'),f=document.getElementById('f'),m=document.getElementById('m');
const sess='web-'+Math.random().toString(36).slice(2);
function add(cls,txt){const d=document.createElement('div');d.className=cls;const s=document.createElement('span');s.textContent=txt;d.appendChild(s);log.appendChild(d);log.scrollTop=1e9}
async function send(text){add('u',text);const r=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session:sess,message:text})});const j=await r.json();add('a',j.reply)}
f.onsubmit=e=>{e.preventDefault();const v=m.value.trim();if(v){m.value='';send(v)}};
send('hello');
</script></body></html>"""


def send_whatsapp(to: str, text: str) -> None:
    token = os.environ.get("WHATSAPP_TOKEN")
    phone_id = os.environ.get("WHATSAPP_PHONE_NUMBER_ID")
    if not token or not phone_id:
        print(f"[whatsapp not configured] would send to {to}: {text[:80]}...")
        return
    body = json.dumps({"messaging_product": "whatsapp", "to": to,
                       "type": "text", "text": {"body": text}}).encode()
    req = urllib.request.Request(
        f"https://graph.facebook.com/v20.0/{phone_id}/messages",
        data=body, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    urllib.request.urlopen(req, timeout=20)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/":
            return self._send(200, CHAT_PAGE, "text/html")
        if self.path == "/board":
            return self._send(200, BOARD_PAGE, "text/html")
        if self.path == "/api/queue":
            return self._send(200, json.dumps(queue.status()), "application/json")
        if self.path.startswith("/webhook"):
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            if (q.get("hub.mode", [""])[0] == "subscribe"
                    and q.get("hub.verify_token", [""])[0] == os.environ.get("WHATSAPP_VERIFY_TOKEN", "")):
                return self._send(200, q.get("hub.challenge", [""])[0], "text/plain")
            return self._send(403, "verification failed", "text/plain")
        return self._send(404, "not found", "text/plain")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode())
        except Exception:
            return self._send(400, "{}", "application/json")
        if self.path == "/api/chat":
            reply = agent.reply(payload.get("session", "web"), payload.get("message", ""))
            return self._send(200, json.dumps({"reply": reply}), "application/json")
        if self.path == "/api/queue/issue":
            t = queue.issue(payload.get("patient", ""), payload.get("service", ""),
                            payload.get("source", "walk-in"))
            return self._send(200, json.dumps(t), "application/json")
        if self.path == "/api/queue/next":
            cur = queue.call_next()
            return self._send(200, json.dumps({"now_serving": cur}), "application/json")
        if self.path == "/api/queue/done":
            done = queue.complete_current()
            return self._send(200, json.dumps({"completed": done}), "application/json")
        if self.path == "/webhook":
            try:
                for entry in payload.get("entry", []):
                    for change in entry.get("changes", []):
                        for msg in change.get("value", {}).get("messages", []):
                            if msg.get("type") == "text":
                                reply = agent.reply(msg["from"], msg["text"]["body"])
                                send_whatsapp(msg["from"], reply)
            except Exception as e:
                print("webhook error:", e)
            return self._send(200, "{}", "application/json")
        return self._send(404, "{}", "application/json")

    def _send(self, code, body, ctype):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    print(f"Clinic agent running on http://localhost:{port}  (config: {CONFIG})")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
