import argparse
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from movie_manager.db import (
    all_video_ids, init_db, get_language_target, get_setting, set_setting, count_movies,
    reset_interrupted_downloads,
)
from movie_manager.runtime import runtime
from movie_manager.source_mappings import import_mapping_entries, parse_csv_mapping, parse_json_mapping


def main():
    parser = argparse.ArgumentParser(description="Movie Manager V3 command-line controls")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init")
    sub.add_parser("status")

    d = sub.add_parser("discover")
    d.add_argument("--target", type=int, required=True)
    d.add_argument("--language", default="yoruba")
    d.add_argument("--wait", action="store_true")

    sub.add_parser("pause")
    sub.add_parser("resume")
    sub.add_parser("stop")

    i = sub.add_parser("import-sources", help="Import authorised download-source mappings from CSV/JSON.")
    i.add_argument("--file", required=True)
    i.add_argument("--format", choices=["csv", "json"], default=None)

    args = parser.parse_args()
    init_db()
    reset_interrupted_downloads()

    if args.command == "init":
        print("Database initialised.")
    elif args.command == "status":
        language = get_setting("active_language", "yoruba")
        target = get_language_target(language)
        print(f"Language: {language}")
        print(f"Target: {target}")
        print(f"Accepted: {count_movies(language, statuses=['ACCEPTED','QUEUED','DOWNLOADING','DOWNLOADED'])}")
        print(f"Downloaded: {count_movies(language, statuses=['DOWNLOADED'])}")
        print(f"Runtime status: {runtime.snapshot()['discovery_status']}")
    elif args.command == "discover":
        set_setting("active_language", args.language)
        set_setting(f"target:{args.language}", str(args.target))
        runtime.start_discovery(args.language, args.target)
        print(f"Discovery started for {args.language}, target {args.target}.")
        if args.wait:
            import time
            try:
                while runtime.snapshot()["discovery_status"] not in {"COMPLETED", "EXHAUSTED", "STOPPED", "ERROR"}:
                    print(runtime.snapshot())
                    time.sleep(2)
            except KeyboardInterrupt:
                runtime.pause_discovery()
                print("Paused.")
    elif args.command == "pause":
        runtime.pause_discovery()
        print("Pause requested.")
    elif args.command == "resume":
        runtime.resume_discovery()
        print("Resume requested.")
    elif args.command == "stop":
        runtime.stop_discovery()
        print("Stop requested.")
    elif args.command == "import-sources":
        file_path = Path(args.file)
        fmt = args.format or ("csv" if file_path.suffix.lower() == ".csv" else "json")
        content = file_path.read_text(encoding="utf-8")
        entries = parse_csv_mapping(content) if fmt == "csv" else parse_json_mapping(content)
        result = import_mapping_entries(entries, known_video_ids=all_video_ids())
        print(
            f"Imported: {result['imported']}  Updated: {result['updated']}  "
            f"Invalid: {result['invalid']}  Unknown video IDs: {result['unknown_video_ids']}  "
            f"Duplicates: {result['duplicates']}"
        )
        for err in result["errors"]:
            print(f"  Invalid row [{err['video_id']}]: {err['error']}")


if __name__ == "__main__":
    main()
