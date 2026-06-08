# Transit Display

A config-driven Raspberry Pi departure board. It pulls real-time transit
departures from one or more agency APIs and renders them to a Pimoroni **Inky
Impression 7.3"** e-ink display (800×480, 6/7-color), refreshing every 60s.

On a non-Pi machine it skips the hardware and writes `preview.png` instead, so
you can iterate on layout locally. A small Flask UI (`:5000`) lets you toggle
feeds, edit stop IDs / API keys, and watch a live preview.

```
transit-display/
├── main.py              # fetch → render → sleep loop (+ web UI thread)
├── appconfig.py         # shared config.json load/save
├── config.json          # which feeds are active, stop ids, keys
├── fetcher/
│   ├── base.py          # BaseFetcher + Departure/FeedResult
│   ├── cta.py           # Chicago CTA Train Tracker (REST/XML)
│   ├── mta.py           # NYC Subway (GTFS-RT protobuf)
│   └── njt.py           # NJ Transit (GTFS-RT protobuf, stub)
├── renderer/
│   └── display.py       # Inky on a Pi, preview.png otherwise
├── web/
│   └── app.py           # Flask config UI + live preview
├── deploy/
│   ├── autoupdate.sh    # git pull + restart-if-changed
│   └── crontab.txt      # 5-min auto-update cron line
├── transit.service      # systemd unit
└── requirements.txt
```

## Local development (no Pi)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python3 main.py --once                 # render one frame to preview.png and exit
TRANSIT_PORT=8080 python3 main.py      # loop + admin UI at http://localhost:8080
```

Not on a Pi? The renderer detects this (via `/proc/device-tree/model`), skips
the `inky` import, and writes `preview.png`.

> macOS owns port 5000 (AirPlay), so use `TRANSIT_PORT=8080` (or `--port 8080`)
> locally. On the Pi, leave the default 5000.

### The two pages (both testable in a browser)
- **`/` — admin.** Toggle feeds, set stop IDs / API keys, pick layout
  (sections vs stacked), theme, and weather ZIP. Has a live preview, a
  **6-color panel** preview (real dithering), and **sample-data** buttons
  (CTA/MTA/stacked) so you can QA layout with **no API keys**.
- **`/display` — the tracker.** Fullscreen, auto-refreshing — exactly what the
  e-ink panel shows. Open it on any screen/monitor.

### Render sample data from the CLI (no keys, no server)
```bash
python3 dev_preview.py                 # CTA sections -> preview.png
python3 dev_preview.py --theme mta     # NYC subway
python3 dev_preview.py --stacked       # merged chronological list
python3 dev_preview.py --all           # one PNG per theme + stacked
python3 dev_preview.py --dither        # simulate the real 6-color panel
```

## Configuration

Edit `config.json` directly or use the web UI. Each feed:

```json
{ "type": "cta", "enabled": true, "stop_id": "40380", "api_key": "xxx", "label": "Chicago L" }
```

- `type` — `cta`, `mta`, or `njt`
- `enabled` — show this feed (sections are laid out only for enabled feeds)
- `stop_id` — see per-agency notes below
- `api_key` — agency API key / token
- `label` — section header text
- optional: `limit` (rows, default 5), `timeout` (seconds)

Top-level:
- `title` — header title; blank uses the agency name (e.g. "Chicago Transit")
- `refresh_seconds` — min 15
- `display.mode` — `"sections"` (one labeled block per stop) or `"stacked"`
  (all stops merged into one chronological "what's next" list, MTA-style)
- `display.theme` — `""` (auto, match first feed) or force `cta`/`mta`/`njt`
- `weather.enabled` + `weather.zip` — show a weather widget in the header
  (US ZIP; uses free, keyless zippopotam.us + open-meteo)

### Themes
Each agency has its own colors. **CTA** uses the Chicago-flag motif (sky-blue
accent + four red six-pointed stars). **MTA** uses subway navy with line-color
bullets. **NJT** uses NJ Transit blue. Route badges are colored per line.

> Note: the panel is **6-color** (no orange/brown). The RGB `preview.png` is
> optimistic — use the **6-color panel** preview link (or `--dither`) to see
> the real dithered output. Light header tints and brown/orange badges dither
> noticeably; pure-white backgrounds and primary-color badges render cleanest.

### CTA (Chicago)
- Get a key: <https://www.transitchicago.com/developers/traintracker/>
- `stop_id`: a 5-digit **map id** (e.g. `40380`, both directions) or a
  platform-level **stop id**. We auto-detect which to use.

### MTA (NYC Subway)
- Feeds are public (no key needed as of 2023). Set `api_key` only if you have
  one — it's sent as `x-api-key`.
- `stop_id`: a GTFS stop id **with direction suffix**, e.g. `R31N`
  (northbound) / `R31S` (southbound). The correct line-group feed is chosen
  from the first letter; override with a `"feed"` key (e.g. `"nqrw"`) if needed.
- Stop ids: <http://web.mta.info/developers/data/nyct/subway/google_transit.zip> (`stops.txt`).

### NJ Transit
- Stub. NJ Transit requires a token from <https://developer.njtransit.com/>.
  Set `feed_url` (the GTFS-RT trip-updates endpoint) and `api_key` (token) in
  the feed config; adjust the auth header in `fetcher/njt.py` to match your
  data product. Until configured it shows a friendly error on screen.

## API keys & secrets

These transit keys are low-sensitivity, but you still don't want them in git.
The setup keeps them safe with zero cloud infrastructure:

- **`config.json` is git-ignored.** The repo ships `config.example.json` as a
  template; on first run the app copies it to `config.json`, which stays local.
- So your keys are **never committed**, and the auto-update `git reset --hard`
  **updates code only** — it never touches your `config.json`.

Declare each value **once** in a top-level `vars` block and reference it as
`${name}` from any feed — so reusing one key across multiple feeds (e.g. a CTA
train stop + a CTA bus stop) means editing it in a single place:

```json
{
  "vars": { "cta_key": "your-key", "mta_key": "", "njt_key": "" },
  "feeds": [
    { "type": "cta", "api_key": "${cta_key}", "stop_id": "40380", ... },
    { "type": "cta", "api_key": "${cta_key}", "stop_id": "41450", ... }
  ]
}
```

Resolution order for `${name}`: the `vars` block first, then an **environment
variable** of the same name, otherwise left as-is. So you can keep keys out of
the file entirely by leaving `vars` empty and defining the value in the systemd
unit instead:

```ini
Environment=cta_key=your-key-here
```

`${...}` works in any string field (keys, stop IDs, labels), and the reference
stays in the file/admin UI — only the live fetch sees the resolved value.

CTA keys are issued instantly; NJ Transit's developer approval takes ~5
business days, so leave the NJT feed disabled until your token arrives.

### Azure Key Vault (keeps keys fully out of git)
Set a vault URL (admin UI → Secrets, or `key_vault_url` in config / the
`AZURE_KEYVAULT_URL` env var) and `${name}` references resolve from the vault.
Resolution order is **vars → env var → Key Vault**, so leaving a `vars` entry
blank makes it fall through to the vault.

Add the secrets (names use hyphens — `${cta_key}` → secret `cta-key`):
```bash
az keyvault secret set --vault-name kv-emil --name cta-key --value "<cta key>"
az keyvault secret set --vault-name kv-emil --name mta-key --value "<bus time key>"
```

Networking: Key Vault is **always** Entra-authenticated (no anonymous access),
so "public but protected by Entra" = Public network access **Enabled** +
Permission model **Azure RBAC**. Grant roles:
```bash
VAULT=$(az keyvault show --name kv-emil --query id -o tsv)
# you, for local dev:
az role assignment create --assignee <you> --role "Key Vault Secrets Officer" --scope $VAULT
# the device (read-only) via a service principal:
az ad sp create-for-rbac --name transitpi-display
az role assignment create --assignee <appId> --role "Key Vault Secrets User" --scope $VAULT
```

Auth uses `DefaultAzureCredential`:
- **Local:** `az login` (install `azure-cli`).
- **Pi:** set `AZURE_CLIENT_ID` / `AZURE_TENANT_ID` / `AZURE_CLIENT_SECRET`
  (from the service principal) in `transit.service`.

Then `pip install -r requirements-azure.txt`, blank the `vars` keys, and no API
key lives in config.json at all. The vault URL itself isn't secret.

## Adding a new transit system

1. Create `fetcher/<name>.py` with a `BaseFetcher` subclass implementing
   `fetch() -> list[Departure]` and a `feed_type` class attribute.
2. Register it in `fetcher/__init__.py`'s `FETCHERS` dict.

That's it — the renderer, web UI, and loop pick it up automatically.

## Deploy on the Pi

```bash
# On the Pi (Raspberry Pi OS):
sudo apt install python3-venv
git clone <your-repo> /home/pi/transit-display
cd /home/pi/transit-display
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install inky[rpi]          # Inky driver (Pi only)

# systemd auto-start
sudo cp transit.service /etc/systemd/system/
sudo systemctl enable --now transit.service
journalctl -u transit.service -f      # logs
```

`http://transitpi.local:5000` — set the Pi's hostname to `transitpi`
(`sudo raspi-config` → System → Hostname) so the admin UI is reachable by name.
The fullscreen tracker is at `http://transitpi.local:5000/display`.

### Auto-update (cron)

```bash
chmod +x deploy/autoupdate.sh
crontab -e        # paste the line from deploy/crontab.txt
```

Every 5 minutes it does `git fetch` + hard reset to `origin/main` and restarts
the service only if the commit changed.
```
*/5 * * * * /home/pi/transit-display/deploy/autoupdate.sh >> /home/pi/transit-display/autoupdate.log 2>&1
```
