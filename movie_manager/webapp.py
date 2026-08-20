import os
import queue
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, send_file

from .db import (
    all_settings, count_movies, counts, get_movie, get_setting, init_db, list_movies,
    reject_movie, reset_search_state, restore_movie, retry_download, set_download_source, set_setting,
)
from .events import events
from .language_profiles import PROFILES
from .runtime import runtime
from .utils import format_duration, is_http_url

ACCEPTED = ["ACCEPTED", "QUEUED", "DOWNLOADING", "DOWNLOADED"]


def _serialise_movie(movie):
    movie = dict(movie)
    movie["duration_label"] = format_duration(movie.get("duration_seconds", 0))
    movie["downloadable"] = bool(movie.get("download_url"))
    return movie


def create_app():
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    init_db()

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/bootstrap")
    def bootstrap():
        language = get_setting("active_language", "yoruba")
        runtime.restore_discovery(language)
        target = int(get_setting(f"target:{language}", "30"))
        status_counts, download_counts = counts(language)
        movies = [_serialise_movie(m) for m in list_movies(language, limit=24)]
        key = os.getenv("YOUTUBE_API_KEY", "").strip()
        return jsonify({
            "language": language,
            "target": target,
            "settings": all_settings(),
            "languages": PROFILES,
            "api_configured": bool(key and key != "PASTE_YOUR_PRIVATE_KEY_HERE"),
            "counts": {
                "accepted": sum(status_counts.get(x, 0) for x in ACCEPTED),
                "downloaded": status_counts.get("DOWNLOADED", 0),
                "rejected": status_counts.get("REJECTED", 0),
                "all": sum(status_counts.values()),
                "download": download_counts,
            },
            "runtime": runtime.snapshot(),
            "movies": movies,
        })

    @app.get("/api/movies")
    def movies():
        language = request.args.get("language") or get_setting("active_language", "yoruba")
        status = request.args.get("status", "ALL")
        search = request.args.get("search", "")
        limit = min(200, max(1, int(request.args.get("limit", 60))))
        offset = max(0, int(request.args.get("offset", 0)))
        rows = list_movies(language, status=status, search=search, limit=limit, offset=offset)
        return jsonify([_serialise_movie(m) for m in rows])

    @app.post("/api/discovery/start")
    def discovery_start():
        data = request.get_json(force=True)
        language = data.get("language", "yoruba")
        target = max(1, int(data.get("target", 30)))
        profile = PROFILES.get(language)
        if not profile or not profile.get("enabled"):
            return jsonify({"ok": False, "error": "That language profile is not enabled yet."}), 400
        set_setting("active_language", language)
        set_setting(f"target:{language}", target)
        try:
            runtime.start_discovery(language, target)
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({"ok": True, "runtime": runtime.snapshot()})

    @app.post("/api/discovery/pause")
    def discovery_pause():
        runtime.pause_discovery()
        return jsonify({"ok": True})

    @app.post("/api/discovery/resume")
    def discovery_resume():
        runtime.resume_discovery()
        return jsonify({"ok": True})

    @app.post("/api/discovery/stop")
    def discovery_stop():
        runtime.stop_discovery()
        return jsonify({"ok": True})

    @app.post("/api/discovery/reset-search")
    def discovery_reset_search():
        language = (request.get_json(silent=True) or {}).get(
            "language", get_setting("active_language", "yoruba")
        )
        reset_search_state(language)
        return jsonify({"ok": True})

    @app.post("/api/movies/<int:movie_id>/reject")
    def movie_reject(movie_id):
        data = request.get_json(silent=True) or {}
        result = reject_movie(movie_id, data.get("reason", "USER_REJECTED"))
        if not result:
            return jsonify({"ok": False, "error": "Movie not found."}), 404

        language = result["language"]
        maintain = get_setting("maintain_target", "1") == "1"
        if maintain:
            target = int(get_setting(f"target:{language}", "30"))
            accepted = count_movies(language, ACCEPTED)
            if accepted < target and runtime.discovery.snapshot()["status"] not in {"RUNNING", "PAUSED"}:
                try:
                    runtime.start_discovery(language, target)
                except Exception:
                    pass
        events.emit("movie_rejected", {"movie_id": movie_id})
        return jsonify({"ok": True})

    @app.post("/api/movies/<int:movie_id>/restore")
    def movie_restore(movie_id):
        restore_movie(movie_id)
        events.emit("movie_restored", {"movie_id": movie_id})
        return jsonify({"ok": True})

    @app.post("/api/movies/<int:movie_id>/download-source")
    def movie_download_source(movie_id):
        data = request.get_json(force=True)
        url = (data.get("download_url") or "").strip()
        if not is_http_url(url):
            return jsonify({"ok": False, "error": "Enter a valid authorised HTTP/HTTPS direct file URL."}), 400
        movie = get_movie(movie_id)
        if not movie:
            return jsonify({"ok": False, "error": "Movie not found."}), 404

        lower = url.lower()
        if "youtube.com/watch" in lower or "youtu.be/" in lower:
            return jsonify({
                "ok": False,
                "error": "A YouTube watch URL is not a direct local-file download source."
            }), 400

        set_download_source(movie_id, url)
        events.emit("download_source_ready", {"movie_id": movie_id})
        return jsonify({"ok": True})


    @app.post("/api/movies/<int:movie_id>/retry-download")
    def movie_retry_download(movie_id):
        movie = get_movie(movie_id)
        if not movie:
            return jsonify({"ok": False, "error": "Movie not found."}), 404
        if not movie.get("download_url"):
            return jsonify({"ok": False, "error": "This movie has no local-file download source yet."}), 400
        retry_download(movie_id)
        events.emit("download_retry", {"movie_id": movie_id})
        return jsonify({"ok": True})

    @app.get("/api/movies/<int:movie_id>/local-media")
    def movie_local_media(movie_id):
        movie = get_movie(movie_id)
        if not movie or movie.get("download_status") != "DOWNLOADED" or not movie.get("file_path"):
            return jsonify({"ok": False, "error": "Completed local movie file not found."}), 404
        path = Path(movie["file_path"]).expanduser().resolve()
        root = Path(get_setting("download_root")).expanduser().resolve()
        try:
            if not path.is_relative_to(root):
                return jsonify({"ok": False, "error": "Invalid local media path."}), 403
        except AttributeError:
            if root not in path.parents and path != root:
                return jsonify({"ok": False, "error": "Invalid local media path."}), 403
        if not path.exists() or not path.is_file():
            return jsonify({"ok": False, "error": "Local movie file is missing."}), 404
        return send_file(path, conditional=True)

    @app.post("/api/downloads/start")
    def downloads_start():
        language = (request.get_json(silent=True) or {}).get(
            "language", get_setting("active_language", "yoruba")
        )
        runtime.download.start(language)
        return jsonify({"ok": True})

    @app.post("/api/downloads/pause")
    def downloads_pause():
        runtime.download.pause()
        return jsonify({"ok": True})

    @app.post("/api/downloads/resume")
    def downloads_resume():
        runtime.download.resume()
        return jsonify({"ok": True})

    @app.post("/api/downloads/stop")
    def downloads_stop():
        runtime.download.stop()
        return jsonify({"ok": True})

    @app.post("/api/settings")
    def settings_update():
        data = request.get_json(force=True)
        allowed = {"active_language", "maintain_target", "download_root"}
        for key, value in data.items():
            if key in allowed:
                if key == "download_root":
                    Path(str(value)).expanduser().mkdir(parents=True, exist_ok=True)
                set_setting(key, value)
        return jsonify({"ok": True, "settings": all_settings()})

    @app.post("/api/settings/select-folder")
    def select_folder():
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            initial = get_setting("download_root")
            path = filedialog.askdirectory(initialdir=initial, title="Choose Movie Manager download folder")
            root.destroy()
            if path:
                Path(path).mkdir(parents=True, exist_ok=True)
                set_setting("download_root", path)
                return jsonify({"ok": True, "path": path})
            return jsonify({"ok": True, "path": initial, "cancelled": True})
        except Exception as exc:
            return jsonify({
                "ok": False,
                "error": f"Folder picker could not open: {exc}. You can type the folder path instead."
            }), 500

    @app.get("/api/events")
    def event_stream():
        q = events.subscribe()

        def generate():
            try:
                yield "retry: 2000\n\n"
                while True:
                    try:
                        event = q.get(timeout=20)
                        yield events.encode(event)
                    except queue.Empty:
                        yield "event: ping\ndata: {}\n\n"
            finally:
                events.unsubscribe(q)

        return Response(generate(), mimetype="text/event-stream", headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        })

    @app.get("/api/health")
    def health():
        return jsonify({"ok": True})

    return app
