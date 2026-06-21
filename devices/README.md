# Per-device configs

Each file here is **one device's** config (one friend's display). A device
loads its own file based on `TRANSIT_DEVICE` in that Pi's `.env`
(e.g. `TRANSIT_DEVICE=advait` → loads `devices/advait.json`).

These files are safe to commit: they contain **no secrets** — just stop IDs,
labels, ZIP, and `${...}` references. The real API keys live in Azure Key Vault.

## Add a new friend (e.g. "sam")

1. Copy the template and edit it:
   ```bash
   cp devices/example.json devices/sam.json
   # edit devices/sam.json: set type/label/stop_id/zip for their city
   git add devices/sam.json && git commit -m "Add sam device" && git push
   ```
   (Tip: run the app locally with `TRANSIT_DEVICE=sam` and use the admin's
   "Find stop" to look up their stop IDs, then paste them in.)

2. On Sam's Pi (after flashing + cloning + venv, see `deploy/PI_SETUP.md`):
   ```bash
   bash deploy/setup_kiosk.sh sam
   sudo reboot
   ```

That's it. Sam's Pi now shows `devices/sam.json`, independent of every other
device.

## Change a deployed device's stops (no SSH needed)

Edit that device's file (here on GitHub or on your Mac) → commit → push.
Each Pi's auto-update cron pulls it within ~5 minutes and the board updates.

## Notes

- Device id = the filename without `.json` (keep it lowercase/simple).
- Mix agencies freely per device (CTA, MTA subway+bus, NJT) and multiple stops
  per feed (comma-separated `stop_id`).
- If `TRANSIT_DEVICE` points at a file that doesn't exist, the board shows
  "No feeds enabled" — a clear signal the id is wrong.
