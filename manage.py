import argparse
from dotenv import load_dotenv

load_dotenv()

from movie_manager.db import init_db, get_setting, set_setting, count_movies
from movie_manager.runtime import runtime


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

    args = parser.parse_args()
    init_db()

    if args.command == "init":
        print("Database initialised.")
    elif args.command == "status":
        language = get_setting("active_language", "yoruba")
        target = int(get_setting(f"target:{language}", "30"))
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
                while runtime.snapshot()["discovery_status"] not in {"COMPLETED", "STOPPED", "ERROR"}:
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


if __name__ == "__main__":
    main()
