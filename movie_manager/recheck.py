"""Re-judge earlier rule-based rejections against the current content rules.

Works offline from the stored title/description/channel/duration (no YouTube
requests). Dry-run unless apply=True. Only rejections made by the rules
themselves (blocked terms, compilations) are considered, never a person's
"remove" / "wrong language" decision, and a language marked `protected`
(Yoruba, whose goal is complete) can never be written to.
"""
from .db import get_language_target, list_rule_rejections, mark_youtube_download_ready, reaccept_rule_rejection
from .discovery import ACCEPTED_STATUSES, DiscoveryController
from .language_profiles import PROFILES


def recheck_rule_rejections(language, apply=False):
    profile = PROFILES[language]
    if apply and profile.get("protected"):
        raise ValueError(f"{profile['label']} is protected: its catalogue is never rewritten. Run without --apply.")

    judge = DiscoveryController()
    judge.language = language
    target = get_language_target(language)
    result = {"checked": 0, "still_rejected": 0, "would_accept": [], "accepted": 0, "blocked_by_target": 0}
    for row in list_rule_rejections(language):
        result["checked"] += 1
        item = {
            "id": row["video_id"],
            "snippet": {
                "title": row["title"], "description": row["description"] or "",
                "channelTitle": row["channel_title"], "channelId": row["channel_id"],
                "defaultAudioLanguage": row["default_audio_language"],
                "defaultLanguage": row["default_language"], "thumbnails": {},
            },
            "contentDetails": {"duration": f"PT{int(row['duration_seconds'] or 0)}S"},
            "status": {"embeddable": bool(row["embeddable"])},
        }
        _, outcome = judge._evaluate(item, row["source_query"] or f"{profile['label']} movie", 3600)
        if outcome != "ACCEPTED":
            result["still_rejected"] += 1
            continue
        result["would_accept"].append({"id": row["id"], "title": row["title"]})
        if apply:
            if reaccept_rule_rejection(row["id"], language, ACCEPTED_STATUSES, target):
                mark_youtube_download_ready(row["id"])
                result["accepted"] += 1
            else:
                result["blocked_by_target"] += 1
    return result
