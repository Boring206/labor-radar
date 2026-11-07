"""String catalogs. zh-TW is the reference; other languages fall back to it key by key."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

REFERENCE = "zh-TW"
DRAFT = "machine_translation_draft"
_DIR = Path(__file__).parent
CATALOGS: dict[str, dict[str, str]] = {
    path.stem: json.loads(path.read_text(encoding="utf-8")) for path in sorted(_DIR.glob("*.json"))
}


def resolve(lang: str) -> str:
    return lang if lang in CATALOGS else REFERENCE


def is_draft(lang: str) -> bool:
    return CATALOGS[resolve(lang)]["_meta.status"] == DRAFT


def languages() -> list[dict[str, object]]:
    codes = sorted(CATALOGS, key=lambda code: (code != REFERENCE, code))
    return [{"code": code, "name": CATALOGS[code]["_meta.name"], "draft": is_draft(code)} for code in codes]


@dataclass(frozen=True)
class Text:
    """A parameter that is itself a catalog key, translated along with the sentence it fills."""

    key: str


def fill(lang: str, template: str, params: Mapping[str, object]) -> str:
    return template.format_map({k: t(lang, v.key) if isinstance(v, Text) else v for k, v in params.items()})


def t(lang: str, key: str, **params: object) -> str:
    return fill(lang, CATALOGS[resolve(lang)].get(key) or CATALOGS[REFERENCE][key], params)
