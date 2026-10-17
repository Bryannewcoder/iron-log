#!/usr/bin/env python3
"""IronLog custom HTTP server — serves static files and accepts POST /api/sync."""

import os
import sys
import json
import time
import shutil
import http.server
import urllib.parse

HOST = "0.0.0.0"
PORT = 8080
ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "data")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
WORKOUTS_FILE = os.path.join(DATA_DIR, "workouts.json")

# Ensure data directories exist
os.makedirs(BACKUP_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)


def _empty_store():
    return {"version": 1, "exported": None, "workouts": [], "library": [], "oneRepMaxes": {}}


def _load_store():
    """Load workouts.json, tolerating a missing or corrupt file."""
    if not os.path.exists(WORKOUTS_FILE):
        return _empty_store()
    try:
        with open(WORKOUTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return _empty_store()
    if not isinstance(data, dict):
        return _empty_store()
    data.setdefault("version", 1)
    data.setdefault("exported", None)
    if not isinstance(data.get("workouts"), list):
        data["workouts"] = []
    if not isinstance(data.get("library"), list):
        data["library"] = []
    if not isinstance(data.get("oneRepMaxes"), dict):
        data["oneRepMaxes"] = {}
    return data


def _merge_workouts(existing, incoming):
    """Union by id; on collision keep whichever has the newer lastModified."""
    by_id = {}
    order = []
    for w in existing:
        if not isinstance(w, dict) or "id" not in w:
            continue
        wid = w["id"]
        if wid not in by_id:
            order.append(wid)
        by_id[wid] = w
    for w in incoming:
        if not isinstance(w, dict) or "id" not in w:
            continue
        wid = w["id"]
        if wid not in by_id:
            by_id[wid] = w
            order.append(wid)
            continue
        existing_lm = str(by_id[wid].get("lastModified") or "")
        incoming_lm = str(w.get("lastModified") or "")
        if incoming_lm > existing_lm:
            by_id[wid] = w
    return [by_id[wid] for wid in order]


def _merge_library(existing, incoming):
    """Union of custom exercises by case-insensitive name — never drop entries."""
    by_key = {}
    order = []
    for e in list(existing) + list(incoming):
        if not isinstance(e, dict):
            continue
        key = str(e.get("name", "")).strip().lower()
        if not key or key in by_key:
            continue
        by_key[key] = e
        order.append(key)
    return [by_key[k] for k in order]


def _merge_one_rep_maxes(existing, incoming):
    """Union per-exercise history, deduping identical (weight, date, auto) entries."""
    merged = {}
    for name, entries in (existing or {}).items():
        merged[name] = list(entries) if isinstance(entries, list) else []
    for name, entries in (incoming or {}).items():
        if not isinstance(entries, list):
            continue
        bucket = merged.setdefault(name, [])
        seen = {(e.get("weight"), e.get("date"), e.get("auto")) for e in bucket if isinstance(e, dict)}
        for e in entries:
            if not isinstance(e, dict):
                continue
            key = (e.get("weight"), e.get("date"), e.get("auto"))
            if key not in seen:
                bucket.append(e)
                seen.add(key)
    return merged


class IronLogHandler(http.server.SimpleHTTPRequestHandler):
    """Custom handler — serves static files and handles /api/sync."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def _send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def end_headers(self):
        # Add CORS to every response
        self._send_cors_headers()
        super().end_headers()

    def do_OPTIONS(self):
        """Handle CORS preflight."""
        self.send_response(204)
        self.end_headers()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/sync":
            self._handle_sync()
        else:
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Not found"}).encode())

    def do_GET(self):
        """Override GET to handle /api/sync and /api/data endpoints."""
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/sync":
            self.send_response(405)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Use POST for sync"}).encode())
            return
        if parsed.path == "/api/data":
            self._handle_get_data()
            return
        super().do_GET()

    def _handle_get_data(self):
        store = _load_store()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(store).encode())

    def _handle_sync(self):
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Empty request body"}).encode())
            return

        body = self.rfile.read(content_length)
        try:
            payload = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": f"Invalid JSON: {e}"}).encode())
            return

        if not isinstance(payload, dict):
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Payload must be a JSON object"}).encode())
            return

        incoming_workouts = payload.get("workouts", [])
        incoming_library = payload.get("library", [])
        incoming_orm = payload.get("oneRepMaxes", {})
        if not isinstance(incoming_workouts, list):
            incoming_workouts = []
        if not isinstance(incoming_library, list):
            incoming_library = []
        if not isinstance(incoming_orm, dict):
            incoming_orm = {}

        store = _load_store()

        # Timestamped backup before every write — unchanged from prior behavior.
        backup_name = None
        if os.path.exists(WORKOUTS_FILE):
            timestamp = time.strftime("%Y%m%d-%H%M%S")
            backup_name = f"workouts-{timestamp}.json"
            backup_path = os.path.join(BACKUP_DIR, backup_name)
            shutil.copy2(WORKOUTS_FILE, backup_path)
            self._prune_backups()

        merged = {
            "version": 1,
            "exported": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "workouts": _merge_workouts(store.get("workouts", []), incoming_workouts),
            "library": _merge_library(store.get("library", []), incoming_library),
            "oneRepMaxes": _merge_one_rep_maxes(store.get("oneRepMaxes", {}), incoming_orm),
        }

        with open(WORKOUTS_FILE, "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2)

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        response = {
            "status": "ok",
            "workouts": len(merged["workouts"]),
            "backup": backup_name
        }
        self.wfile.write(json.dumps(response).encode())

    def _prune_backups(self, keep=50):
        """Remove oldest backup files beyond keep count."""
        try:
            backups = sorted(
                f for f in os.listdir(BACKUP_DIR) if f.endswith(".json")
            )
            while len(backups) > keep:
                oldest = backups.pop(0)
                os.remove(os.path.join(BACKUP_DIR, oldest))
        except OSError:
            pass


def main():
    server = http.server.HTTPServer((HOST, PORT), IronLogHandler)
    print(f"IronLog server running on http://{HOST}:{PORT}")
    print(f"Serving from: {ROOT}")
    print(f"Data dir: {DATA_DIR}")
    sys.stdout.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.server_close()


if __name__ == "__main__":
    main()
