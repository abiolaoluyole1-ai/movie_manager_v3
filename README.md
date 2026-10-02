# Movie Manager V3

Local Windows dashboard for discovering and managing long-form Yoruba movie candidates from YouTube metadata, with persistent SQLite tracking, live progress, pause/resume/stop, thumbnails, embedded playback where permitted, rejection/replacement workflow, and an authorised direct-file download queue.

## What V3 includes

- Local only — no deployment.
- `Start Movie Manager.bat` launcher.
- Red/white glassmorphism dashboard.
- Dashboard / Movies / Downloads / Settings.
- Yoruba, Igbo and Hausa each have their own catalogue, target, search history and download folder; pick one in the Language dropdown.
- Dashboard Live Log shows discovery and download activity as it happens (also saved to `data/logs/movie-manager.log`).
- Any target number.
- Real duration check: accepted videos must be **60 minutes or longer**.
- Reject obvious trailers, clips, interviews, reviews, music, BTS, episodes and promos.
- Exact YouTube ID dedupe plus conservative same-title/duration duplicate detection.
- Start / Pause / Resume / Stop.
- Automatic retry after temporary network/API interruption.
- SQLite persistence across restarts.
- Thumbnail cards and YouTube embed playback where the uploader allows embedding.
- Remove & Replace and rejected-history protection.
- User-selectable local download folder.
- Download `.part` files, HTTP Range resume when supported, progress, bytes, speed, ETA, retry count and detailed errors.
- CLI remains available.

## Important local-download boundary

The YouTube Data API provides metadata and playback information; it does **not** provide a general MP4 URL for ordinary YouTube watch pages. V3 therefore does not treat `youtube.com/watch?...` or `youtu.be/...` as a direct file source.

The local download engine is implemented for **authorised/direct HTTP(S) movie-file URLs**. Once one is attached to a movie, V3 can queue it, save it to your chosen folder, retry after network failures, resume when the source supports HTTP Range, verify completion, and rename `.part` to the completed video file.

## First run on Windows

1. Extract the ZIP and keep the folder together.
2. Double-click `Start Movie Manager.bat`.
3. On first launch, the BAT creates `.venv`, installs the required Python packages, and creates `.env` if needed.
4. Open `.env` in Notepad and replace:

```text
YOUTUBE_API_KEY=PASTE_YOUR_PRIVATE_KEY_HERE
```

with your private API key. Never share that file/key.
5. Double-click `Start Movie Manager.bat` again.
6. The dashboard opens automatically at `http://127.0.0.1:5000`.
7. Keep the black BAT window open while using the dashboard.

## Recommended first test

1. Target = `10`.
2. Mode = **Discover only**.
3. Click **Start**.
4. Inspect thumbnails and several movie cards.
5. Confirm all accepted results are at least 60 minutes.
6. Test Pause, Resume and Remove & Replace.
7. Close/restart the app and confirm the catalogue remains.
8. Then test target `30`.
9. Only after the quality looks right, increase toward `1000`.

## Download folder

Default:

```text
%USERPROFILE%\Movies\Movie Manager\Yoruba
```

Use **Settings → Choose folder** to select another location. V3 creates the language subfolder automatically.

Incomplete file:

```text
Movie Name.mp4.part
```

Completed file:

```text
Movie Name.mp4
```

## CLI

```powershell
python manage.py init
python manage.py status
python manage.py discover --target 30 --language yoruba --wait
```

Normal use should be through the dashboard.

## Persistent data

SQLite database:

```text
data\movies.db
```
