"""Movie-candidate content classification shared by every discovery provider.

Ported verbatim from the original YouTube-only logic in discovery.py so
every provider (YouTube, Internet Archive, and future ones) applies the
exact same movie/language/compilation rules instead of drifting copies.
"""
from .language_profiles import BLOCKED_TERMS, COMPILATION_DESCRIPTION_PHRASES, COMPILATION_TITLE_PHRASES


def is_yoruba_language_tag(value):
    value = (value or "").strip().lower().replace("_", "-")
    return value in {"yo", "yor", "yoruba"} or value.startswith("yo-")


def is_english_language_tag(value):
    value = (value or "").strip().lower().replace("_", "-")
    return value in {"en", "eng", "english"} or value.startswith("en-")


def has_yoruba_content_evidence(title, description, channel_title):
    title = (title or "").lower()
    description = (description or "").lower()
    channel_title = (channel_title or "").lower()
    return (
        "yoruba" in title
        or "yorùbá" in title
        or any(phrase in description for phrase in (
            "yoruba movie", "yoruba film", "yoruba language", "yoruba nollywood",
        ))
        or "yoruba" in channel_title
        or "yorùbá" in channel_title
    )


def is_yoruba_candidate(audio_language, default_language, title, description, channel_title, query):
    has_yoruba_metadata = any(
        is_yoruba_language_tag(value) for value in (audio_language, default_language)
    )
    has_english_metadata = any(
        is_english_language_tag(value) for value in (audio_language, default_language)
    )
    has_content_evidence = has_yoruba_content_evidence(title, description, channel_title)
    query_is_yoruba_specific = "yoruba" in (query or "").lower()

    if has_yoruba_metadata:
        return True
    if has_english_metadata and not has_content_evidence:
        return False
    return query_is_yoruba_specific and has_content_evidence


def find_compilation_phrase(title_lowered, description_lowered):
    """Returns (scope, phrase) if the title/description matches a known
    compilation/highlights pattern, else None."""
    title_phrase = next((phrase for phrase in COMPILATION_TITLE_PHRASES if phrase in title_lowered), None)
    if title_phrase:
        return "TITLE", title_phrase
    description_phrase = next(
        (phrase for phrase in COMPILATION_DESCRIPTION_PHRASES if phrase in description_lowered), None
    )
    if description_phrase:
        return "DESCRIPTION", description_phrase
    return None


def find_blocked_term(text_lowered):
    return next((term for term in BLOCKED_TERMS if term in text_lowered), None)
