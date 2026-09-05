"""Safe shared project language preference handling."""

from __future__ import annotations

import json
import re
from typing import Any


LANGUAGE_TAG = re.compile(r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8}){0,7}$")
MAX_LANGUAGE_LENGTH = 35


def validate_language_tag(value: str) -> str:
    if not isinstance(value, str) or len(value) > MAX_LANGUAGE_LENGTH or not LANGUAGE_TAG.fullmatch(value):
        raise ValueError("invalid language tag; use a BCP-47-style tag such as vi, en, ja, or pt-BR")
    parts = value.split("-")
    normalized = [parts[0].lower()]
    for part in parts[1:]:
        if len(part) == 4 and part.isalpha():
            normalized.append(part.title())
        elif (len(part) == 2 and part.isalpha()) or (
            len(part) == 3 and part.isdigit()
        ):
            normalized.append(part.upper())
        else:
            normalized.append(part.lower())
    return "-".join(normalized)


def load_preferences(path) -> dict[str, Any]:
    if path.is_symlink():
        raise ValueError("preferences path must be a regular file")
    if not path.exists():
        return {}
    if not path.is_file():
        raise ValueError("preferences path must be a regular file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"malformed preferences file: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("preferences must be a JSON object")
    language = value.get("language", {})
    if not isinstance(language, dict):
        raise ValueError("preferences.language must be an object")
    for key in ("responses", "generated_documents"):
        if key in language:
            validate_language_tag(language[key])
    if "preserve_existing_document_language" in language and not isinstance(language["preserve_existing_document_language"], bool):
        raise ValueError("preferences.language.preserve_existing_document_language must be boolean")
    return value


def with_language(preferences: dict[str, Any], language: str) -> dict[str, Any]:
    language = validate_language_tag(language)
    result = dict(preferences)
    section = dict(result.get("language") or {})
    section["responses"] = language
    section["generated_documents"] = language
    section.setdefault("preserve_existing_document_language", True)
    result["language"] = section
    return result


def serialize(preferences: dict[str, Any]) -> str:
    return json.dumps(preferences, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
