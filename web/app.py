"""Flask admin UI for the transit display.

Two pages:
  * "/"         admin — control THIS machine: feeds (CTA/MTA/NJT), display mode
                (sections vs stacked), agency theme, weather ZIP, plus a live
                preview of the panel output.
  * "/display"  the tracker itself — fullscreen, auto-refreshing. This is what
                the e-ink panel shows; open it on any screen to QA the layout.

Helpers for QA:
  * /preview.png?demo=cta|mta|njt|stacked  render sample data (no API keys)
  * /preview.png?dither=1                  simulate the real 6-color panel
"""

from __future__ import annotations

import os
import sys
import time
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from flask import Flask, redirect, render_template_string, request, send_file, url_for

import netsetup
from appconfig import list_devices, load_config, load_config_for, save_config
from fetcher import FETCHERS, fetch_all
from geocode import geocode
from renderer import Display, render_image, simulate_eink
from renderer.demo import sample_results, sample_stacked
from renderer.display import ASSETS_DIR, PREVIEW_PATH, _is_gameday
from renderer.themes import resolve_theme
from weather import get_weather

app = Flask(__name__)
_display = Display(load_config())

# ----------------------------------------------------------------- templates -

ADMIN = """
<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Transit Display · Admin</title>
<style>
  :root{ --bg:#f6f7f9; --card:#ffffff; --line:#e4e7ec; --fg:#1f2733;
         --muted:#6b7280; --accent:#1d80d6; --ok:#0e8a3e; }
  *{box-sizing:border-box}
  body{margin:0;font:15px/1.55 system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--fg)}
  .wrap{max-width:1040px;margin:0 auto;padding:26px 18px 70px}
  h1{font-size:22px;margin:0 0 2px}
  .sub{color:var(--muted);margin:0 0 22px}
  .sub b{color:var(--fg)}
  .grid{display:grid;grid-template-columns:1.25fr 1fr;gap:22px;align-items:start}
  @media(max-width:860px){.grid{grid-template-columns:1fr}}
  .card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px;
        box-shadow:0 1px 2px rgba(16,24,40,.04)}
  .card h2{font-size:14px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);margin:0 0 14px}
  .preview img{width:100%;border-radius:10px;border:1px solid var(--line);display:block;background:#fff}
  .pbar{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;gap:8px;flex-wrap:wrap}
  .pbar .links a{font-size:13px;margin-right:12px}
  .feed{border:1px solid var(--line);border-radius:12px;padding:14px;margin-bottom:14px;background:#fcfcfd}
  .feed.off{opacity:.62}
  .preview.off{opacity:.4}
  .preview.off img{filter:grayscale(.7)}
  .row{display:flex;gap:12px;flex-wrap:wrap}
  .row>label{flex:1 1 150px;font-size:12px;color:var(--muted);font-weight:600}
  input,select{width:100%;margin-top:5px;padding:9px 11px;border-radius:9px;border:1px solid var(--line);
        background:#fff;color:var(--fg);font-size:14px}
  input[type=checkbox]{width:auto;margin:0;transform:scale(1.15)}
  .toggle{display:flex;align-items:center;gap:8px;font-size:14px;font-weight:600}
  .feedhead{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}
  .pill{font-size:12px;color:var(--muted);border:1px solid var(--line);padding:2px 9px;border-radius:999px}
  button{cursor:pointer;border:none;border-radius:9px;padding:10px 16px;font-size:14px;font-weight:600}
  .primary{background:var(--accent);color:#fff}
  .ghost{background:#fff;color:var(--fg);border:1px solid var(--line)}
  .actions{display:flex;gap:10px;margin-top:16px;align-items:center}
  .small{font-size:12px;color:var(--muted)}
  a{color:var(--accent);text-decoration:none}
  a:hover{text-decoration:underline}
  hr{border:none;border-top:1px solid var(--line);margin:16px 0}
  .finder{margin-top:10px;padding:10px;border:1px dashed var(--line);border-radius:9px;background:#fff}
  .stopbtn{display:block;width:100%;text-align:left;background:#fff;border:1px solid var(--line);
           border-radius:8px;padding:7px 10px;margin:5px 0;cursor:pointer;font-size:13px;color:var(--fg)}
  .stopbtn:hover{border-color:var(--accent);background:#f5faff}
</style></head><body>
<div class="wrap">
  <h1>🚆 Transit Display · Admin</h1>
  <p class="sub">Mode: <b>{{ mode }}</b> ·
     <a href="{{ url_for('display_index') }}" target="_blank">open displays ↗</a></p>

  <div class="grid">
    <div>
      <div class="pbar">
        <b>Live preview{{ '' if merged else 's' }} — this device's board</b>
      </div>
      {% if merged %}
      <div class="card preview" style="margin-bottom:16px">
        <div class="pbar">
          <b>{{ board_label }}</b>
          <span class="links">
            <span class="pill">{{ feeds|length }} feeds · merged</span>
            <a href="{{ url_for('display_feed', idx=0) }}?merge=1" target="_blank">open ↗</a>
            <a href="{{ url_for('preview') }}?merge=1&dither=1&t={{ cb }}" target="_blank">6-color</a>
          </span>
        </div>
        <img src="{{ url_for('preview') }}?merge=1&t={{ cb }}" alt="{{ board_label }} board" loading="lazy">
      </div>
      <p class="small">This device has several feeds (e.g. PATH + NJT) — they merge
         into one board, soonest-first. Edit each feed's stops in the cards on the right.</p>
      {% else %}
      {% for feed in feeds %}
      <div class="card preview {{ '' if feed.enabled else 'off' }}" id="preview-{{ loop.index0 }}" style="margin-bottom:16px">
        <div class="pbar">
          <b>{{ feed.label or feed.type.upper() }}</b>
          <span class="links">
            <span class="pill" id="pvpill-{{ loop.index0 }}" data-type="{{ feed.type.upper() }}">{{ feed.type.upper() }}{{ ' · off' if not feed.enabled }}</span>
            <a href="{{ url_for('display_feed', idx=loop.index0) }}" target="_blank">open ↗</a>
            <a href="{{ url_for('preview') }}?feed={{ loop.index0 }}&dither=1&t={{ cb }}" target="_blank">6-color</a>
          </span>
        </div>
        <img src="{{ url_for('preview') }}?feed={{ loop.index0 }}&t={{ cb }}"
             alt="{{ feed.label }} preview" loading="lazy">
      </div>
      {% endfor %}
      <p class="small">Each board is what that person's 800×480 panel shows — its own
         colors, time zone, and weather. "6-color panel" simulates dithered output.</p>
      {% endif %}
    </div>

    <form method="post" action="{{ url_for('save') }}" class="card">
      <h2>Secrets (optional)</h2>
      <label>Azure Key Vault URL
        <input type="text" name="key_vault_url" value="{{ config.key_vault_url }}"
               placeholder="https://my-vault.vault.azure.net/">
      </label>
      <p class="small" style="margin:6px 0 0">Leave blank to keep keys in <code>config.json</code>.
        If set, a <code>${name}</code> key (e.g. <code>${cta_key}</code>) is pulled from the Key Vault
        secret <code>name</code> (with hyphens: <code>cta-key</code>) — so no key ever touches git.</p>
      <hr>

      <h2>Schedule (optional)</h2>
      <label>Off hours — turn the screen off during this window (board local time)
        <input type="text" name="off_hours" value="{{ config.off_hours }}" placeholder="blank = always on, e.g. 23:00-07:00">
      </label>
      <hr>

      <h2>Displays</h2>
      <p class="small" style="margin:-4px 0 14px">Each card is one independent display. Everything is set
        per-display: its name, transit system (which sets the colors &amp; time zone), stops, weather, and refresh.
        Each gets its own screen at <code>/display/N</code>.</p>
      {% for feed in feeds %}
      <div class="feed {{ '' if feed.enabled else 'off' }}" id="feedcard-{{ loop.index0 }}">
        <div class="feedhead">
          <label class="toggle"><input type="checkbox" name="feed-{{ loop.index0 }}-enabled" {{ 'checked' if feed.enabled }} onchange="toggleEnabled({{ loop.index0 }}, this)"> Enabled</label>
          <span class="pill" id="pill-{{ loop.index0 }}" data-type="{{ feed.type.upper() or '#'+loop.index|string }}">{{ feed.type.upper() or '#'+loop.index|string }}{{ ' · off' if not feed.enabled }}</span>
        </div>
        <div class="row">
          <label>Display name<input type="text" name="feed-{{ loop.index0 }}-label" value="{{ feed.label }}" placeholder="e.g. Advait's West Loop"></label>
          <label>Transit system
            <select name="feed-{{ loop.index0 }}-type">
              {% for t in feed_types %}<option value="{{ t }}" {{ 'selected' if feed.type==t }}>{{ t.upper() }}</option>{% endfor %}
            </select>
          </label>
        </div>
        <div class="row" style="margin-top:10px">
          <label>Stop ID(s) — comma-separated for multiple
            <input type="text" name="feed-{{ loop.index0 }}-stop_id" value="{{ feed.stop_id }}"
                   placeholder="e.g. 40380, 41700"></label>
          <label>API key<input type="text" name="feed-{{ loop.index0 }}-api_key" value="{{ feed.api_key }}"></label>
        </div>
        <div class="finder">
          <div class="row" style="gap:8px">
            <input type="text" id="find-{{ loop.index0 }}" placeholder="ZIP or address → find nearest stop">
            <select id="findmode-{{ loop.index0 }}" style="flex:0 0 auto;width:auto">
              <option value="">All</option>
              <option value="train">🚆 Train</option>
              <option value="bus">🚌 Bus</option>
            </select>
            <button type="button" class="ghost" style="flex:0 0 auto"
                    onclick="findStops({{ loop.index0 }})">Find stop</button>
          </div>
          <div id="finds-{{ loop.index0 }}"></div>
        </div>
        <div class="row" style="margin-top:10px">
          <label>Weather ZIP (blank = no weather)
            <input type="text" name="feed-{{ loop.index0 }}-zip" value="{{ feed.zip }}" placeholder="e.g. 60601">
          </label>
          <label>Refresh seconds
            <input type="number" name="feed-{{ loop.index0 }}-refresh" min="15" value="{{ feed.refresh_seconds }}">
          </label>
        </div>
        <label class="toggle" style="margin-top:10px;color:var(--muted);font-weight:500">
          <input type="checkbox" name="feed-{{ loop.index0 }}-delete"> Delete this feed
        </label>
      </div>
      {% endfor %}

      <div class="feed">
        <div class="feedhead"><b>+ Add a feed</b></div>
        <div class="row">
          <label>Label<input type="text" name="feed-new-label" placeholder="blank = skip"></label>
          <label>Type<select name="feed-new-type">{% for t in feed_types %}<option value="{{ t }}">{{ t.upper() }}</option>{% endfor %}</select></label>
        </div>
        <div class="row" style="margin-top:10px">
          <label>Stop ID<input type="text" name="feed-new-stop_id"></label>
          <label>API key<input type="text" name="feed-new-api_key"></label>
        </div>
        <label class="toggle" style="margin-top:10px"><input type="checkbox" name="feed-new-enabled" checked> Enabled</label>
      </div>

      <div class="actions">
        <button class="primary" type="submit">Save</button>
        <a class="small" href="{{ url_for('index') }}">Discard changes</a>
      </div>
    </form>
  </div>
</div>
<script>
function toggleEnabled(i, cb){
  var on=cb.checked;
  var card=document.getElementById('feedcard-'+i);
  if(card){card.classList.toggle('off', !on);}
  var pv=document.getElementById('preview-'+i);
  if(pv){pv.classList.toggle('off', !on);}
  ['pill-'+i,'pvpill-'+i].forEach(function(id){
    var p=document.getElementById(id);
    if(p){p.textContent=p.dataset.type+(on?'':' · off');}
  });
}
async function findStops(idx){
  var type=document.querySelector('[name=feed-'+idx+'-type]').value;
  var q=document.getElementById('find-'+idx).value;
  var mode=document.getElementById('findmode-'+idx).value;
  var box=document.getElementById('finds-'+idx);
  box.innerHTML='<span class="small">Searching…</span>';
  try{
    var r=await fetch('/find_stops?type='+encodeURIComponent(type)+'&q='+encodeURIComponent(q)+'&mode='+encodeURIComponent(mode));
    var d=await r.json();
    if(d.error){box.innerHTML='<span class="small">'+d.error+'</span>';return;}
    if(!d.stops||!d.stops.length){box.innerHTML='<span class="small">No stops found near that location.</span>';return;}
    box.innerHTML='<div class="small" style="margin:8px 0 2px">Near '+d.label+' — tap to add (multiple OK):</div>';
    d.stops.forEach(function(s){
      var b=document.createElement('button');
      b.type='button';b.className='stopbtn';
      var icon=(s.mode==='bus')?'🚌 ':'🚆 ';
      b.innerHTML=icon+'<b>'+s.name+'</b> &nbsp;<span class="small">'+s.detail+' · id '+s.id+'</span>';
      b.onclick=function(){
        var inp=document.querySelector('[name=feed-'+idx+'-stop_id]');
        var ids=inp.value.split(',').map(function(x){return x.trim();}).filter(Boolean);
        if(ids.indexOf(s.id)===-1){ids.push(s.id);}
        inp.value=ids.join(', ');
        var lbl=document.querySelector('[name=feed-'+idx+'-label]');
        if(!lbl.value){lbl.value=s.name;}
        var note=document.getElementById('note-'+idx);
        note.innerHTML='✓ Stops: <b>'+inp.value+'</b> — click Save to apply.';
      };
      box.appendChild(b);
    });
    if(!document.getElementById('note-'+idx)){
      var n=document.createElement('div');n.id='note-'+idx;n.className='small';n.style.marginTop='6px';box.appendChild(n);
    }
  }catch(e){box.innerHTML='<span class="small">Search failed — try again.</span>';}
}
</script>
</body></html>
"""

DISPLAY = """
<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ title }}</title>
<style>
  html,body{margin:0;height:100%;background:#111;display:flex;align-items:center;justify-content:center;overflow:hidden}
  .stage{position:relative;width:min(100vw,166.667vh);aspect-ratio:800/480;container-type:size}
  .board{width:100%;height:100%;object-fit:contain;display:block}
  /* game-day overlay: the GIF box is centered on the header's vertical mid (so it
     lines up with the weather + logo); GAME DAY hangs below, centered under the
     "GO PACK GO" words (which sit right-of-box-center because of the # hashtag). */
  .gd{position:absolute;left:48.8%;top:12%;transform:translate(-50%,-50%);pointer-events:none}
  .gd img{width:21cqw;display:block}
  .gd .label{position:absolute;left:61.4%;top:100%;transform:translateX(-50%);margin-top:.5cqh;
      white-space:nowrap;color:#FFB612;font:800 2.3cqh/1 system-ui,sans-serif;letter-spacing:.14em}
</style></head><body>
<div class="stage">
  <img id="d" class="board" src="{{ img_url }}&t={{ cb }}">
  {% if gameday and gif_url %}
  <div class="gd">
    <img src="{{ gif_url }}" alt="GO PACK GO">
    <div class="label">GAME DAY</div>
  </div>
  {% endif %}
</div>
<script>
  // refresh the board image periodically so this mirrors the panel
  setInterval(function(){
    document.getElementById('d').src='{{ img_url }}&t='+Date.now();
  }, {{ refresh_ms }});
</script>
</body></html>
"""

DISPLAY_INDEX = """
<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Displays</title>
<style>
  body{margin:0;font:15px/1.5 system-ui,sans-serif;background:#11151c;color:#e8edf5;padding:24px}
  h1{font-size:20px;margin:0 0 4px}
  .sub{color:#8b97ab;margin:0 0 20px}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:18px}
  a.card{display:block;background:#1b2230;border:1px solid #2c3547;border-radius:12px;
         padding:12px;text-decoration:none;color:inherit}
  a.card:hover{border-color:#3b82f6}
  a.card img{width:100%;border-radius:8px;border:1px solid #2c3547;display:block;margin-top:8px}
  .row{display:flex;justify-content:space-between;align-items:center}
  .pill{font-size:12px;color:#8b97ab;border:1px solid #2c3547;padding:2px 8px;border-radius:999px}
  code{background:#0e131b;padding:1px 6px;border-radius:5px;color:#9fb4cf}
</style></head><body>
  <h1>🚆 Displays</h1>
  <p class="sub">One board per feed — open each on its own screen. Each has a unique URL.</p>
  <div class="grid">
    {% for feed in feeds %}
    <a class="card" href="{{ url_for('display_feed', idx=loop.index0) }}" target="_blank">
      <div class="row">
        <b>{{ feed.label or feed.type.upper() }}</b>
        <span class="pill">{{ feed.type.upper() }}{{ ' · off' if not feed.enabled }}</span>
      </div>
      <div class="sub" style="margin:4px 0 0"><code>/display/{{ loop.index0 }}</code></div>
      <img src="{{ url_for('preview') }}?feed={{ loop.index0 }}&t={{ cb }}" loading="lazy">
    </a>
    {% endfor %}
  </div>
</body></html>
"""

# Touchscreen WiFi onboarding. Shown by the kiosk INSTEAD of the board when the
# device boots with no internet (e.g. moved to a new home). Returned as a raw
# string (not via Jinja) so the inline JS/CSS braces are left untouched.
WIFI = """
<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<title>Connect to WiFi</title>
<style>
  :root{--bg:#f6f7f9;--card:#fff;--line:#e4e7ec;--fg:#1f2733;--muted:#6b7280;--accent:#1d80d6;--ok:#0e8a3e;--err:#c23b3b}
  *{box-sizing:border-box;-webkit-tap-highlight-color:transparent;user-select:none}
  html,body{margin:0;height:100%;background:var(--bg);color:var(--fg);
    font:16px/1.4 system-ui,-apple-system,sans-serif;overflow:hidden}
  .wrap{height:100%;display:flex;flex-direction:column;max-width:780px;margin:0 auto;padding:16px}
  h1{font-size:22px;margin:0}
  .sub{color:var(--muted);margin:2px 0 12px;font-size:14px}
  .top{display:flex;justify-content:space-between;align-items:flex-end}
  .btn{cursor:pointer;border:1px solid var(--line);background:#fff;color:var(--fg);
    border-radius:10px;padding:10px 14px;font-size:15px;font-weight:600}
  .btn.primary{background:var(--accent);color:#fff;border-color:var(--accent)}
  .btn:active{transform:translateY(1px)}
  #list{flex:1;overflow-y:auto;border:1px solid var(--line);border-radius:14px;background:#fff}
  .net{display:flex;align-items:center;gap:12px;padding:15px 16px;border-bottom:1px solid var(--line);cursor:pointer}
  .net:last-child{border-bottom:none}
  .net:active{background:#f2f7fd}
  .net .name{flex:1;font-weight:600}
  .net .lock{color:var(--muted)}
  .bars{display:inline-flex;align-items:flex-end;gap:2px;height:18px}
  .bars i{width:4px;background:#cfd5de;border-radius:1px}
  .bars i.on{background:var(--accent)}
  .empty{padding:22px;text-align:center;color:var(--muted)}
  #pw{display:none;flex:1;flex-direction:column}
  .pwhead{display:flex;align-items:center;gap:10px;margin-bottom:8px}
  .field input{width:100%;padding:14px;border:1px solid var(--line);border-radius:10px;font-size:18px}
  .status{min-height:22px;font-size:14px;margin:8px 2px}
  .status.err{color:var(--err)}.status.ok{color:var(--ok)}
  .kb{margin-top:auto}
  .krow{display:flex;gap:6px;justify-content:center;margin:6px 0}
  .key{flex:1;max-width:64px;text-align:center;padding:14px 0;background:#fff;border:1px solid var(--line);
    border-radius:9px;font-size:18px;font-weight:600;cursor:pointer}
  .key:active{background:#eef3f9}
  .key.wide{max-width:150px}.key.ctrl{background:#eef1f5}
</style></head><body>
<div class="wrap">
  <div class="top">
    <div><h1>Connect to WiFi</h1><div class="sub">Pick your network to start the board.</div></div>
    <button class="btn" id="rescan" onclick="loadNets(true)">Rescan</button>
  </div>
  <div id="list"><div class="empty">Scanning…</div></div>
  <div id="pw">
    <div class="pwhead"><button class="btn" onclick="showList()">‹ Back</button><b id="pwssid"></b></div>
    <div class="field"><input id="pwinput" type="text" placeholder="Password" autocomplete="off" readonly></div>
    <div class="status" id="status"></div>
    <div class="kb" id="kb"></div>
    <button class="btn primary" style="margin-top:8px;padding:15px" onclick="doConnect()">Connect</button>
  </div>
</div>
<script>
var selected=null, shift=false, sym=false, pw="";
var L1=["q","w","e","r","t","y","u","i","o","p"],L2=["a","s","d","f","g","h","j","k","l"],L3=["z","x","c","v","b","n","m"];
var S1=["1","2","3","4","5","6","7","8","9","0"],S2=["!","@","#","$","%","&","*","-","_"],S3=["+","=","/","?",".",",",":",";"];
function bars(sig){var n=sig>=75?4:sig>=50?3:sig>=25?2:1,h=[8,11,14,18],s="";for(var i=0;i<4;i++){s+='<i class="'+(i<n?'on':'')+'" style="height:'+h[i]+'px"></i>';}return '<span class="bars">'+s+'</span>';}
async function loadNets(rescan){
  var box=document.getElementById('list');
  box.innerHTML='<div class="empty">'+(rescan?'Rescanning…':'Scanning…')+'</div>';
  try{
    var r=await fetch('/wifi/scan'+(rescan?'?rescan=1':''));var d=await r.json();
    if(d.online){location.href='/display/0';return;}
    if(!d.supported){box.innerHTML='<div class="empty">WiFi setup runs on the device itself.</div>';return;}
    if(!d.networks||!d.networks.length){box.innerHTML='<div class="empty">No networks found. Tap Rescan.</div>';return;}
    box.innerHTML='';
    d.networks.forEach(function(n){
      var el=document.createElement('div');el.className='net';
      el.innerHTML='<span class="name"></span>'+(n.secure?'<span class="lock">&#128274;</span>':'')+bars(n.signal);
      el.querySelector('.name').textContent=n.ssid;el.onclick=function(){pick(n);};box.appendChild(el);
    });
  }catch(e){box.innerHTML='<div class="empty">Scan failed. Tap Rescan.</div>';}
}
function pick(n){
  selected=n;pw="";shift=false;sym=false;
  document.getElementById('pwssid').textContent=n.ssid;document.getElementById('pwinput').value="";setStatus("","");
  document.getElementById('list').style.display='none';document.getElementById('pw').style.display='flex';
  document.getElementById('rescan').style.visibility='hidden';drawKb();
}
function showList(){
  document.getElementById('pw').style.display='none';document.getElementById('list').style.display='block';
  document.getElementById('rescan').style.visibility='visible';
}
function setStatus(msg,kind){var s=document.getElementById('status');s.textContent=msg;s.className='status '+(kind||'');}
function tapKey(k){
  if(k==='shift'){shift=!shift;drawKb();return;}
  if(k==='sym'){sym=!sym;shift=false;drawKb();return;}
  if(k==='back'){pw=pw.slice(0,-1);}else if(k==='space'){pw+=' ';}
  else{pw+=(shift?k.toUpperCase():k);if(shift){shift=false;drawKb();}}
  document.getElementById('pwinput').value=pw;
}
function mkRow(keys){
  var row=document.createElement('div');row.className='krow';
  keys.forEach(function(k){var b=document.createElement('div');b.className='key';b.textContent=shift?k.toUpperCase():k;b.onclick=function(){tapKey(k);};row.appendChild(b);});
  return row;
}
function ctrlKey(label,k,wide){var b=document.createElement('div');b.className='key ctrl'+(wide?' wide':'');b.textContent=label;b.onclick=function(){tapKey(k);};return b;}
function drawKb(){
  var kb=document.getElementById('kb');kb.innerHTML='';
  kb.appendChild(mkRow(sym?S1:L1));kb.appendChild(mkRow(sym?S2:L2));
  var row3=mkRow(sym?S3:L3);row3.insertBefore(ctrlKey(shift?'\\u21E7':'\\u21EA','shift'),row3.firstChild);
  row3.appendChild(ctrlKey('\\u232B','back'));kb.appendChild(row3);
  var row4=document.createElement('div');row4.className='krow';
  row4.appendChild(ctrlKey(sym?'abc':'?123','sym'));row4.appendChild(ctrlKey('space','space',true));kb.appendChild(row4);
}
async function doConnect(){
  if(!selected){return;}
  setStatus('Connecting to '+selected.ssid+'…','');
  try{
    var r=await fetch('/wifi/connect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ssid:selected.ssid,password:pw})});
    var d=await r.json();
    if(d.ok){setStatus('Connected! Starting board…','ok');waitOnline();}
    else{setStatus(d.message||'Could not connect.','err');}
  }catch(e){setStatus('Could not connect. Try again.','err');}
}
async function waitOnline(){
  for(var i=0;i<20;i++){
    try{var r=await fetch('/wifi/online');var t=await r.text();if(t.trim()==='yes'){location.href='/display/0';return;}}catch(e){}
    await new Promise(function(res){setTimeout(res,1500);});
  }
  location.href='/display/0';
}
loadNets(false);
</script>
</body></html>
"""

# ------------------------------------------------------------------- routes --

def _feeds_for_form(config):
    return [{
        "enabled": bool(f.get("enabled")),
        "label": f.get("label", ""),
        "type": str(f.get("type", "")).lower(),
        "stop_id": f.get("stop_id", ""),
        "api_key": f.get("api_key", ""),
        "zip": f.get("zip", ""),
        "refresh_seconds": f.get("refresh_seconds", 60),
    } for f in config.get("feeds", [])]


@app.route("/")
def index():
    config = load_config()
    feeds = _feeds_for_form(config)
    board_label = next((f["label"] for f in feeds if f.get("label")), "") or "This board"
    return render_template_string(
        ADMIN, config=config, feeds=feeds,
        feed_types=sorted(FETCHERS),
        mode="Raspberry Pi (Inky)" if _display.on_pi else "dev (preview.png)",
        cb=int(time.time()),
        merged=len(feeds) > 1, board_label=board_label,
    )


@app.route("/display")
def display_index():
    config = load_config()
    return render_template_string(
        DISPLAY_INDEX, feeds=_feeds_for_form(config), cb=int(time.time()),
    )


@app.route("/display/<int:idx>")
def display_feed(idx):
    device = request.args.get("device")
    merge = request.args.get("merge")
    config = load_config_for(device) if device else load_config()
    feeds = config.get("feeds", [])
    dev_q = f"&device={device}" if device else ""
    if merge or len(feeds) > 1:  # a device with several feeds -> one merged board
        label = next((f.get("label") for f in feeds if f.get("label")), "Transit Display")
        img_url = url_for("preview") + "?merge=1" + dev_q
        refresh = min([_feed_refresh(f) for f in feeds], default=60)
    else:
        feed = feeds[idx] if 0 <= idx < len(feeds) else {}
        label = feed.get("label") or "Transit Display"
        img_url = url_for("preview") + f"?feed={idx}" + dev_q
        refresh = _feed_refresh(feed)

    # game day: overlay the live animated graphic on top of the board (browser only)
    theme = resolve_theme(config, [])
    gameday = bool(theme.gameday_team and theme.gameday_img
                   and _is_gameday(theme.gameday_team, theme.tz))
    override = request.args.get("gameday")   # ?gameday=1/0 to force on/off for previewing
    if override == "1":
        gameday = bool(theme.gameday_img)
    elif override == "0":
        gameday = False
    gif_url = (url_for("gameday_gif") + (f"?device={device}" if device else "")) if gameday else ""

    return render_template_string(
        DISPLAY, title=label, img_url=img_url,
        cb=int(time.time()), refresh_ms=refresh * 1000,
        gameday=gameday, gif_url=gif_url,
    )


_GD_GIF_CACHE: dict = {}


def _trim_gif(name: str) -> bytes:
    """Crop an animated GIF to its content (drops big transparent margins) so it can
    be sized nicely as an overlay. Returns animated GIF bytes, or b'' on failure."""
    from io import BytesIO
    from PIL import Image, ImageSequence
    try:
        im = Image.open(ASSETS_DIR / name)
        union = None
        for fr in ImageSequence.Iterator(im):
            bb = fr.convert("RGBA").getbbox()
            if bb:
                union = bb if union is None else (
                    min(union[0], bb[0]), min(union[1], bb[1]),
                    max(union[2], bb[2]), max(union[3], bb[3]))
        if not union:
            return b""
        frames, durations = [], []
        for fr in ImageSequence.Iterator(im):
            rgba = fr.convert("RGBA").crop(union)
            durations.append(fr.info.get("duration", im.info.get("duration", 300)))
            alpha = rgba.split()[3]
            p = rgba.convert("RGB").convert("P", palette=Image.ADAPTIVE, colors=255)
            p.paste(255, alpha.point(lambda v: 255 if v < 128 else 0))  # index 255 = clear
            p.info["transparency"] = 255
            frames.append(p)
        buf = BytesIO()
        frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:],
                       duration=durations, loop=0, transparency=255, disposal=2)
        return buf.getvalue()
    except Exception as exc:  # noqa: BLE001
        print(f"[gameday] gif trim failed: {exc}")
        return b""


@app.route("/gameday.gif")
def gameday_gif():
    device = request.args.get("device") or ""
    if device not in _GD_GIF_CACHE:
        config = load_config_for(device) if device else load_config()
        theme = resolve_theme(config, [])
        _GD_GIF_CACHE[device] = _trim_gif(theme.gameday_img) if theme.gameday_img else b""
    data = _GD_GIF_CACHE[device]
    if not data:
        return ("", 404)
    return app.response_class(data, mimetype="image/gif")


DEVICES_DASH = """
<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>All Setups</title>
<style>
  body{margin:0;font:15px/1.5 system-ui,sans-serif;background:#11151c;color:#e8edf5;padding:24px}
  h1{font-size:21px;margin:0 0 4px} .sub{color:#8b97ab;margin:0 0 20px}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(400px,1fr));gap:20px}
  .card{background:#1b2230;border:1px solid #2c3547;border-radius:12px;padding:14px}
  .card h2{font-size:17px;margin:0 0 2px} .card .id{color:#8b97ab;font-size:13px;margin:0 0 10px}
  .card img{width:100%;border-radius:8px;border:1px solid #2c3547;display:block;background:#fff}
  .links{margin-top:10px;display:flex;gap:16px}
  a{color:#5aa9f8;text-decoration:none} a:hover{text-decoration:underline}
  code{background:#0e131b;padding:1px 6px;border-radius:5px;color:#9fb4cf}
</style></head><body>
  <h1>All setups</h1>
  <p class="sub">Every device config in <code>devices/</code>, live preview. Dev-machine view.</p>
  <div class="grid">
    {% for d in devices %}
    <div class="card">
      <h2>{{ d.label }}</h2>
      <p class="id"><code>devices/{{ d.id }}.json</code> · {{ d.feed_count }} board(s)</p>
      <img src="{{ url_for('preview') }}?device={{ d.id }}&{{ 'merge=1' if d.merge else 'feed=0' }}&t={{ cb }}" loading="lazy" alt="{{ d.label }}">
      <div class="links"><a href="{{ url_for('display_feed', idx=0) }}?device={{ d.id }}{{ '&merge=1' if d.merge else '' }}" target="_blank">open board ↗</a></div>
    </div>
    {% endfor %}
  </div>
</body></html>
"""


@app.route("/devices")
def devices_dashboard():
    devs = []
    for did in list_devices():
        if did == "example":
            continue
        feeds = load_config_for(did).get("feeds", [])
        devs.append({"id": did, "feed_count": len(feeds), "merge": len(feeds) > 1,
                     "label": (feeds[0].get("label") if feeds else "") or did})
    return render_template_string(DEVICES_DASH, devices=devs, cb=int(time.time()))


def _feed_refresh(feed: dict) -> int:
    try:
        return max(15, int(feed.get("refresh_seconds", 60)))
    except (ValueError, TypeError):
        return 60


@app.route("/save", methods=["POST"])
def save():
    form = request.form
    config = load_config()
    config["key_vault_url"] = form.get("key_vault_url", "").strip()
    config["off_hours"] = form.get("off_hours", "").strip()

    # per-feed settings the admin UI doesn't expose (carried over on save so an
    # edit here doesn't wipe them). form index i maps to old feed i.
    old_feeds = config.get("feeds", [])
    _PRESERVE = ("bus_direction",)

    feeds = []
    i = 0
    while f"feed-{i}-type" in form:
        if not form.get(f"feed-{i}-delete"):
            nf = _feed_from_form(form, str(i))
            if i < len(old_feeds):
                for k in _PRESERVE:
                    if old_feeds[i].get(k) is not None:
                        nf[k] = old_feeds[i][k]
            feeds.append(nf)
        i += 1
    new = _feed_from_form(form, "new")
    if new["type"] and (new["stop_id"] or new["label"]):
        feeds.append(new)
    config["feeds"] = feeds

    # the background loop ticks at the fastest display's cadence
    config["refresh_seconds"] = min([_feed_refresh(f) for f in feeds], default=60)
    # drop legacy global settings (now per-display)
    for stale in ("title", "display", "weather", "mode", "theme"):
        config.pop(stale, None)

    save_config(config)
    return redirect(url_for("index"))


def _feed_from_form(form, key):
    feed = {
        "type": str(form.get(f"feed-{key}-type", "")).lower().strip(),
        "enabled": bool(form.get(f"feed-{key}-enabled")),
        "label": form.get(f"feed-{key}-label", "").strip(),
        "stop_id": form.get(f"feed-{key}-stop_id", "").strip(),
        "api_key": form.get(f"feed-{key}-api_key", "").strip(),
        "zip": form.get(f"feed-{key}-zip", "").strip(),
        "refresh_seconds": _feed_refresh({"refresh_seconds": form.get(f"feed-{key}-refresh", 60)}),
    }
    # CTA boards may include bus stops, which need the Bus Tracker key. The admin
    # doesn't expose it, so always carry the vault reference for CTA feeds (the
    # fetcher only uses it when a bus stop is actually present). Without this, the
    # admin would silently drop bus_key on save and bus stops would stop working.
    if feed["type"] == "cta":
        feed["bus_key"] = "${cta_bus_key}"
    return feed


@app.route("/refresh", methods=["POST"])
def refresh():
    config = load_config()
    _display.show(fetch_all(config), config)
    return redirect(url_for("index"))


@app.route("/find_stops")
def find_stops():
    ftype = request.args.get("type", "").lower().strip()
    q = request.args.get("q", "").strip()
    cls = FETCHERS.get(ftype)
    if cls is None:
        return {"error": f"Unknown feed type '{ftype}'."}
    if not getattr(cls, "supports_stop_search", False):
        return {"error": f"Stop search isn't available for {ftype.upper()} yet — "
                          f"enter the stop id manually."}
    if not q:
        return {"error": "Enter a ZIP code or address."}
    mode = request.args.get("mode", "").lower().strip()
    if mode not in ("", "bus", "train"):
        mode = ""
    key = _resolved_key_for(ftype)

    try:
        if getattr(cls, "stop_search_by_name", False):
            # name/text search (e.g. NJT) — no geocoding
            label = f'"{q}"'
            stops = cls.find_stops(0.0, 0.0, api_key=key, mode=mode, query=q)
        else:
            located = geocode(q)
            if not located:
                return {"error": "Couldn't find that location. Try a ZIP or a fuller address."}
            lat, lon, label = located
            stops = cls.find_stops(lat, lon, api_key=key, mode=mode, query=q)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Stop lookup failed: {exc}"}
    return {
        "label": label,
        "stops": [{"id": s.id, "name": s.name, "detail": s.detail, "mode": s.mode}
                  for s in stops],
    }


def _resolved_key_for(ftype: str) -> str:
    """Resolve the api_key of the first feed of this type (for bus search auth)."""
    from fetcher import _resolve_str

    config = load_config()
    vault = config.get("key_vault_url") or os.environ.get("AZURE_KEYVAULT_URL", "")
    for f in config.get("feeds", []):
        if str(f.get("type", "")).lower() == ftype:
            return _resolve_str(f.get("api_key", ""), config.get("vars", {}), vault)
    return ""


def _render_single_feed(config, idx, dither):
    """Render one feed as its own full themed board (its city's colors/weather)."""
    feeds = config.get("feeds", [])
    if not (0 <= idx < len(feeds)):
        img = render_image([], config)
        return simulate_eink(img) if dither else img
    feed = {**feeds[idx], "enabled": True}
    zip_code = (feed.get("zip") or "").strip()
    single = {
        **config,
        "title": feed.get("label", ""),
        "display": {"mode": "sections", "theme": str(feed.get("type", "")).lower()},
        "weather": {"enabled": bool(zip_code), "zip": zip_code},
        "feeds": [feed],
        "vars": config.get("vars", {}),
    }
    results = fetch_all(single)
    try:
        weather = get_weather(single)
    except Exception:  # noqa: BLE001 - weather is decorative; never fail the board
        weather = None
    img = render_image(results, single, weather)
    return simulate_eink(img) if dither else img


def _render_merged(config, dither):
    """One board merging EVERY enabled feed, soonest-first (e.g. PATH + light
    rail + bus). Title + weather come from the first feed that defines them."""
    feeds = config.get("feeds", [])
    title = next((f.get("label") for f in feeds if f.get("label")), "")
    zip_code = next((f.get("zip") for f in feeds if f.get("zip")), "")
    merged = {**config, "title": title, "display": {"mode": "stacked"},
              "weather": {"enabled": bool(zip_code), "zip": zip_code}}
    results = fetch_all(merged)
    try:
        weather = get_weather(merged)
    except Exception:  # noqa: BLE001
        weather = None
    img = render_image(results, merged, weather)
    return simulate_eink(img) if dither else img


@app.route("/wifi")
def wifi_page():
    return WIFI


@app.route("/wifi/scan")
def wifi_scan():
    return {
        "supported": netsetup.wifi_supported(),
        "online": netsetup.has_internet(),
        "networks": netsetup.scan(rescan=request.args.get("rescan") == "1"),
    }


@app.route("/wifi/connect", methods=["POST"])
def wifi_connect():
    data = request.get_json(silent=True) or {}
    ok, msg = netsetup.connect((data.get("ssid") or "").strip(), data.get("password") or "")
    return {"ok": ok, "message": msg, "online": netsetup.has_internet() if ok else False}


@app.route("/wifi/online")
def wifi_online():
    # plain text so the kiosk launcher can branch on it with a simple shell test
    return ("yes" if netsetup.has_internet() else "no"), 200, {"Content-Type": "text/plain"}


@app.route("/wifi/status")
def wifi_status():
    return {"online": netsetup.has_internet(), "supported": netsetup.wifi_supported()}


HEALTH = """
<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Board health</title>
<style>
  body{margin:0;font:15px/1.5 system-ui,sans-serif;background:#11151c;color:#e8edf5;padding:24px}
  h1{font-size:20px;margin:0 0 2px} .sub{color:#8b97ab;margin:0 0 18px}
  .pill{display:inline-block;padding:3px 12px;border-radius:20px;font-weight:600;font-size:13px}
  .ok{background:#12351f;color:#5fd48b} .bad{background:#3a1720;color:#ff8098}
  table{border-collapse:collapse;width:100%;margin-top:16px}
  th,td{text-align:left;padding:8px 10px;border-bottom:1px solid #232c3a;vertical-align:top}
  th{color:#8b97ab;font-weight:600;font-size:13px}
  code{background:#0e131b;padding:1px 6px;border-radius:5px;color:#9fb4cf}
  .c{font-weight:700;color:#ffcf6b} .d{color:#8b97ab;font-size:13px;word-break:break-all}
  .none{color:#5fd48b;margin-top:18px}
</style></head><body>
  <h1>Board health <span class="pill {{ 'ok' if online else 'bad' }}">{{ 'online' if online else 'OFFLINE' }}</span></h1>
  <p class="sub">Device <code>{{ device }}</code> · {{ errors|length }} recent error(s) · refreshes every 20s</p>
  {% if errors %}
  <table>
    <tr><th>When</th><th>Code</th><th>What</th><th>Feed</th><th>Detail</th></tr>
    {% for e in errors %}
    <tr><td>{{ e.time }}</td><td class="c">{{ e.code }}</td><td>{{ e.headline }}</td>
        <td>{{ e.label }} <code>{{ e.feed }}</code></td><td class="d">{{ e.detail }}</td></tr>
    {% endfor %}
  </table>
  {% else %}<p class="none">✓ No errors recorded since the last restart.</p>{% endif %}
  <script>setTimeout(function(){location.reload()}, 20000)</script>
</body></html>
"""


@app.route("/health")
def health():
    from errlog import recent_errors
    try:
        online = netsetup.has_internet()
    except Exception:  # noqa: BLE001
        online = False
    device = os.environ.get("TRANSIT_DEVICE", "") or "default"
    return render_template_string(HEALTH, online=online, device=device,
                                  errors=recent_errors(40))


@app.route("/preview.png")
def preview():
    demo = request.args.get("demo")
    dither = request.args.get("dither") == "1"
    feed_param = request.args.get("feed")
    device = request.args.get("device")  # preview a specific device's config
    config = load_config_for(device) if device else load_config()

    if request.args.get("merge"):        # all feeds merged onto one board
        return _png(_render_merged(config, dither))

    if feed_param is not None:
        try:
            idx = int(feed_param)
        except (ValueError, TypeError):
            idx = None
        if idx is not None:
            # a specific feed/device was requested -> render exactly that. Never
            # fall through to the shared preview.png (that's another config's board).
            return _png(_render_single_feed(config, idx, dither))

    if demo:
        # render sample data without touching live APIs or the saved preview
        if demo == "stacked":
            results = sample_stacked()
            config = {**config, "display": {**config.get("display", {}), "mode": "stacked"}}
        else:
            results = sample_results(demo)
            config = {**config, "display": {**config.get("display", {}), "theme": demo}}
        weather = type("W", (), {"temp_f": 39, "condition": "Partly cloudy", "icon": "clouds"})()
        img = render_image(results, config, weather)
        if dither:
            img = simulate_eink(img)
        return _png(img)

    if dither:
        img = render_image(fetch_all(config), config, get_weather(config))
        return _png(simulate_eink(img))

    if not PREVIEW_PATH.exists():
        _display.show(fetch_all(config), config)
    return send_file(PREVIEW_PATH, mimetype="image/png")


def _png(img):
    buf = BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")


if __name__ == "__main__":
    import os
    port = int(os.environ.get("TRANSIT_PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True, use_reloader=False)
