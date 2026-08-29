"""
server.py — AgentRx console: chat on the left, live swarm sidebar on the right.

Stdlib only (http.server + SSE) so there's nothing to install mid-demo.

    python server.py          # then open http://localhost:8765

The sidebar is fed by events.publish("swarm", ...) calls made from
attending.py's real event-stream loop — so what you see scrolling is the
actual Managed Agents fan-out, not a re-enactment.
"""
import json
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import events

HERE = Path(__file__).resolve().parent
PORT = 8765

PAGE = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>AgentRx Console</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
  :root {
    --bg:#0b0d12; --panel:#12151c; --panel2:#171b24; --border:#232936;
    --text:#e7eaf0; --muted:#8a94a6; --dim:#5d6675;
    --accent:#6ea8fe; --accent2:#a78bfa; --good:#4ade80; --warn:#fbbf24; --bad:#f87171;
    --mono:'JetBrains Mono',ui-monospace,SFMono-Regular,Consolas,monospace;
  }
  * { box-sizing:border-box; }
  html,body { height:100%; }
  body { margin:0; display:flex; overflow:hidden;
         font:14.5px/1.6 Inter,ui-sans-serif,system-ui,-apple-system,'Segoe UI',sans-serif;
         background:var(--bg); color:var(--text); -webkit-font-smoothing:antialiased; }

  /* ---------- left: chat ---------- */
  #left { flex:1; display:flex; flex-direction:column; min-width:0; }
  .topbar { display:flex; align-items:center; gap:12px; padding:14px 20px;
            border-bottom:1px solid var(--border); background:var(--panel); }
  .logo { width:30px; height:30px; border-radius:8px; flex:0 0 auto; display:grid; place-items:center;
          background:linear-gradient(135deg,var(--accent),var(--accent2)); color:#0b0d12;
          font-weight:700; font-size:15px; }
  .titles { flex:1; min-width:0; }
  .titles h1 { margin:0; font-size:15px; font-weight:600; letter-spacing:-.01em; }
  .titles p { margin:1px 0 0; font-size:12px; color:var(--muted); }
  .pill { font:500 11px/1 var(--mono); padding:5px 9px; border-radius:999px;
          border:1px solid var(--border); color:var(--muted); white-space:nowrap; }
  .pill.live { color:var(--good); border-color:rgba(74,222,128,.3); background:rgba(74,222,128,.08); }
  .pill.busy { color:var(--warn); border-color:rgba(251,191,36,.3); background:rgba(251,191,36,.08); }
  .pill.off  { color:var(--bad);  border-color:rgba(248,113,113,.3); background:rgba(248,113,113,.08); }

  #log { flex:1; overflow-y:auto; padding:22px 20px 8px; }
  .msg { margin-bottom:18px; max-width:74ch; }
  .who { display:flex; align-items:center; gap:7px; font-size:11px; font-weight:600;
         text-transform:uppercase; letter-spacing:.07em; color:var(--dim); margin-bottom:5px; }
  .dot { width:6px; height:6px; border-radius:50%; background:var(--dim); }
  .you .who { color:var(--accent); } .you .dot { background:var(--accent); }
  .rx .who { color:var(--accent2); } .rx .dot { background:var(--accent2); }
  .sys .who { color:var(--warn); }  .sys .dot { background:var(--warn); }
  .bubble { white-space:pre-wrap; word-wrap:break-word; }
  .bubble code { font:500 12.5px var(--mono); background:var(--panel2);
                 border:1px solid var(--border); border-radius:4px; padding:1px 5px; }
  .bubble strong { color:#fff; font-weight:600; }
  .typing { display:inline-flex; gap:4px; align-items:center; }
  .typing i { width:5px; height:5px; border-radius:50%; background:var(--accent2);
              animation:bounce 1.3s infinite; }
  .typing i:nth-child(2){animation-delay:.16s} .typing i:nth-child(3){animation-delay:.32s}
  @keyframes bounce { 0%,60%,100%{opacity:.25;transform:translateY(0)} 30%{opacity:1;transform:translateY(-3px)} }

  .quick { display:flex; gap:8px; flex-wrap:wrap; padding:0 20px 12px; }
  .chip { font-size:12.5px; padding:6px 12px; border-radius:999px; cursor:pointer;
          background:var(--panel2); border:1px solid var(--border); color:var(--muted); }
  .chip:hover { color:var(--text); border-color:var(--accent); }
  form { display:flex; gap:10px; padding:14px 20px 18px; border-top:1px solid var(--border);
         background:var(--panel); }
  input[type=text] { flex:1; background:var(--bg); border:1px solid var(--border); color:var(--text);
                     padding:11px 14px; border-radius:9px; font:inherit; outline:none; }
  input[type=text]:focus { border-color:var(--accent); box-shadow:0 0 0 3px rgba(110,168,254,.12); }
  button { background:linear-gradient(135deg,var(--accent),var(--accent2)); border:0; color:#0b0d12;
           font-weight:600; padding:11px 20px; border-radius:9px; cursor:pointer; font:inherit; }
  button:disabled { opacity:.45; cursor:default; }

  /* ---------- right: swarm ---------- */
  #right { width:400px; flex:0 0 400px; border-left:1px solid var(--border);
           background:var(--panel); display:flex; flex-direction:column; }
  .rhead { padding:14px 18px; border-bottom:1px solid var(--border); }
  .rhead h2 { margin:0; font-size:12px; font-weight:600; text-transform:uppercase;
              letter-spacing:.09em; color:var(--muted); }
  .roster { padding:14px 18px; border-bottom:1px solid var(--border);
            display:flex; flex-direction:column; gap:9px; }
  .sp { display:flex; align-items:center; gap:10px; }
  .sp .led { width:8px; height:8px; border-radius:50%; background:var(--dim); flex:0 0 auto;
             transition:background .2s, box-shadow .2s; }
  .sp.running .led { background:var(--warn); box-shadow:0 0 0 4px rgba(251,191,36,.16);
                     animation:pulse 1.2s infinite; }
  .sp.done .led { background:var(--good); box-shadow:0 0 0 4px rgba(74,222,128,.14); }
  @keyframes pulse { 50%{opacity:.45} }
  .sp .nm { flex:1; font-size:13px; }
  .sp .st { font:500 10.5px var(--mono); color:var(--dim); text-transform:uppercase; letter-spacing:.06em; }
  .sp.running .st { color:var(--warn); } .sp.done .st { color:var(--good); }

  .metrics { padding:12px 18px; border-bottom:1px solid var(--border);
             display:grid; grid-template-columns:1fr 1fr; gap:10px; }
  .m { background:var(--panel2); border:1px solid var(--border); border-radius:8px; padding:8px 10px; }
  .m .k { font-size:10px; text-transform:uppercase; letter-spacing:.07em; color:var(--dim); }
  .m .v { font:600 15px var(--mono); margin-top:2px; }

  .streamhead { display:flex; align-items:center; justify-content:space-between;
                padding:11px 18px 7px; }
  .streamhead span { font-size:11px; text-transform:uppercase; letter-spacing:.09em; color:var(--dim); }
  #swarm { flex:1; overflow-y:auto; padding:0 18px 16px; font:12.5px/1.7 var(--mono); }
  .ev { display:flex; gap:9px; padding:4px 0; border-bottom:1px solid rgba(255,255,255,.035);
        animation:slide .22s ease-out; }
  @keyframes slide { from{opacity:0;transform:translateX(-5px)} to{opacity:1;transform:none} }
  .ev .ts { flex:0 0 46px; color:var(--dim); font-size:11px; }
  .ev .tag { flex:0 0 66px; }
  .ev .actor { flex:1; word-break:break-word; color:var(--muted); }
  .t-spawned  .tag { color:var(--accent); }
  .t-delegate .tag { color:var(--warn); }
  .t-reply    .tag { color:var(--good); }
  .t-running  .tag { color:var(--accent2); }
  .t-tool     .tag { color:var(--muted); }
  .t-idle     .tag { color:var(--dim); }
  .empty { color:var(--dim); font:12.5px/1.7 var(--mono); padding:8px 0; }
  #hint { padding:11px 18px; border-top:1px solid var(--border); color:var(--dim); font-size:11.5px; }
  ::-webkit-scrollbar { width:9px; } ::-webkit-scrollbar-track { background:transparent; }
  ::-webkit-scrollbar-thumb { background:#252b38; border-radius:5px; }
  ::-webkit-scrollbar-thumb:hover { background:#323a4b; }
  @media (max-width:900px){ body{flex-direction:column} #right{width:auto;flex:0 0 45%;
    border-left:0;border-top:1px solid var(--border)} }
</style></head><body>

<div id="left">
  <div class="topbar">
    <div class="logo">Rx</div>
    <div class="titles">
      <h1>AgentRx</h1>
      <p>Tell me which agent to diagnose — a specialist swarm does the rest.</p>
    </div>
    <div class="pill" id="conn">connecting</div>
  </div>
  <div id="log"></div>
  <div class="quick">
    <div class="chip" data-q="Which agents can you diagnose?">Which agents?</div>
    <div class="chip" data-q="Diagnose meridian">Diagnose Meridian</div>
    <div class="chip" data-q="Show me the last diagnosis report for meridian">Last report</div>
    <div class="chip" data-q="How does your diagnosis actually work?">How it works</div>
  </div>
  <form id="f">
    <input type="text" id="q" placeholder="Which agent should I diagnose?" autocomplete="off">
    <button id="send">Send</button>
  </form>
</div>

<div id="right">
  <div class="rhead"><h2>Specialist swarm</h2></div>

  <div class="roster">
    <div class="sp" data-match="prompt-logic"><div class="led"></div>
      <div class="nm">Prompt-Logic</div><div class="st">idle</div></div>
    <div class="sp" data-match="tool-spec"><div class="led"></div>
      <div class="nm">Tool-Spec</div><div class="st">idle</div></div>
    <div class="sp" data-match="context"><div class="led"></div>
      <div class="nm">Context</div><div class="st">idle</div></div>
    <div class="sp" data-match="attending"><div class="led"></div>
      <div class="nm">Attending <span style="color:var(--dim)">(coordinator)</span></div>
      <div class="st">idle</div></div>
  </div>

  <div class="metrics">
    <div class="m"><div class="k">Threads</div><div class="v" id="m-threads">0</div></div>
    <div class="m"><div class="k">Replies in</div><div class="v" id="m-replies">0</div></div>
    <div class="m"><div class="k">Events</div><div class="v" id="m-events">0</div></div>
    <div class="m"><div class="k" id="m-elapsed-label">Elapsed</div><div class="v" id="m-elapsed">—</div></div>
  </div>

  <div class="streamhead"><span id="streamhead">Live event stream</span><span id="m-stage">idle</span></div>
  <div id="swarm"><div class="empty">no activity yet — start a diagnosis</div></div>
  <div id="hint">spawned · delegate · reply = real Managed Agents threads</div>
</div>

<script>
const log=document.getElementById('log'), swarm=document.getElementById('swarm');
const f=document.getElementById('f'), q=document.getElementById('q'), send=document.getElementById('send');
const conn=document.getElementById('conn'), stage=document.getElementById('m-stage');
let nThreads=0, nReplies=0, nEvents=0, t0=null, timer=null, typingEl=null;

function esc(s){return s.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
function fmt(s){return esc(s).replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>')
                             .replace(/`([^`]+)`/g,'<code>$1</code>');}
function add(who,text,cls){
  const d=document.createElement('div'); d.className='msg '+(cls||'');
  d.innerHTML='<div class="who"><span class="dot"></span><span></span></div><div class="bubble"></div>';
  d.querySelector('.who span:last-child').textContent=who;
  d.querySelector('.bubble').innerHTML=fmt(text);
  log.appendChild(d); log.scrollTop=log.scrollHeight; return d;
}
function showTyping(){
  if(typingEl) return;
  typingEl=document.createElement('div'); typingEl.className='msg rx';
  typingEl.innerHTML='<div class="who"><span class="dot"></span><span>agentrx</span></div>'+
    '<div class="bubble"><span class="typing"><i></i><i></i><i></i></span></div>';
  log.appendChild(typingEl); log.scrollTop=log.scrollHeight;
}
function hideTyping(){ if(typingEl){typingEl.remove(); typingEl=null;} }

function setSp(match,state,label){
  document.querySelectorAll('.sp').forEach(sp=>{
    if(sp.dataset.match===match){ sp.classList.remove('running','done');
      if(state) sp.classList.add(state);
      sp.querySelector('.st').textContent=label||state||'idle'; }
  });
}
function matchOf(actor){
  const a=(actor||'').toLowerCase();
  if(a.includes('prompt')) return 'prompt-logic';
  if(a.includes('tool-spec')||a.includes('tool spec')) return 'tool-spec';
  if(a.includes('context')) return 'context';
  if(a.includes('attending')) return 'attending';
  return null;
}
function startClock(){
  if(timer) return; t0=Date.now();
  timer=setInterval(()=>{const s=Math.floor((Date.now()-t0)/1000);
    document.getElementById('m-elapsed').textContent=
      (s<60? s+'s' : Math.floor(s/60)+'m '+String(s%60).padStart(2,'0')+'s');},1000);
}
function stopClock(){ if(timer){clearInterval(timer); timer=null;} }

function setMode(actor){
  // Without a PERSISTENT badge the header still reads "Live event stream" and
  // the Elapsed tile counts the replay's wall-clock — a measured number a
  // viewer reads as the diagnosis duration, in the one panel whose job is
  // showing what really happened. The one-off "REPLAY" line scrolls away.
  const live = actor!=='replay';
  document.getElementById('streamhead').textContent =
    live ? 'Live event stream' : 'REPLAY — recorded run, no API calls';
  document.getElementById('m-elapsed-label').textContent = live ? 'Elapsed' : 'Replay time';
  document.getElementById('hint').textContent = live
    ? 'spawned · delegate · reply = real Managed Agents threads'
    : 'replayed from artifacts/full_run.json — nothing is running now';
}
function addEv(label,actor){
  if(label==='mode'){ setMode(actor); return; }
  if(swarm.querySelector('.empty')) swarm.innerHTML='';
  const d=document.createElement('div'); d.className='ev t-'+label;
  const ts=new Date().toLocaleTimeString([], {hour12:false, minute:'2-digit', second:'2-digit'});
  d.innerHTML='<div class="ts"></div><div class="tag"></div><div class="actor"></div>';
  d.querySelector('.ts').textContent=ts;
  d.querySelector('.tag').textContent=label;
  d.querySelector('.actor').textContent=actor;
  swarm.appendChild(d); swarm.scrollTop=swarm.scrollHeight;

  nEvents++; document.getElementById('m-events').textContent=nEvents;
  const m=matchOf(actor);
  if(label==='spawned'){ nThreads++; document.getElementById('m-threads').textContent=nThreads;
    if(m) setSp(m,'running','spawned'); startClock(); stage.textContent='diagnosing'; }
  else if(label==='running'){ if(m) setSp(m,'running','running'); startClock(); }
  else if(label==='reply'){ nReplies++; document.getElementById('m-replies').textContent=nReplies;
    if(m) setSp(m,'done','reported'); }
  else if(label==='idle'){ stage.textContent='complete'; stopClock();
    document.querySelectorAll('.sp.running').forEach(sp=>{sp.classList.remove('running');
      sp.classList.add('done'); sp.querySelector('.st').textContent='done';}); }
  else if(label==='tool'){ stage.textContent='working'; startClock(); }
}

const es=new EventSource('/events');
es.onopen=()=>{conn.textContent='connected'; conn.className='pill live';};
es.onerror=()=>{conn.textContent='reconnecting'; conn.className='pill off';};
es.onmessage=e=>{const m=JSON.parse(e.data);
  if(m.channel==='swarm'){ addEv(m.data.label,m.data.actor); }
  else if(m.channel==='chat'){ hideTyping(); send.disabled=false;
    conn.textContent='connected'; conn.className='pill live';
    add('agentrx',m.data.text,'rx'); }};

async function ask(text){
  add('you',text,'you'); send.disabled=true; showTyping();
  conn.textContent='thinking'; conn.className='pill busy';
  try{ await fetch('/ask',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({text})}); }
  catch(err){ hideTyping(); send.disabled=false; add('system','request failed: '+err,'sys'); }
}
f.onsubmit=ev=>{ev.preventDefault(); if(send.disabled) return; const t=q.value.trim(); if(!t) return; q.value=''; ask(t); q.focus();};
document.querySelectorAll('.chip').forEach(c=>c.onclick=()=>{ if(!send.disabled) ask(c.dataset.q); });

add('agentrx','Ready. Ask **which agents** I can diagnose, then pick one — '+
  'the sidebar shows the specialist swarm working in real time.','rx');
</script></body></html>
"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass  # keep the console clean for the demo

    def handle_one_request(self):
        # Browsers drop SSE/keepalive sockets routinely; the stdlib logs a full
        # traceback for each one, which would bury the demo output.
        try:
            super().handle_one_request()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            self.close_connection = True

    def do_GET(self):
        if self.path == "/":
            body = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            # Subscribe BEFORE reading the backlog, so an event published
            # between the two still arrives (on the queue) rather than falling
            # down the gap between them.
            q = events.subscribe()

            # A tab refresh, or a laptop sleeping mid-diagnosis, drops the
            # EventSource for ~3s. Anything published in that window used to be
            # gone for good - including the chat reply, which left the UI stuck
            # on typing dots forever. Replay from the client's Last-Event-ID.
            # A RECONNECT sends Last-Event-ID and wants the gap. A fresh page
            # load (F5) sends nothing - and treating that as "from zero" replayed
            # the whole ring buffer: every past reply re-rendered as a new bubble
            # with none of the user's own turns between them, and every swarm
            # event re-stamped `now`. A reload mid-demo produced a wall of
            # duplicated bot replies. With no header, start at the head.
            header = self.headers.get("Last-Event-ID")
            if header is None:
                last_id = events.last_id()
            else:
                try:
                    last_id = int(header)
                except ValueError:
                    last_id = 0
            backlog = events.recent(after_id=last_id)
            seen = set()

            def _write(item):
                payload = json.dumps(item, ensure_ascii=True)
                self.wfile.write(f"id: {item['id']}\ndata: {payload}\n\n".encode("utf-8"))

            try:
                for item in backlog:
                    seen.add(item["id"])
                    _write(item)
                if backlog:
                    self.wfile.flush()
                while True:
                    try:
                        item = q.get(timeout=15)
                        if item["id"] in seen:
                            continue  # already sent as backlog
                        # ensure_ascii keeps non-ASCII intact through the SSE
                        # transport instead of emitting raw bytes the browser
                        # may mis-decode.
                        _write(item)
                    except queue.Empty:
                        self.wfile.write(b": keepalive\n\n")  # keep the connection warm
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                events.unsubscribe(q)
            return
        self.send_error(404)

    def do_POST(self):
        if self.path != "/ask":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        text = (payload.get("text") or "").strip()

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

        # Run the (slow, tool-using) turn off the request thread so the swarm
        # events stream to the sidebar while it works.
        threading.Thread(target=self._handle_turn, args=(text,), daemon=True).start()

    def _handle_turn(self, text):
        import chat_backend
        try:
            reply = chat_backend.ask(text)
            events.publish("chat", {"text": reply})
        except chat_backend.Busy as e:
            events.publish("chat", {"text": f"(still working — {e}. "
                                            f"Your message wasn't sent; try again once it finishes.)"})
        except (Exception, SystemExit) as e:
            # SystemExit is a BaseException. Uncaught here it killed the worker
            # thread silently - threading swallows it - so no chat event was
            # ever published and the browser sat on typing dots forever with a
            # dead send button. Report the message, not a formatted traceback:
            # tracebacks render this machine's absolute paths onto a projector.
            events.publish("chat", {"text": f"error: {type(e).__name__}: {e}"})
        finally:
            # Every front-desk tool publishes a `tool` event, and `tool` starts
            # the sidebar clock - but only `idle` stopped it, and only a replay
            # ever emitted one. So the FIRST thing typed on stage ("which agents
            # can you diagnose?") left the sidebar reading `working` with a
            # live-counting Elapsed timer and nothing running; after an error it
            # contradicted the chat pane outright. The turn ending is the fact
            # the sidebar needs, and it is true on all three exit paths.
            events.publish("swarm", {"label": "idle", "actor": "session"})


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"AgentRx console -> http://localhost:{PORT}")
    print("  left pane: chat.  right pane: live swarm events.  Ctrl+C to stop.")
    server.serve_forever()


if __name__ == "__main__":
    main()
