import os
import queue
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, send_file

from .config import (
    YOUTUBE_BLOCKED_COOLDOWN_SECONDS, YOUTUBE_BLOCKED_MESSAGE,
    clamp_concurrency, clamp_download_quality, clamp_min_free_disk_gb, clamp_youtube_browser,
)
from .db import (
    all_settings, all_video_ids, apply_source_resolution, bulk_reject_movies,
    bulk_restore_movies, count_downloadable, count_movies, counts, get_movie,
    get_language_target, get_setting, init_db, list_movie_ids, list_movies, list_movies_for_source_resolution,
    clear_movie_library, delete_movie_from_library, reject_movie, reset_search_state, restore_movie,
    retry_download, retry_failed_downloads, set_download_source, set_setting, source_status_counts, queue_movies_for_download,
    start_fresh,
)
from .discovery import DISCOVERY_PROVIDERS
from .events import activity, events
from .language_profiles import PROFILES, public_languages
from .runtime import runtime
from .source_adapters import resolver
from .source_mappings import import_mapping_entries, parse_csv_mapping, parse_json_mapping
from .supply_scan import SUPPLY_SCAN_PROVIDERS
from .utils import format_duration, is_direct_http_candidate, is_http_url

ACCEPTED = ["ACCEPTED", "QUEUED", "DOWNLOADING", "DOWNLOADED"]
BULK_REJECT_REASONS = {"USER_REJECTED", "WRONG_LANGUAGE"}
SOURCE_RESOLVE_WORKERS = 8


def _serialise_movie(movie):
    movie = dict(movie)
    movie["duration_label"] = format_duration(movie.get("duration_seconds", 0))
    movie["downloadable"] = bool(movie.get("download_url")) or (
        movie.get("download_backend") == "YTDLP" and movie.get("provider") == "youtube"
    )
    return movie


def _parse_bulk_ids(data):
    raw_ids = data.get("ids")
    if not isinstance(raw_ids, list):
        return None
    ids = []
    seen = set()
    for item in raw_ids:
        try:
            value = int(item)
        except (TypeError, ValueError):
            continue
        if value not in seen:
            seen.add(value)
            ids.append(value)
    return ids


def _maybe_start_replacement_discovery(language):
    """Start at most one discovery worker if the accepted catalogue fell below target."""
    maintain = get_setting("maintain_target", "1") == "1"
    if not maintain:
        return False
    target = get_language_target(language)
    provider = "youtube"
    accepted = count_movies(language, ACCEPTED)
    if accepted < target and runtime.discovery.snapshot()["status"] not in {"RUNNING", "PAUSED"}:
        try:
            runtime.start_discovery(language, target, provider=provider)
            return True
        except Exception:
            return False
    return False


def _start_downloads(language):
    """Start the download workers; (True, None) or (False, reason)."""
    try:
        runtime.download.start(language, concurrency=clamp_concurrency(get_setting("max_concurrent_downloads", "3")))
    except RuntimeError as exc:
        return False, str(exc)
    return True, None


def _workers_active():
    snapshot = runtime.snapshot()
    return any(snapshot[key]["status"] in {"RUNNING", "PAUSED"} for key in (
        "discovery", "downloads", "supply_scan"
    ))


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
        target = get_language_target(language)
        provider = "youtube"
        status_counts, download_counts = counts(language)
        src_counts = source_status_counts(language, ACCEPTED)
        movies = [_serialise_movie(m) for m in list_movies(language, limit=24)]
        key = os.getenv("YOUTUBE_API_KEY", "").strip()
        return jsonify({
            "language": language,
            "target": target,
            "provider": provider,
            "providers": ["youtube"],
            "settings": all_settings(),
            "languages": public_languages(),
            "api_configured": bool(key and key != "PASTE_YOUR_PRIVATE_KEY_HERE"),
            "counts": {
                "accepted": sum(status_counts.get(x, 0) for x in ACCEPTED),
                "downloadable": count_downloadable(language, ACCEPTED),
                "downloaded": status_counts.get("DOWNLOADED", 0),
                "rejected": status_counts.get("REJECTED", 0),
                "all": sum(status_counts.values()),
                "source_missing": src_counts.get("SOURCE_MISSING", 0),
                "source_invalid": src_counts.get("SOURCE_INVALID", 0),
                "source_pending": src_counts.get("SOURCE_PENDING", 0),
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
        # The catalogue and completed-download view share this endpoint.  A
        # larger safe cap lets a finished session show its whole result rather
        # than making the completion total disagree with the visible list.
        limit = min(500, max(1, int(request.args.get("limit", 60))))
        offset = max(0, int(request.args.get("offset", 0)))
        rows = list_movies(language, status=status, search=search, limit=limit, offset=offset)
        return jsonify([_serialise_movie(m) for m in rows])

    @app.get("/api/movies/ids")
    def movies_ids():
        language = request.args.get("language") or get_setting("active_language", "yoruba")
        status = request.args.get("status", "ALL")
        search = request.args.get("search", "")
        ids = list_movie_ids(language, status=status, search=search)
        return jsonify({"ids": ids, "count": len(ids)})

    @app.post("/api/discovery/start")
    def discovery_start():
        data = request.get_json(force=True)
        language = data.get("language", "yoruba")
        target = max(1, int(data.get("target", 30)))
        provider = "youtube"
        profile = PROFILES.get(language)
        if not profile or not profile.get("enabled"):
            return jsonify({"ok": False, "error": "That language profile is not enabled yet."}), 400
        set_setting("active_language", language)
        set_setting(f"target:{language}", target)
        set_setting(f"provider:{language}", provider)
        try:
            # In combined mode start the existing worker pool first, then let
            # discovery continuously feed its atomic queue.
            if data.get("mode") == "both":
                started, reason = _start_downloads(language)
                if not started:
                    return jsonify({"ok": False, "error": reason}), 400
            runtime.start_discovery(language, target, provider=provider)
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

    @app.post("/api/supply-scan/start")
    def supply_scan_start():
        """Diagnostic-only: searches and checks metadata/rights/direct-URL
        reachability for up to max_candidates items. Never writes to the
        movies table and never downloads a full file."""
        data = request.get_json(force=True)
        language = data.get("language") or get_setting("active_language", "yoruba")
        provider = data.get("provider", "internet_archive")
        max_candidates = data.get("max_candidates", 250)
        if provider not in SUPPLY_SCAN_PROVIDERS:
            return jsonify({
                "ok": False, "error": f"Supply scan does not support provider '{provider}' yet."
            }), 400
        try:
            max_candidates = int(max_candidates)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "max_candidates must be a number."}), 400
        try:
            runtime.start_supply_scan(language, provider, max_candidates)
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({"ok": True, "supply_scan": runtime.supply_scan.snapshot()})

    @app.post("/api/supply-scan/stop")
    def supply_scan_stop():
        runtime.stop_supply_scan()
        return jsonify({"ok": True})

    @app.get("/api/supply-scan/status")
    def supply_scan_status():
        return jsonify(runtime.supply_scan.snapshot())

    @app.post("/api/movies/<int:movie_id>/reject")
    def movie_reject(movie_id):
        data = request.get_json(silent=True) or {}
        result = reject_movie(movie_id, data.get("reason", "USER_REJECTED"))
        if not result:
            return jsonify({"ok": False, "error": "Movie not found."}), 404

        language = result["language"]
        _maybe_start_replacement_discovery(language)
        events.emit("movie_rejected", {"movie_id": movie_id})
        return jsonify({"ok": True})

    @app.delete("/api/movies/<int:movie_id>")
    def movie_delete(movie_id):
        if _workers_active():
            return jsonify({"ok": False, "error": "Stop discovery, downloads, and supply scans before deleting library data."}), 409
        if not delete_movie_from_library(movie_id):
            return jsonify({"ok": False, "error": "Movie not found."}), 404
        events.emit("movie_deleted", {"movie_id": movie_id})
        return jsonify({"ok": True})

    @app.post("/api/movies/<int:movie_id>/restore")
    def movie_restore(movie_id):
        restore_movie(movie_id)
        events.emit("movie_restored", {"movie_id": movie_id})
        return jsonify({"ok": True})

    @app.post("/api/movies/bulk/reject")
    def movies_bulk_reject():
        data = request.get_json(silent=True) or {}
        ids = _parse_bulk_ids(data)
        if ids is None:
            return jsonify({"ok": False, "error": "ids must be a list of movie IDs."}), 400
        language = data.get("language") or get_setting("active_language", "yoruba")
        reason = data.get("reason", "USER_REJECTED")
        if reason not in BULK_REJECT_REASONS:
            return jsonify({
                "ok": False,
                "error": f"reason must be one of {sorted(BULK_REJECT_REASONS)}."
            }), 400

        result = bulk_reject_movies(language, ids, reason)
        replacement_triggered = (
            _maybe_start_replacement_discovery(language) if result["updated"] > 0 else False
        )
        result["ok"] = True
        result["replacement_triggered"] = replacement_triggered
        events.emit("movies_bulk_rejected", {"language": language, "reason": reason, **result})
        return jsonify(result)

    @app.post("/api/movies/bulk/restore")
    def movies_bulk_restore():
        data = request.get_json(silent=True) or {}
        ids = _parse_bulk_ids(data)
        if ids is None:
            return jsonify({"ok": False, "error": "ids must be a list of movie IDs."}), 400
        language = data.get("language") or get_setting("active_language", "yoruba")

        result = bulk_restore_movies(language, ids)
        result["ok"] = True
        events.emit("movies_bulk_restored", {"language": language, **result})
        return jsonify(result)

    @app.post("/api/movies/bulk/download")
    def movies_bulk_download():
        data = request.get_json(silent=True) or {}
        ids = _parse_bulk_ids(data)
        if ids is None:
            return jsonify({"ok": False, "error": "ids must be a list of movie IDs."}), 400
        language = data.get("language") or get_setting("active_language", "yoruba")
        result = queue_movies_for_download(language, ids)
        started, reason = _start_downloads(language)
        if not started:
            return jsonify({"ok": False, "error": reason, **result}), 409
        result["ok"] = True
        events.emit("downloads_queued", {"language": language, **result})
        return jsonify(result)

    @app.post("/api/movies/<int:movie_id>/download-source")
    def movie_download_source(movie_id):
        data = request.get_json(force=True)
        url = (data.get("download_url") or "").strip()
        if not is_http_url(url):
            return jsonify({"ok": False, "error": "Enter a valid authorised HTTP/HTTPS direct file URL."}), 400
        movie = get_movie(movie_id)
        if not movie:
            return jsonify({"ok": False, "error": "Movie not found."}), 404

        if not is_direct_http_candidate(url):
            return jsonify({
                "ok": False,
                "error": "A YouTube watch URL is not a direct local-file download source."
            }), 400

        set_download_source(movie_id, url)
        events.emit("download_source_ready", {"movie_id": movie_id})
        return jsonify({"ok": True})

    @app.post("/api/movies/bulk/resolve-sources")
    def movies_bulk_resolve_sources():
        """Automatic bulk source resolution -- one backend operation, not one
        request per movie. Runs HEAD probes concurrently; DB writes stay
        serialised through apply_source_resolution's own lock."""
        data = request.get_json(silent=True) or {}
        language = data.get("language") or get_setting("active_language", "yoruba")
        raw_ids = data.get("ids")
        ids = _parse_bulk_ids(data) if isinstance(raw_ids, list) else None

        candidates = list_movies_for_source_resolution(language, ids=ids, accepted_statuses=ACCEPTED)
        result = {"checked": 0, "ready": 0, "missing": 0, "invalid": 0, "errors": 0}

        if candidates:
            with ThreadPoolExecutor(max_workers=SOURCE_RESOLVE_WORKERS) as pool:
                futures = {pool.submit(resolver.resolve_movie, m): m for m in candidates}
                for future in as_completed(futures):
                    movie = futures[future]
                    result["checked"] += 1
                    try:
                        resolution = future.result()
                        apply_source_resolution(movie["id"], language, resolution, ACCEPTED)
                    except Exception:
                        result["errors"] += 1
                        continue
                    status = resolution.get("source_status")
                    if status == "SOURCE_READY":
                        result["ready"] += 1
                    elif status == "SOURCE_MISSING":
                        result["missing"] += 1
                    elif status == "SOURCE_INVALID":
                        result["invalid"] += 1

        result["ok"] = True
        events.emit("sources_bulk_resolved", {"language": language, **result})
        return jsonify(result)

    @app.post("/api/download-sources/import")
    def download_sources_import():
        """Import authorised source mappings from pasted CSV/JSON content.
        Never starts a download automatically."""
        data = request.get_json(silent=True) or {}
        fmt = (data.get("format") or "").strip().lower()
        content = data.get("content") or ""
        try:
            if fmt == "csv":
                entries = parse_csv_mapping(content)
            elif fmt == "json":
                entries = parse_json_mapping(content)
            else:
                return jsonify({"ok": False, "error": "format must be 'csv' or 'json'."}), 400
        except Exception as exc:
            return jsonify({"ok": False, "error": f"Could not parse {fmt.upper()} content: {exc}"}), 400

        result = import_mapping_entries(entries, known_video_ids=all_video_ids())
        result["ok"] = True
        return jsonify(result)


    @app.post("/api/movies/<int:movie_id>/retry-download")
    def movie_retry_download(movie_id):
        movie = get_movie(movie_id)
        if not movie:
            return jsonify({"ok": False, "error": "Movie not found."}), 404
        if movie.get("download_status") == "DOWNLOADED":
            return jsonify({"ok": False, "error": "This movie has already been downloaded."}), 400
        is_youtube = movie.get("provider") == "youtube" and movie.get("download_backend") == "YTDLP"
        if not movie.get("download_url") and not is_youtube:
            return jsonify({"ok": False, "error": "This movie has no local-file download source yet."}), 400
        if movie.get("download_error") == YOUTUBE_BLOCKED_MESSAGE:
            try:
                elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(movie["updated_at"])).total_seconds()
            except (TypeError, ValueError):
                elapsed = YOUTUBE_BLOCKED_COOLDOWN_SECONDS
            if elapsed < YOUTUBE_BLOCKED_COOLDOWN_SECONDS:
                wait = int(YOUTUBE_BLOCKED_COOLDOWN_SECONDS - elapsed)
                return jsonify({
                    "ok": False,
                    "error": f"YouTube recently blocked this download. Wait about {wait}s before retrying.",
                }), 429
        retry_download(movie_id)
        events.emit("download_retry", {"movie_id": movie_id})
        return jsonify({"ok": True})

    @app.post("/api/downloads/retry-failed")
    def downloads_retry_failed():
        data = request.get_json(silent=True) or {}
        language = data.get("language") or get_setting("active_language", "yoruba")
        retried = retry_failed_downloads(language)
        started, reason = _start_downloads(language)
        if not started:
            return jsonify({"ok": False, "error": reason}), 409
        events.emit("downloads_retried", {"language": language, "retried": retried})
        return jsonify({"ok": True, "retried": retried})

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
        started, reason = _start_downloads(language)
        if not started:
            return jsonify({"ok": False, "error": reason}), 409
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

    @app.post("/api/settings/clear-library")
    def settings_clear_library():
        if _workers_active():
            return jsonify({"ok": False, "error": "Stop discovery, downloads, and supply scans before clearing the library."}), 409
        return jsonify({"ok": True, "deleted": clear_movie_library()})

    @app.post("/api/settings/start-fresh")
    def settings_start_fresh():
        if _workers_active():
            return jsonify({"ok": False, "error": "Stop discovery, downloads, and supply scans before starting fresh."}), 409
        return jsonify({"ok": True, "deleted": start_fresh()})

    @app.post("/api/settings")
    def settings_update():
        data = request.get_json(force=True)
        allowed = {
            "active_language", "maintain_target", "download_root", "count_only_downloadable",
            "max_concurrent_downloads", "movies_per_page", "min_free_disk_gb", "download_quality",
            "youtube_use_browser_session", "youtube_browser",
        }
        language = data.get("active_language")
        if language is not None and not (PROFILES.get(language) or {}).get("enabled"):
            return jsonify({"ok": False, "error": "That language profile is not enabled."}), 400
        for key, value in data.items():
            if key in allowed:
                if key == "download_root":
                    Path(str(value)).expanduser().mkdir(parents=True, exist_ok=True)
                elif key == "max_concurrent_downloads":
                    value = clamp_concurrency(value)
                    runtime.download.set_concurrency(value)
                elif key == "movies_per_page":
                    value = str(value) if str(value) in {"12", "24", "30", "48"} else "30"
                elif key == "min_free_disk_gb":
                    value = clamp_min_free_disk_gb(value)
                elif key == "download_quality":
                    value = clamp_download_quality(value)
                elif key == "youtube_browser":
                    value = clamp_youtube_browser(value)
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

    @app.get("/api/log")
    def activity_log():
        """Recent Live Log lines, so a reloaded page or a late-opened tab can catch up."""
        try:
            after = max(0, int(request.args.get("after", 0)))
            limit = min(500, max(1, int(request.args.get("limit", 300))))
        except ValueError:
            return jsonify({"ok": False, "error": "after and limit must be numbers."}), 400
        language = request.args.get("language") or None
        return jsonify({"entries": activity.recent(limit=limit, after=after, language=language)})

    @app.post("/api/log/clear")
    def activity_log_clear():
        activity.clear()
        return jsonify({"ok": True})

    @app.get("/api/health")
    def health():
        return jsonify({"ok": True})

    return app
