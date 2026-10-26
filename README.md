# PIP-OS

A green phosphor interface for the Raspberry Pi on caterpie, in the shape of a Fallout 3 / New Vegas Pip-Boy 3000. The screen has three modes:

- **STAT** — machine condition (CPU, temperature, memory, disk, network, load) and S.P.E.C.I.A.L. traits derived from the hardware.
- **INV** — the home directory as an inventory. Read only. Hidden files stay hidden.
- **DATA** — local and world position from a USB GPS receiver, a quest log, and FM broadcast through the attached SDR.

Run it on the machine you want it to describe:

```bash
python3 server.py
```

Then open http://127.0.0.1:8080 . On the Pi, bind the LAN so another machine can reach it:

```bash
python3 server.py --host 0.0.0.0
```

Quests are stored in `~/.local/share/pipboy/quests.json`. A u-blox receiver on `/dev/ttyACM0` is read as NMEA. Map tiles come from OpenStreetMap, are cached under `~/.cache/pipboy/tiles`, and are graded green in the page. FM plays from the RTL-SDR at `/dev/swradio0` through the USB sound card. `deploy/kiosk.sh` opens the page fullscreen on the Wayland session. Alt+F4 closes it and leaves the desktop.

The interface is original. Share Tech Mono is included under the SIL Open Font License; see `static/fonts/OFL.txt`.
