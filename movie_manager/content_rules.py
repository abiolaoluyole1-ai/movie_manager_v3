"""Movie-candidate content classification shared by every discovery provider.

Ported verbatim from the original YouTube-only logic in discovery.py so
every provider (YouTube, Internet Archive, and future ones) applies the
exact same movie/language/compilation rules instead of drifting copies.
"""
import re
from functools import lru_cache

from .language_profiles import (
    BLOCKED_TERMS, COMPILATION_DESCRIPTION_PHRASES, COMPILATION_TITLE_PHRASES,
    DESCRIPTION_NON_MOVIE_PHRASES, DESCRIPTION_TAG_PHRASES, NON_MOVIE_CHANNEL_WORDS,
    NON_MOVIE_FORMAT_TERMS, PROFILES,
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


@lru_cache(maxsize=512)
def _whole_word(term):
    # Not glued to other letters on either side (Unicode-aware, so "igboegwu" does not contain "igbo").
    return re.compile(rf"(?<![^\W\d_]){re.escape(term)}(?![^\W\d_])")


def _mentions(text, terms, whole_word):
    if whole_word:
        return any(_whole_word(term).search(text) for term in terms)
    return any(term in text for term in terms)


def has_language_evidence(language, title, description, channel_title):
    """Title / description / channel text that names the language itself."""
    profile = PROFILES[language]
    whole = bool(profile.get("whole_word_keywords"))
    title = (title or "").lower()
    for noise in profile.get("title_noise", []):
        title = title.replace(noise, " ")
    description = (description or "").lower()
    channel_title = (channel_title or "").lower()
    cast = profile.get("cast_keywords", [])
    return (
        _mentions(title, profile["title_keywords"], whole)
        or _mentions(description, profile["description_phrases"], whole)
        or _mentions(channel_title, profile["channel_keywords"], whole)
        or _mentions(title, cast, True)
        or _mentions(description, cast, True)
        or _mentions(channel_title, cast, True)
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


def _word_pattern(terms):
    # Whole words (plus a plural "s"), longest first, so "clip" no longer fires inside "eclipse".
    alternatives = "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True))
    return re.compile(rf"(?<![a-z0-9])(?:{alternatives})s?(?![a-z0-9])")


_TITLE_BLOCK_RE = _word_pattern(set(BLOCKED_TERMS) | set(NON_MOVIE_FORMAT_TERMS))
_CHANNEL_BLOCK_RE = _word_pattern(NON_MOVIE_CHANNEL_WORDS)
_FULL_MOVIE_RE = re.compile(
    r"(?<![a-z0-9])(?:full|complete|entire)[\s_\-]*(?:length[\s_\-]*)?"
    r"(?:(?:hd|nollywood|nigerian|nigeria|african|epic|igbo|hausa|yoruba|kannywood|new|latest)[\s_\-]*)*"
    r"(?:movie|film|feature)s?(?![a-z0-9])"
    r"|(?<![a-z0-9])feature[\s_\-]+film(?![a-z0-9])"
)
# A 60+ minute video this long is a feature, not a clip, whatever its description boilerplate says.
FEATURE_LENGTH_SECONDS = 80 * 60


def has_full_movie_evidence(title, description, duration_seconds=0):
    """Positive evidence that this is a whole movie: it says so, or it is feature length."""
    if int(duration_seconds or 0) >= FEATURE_LENGTH_SECONDS:
        return True
    return bool(_FULL_MOVIE_RE.search(f"{title or ''} {description or ''}".lower()))


def find_non_movie_evidence(title, description, duration_seconds=0, channel_title=""):
    """Context-aware trailer/clip/interview/review detection.

    Returns ("TITLE", term) or ("DESCRIPTION", phrase), else None.

    * A blocked word in the TITLE is strong evidence and always rejects.
    * One stray word in a long description (channel boilerplate such as "see clips,
      trailers and exclusives") never rejects, and neither does a tag-style list
      ("movie clips, movie trailer, behind the scenes"). The description must
      explicitly say this video is a trailer/clip/interview/etc.
    * Even then, positive full-movie evidence (a "full movie" title or description,
      or a feature-length runtime) overrules it, unless the title is not claiming
      to be a full movie and the description says it more than once.
    * A channel that only posts clips/trailers/highlights makes any such phrase
      count, because the channel metadata supports it.
    """
    title_l = (title or "").lower()
    match = _TITLE_BLOCK_RE.search(title_l)
    if match:
        word = match.group(0)
        if word not in BLOCKED_TERMS and word.endswith("s") and word[:-1] in BLOCKED_TERMS:
            word = word[:-1]
        return "TITLE", word

    description_l = " ".join((description or "").lower().split())
    strong = [phrase for phrase in DESCRIPTION_NON_MOVIE_PHRASES if phrase in description_l]
    weak = [phrase for phrase in DESCRIPTION_TAG_PHRASES if phrase in description_l]
    if not strong and not weak:
        return None
    if _CHANNEL_BLOCK_RE.search((channel_title or "").lower()):
        return "DESCRIPTION", (strong or weak)[0]
    if not strong:
        return None
    positive = has_full_movie_evidence(title, description, duration_seconds)
    title_says_full_movie = bool(_FULL_MOVIE_RE.search(title_l))
    if not positive or (len(strong) >= 2 and not title_says_full_movie):
        return "DESCRIPTION", strong[0]
    return None
