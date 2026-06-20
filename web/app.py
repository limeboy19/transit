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

from appconfig import load_config, save_config
from fetcher import FETCHERS, fetch_all
from geocode import geocode
from renderer import Display, render_image, simulate_eink
from renderer.demo import sample_results, sample_stacked
from renderer.display import PREVIEW_PATH
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
        <b>Live previews — one board per feed</b>
      </div>
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
      <p class="small">Each board is what that person's 800×480 e-ink panel shows —
         its own city colors, time zone, and weather. "6-color panel" simulates
         the real dithered hardware output.</p>
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
  img{width:100vw;height:100vh;object-fit:contain;display:block}
</style></head><body>
<img id="d" src="{{ img_url }}&t={{ cb }}">
<script>
  // refresh the image periodically so this mirrors the panel
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
    return render_template_string(
        ADMIN, config=config, feeds=_feeds_for_form(config),
        feed_types=sorted(FETCHERS),
        mode="Raspberry Pi (Inky)" if _display.on_pi else "dev (preview.png)",
        cb=int(time.time()),
    )


@app.route("/display")
def display_index():
    config = load_config()
    return render_template_string(
        DISPLAY_INDEX, feeds=_feeds_for_form(config), cb=int(time.time()),
    )


@app.route("/display/<int:idx>")
def display_feed(idx):
    config = load_config()
    feeds = config.get("feeds", [])
    feed = feeds[idx] if 0 <= idx < len(feeds) else {}
    label = feed.get("label") or "Transit Display"
    return render_template_string(
        DISPLAY,
        title=label,
        img_url=url_for("preview") + f"?feed={idx}",
        cb=int(time.time()),
        refresh_ms=_feed_refresh(feed) * 1000,
    )


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

    feeds = []
    i = 0
    while f"feed-{i}-type" in form:
        if not form.get(f"feed-{i}-delete"):
            feeds.append(_feed_from_form(form, str(i)))
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
    return {
        "type": str(form.get(f"feed-{key}-type", "")).lower().strip(),
        "enabled": bool(form.get(f"feed-{key}-enabled")),
        "label": form.get(f"feed-{key}-label", "").strip(),
        "stop_id": form.get(f"feed-{key}-stop_id", "").strip(),
        "api_key": form.get(f"feed-{key}-api_key", "").strip(),
        "zip": form.get(f"feed-{key}-zip", "").strip(),
        "refresh_seconds": _feed_refresh({"refresh_seconds": form.get(f"feed-{key}-refresh", 60)}),
    }


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
    img = render_image(results, single, get_weather(single))
    return simulate_eink(img) if dither else img


@app.route("/preview.png")
def preview():
    demo = request.args.get("demo")
    dither = request.args.get("dither") == "1"
    feed_param = request.args.get("feed")
    config = load_config()

    if feed_param is not None:
        try:
            return _png(_render_single_feed(config, int(feed_param), dither))
        except (ValueError, TypeError):
            pass

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
