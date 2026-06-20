# Raspberry Pi setup — transit display

End-to-end setup for a Pi Zero 2 W + Inky Impression 7.3" (2025 / Spectra 6).
Plan ~30–45 min. You'll do steps 1–2 at your desk, the rest over SSH.

---

## 1. Flash the microSD (on your Mac)

1. Install **Raspberry Pi Imager** → https://www.raspberrypi.com/software/
2. Insert the microSD card.
3. In Imager:
   - **Device:** Raspberry Pi Zero 2 W
   - **OS:** Raspberry Pi OS **Lite (64-bit)**  (no desktop needed)
   - **Storage:** your SD card
4. Click **Next → Edit Settings** (the customization screen) and set:
   - **Hostname:** `transitpi`
   - **Enable SSH** → "Use password authentication", **username `pi`** + a password
     (use `pi` so it matches `transit.service`)
   - **Configure wireless LAN:** your WiFi SSID + password + country
   - **Locale:** your timezone
5. **Save → Write.** This erases the card and writes the OS (~5 min). That's "flashing."

## 2. Assemble the hardware

1. With the Pi **unplugged**, press the **Inky Impression** onto the Pi's 40-pin
   GPIO header (all 40 pins, the HAT sits over the Pi).
2. Insert the flashed microSD into the Pi.
3. Plug power into the Pi's **PWR** USB port (use a solid 5V/2.5A+ supply).
4. First boot takes ~1–2 minutes.

## 3. SSH in (from your Mac)

```bash
ssh pi@transitpi.local
```
(If `transitpi.local` doesn't resolve, find the Pi's IP in your router and use that.)

## 4. Enable SPI + I2C (the Inky needs both)

```bash
sudo raspi-config nonint do_spi 0
sudo raspi-config nonint do_i2c 0
sudo reboot
```
Wait a minute, then `ssh pi@transitpi.local` again.

## 5. Install the app

```bash
sudo apt update && sudo apt install -y git python3-venv python3-pip
git clone https://github.com/limeboy19/transit.git ~/transit-display
cd ~/transit-display
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install "inky[rpi]"                 # Inky driver (Pi only)
pip install -r requirements-azure.txt   # only if using Key Vault
```

> **Private repo note:** the clone above works only if the repo is public.
> Since this repo contains **no secrets**, making it public is the easy path and
> makes auto-update "just work." To keep it private, add a read-only **deploy
> key**: `ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519` then add `~/.ssh/id_ed25519.pub`
> to the repo's Settings → Deploy keys, and clone the `git@github.com:...` URL.

## 6. Add this Pi's secrets / config

The app needs API keys. Two options:

**A. Azure Key Vault (matches our setup).** Create `.env` with the service-principal
creds so the Pi can read `kv-emil`:
```bash
nano ~/transit-display/.env
```
```
AZURE_CLIENT_ID=...
AZURE_TENANT_ID=...
AZURE_CLIENT_SECRET=...
```
Leave `key_vault_url` set in `config.json` and the `vars` blank — keys come from the vault.

**B. Simpler (no Azure):** put the keys straight into this Pi's `config.json` `vars`
(it's git-ignored, so never committed). Edit later from the web UI.

`config.json` auto-creates from the template on first run; edit it in the web UI
(step 9) or with `nano config.json`.

## 7. First test — push one frame to the panel

```bash
cd ~/transit-display && source .venv/bin/activate
python main.py --once
```
- It should print `running on Raspberry Pi (Inky)` and the panel does a ~12s
  flashy refresh, then shows your board.
- ⚠️ This is the first run on real hardware. If you see an Inky/SPI error, copy
  the full output — that's the moment to debug (library version, wiring, SPI).

## 8. Auto-start + auto-update

```bash
sudo cp ~/transit-display/transit.service /etc/systemd/system/
sudo systemctl enable --now transit.service
journalctl -u transit.service -f          # watch logs (Ctrl-C to stop)
```
Auto-update (pull new code every 5 min, restart only if changed):
```bash
chmod +x ~/transit-display/deploy/autoupdate.sh
crontab -e        # paste the line from deploy/crontab.txt
```

## 9. Daily use

- **Admin / control panel:** http://transitpi.local:5000
- **The board itself (per display):** http://transitpi.local:5000/display/0
- Set stops, weather ZIP, etc. from the admin; the panel refreshes on its loop.
- Code changes you push to GitHub land on the Pi within 5 min; your `config.json`
  and `.env` are never touched by updates.

---

### Quick troubleshooting
- **`transitpi.local` won't resolve:** use the Pi's IP from your router.
- **Panel stays blank, no error:** confirm SPI + I2C enabled (step 4) and the HAT
  is fully seated on all 40 pins.
- **`inky` import/board-detect error:** ensure `pip install "inky[rpi]"` ran in the
  venv and you're on a recent `inky` (Spectra 6 needs current versions).
- **Service won't start:** `journalctl -u transit.service -e` shows why.
