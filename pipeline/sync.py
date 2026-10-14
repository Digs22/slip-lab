"""Settings sync between devices. The app posts every change to the private ntfy channel "<topic>-prefs":

    {"key": "settings", "updated": iso, "prefs": {...}, "theme": "dynamic"}
    {"key": "filters", "updated": iso, "off_leagues": [...], "off_teams": {...}}
    {"key": "placed", "id": bet id, "on": true, "at": iso, "units": 4, "unit": 3.0}

Devices read the channel directly. ntfy keeps messages about 12 hours, so each run also saves the newest
copy here and export puts it in data.json: data/settings.json, data/filters.json (teams.py), and placed
taps go into the ledger (placed, placed_units, placed_unit) plus data/placed_events.json.
"""
import json

from . import config, ledger, teams

SETTINGS_FILE = config.DATA / "settings.json"
PLACED_FILE = config.DATA / "placed_events.json"


def _read(path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return default


def load_settings() -> dict | None:
    return _read(SETTINGS_FILE, None)


def load_placed_events() -> list[dict]:
    return list(_read(PLACED_FILE, {}).values())


def apply(msgs: list[dict], bets: list[dict]) -> dict:
    """Apply app messages (oldest first). Returns counts of what changed."""
    settings = load_settings() or {}
    events = _read(PLACED_FILE, {})
    by_id = {b["id"]: b for b in bets}
    out = {"settings": 0, "filters": 0, "placed": 0}
    for m in msgs:
        k = m.get("key")
        if k == "settings" and str(m.get("updated") or "") > str(settings.get("updated") or ""):
            settings = {"key": "settings", "updated": m["updated"], "prefs": m.get("prefs") or {}, "theme": m.get("theme")}
            out["settings"] += 1
        elif k == "filters":
            cur = teams.load_filters(remote=False)
            if str(m.get("updated") or "") > cur["updated"]:
                teams.save_filters(m)
                out["filters"] += 1
        elif k == "placed" and m.get("id") and m.get("at") and str(m["at"]) > str(events.get(m["id"], {}).get("at", "")):
            events[m["id"]] = {k2: m.get(k2) for k2 in ("id", "on", "at", "units", "unit")}
            b = by_id.get(m["id"])
            if b:
                if m.get("on"):
                    b["placed"] = True
                    if m.get("units") is not None and m.get("unit"):
                        b["placed_units"], b["placed_unit"] = m["units"], m["unit"]
                else:
                    for f in ("placed", "placed_units", "placed_unit"):
                        b.pop(f, None)
            out["placed"] += 1
    if out["settings"]:
        SETTINGS_FILE.write_text(json.dumps(settings, indent=2) + "\n")
    if out["placed"]:
        PLACED_FILE.write_text(json.dumps(events, indent=1) + "\n")
    return out


def pull(bets: list[dict]) -> dict:
    """Read the channel and apply it. Network failures change nothing."""
    msgs = teams.ntfy_messages()
    return apply(msgs, bets) if msgs else {}
