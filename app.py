import os
import threading
import time
import webbrowser

from dotenv import load_dotenv
from waitress import serve

load_dotenv()

from movie_manager.webapp import create_app  # noqa: E402

app = create_app()


def open_browser():
    time.sleep(1.2)
    port = int(os.getenv("MOVIE_MANAGER_PORT", "5000"))
    webbrowser.open(f"http://127.0.0.1:{port}")


if __name__ == "__main__":
    port = int(os.getenv("MOVIE_MANAGER_PORT", "5000"))
    threading.Thread(target=open_browser, daemon=True).start()
    print(f"Movie Manager is running at http://127.0.0.1:{port}")
    serve(app, host="127.0.0.1", port=port, threads=10)
