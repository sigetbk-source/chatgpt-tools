from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from docx import Document
from pypdf import PdfReader
from pykakasi import kakasi

from .project import Project


LATIN_TERM = re.compile(r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9.+&_-]{1,}(?:\s+[A-Za-z][A-Za-z0-9.+&_-]+){0,3}")
QUOTED_TERM = re.compile(r"[「“\"]([^」”\"\n]{2,40})[」”\"]")
JAPANESE_PERSON = re.compile(r"(?<![一-龯々])([一-龯々]{2,8})(?:さん|氏)(?![一-龯々])")
ROLE_PERSON = re.compile(
    r"(?:妻|夫|主人|店主|代表|社長)(?:の)?([一-龯々]{2,8})(?:です|と申します|といいます)"
)
READING_PERSON = re.compile(
    r"(?<![一-龯々])([一-龯々]{2,12})\s*[（(]\s*([ぁ-んァ-ヶー・\s]{2,30})\s*[）)](?:さん|氏)?"
)
PERSON_STOPWORDS = {
    "皆", "みな", "お客", "奥", "お母", "お父", "旦那", "スタッフ",
    "店主", "主人", "ご主人", "出演者", "担当者", "生産者", "常連",
    "奥様", "養鶏場", "養鶏所", "卵屋", "玉子",
}


def build_references(project: Project) -> dict[str, Any]:
    sources = []
    terms = set(project.glossary_terms)
    terms.update(project.glossary_aliases)
    terms.update(project.cast.values())

    for path in project.reference_documents:
        text = extract_document_text(path)
        sources.append({"path": str(path), "name": path.name, "text": text})
        terms.update(extract_terms(text))

    person_entities, person_aliases = build_person_dictionary(
        sources,
        project.cast.values(),
    )
    terms.update(entity["canonical"] for entity in person_entities)

    cleaned = sorted(
        {normalize_term(term) for term in terms if is_useful_term(normalize_term(term))},
        key=lambda item: (item.casefold(), len(item)),
    )
    result = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "project_name": project.project_name,
        "terms": cleaned,
        "person_entities": person_entities,
        "person_aliases": person_aliases,
        "sources": sources,
    }
    project.analysis_dir.mkdir(parents=True, exist_ok=True)
    project.references_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def extract_document_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        document = Document(path)
        blocks = [paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()]
        for table in document.tables:
            for row in table.rows:
                blocks.append("\t".join(cell.text for cell in row.cells if cell.text.strip()))
        return "\n".join(blocks)
    if suffix == ".pdf":
        reader = PdfReader(path)
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    return path.read_text(encoding="utf-8", errors="replace")


def extract_terms(text: str) -> set[str]:
    terms = set(LATIN_TERM.findall(text))
    terms.update(
        normalize_term(match.group(1))
        for match in QUOTED_TERM.finditer(text)
        if is_named_quoted_term(normalize_term(match.group(1)))
    )
    terms.update(extract_person_names(text))
    return terms


def is_named_quoted_term(value: str) -> bool:
    """Keep quoted names/titles, not ordinary scripted dialogue.

    Japanese scripts quote both proper names and complete spoken lines. Treating
    every quotation as a dictionary term made greetings such as ``こんにちは``
    look like reference-backed corrections.  Hiragana-only utterances and
    sentence-like quotations are therefore excluded; distinctive Latin,
    numeric, katakana, or kanji-only labels remain eligible.
    """
    if not is_useful_term(value) or re.search(r"[。！？!?\n]", value):
        return False
    compact = re.sub(r"\s+", "", value)
    if re.fullmatch(r"[ぁ-んー]+", compact):
        return False
    if re.search(r"[A-Za-z0-9ァ-ヶ]", compact):
        return True
    return bool(re.fullmatch(r"[一-龯々・]+", compact))


def extract_person_names(text: str) -> set[str]:
    names = set(JAPANESE_PERSON.findall(text))
    names.update(ROLE_PERSON.findall(text))
    names.update(match.group(1) for match in READING_PERSON.finditer(text))
    return {
        normalize_term(name) for name in names
        if normalize_term(name) not in PERSON_STOPWORDS
    }


def reading_forms(reading: str) -> tuple[str, ...]:
    compact = re.sub(r"[\s・]+", "", reading)
    hira = "".join(
        chr(ord(character) - 0x60) if "ァ" <= character <= "ヶ" else character
        for character in compact
    )
    kata = "".join(
        chr(ord(character) + 0x60) if "ぁ" <= character <= "ゖ" else character
        for character in hira
    )
    return tuple(dict.fromkeys(value for value in (hira, kata) if value))


def extract_explicit_person_readings(text: str) -> list[tuple[str, tuple[str, ...]]]:
    return [
        (normalize_term(match.group(1)), reading_forms(match.group(2)))
        for match in READING_PERSON.finditer(text)
    ]


def is_primary_person_source(source: dict[str, str]) -> bool:
    path = str(source.get("path", ""))
    name = str(source.get("name", ""))
    return "Premiere文字起こしJSON" not in path and "修正版候補" not in name


def person_readings(name: str) -> tuple[str, ...]:
    converted = kakasi().convert(name)
    readings = {
        "".join(item.get("hira", item.get("orig", "")) for item in converted),
        "".join(item.get("kana", item.get("orig", "")) for item in converted),
    }
    return tuple(sorted(reading for reading in readings if reading and reading != name))


def build_person_dictionary(
    sources: list[dict[str, str]],
    cast_names=(),
) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    """Build the highest-priority reference dictionary: people and their readings."""
    counts: Counter[str] = Counter()
    evidence: defaultdict[str, set[str]] = defaultdict(set)
    explicit_readings: defaultdict[str, set[str]] = defaultdict(set)
    for source in sources:
        if not is_primary_person_source(source):
            continue
        for name in extract_person_names(source.get("text", "")):
            counts[name] += 1
            evidence[name].add(source.get("name", ""))
        for name, readings in extract_explicit_person_readings(source.get("text", "")):
            counts[name] += 1
            explicit_readings[name].update(readings)
            evidence[name].add(source.get("name", ""))
    for name in cast_names:
        canonical = normalize_term(str(name))
        if canonical:
            counts[canonical] += 1
            evidence[canonical].add("出演者情報")

    # If a full name has an explicit reading, use a known surname prefix to derive
    # the given-name reading as well (名取菜穂子・なとりなおこ → 菜穂子・なおこ).
    for full_name, full_readings in list(explicit_readings.items()):
        full_hira = next((value for value in full_readings if re.search(r"[ぁ-ん]", value)), "")
        if not full_hira:
            continue
        for prefix in sorted(counts, key=len, reverse=True):
            if prefix == full_name or not full_name.startswith(prefix):
                continue
            prefix_hira = next((value for value in person_readings(prefix) if re.search(r"[ぁ-ん]", value)), "")
            if not prefix_hira or not full_hira.startswith(prefix_hira):
                continue
            given_name = full_name[len(prefix):]
            given_reading = full_hira[len(prefix_hira):]
            if given_name and given_reading:
                counts[given_name] += counts[full_name]
                explicit_readings[given_name].update(reading_forms(given_reading))
                evidence[given_name].update(evidence[full_name])
            break

    reading_owners: defaultdict[str, set[str]] = defaultdict(set)
    explicit_owners: defaultdict[str, set[str]] = defaultdict(set)
    readings_by_name: dict[str, tuple[str, ...]] = {}
    for name in counts:
        readings_by_name[name] = tuple(sorted(explicit_readings[name])) if explicit_readings[name] else person_readings(name)
        for reading in readings_by_name[name]:
            reading_owners[reading].add(name)
            if explicit_readings[name]:
                explicit_owners[reading].add(name)

    aliases = {
        name: [
            reading for reading in readings_by_name[name]
            if len(explicit_owners[reading] or reading_owners[reading]) == 1
            and name in (explicit_owners[reading] or reading_owners[reading])
        ]
        for name in counts
    }
    aliases = {name: values for name, values in aliases.items() if values}
    entities = [
        {
            "canonical": name,
            "readings": list(readings_by_name[name]),
            "occurrences": counts[name],
            "sources": sorted(value for value in evidence[name] if value)[:10],
            "priority": "person",
            "reading_source": "explicit" if explicit_readings[name] else "inferred",
        }
        for name in sorted(counts, key=lambda item: (-counts[item], -len(item), item))
    ]
    return entities, aliases


def normalize_term(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" \t\r\n・、。:：()（）")


def is_useful_term(value: str) -> bool:
    if not 2 <= len(value) <= 40:
        return False
    if value.casefold() in {"the", "and", "for", "with", "from", "http", "https", "www"}:
        return False
    return any(character.isalpha() or ord(character) > 127 for character in value)
