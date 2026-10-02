"""Movie-candidate content classification shared by every discovery provider.

Ported verbatim from the original YouTube-only logic in discovery.py so
every provider (YouTube, Internet Archive, and future ones) applies the
exact same movie/language/compilation rules instead of drifting copies.
"""
from .language_profiles import (
    BLOCKED_TERMS, COMPILATION_DESCRIPTION_PHRASES, COMPILATION_TITLE_PHRASES, PROFILES,
)


def _language_base(value):
    return (value or "").strip().lower().replace("_", "-").split("-")[0]


def is_language_tag(language, value):
    """True when a YouTube language tag (yo, yo-NG, yoruba, ...) names `language`."""
    return _language_base(value) in PROFILES[language]["language_tags"]


def is_yoruba_language_tag(value):
    return is_language_tag("yoruba", value)


def is_english_language_tag(value):
    value = (value or "").strip().lower().replace("_", "-")
    return value in {"en", "eng", "english"} or value.startswith("en-")


def has_language_evidence(language, title, description, channel_title):
    """Title / description / channel text that names the language itself."""
    profile = PROFILES[language]
    title = (title or "").lower()
    for noise in profile.get("title_noise", []):
        title = title.replace(noise, " ")
    description = (description or "").lower()
    channel_title = (channel_title or "").lower()
    return (
        any(word in title for word in profile["title_keywords"])
        or any(phrase in description for phrase in profile["description_phrases"])
        or any(word in channel_title for word in profile["channel_keywords"])
    )


def has_yoruba_content_evidence(title, description, channel_title):
    return has_language_evidence("yoruba", title, description, channel_title)


def has_other_language_evidence(language, title, description, channel_title):
    return any(
        has_language_evidence(other, title, description, channel_title)
        for other in PROFILES if other != language
    )


def is_language_candidate(language, audio_language, default_language, title, description, channel_title, query):
    """Shared per-language gate: does this video look like a `language` movie?

    Explicit metadata wins; otherwise the language must be named in the
    title/description/channel and the search itself must have been for that
    language, so generic English/Nollywood results never get in.
    """
    profile = PROFILES[language]
    has_language_metadata = any(is_language_tag(language, v) for v in (audio_language, default_language))
    has_english_metadata = any(is_english_language_tag(v) for v in (audio_language, default_language))
    has_content_evidence = has_language_evidence(language, title, description, channel_title)
    query_is_specific = any(marker in (query or "").lower() for marker in profile["query_markers"])

    if has_language_metadata:
        return True
    if profile.get("reject_other_language") and has_other_language_evidence(
        language, title, description, channel_title
    ):
        return False
    if has_english_metadata and not has_content_evidence:
        return False
    return query_is_specific and has_content_evidence


def is_yoruba_candidate(audio_language, default_language, title, description, channel_title, query):
    return is_language_candidate(
        "yoruba", audio_language, default_language, title, description, channel_title, query
    )


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
