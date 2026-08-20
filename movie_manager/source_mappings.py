import csv
import io
import json

from .config import DATA_DIR
from .utils import is_direct_http_candidate

MAPPING_PATH = DATA_DIR / "download_sources.json"


def load_mappings():
    """Read data/download_sources.json -> {video_id: {"download_url": url}}."""
    if not MAPPING_PATH.exists():
        return {}
    try:
        with open(MAPPING_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    mapping = {}
    for video_id, entry in data.items():
        if isinstance(entry, str) and entry.strip():
            mapping[video_id] = {"download_url": entry.strip()}
        elif isinstance(entry, dict) and (entry.get("download_url") or "").strip():
            mapping[video_id] = {"download_url": entry["download_url"].strip()}
    return mapping


def save_mappings(mapping):
    MAPPING_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = MAPPING_PATH.with_suffix(".json.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(mapping, f, indent=2, sort_keys=True)
    tmp_path.replace(MAPPING_PATH)


def _validate_entry(video_id, download_url):
    if not video_id:
        return "Missing video_id"
    if not is_direct_http_candidate(download_url):
        return "Not an authorised direct HTTP(S) URL"
    return None


def import_mapping_entries(entries, known_video_ids=None):
    """entries: iterable of (video_id, download_url) pairs already parsed
    from CSV or JSON. Validates, merges into the persisted mapping file
    (never starting downloads), and returns counts."""
    existing = load_mappings()
    result = {
        "imported": 0, "updated": 0, "invalid": 0,
        "duplicates": 0, "unknown_video_ids": 0, "errors": [],
    }
    seen = set()
    for raw_video_id, raw_url in entries:
        video_id = (raw_video_id or "").strip()
        download_url = (raw_url or "").strip()
        error = _validate_entry(video_id, download_url)
        if error:
            result["invalid"] += 1
            result["errors"].append({"video_id": video_id, "error": error})
            continue
        if video_id in seen:
            result["duplicates"] += 1
            continue
        seen.add(video_id)
        if known_video_ids is not None and video_id not in known_video_ids:
            result["unknown_video_ids"] += 1
        if video_id in existing:
            if existing[video_id].get("download_url") != download_url:
                result["updated"] += 1
        else:
            result["imported"] += 1
        existing[video_id] = {"download_url": download_url}
    save_mappings(existing)
    return result


def parse_csv_mapping(text):
    reader = csv.DictReader(io.StringIO(text))
    fieldnames = [(f or "").strip().lower() for f in (reader.fieldnames or [])]
    if "video_id" not in fieldnames or "download_url" not in fieldnames:
        raise ValueError("CSV must have 'video_id' and 'download_url' columns.")
    entries = []
    for row in reader:
        normalised = {(k or "").strip().lower(): (v or "") for k, v in row.items() if k}
        entries.append((normalised.get("video_id", ""), normalised.get("download_url", "")))
    return entries


def parse_json_mapping(text):
    data = json.loads(text)
    entries = []
    if isinstance(data, dict):
        for video_id, value in data.items():
            if isinstance(value, str):
                entries.append((video_id, value))
            elif isinstance(value, dict):
                entries.append((video_id, value.get("download_url", "")))
    elif isinstance(data, list):
        for row in data:
            if isinstance(row, dict):
                entries.append((row.get("video_id", ""), row.get("download_url", "")))
    else:
        raise ValueError("JSON must be an object of video_id -> url/entry, or a list of entries.")
    return entries
