# Deferred — for when we're ready to put this on real hardware

These are intentionally parked. None block local development/QA.

## Raspberry Pi deployment / "will it work on a friend's Pi?"
The app logic (fetch → render → web UI) is plain Python and runs anywhere.
The **actual e-ink display path is unverified** because it hasn't been run on
a real Pi + Inky panel yet. Before handing units to friends:

- [ ] Test on ONE Pi end-to-end first (don't distribute untested).
- [ ] Confirm the `inky` library path: `inky.auto()` detection, `set_image`
      (we already handle both 7-color Impression `saturation=` and 6-color
      Spectra/E673 which has no saturation arg), `show()`, `.resolution`.
- [ ] Enable SPI on the Pi (`sudo raspi-config` → Interface → SPI).
- [ ] `pip install inky[rpi]` (Pi-only; pulls RPi.GPIO/spidev).
- [ ] systemd unit + hostname (`transitpi`). NOTE: if multiple Pis are on the
      same network they'll collide on `transitpi.local` — give each a unique
      hostname.
- [ ] Each Pi needs its own `config.json` (per-person CTA key, stop IDs, ZIP).
      CTA keys are per-person; MTA needs none; NJT token ~5 business days.

## One-shot installer (offered, deferred)
- [ ] `install.sh`: create venv, install requirements + `inky[rpi]`, enable SPI,
      copy & enable the systemd service — so a friend's setup is one command.

## Panel-accurate visual tuning (6-color)
The on-screen RGB preview looks great, but the **real 6-color panel** dithers
the light header tint into noise and turns brown/orange badges to mush
(see the "6-color panel" preview / `dev_preview.py --dither`). When optimizing
for hardware:
- [ ] Switch header band to pure white (accent as text + underline only).
- [ ] Snap route badge colors to the 6 renderable colors (no brown/orange).
- [ ] Re-check small text legibility after dithering.
