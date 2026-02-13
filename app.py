import json
import os
import threading
import atexit
from flask import Flask, render_template, jsonify, Response, request
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GAMES_FILE = os.path.join(BASE_DIR, "games.txt")

app = Flask(__name__)

# ---- Per-tab scraper state ----
TABS = {}
for _tab_name in ("trader", "my"):
    TABS[_tab_name] = {
        "thread": None,
        "running": False,
        "stop_event": threading.Event(),
        "results_file": os.path.join(BASE_DIR, f"{_tab_name}_results.json"),
        "progress_file": os.path.join(BASE_DIR, f"{_tab_name}_progress.json"),
    }

VALID_TABS = set(TABS.keys())


def _get_tab(tab):
    """Return tab dict or None if invalid."""
    return TABS.get(tab)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/results/<tab>")
def get_results(tab):
    t = _get_tab(tab)
    if not t:
        return jsonify({"error": "Invalid tab"}), 400
    if os.path.exists(t["results_file"]):
        with open(t["results_file"], "r", encoding="utf-8") as f:
            results = json.load(f)
        return jsonify(results)
    return jsonify([])


@app.route("/api/progress/<tab>")
def get_progress(tab):
    t = _get_tab(tab)
    if not t:
        return jsonify({"error": "Invalid tab"}), 400
    if os.path.exists(t["progress_file"]):
        with open(t["progress_file"], "r", encoding="utf-8") as f:
            progress = json.load(f)
        return jsonify(progress)
    return jsonify({"current": 0, "total": 0, "game": "", "status": "idle", "percent": 0})


@app.route("/api/progress-stream/<tab>")
def progress_stream(tab):
    """Server-Sent Events stream for real-time progress updates."""
    t = _get_tab(tab)
    if not t:
        return jsonify({"error": "Invalid tab"}), 400
    progress_file = t["progress_file"]

    def generate():
        last_data = None
        start_time = time.time()
        stale_count = 0
        while True:
            if time.time() - start_time > 600:
                break
            try:
                if os.path.exists(progress_file):
                    with open(progress_file, "r", encoding="utf-8") as f:
                        data = f.read()
                    if data != last_data:
                        last_data = data
                        stale_count = 0
                        yield f"data: {data}\n\n"
                        parsed = json.loads(data)
                        if parsed.get("status") in ("completed", "stopped", "error"):
                            break
                    else:
                        stale_count += 1
                else:
                    stale_count += 1

                if stale_count > 10 and not t["running"]:
                    break

                time.sleep(0.5)
            except Exception:
                time.sleep(1)

    return Response(generate(), mimetype="text/event-stream")


@app.route("/api/start/<tab>", methods=["POST"])
def start_scraper(tab):
    t = _get_tab(tab)
    if not t:
        return jsonify({"error": "Invalid tab"}), 400

    if t["running"]:
        return jsonify({"error": "Scraper is already running for this tab"}), 409

    data = request.json or {}
    workers = max(1, int(data.get("workers", 3)))
    games_text = data.get("games", "").strip()
    if not games_text:
        return jsonify({"error": "No games provided"}), 400

    seen = set()
    games_list = []
    for line in games_text.splitlines():
        name = line.strip()
        if name and name.lower() not in seen:
            seen.add(name.lower())
            games_list.append(name)

    stop_event = t["stop_event"]
    stop_event.clear()
    results_file = t["results_file"]
    progress_file = t["progress_file"]
    label = f"{tab.capitalize()}/"

    def run_scraper():
        t["running"] = True
        try:
            from scraper import scrape_prices
            scrape_prices(
                headless=False,
                games_list=games_list,
                workers=workers,
                output_file=results_file,
                progress_file=progress_file,
                stop_event=stop_event,
                label=label,
            )
        except Exception as e:
            error_data = {"current": 0, "total": 0, "game": str(e), "status": "error", "percent": 0}
            with open(progress_file, "w", encoding="utf-8") as f:
                json.dump(error_data, f)
        finally:
            t["running"] = False

    t["thread"] = threading.Thread(target=run_scraper, daemon=True)
    t["thread"].start()

    return jsonify({"status": "started"})


@app.route("/api/stop/<tab>", methods=["POST"])
def stop_scraper(tab):
    t = _get_tab(tab)
    if not t:
        return jsonify({"error": "Invalid tab"}), 400
    if not t["running"]:
        return jsonify({"error": "Scraper is not running"}), 409
    t["stop_event"].set()
    return jsonify({"status": "stop_requested"})


@app.route("/api/status/<tab>")
def scraper_status(tab):
    t = _get_tab(tab)
    if not t:
        return jsonify({"error": "Invalid tab"}), 400
    return jsonify({"running": t["running"]})


@app.route("/api/clear-results/<tab>", methods=["POST"])
def clear_results(tab):
    t = _get_tab(tab)
    if not t:
        return jsonify({"error": "Invalid tab"}), 400
    for f in (t["results_file"], t["progress_file"]):
        if os.path.exists(f):
            os.remove(f)
    return jsonify({"status": "cleared"})


@app.route("/api/delete-result/<tab>", methods=["POST"])
def delete_result(tab):
    t = _get_tab(tab)
    if not t:
        return jsonify({"error": "Invalid tab"}), 400

    data = request.json or {}
    search_name = data.get("search_name", "")
    if not search_name:
        return jsonify({"error": "Missing search_name"}), 400

    if not os.path.exists(t["results_file"]):
        return jsonify({"error": "No results file"}), 404

    with open(t["results_file"], "r", encoding="utf-8") as f:
        results = json.load(f)

    updated = [r for r in results if r.get("search_name") != search_name]
    with open(t["results_file"], "w", encoding="utf-8") as f:
        json.dump(updated, f, ensure_ascii=False, indent=2)

    return jsonify({"status": "deleted", "remaining": len(updated)})


def cleanup():
    """Remove runtime data files on shutdown."""
    for t in TABS.values():
        for f in (t["results_file"], t["progress_file"]):
            try:
                if os.path.exists(f):
                    os.remove(f)
            except Exception:
                pass

atexit.register(cleanup)


if __name__ == "__main__":
    app.run(debug=True, port=5000, threaded=True)
