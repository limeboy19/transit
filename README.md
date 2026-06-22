# Transit Display

A config-driven **Raspberry Pi departure board**. It pulls real-time transit
departures from one or more agency APIs and renders a clean, full-color "what's
next" board — trains and buses merged, soonest-first — on a **Raspberry Pi 5 +
official Raspberry Pi Touch Display 2** (7" LCD touchscreen).

The Pi runs the board fullscreen in a Chromium **kiosk**; a small Flask app
serves both the board and an admin page. On any non-Pi machine the same app
runs and renders to `preview.png` / the browser, so you can build and preview
the layout on your laptop.

**Highlights**
- **Multi-agency**: CTA (L trains **+ buses**), MTA (subway **+ buses**),
  NJ Transit rail — mixed and merged on one board.
- **Per-city theming**: Chicago-flag (stars) for CTA, NYC tricolor flag for
  MTA, with correct timezone clocks (CST/EST), weather widget, route badges in
  line colors, bus/train icons, and per-row stop labels.
- **Many devices, one repo**: each friend's Pi loads its own
  `devices/<id>.json` — change a deployed board's stops by editing that file
  and pushing (no SSH).
- **Secrets in Azure Key Vault**: API keys never touch git or the device's
  config; the config only holds `${...}` references.
- **Self-managing & self-healing**: auto-starts on boot, auto-respawns, and
  pulls new code hourly — validating each update and **rolling back a bad
  commit automatically** so a typo can't brick a deployed device. Reboots
  itself nightly (during off-hours) for a clean slate.

```
transit-display/
├── main.py              # fetch → render → sleep loop (+ Flask thread)
├── appconfig.py         # config load/save (local config.json OR devices/<id>.json)
├── config.example.json  # template; seeds a local config.json for standalone use
├── fetcher/
│   ├── base.py          # BaseFetcher + Departure / FeedResult / StopMatch
│   ├── cta.py           # Chicago: Train Tracker (XML) + Bus Tracker (JSON)
│   ├── mta.py           # NYC: subway GTFS-RT + bus via MTA Bus Time (SIRI)
│   └── njt.py           # NJ Transit Rail Data API (token-based)
├── renderer/
│   ├── display.py       # builds the 800×480 board image (PNG / browser)
│   └── themes.py        # per-agency colors, flags, clocks
├── web/app.py           # Flask: admin (/) + fullscreen board (/display/<n>)
├── weather.py           # ZIP → weather (free, keyless)
├── geocode.py           # ZIP/address → lat/lon (stop search)
├── secrets_azure.py     # Azure Key Vault resolver for ${...}
├── devices/             # per-device configs (advait.json, example.json, …)
├── deploy/
│   ├── setup_kiosk.sh   # one-command Pi setup (kiosk + cron + service + device id)
│   ├── autoupdate.sh    # git pull + restart-if-changed
│   └── PI_SETUP.md      # flashing + first-boot guide
├── fonts/fa-solid-900.ttf   # Font Awesome (bus/train glyphs)
├── transit.service      # systemd unit
├── requirements.txt
└── requirements-azure.txt   # Key Vault deps (optional)
```

## Local development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python3 main.py --once            # render one frame to preview.png and exit
TRANSIT_PORT=8080 python3 main.py # loop + admin at http://localhost:8080
```

> macOS owns port 5000 (AirPlay), so use `TRANSIT_PORT=8080` locally. On the Pi,
> leave the default 5000.

### The two pages
- **`/` — admin.** Add/toggle feeds, set stop IDs and weather ZIP per board,
  and search for stops by ZIP/address ("Find stop", with a Train/Bus filter).
  Shows a live preview per display.
- **`/display/<n>` — the board.** Fullscreen, auto-refreshing — this is what the
  kiosk shows on the Pi. `n` is the feed index (`/display/0` = first feed).

## Configuration

A config is JSON with a top level and a list of `feeds` (each feed = one board):

```json
{
  "key_vault_url": "https://kv-emil.vault.azure.net/",
  "vars": { "cta_key": "", "mta_key": "", "njt_key": "", "cta_bus_key": "" },
  "refresh_seconds": 60,
  "feeds": [
    {
      "type": "cta",
      "enabled": true,
      "label": "Advait",
      "stop_id": "40380, 6034, 15350",
      "api_key": "${cta_key}",
      "bus_key": "${cta_bus_key}",
      "zip": "60607",
      "refresh_seconds": 60
    }
  ]
}
```

Per feed: `type` (`cta`/`mta`/`njt`), `enabled`, `label`, `stop_id`
(comma-separated for multiple stops, merged + trimmed to the soonest few),
`api_key`, `zip` (weather; blank = no weather), `refresh_seconds`, plus
`bus_key` for CTA. The theme + timezone come automatically from `type`.

### CTA (Chicago) — trains **and** buses
- **Train Tracker** key (`api_key`): <https://www.transitchicago.com/developers/traintracker/>
- **Bus Tracker** key (`bus_key`, *separate signup*): <https://www.transitchicago.com/developers/bustracker/>
- Stop IDs: L stations are 5-digit `4xxxx` (map id) or `3xxxx` (stop id); bus
  stops are anything else. The fetcher auto-splits train vs bus stops (use a
  `bus:`/`train:` prefix to force it). Use the admin "Find stop" to look them up.

### MTA (NYC) — subway **and** buses
- Subway GTFS-RT is **public** (no key). Buses use **MTA Bus Time** — put that
  key in `api_key`. Stop search and arrivals both use it.
- Stop IDs: subway is a GTFS id with direction suffix (`R31N`/`R31S`); bus stops
  are `MTA_#####` or numeric. Shared stations are matched across all line feeds.

### NJ Transit — rail
- Uses the **Rail Data API** (token model): `api_key` is `"username:password"`
  (your NJ Transit *API* credentials from <https://developer.njtransit.com/>,
  not your portal login). The app caches the daily token automatically.
- `stop_id`: 2-character station code(s) (e.g. `NP` = Newark Penn). Use the
  admin "Find stop" to search station names.

## Per-device configs (multi-device)

Each physical device loads its **own** config from git, selected by
`TRANSIT_DEVICE` in that Pi's `.env`:

```
devices/advait.json   ← Advait's Pi (TRANSIT_DEVICE=advait)
devices/sam.json      ← Sam's Pi    (TRANSIT_DEVICE=sam)
```

These files are safe to commit — they hold **no secrets** (keys resolve from
Key Vault via `${...}`). So you **change a deployed board's stops by editing its
file and pushing**; the Pi's auto-update pulls it within minutes. See
[`devices/README.md`](devices/README.md) to add a friend.

Without `TRANSIT_DEVICE`, the app uses a local `config.json` (seeded from
`config.example.json`) — handy for standalone/dev use.

## Secrets — `${...}` resolution

Reference secrets as `${name}` in any string field. Resolution order:
**`vars` block → environment variable → Azure Key Vault** (then left as-is).
Leaving a `vars` entry blank makes it fall through to the vault, so **no key
ever lives in git or the config**.

### Azure Key Vault
Set `key_vault_url` (or the `AZURE_KEYVAULT_URL` env var). `${cta_key}` maps to
the vault secret `cta-key` (underscores → hyphens). Add secrets:
```bash
az keyvault secret set --vault-name kv-emil --name cta-key     --value "<train key>"
az keyvault secret set --vault-name kv-emil --name cta-bus-key --value "<bus key>"
az keyvault secret set --vault-name kv-emil --name mta-key     --value "<bus time key>"
az keyvault secret set --vault-name kv-emil --name njt-key     --value "<user>:<pass>"
```
Auth is `DefaultAzureCredential`. On the Pi, a read-only **service principal**
(`Key Vault Secrets User`) supplies `AZURE_CLIENT_ID` / `AZURE_TENANT_ID` /
`AZURE_CLIENT_SECRET` via the gitignored `.env`. Install the deps with
`pip install -r requirements-azure.txt`.

## Deploy on the Pi

Full first-boot walkthrough (flashing, WiFi, SSH): [`deploy/PI_SETUP.md`](deploy/PI_SETUP.md).
In short, on a Pi 5 running Raspberry Pi OS (desktop) with the Touch Display 2:

```bash
sudo apt install -y git python3-venv
git clone <repo-url> ~/transit-display
cd ~/transit-display
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-azure.txt

# create .env with the Azure SP creds + AZURE_KEYVAULT_URL (see PI_SETUP.md)

# one command: device mode + kiosk + rotation + no-blank + service + cron
bash deploy/setup_kiosk.sh advait
sudo reboot
```

It boots straight into the fullscreen board, auto-respawns the browser, and
**auto-updates from git every hour** (pulling new code *and* any change to its
`devices/<id>.json`), restarting only when the commit actually changed. Each
update is validated — if the new code fails to import or the board doesn't come
back up within a minute, it **rolls back to the previous commit automatically**,
so a bad push never bricks an unattended device. The Pi also reboots itself once
a night, but only while the screen is in its off-hours window.

### Off-hours (scheduled screen off)
Set `off_hours` in the device config (e.g. `"23:00-07:00"`) to turn the **screen**
off during that window — evaluated in the board's local time, wrapping midnight.
The Pi keeps running (still fetching/updating); only the display sleeps, and it
comes back on by itself. Blank = always on. Change it anytime by editing the
device's file in git and pushing.

## Adding a new transit system

1. Create `fetcher/<name>.py` with a `BaseFetcher` subclass implementing
   `fetch() -> list[Departure]` and a `feed_type` attribute (optionally
   `find_stops(...)` + `supports_stop_search` for the admin search).
2. Register it in `fetcher/__init__.py`'s `FETCHERS` dict, and add a theme in
   `renderer/themes.py`.

The renderer, admin UI, and loop pick it up automatically.
