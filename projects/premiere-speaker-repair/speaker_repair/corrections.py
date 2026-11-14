from __future__ import annotations

import csv
import difflib
import json
import re
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from rapidfuzz.distance import Levenshtein
from rapidfuzz.fuzz import partial_ratio

from .project import Project


JAPANESE_SPACE = re.compile(r"(?<=[\u3040-\u30ff\u3400-\u9fff])\s+(?=[\u3040-\u30ff\u3400-\u9fff])")
SENTENCE_ENDINGS = "。！？!?"


def build_correction_candidates(project: Project) -> dict[str, Any]:
    manifest = json.loads(project.manifest_path.read_text(encoding="utf-8"))
    whisper = json.loads(project.whisper_path.read_text(encoding="utf-8"))
    references = json.loads(project.references_path.read_text(encoding="utf-8"))
    words = flatten_words(whisper)
    semantic_texts = semantic_split_whisper(manifest["segments"], words)
    items = []

    for segment in manifest["segments"]:
        start = segment["start_seconds"]
        end = segment["end_seconds"]
        matched = [word for word in words if start <= word_midpoint(word) < end]
        time_whisper_text = normalize_whisper_text("".join(word["word"] for word in matched))
        if project.transcript_alignment == "time":
            whisper_text = time_whisper_text
        else:
            whisper_text = semantic_texts[segment["index"]] or time_whisper_text
        original = segment["text"]
        ratio = difflib.SequenceMatcher(None, normalize_compare(original), normalize_compare(whisper_text)).ratio() if whisper_text else 1.0
        exact_terms = [term for term in references["terms"] if term in original or term in whisper_text]
        possible_terms = find_possible_terms(original + " " + whisper_text, references["terms"])
        merged_text = merge_transcripts_preserving_premiere(original, whisper_text)
        combined_aliases = {
            canonical: tuple(variants)
            for canonical, variants in references.get("person_aliases", {}).items()
        }
        for canonical, variants in project.glossary_aliases.items():
            combined_aliases[canonical] = tuple(dict.fromkeys((*combined_aliases.get(canonical, ()), *variants)))
        suggested_text, applied_terms = canonicalize_terms(merged_text, combined_aliases)
        identity_candidates = find_identity_candidates(
            " ".join((original, whisper_text, suggested_text)), project.entity_hints
        )
        verification_notes = find_verification_notes(
            original, whisper_text, suggested_text, project.verification_hints
        )
        raw_suggested_text = suggested_text
        has_evidence = bool(applied_terms or verification_notes)
        review_worthy = is_review_worthy_change(
            original, suggested_text, has_evidence=has_evidence
        )
        if not review_worthy:
            suggested_text = original
        proposal_ratio = difflib.SequenceMatcher(
            None, normalize_compare(original), normalize_compare(suggested_text)
        ).ratio()
        has_suggestion = review_worthy
        items.append({
            "segment_index": segment["index"],
            "speaker_name": segment["speaker_name"],
            "speaker_id": segment["speaker_id"],
            "premiere_start": segment["premiere_start"],
            "premiere_end": segment["premiere_end"],
            "start_seconds": start,
            "duration_seconds": segment["duration_seconds"],
            "original_text": original,
            "whisper_text": whisper_text,
            "time_whisper_text": time_whisper_text,
            "suggested_text": suggested_text,
            "suppressed_suggestion": raw_suggested_text if not review_worthy and raw_suggested_text != original else "",
            "minor_change_suppressed": bool(not review_worthy and normalize_compare(raw_suggested_text) != normalize_compare(original)),
            "applied_reference_terms": applied_terms,
            "similarity": ratio,
            "whisper_change_score": 1.0 - ratio,
            "change_score": 1.0 - proposal_ratio,
            "exact_reference_terms": exact_terms,
            "possible_reference_terms": possible_terms,
            "identity_candidates": identity_candidates,
            "verification_notes": verification_notes,
            "needs_review": has_suggestion,
        })

    result = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "project_name": project.project_name,
        "items": items,
    }
    project.correction_candidates_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def flatten_words(whisper: dict[str, Any]) -> list[dict[str, Any]]:
    words = []
    for segment in whisper.get("segments", []):
        if not is_reliable_whisper_segment(segment):
            continue
        for word in segment.get("words", []):
            text = word.get("word", word.get("text", ""))
            if text:
                words.append({"word": text, "start": float(word["start"]), "end": float(word["end"])})
    return words


def is_reliable_whisper_segment(segment: dict[str, Any]) -> bool:
    """Reject Whisper windows carrying its standard hallucination warning signals."""
    compression_ratio = segment.get("compression_ratio")
    average_log_probability = segment.get("avg_logprob")
    if compression_ratio is not None and float(compression_ratio) > 2.4:
        return False
    if average_log_probability is not None and float(average_log_probability) < -1.0:
        return False
    text = normalize_compare(str(segment.get("text") or ""))
    if len(text) >= 12:
        most_common_count = Counter(text).most_common(1)[0][1]
        if most_common_count / len(text) >= 0.75:
            return False
    return True


def word_midpoint(word: dict[str, Any]) -> float:
    return (float(word["start"]) + float(word["end"])) / 2


def normalize_whisper_text(text: str) -> str:
    return JAPANESE_SPACE.sub("", re.sub(r"\s+", " ", text)).strip()


def normalize_compare(text: str) -> str:
    return re.sub(r"[\s、。,.!?！？・\"'「」『』（）()]", "", text).casefold()


def normalize_review_compare(text: str) -> str:
    """Normalize punctuation and ordinary spoken contractions for review decisions."""
    normalized = normalize_compare(text)
    return re.sub(r"([てで])い(?=る|ます|た|まし|ない|なかっ)", r"\1", normalized)


def is_review_worthy_change(
    original: str, suggested: str, *, has_evidence: bool = False
) -> bool:
    """Ignore tiny unsupported edits while retaining researched or material-backed fixes."""
    before = normalize_review_compare(original)
    after = normalize_review_compare(suggested)
    if before == after:
        return False
    if has_evidence:
        return True
    distance = Levenshtein.distance(before, after)
    length = max(len(before), len(after), 1)
    if any(after == before + particle for particle in ("ね", "よ", "か", "な")):
        return True
    if length <= 20 and distance <= 1:
        return False
    return True


def build_change_highlights(
    original: str,
    whisper_text: str,
    suggested_text: str,
    applied_reference_terms: list[dict[str, str]] | None = None,
    exact_reference_terms: list[str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Return character ranges used to explain a proposed transcript correction.

    Proposal replacements are attributed to reference material when they overlap a
    canonical term replacement; the remaining proposal changes are attributed to
    Whisper.  Source ranges are kept separately so the three-column review UI can
    show what changed without modifying any transcript text or timing.
    """
    applied_reference_terms = applied_reference_terms or []
    exact_reference_terms = [
        term for term in (exact_reference_terms or [])
        if len(normalize_compare(term)) >= 2
    ]
    proposal_changes, premiere_changes = _changed_ranges(original, suggested_text)
    whisper_changes, _ = _changed_ranges(original, whisper_text)

    reference_proposal = _merge_ranges(
        [
            *_replacement_occurrences(
                suggested_text,
                [change.get("to", "") for change in applied_reference_terms],
                proposal_changes,
            ),
            *_replacement_occurrences(
                suggested_text,
                exact_reference_terms,
                proposal_changes,
            ),
        ]
    )
    reference_sources = _merge_ranges(
        [
            *_replacement_occurrences(
                whisper_text,
                [change.get("from", "") for change in applied_reference_terms],
                whisper_changes,
            ),
            *_replacement_occurrences(
                whisper_text,
                exact_reference_terms,
                whisper_changes,
            ),
        ]
    )

    proposal = _split_ranges_by_overlays(
        proposal_changes, reference_proposal, "whisper", "reference"
    )
    whisper_source_forms = [
        suggested_text[item["start"]:item["end"]]
        for item in proposal
        if item["kind"] == "whisper"
    ]
    adopted_whisper_sources = _replacement_occurrences(
        whisper_text, whisper_source_forms, whisper_changes
    )
    whisper = _split_ranges_by_overlays(
        adopted_whisper_sources, reference_sources, "source", "reference-source"
    )
    return {
        "proposal": proposal,
        "premiere": [
            {"start": start, "end": end, "kind": "target"}
            for start, end in premiere_changes
        ],
        "whisper": whisper,
    }


def _changed_ranges(source: str, target: str) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """Return non-empty changed ranges as (target ranges, source ranges)."""
    target_ranges: list[tuple[int, int]] = []
    source_ranges: list[tuple[int, int]] = []
    matcher = difflib.SequenceMatcher(None, source, target, autojunk=False)
    for tag, source_start, source_end, target_start, target_end in matcher.get_opcodes():
        if tag == "equal":
            continue
        if target_start < target_end:
            target_ranges.append((target_start, target_end))
        if source_start < source_end:
            source_ranges.append((source_start, source_end))
    return _merge_ranges(target_ranges), _merge_ranges(source_ranges)


def _replacement_occurrences(
    text: str, forms: list[str], changed_ranges: list[tuple[int, int]]
) -> list[tuple[int, int]]:
    """Find every changed occurrence, while avoiding unchanged glossary repeats."""
    found: list[tuple[int, int]] = []
    for form in dict.fromkeys(form for form in forms if form):
        literal_candidates = [
            (match.start(), match.end())
            for match in re.finditer(re.escape(form), text, re.IGNORECASE if re.search(r"[A-Za-z]", form) else 0)
            if any(match.start() < end and start < match.end() for start, end in changed_ranges)
        ]
        normalized_candidates = _normalized_occurrences(text, form, changed_ranges)
        candidates = sorted(set((*literal_candidates, *normalized_candidates)))
        found.extend(candidates)
    return _merge_ranges(found)


def _normalized_occurrences(
    text: str, form: str, changed_ranges: list[tuple[int, int]]
) -> list[tuple[int, int]]:
    """Locate a reference form while ignoring spaces, punctuation and ASCII case."""
    needle = normalize_compare(form)
    if len(needle) < 2:
        return []
    normalized = []
    source_positions: list[tuple[int, int]] = []
    for index, character in enumerate(text):
        for normalized_character in normalize_compare(character):
            normalized.append(normalized_character)
            source_positions.append((index, index + 1))
    haystack = "".join(normalized)
    output = []
    start = 0
    while True:
        match_start = haystack.find(needle, start)
        if match_start < 0:
            break
        match_end = match_start + len(needle)
        source_start = source_positions[match_start][0]
        source_end = source_positions[match_end - 1][1]
        if any(source_start < end and left < source_end for left, end in changed_ranges):
            output.append((source_start, source_end))
        start = match_start + 1
    return output


def _split_ranges_by_overlays(
    base_ranges: list[tuple[int, int]],
    overlay_ranges: list[tuple[int, int]],
    base_kind: str,
    overlay_kind: str,
) -> list[dict[str, Any]]:
    boundaries = sorted({point for span in (*base_ranges, *overlay_ranges) for point in span})
    output: list[dict[str, Any]] = []
    for start, end in zip(boundaries, boundaries[1:]):
        if start == end:
            continue
        in_overlay = any(left <= start and end <= right for left, right in overlay_ranges)
        in_base = any(left <= start and end <= right for left, right in base_ranges)
        if not in_base and not in_overlay:
            continue
        kind = overlay_kind if in_overlay else base_kind
        if output and output[-1]["end"] == start and output[-1]["kind"] == kind:
            output[-1]["end"] = end
        else:
            output.append({"start": start, "end": end, "kind": kind})
    return output


def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    output: list[list[int]] = []
    for start, end in sorted(ranges):
        if start >= end:
            continue
        if output and start <= output[-1][1]:
            output[-1][1] = max(output[-1][1], end)
        else:
            output.append([start, end])
    return [(start, end) for start, end in output]


def merge_transcripts_preserving_premiere(original: str, whisper_text: str) -> str:
    """Correct likely recognition errors without deleting Premiere's spoken structure.

    Premiere-only spans are never removed, while Whisper-only spans are added.
    This keeps false starts, repetitions, and misstatements while recovering words
    that Premiere missed. Short replacements can still benefit from Whisper. When a
    long Premiere-only lead-in shares a boundary with a short Whisper correction,
    only the final likely mistranscribed character is replaced.
    """
    if not whisper_text:
        return original

    output: list[str] = []
    matcher = difflib.SequenceMatcher(None, original, whisper_text, autojunk=False)
    for tag, original_start, original_end, whisper_start, whisper_end in matcher.get_opcodes():
        original_span = original[original_start:original_end]
        whisper_span = whisper_text[whisper_start:whisper_end]
        if tag in {"equal", "delete"}:
            output.append(original_span)
        elif tag == "insert":
            if normalize_compare(whisper_span):
                output.append(whisper_span)
        elif len(original_span) > len(whisper_span) + 6 and whisper_span:
            output.append(original_span[:-1] + whisper_span)
        elif len(original_span) > 16 and len(whisper_span) > 16:
            output.append(original_span)
        else:
            output.append(whisper_span)
    return "".join(output)


def semantic_split_whisper(segments: list[dict[str, Any]], words: list[dict[str, Any]]) -> list[str]:
    premiere_texts = [segment["text"] for segment in segments]
    premiere_full = "".join(premiere_texts)
    whisper_full = "".join(word["word"] for word in words)
    premiere_normalized, _ = normalize_with_boundaries(premiere_full)
    whisper_normalized, whisper_boundaries = normalize_with_boundaries(whisper_full)
    opcodes = list(Levenshtein.opcodes(premiere_normalized, whisper_normalized))

    premiere_segment_boundaries = [0]
    for text in premiere_texts:
        premiere_segment_boundaries.append(
            premiere_segment_boundaries[-1] + len(normalize_compare(text))
        )
    mapped = [map_source_boundary(opcodes, boundary) for boundary in premiere_segment_boundaries]
    mapped[0] = 0
    mapped[-1] = len(whisper_normalized)
    mapped = [max(0, min(value, len(whisper_normalized))) for value in mapped]
    for index in range(1, len(mapped)):
        mapped[index] = max(mapped[index], mapped[index - 1])

    raw_boundaries = [whisper_boundaries[value] for value in mapped]
    for index in range(1, len(raw_boundaries) - 1):
        if premiere_texts[index - 1].rstrip().endswith(tuple(SENTENCE_ENDINGS)):
            raw_boundaries[index] = nearest_sentence_boundary(
                whisper_full, raw_boundaries[index]
            )
    for index in range(1, len(raw_boundaries)):
        raw_boundaries[index] = max(raw_boundaries[index], raw_boundaries[index - 1])

    output = []
    for start, end in zip(raw_boundaries, raw_boundaries[1:]):
        output.append(normalize_whisper_text(whisper_full[start:end]))
    return output


def nearest_sentence_boundary(text: str, position: int, max_distance: int = 32) -> int:
    candidates = [
        index + 1
        for index in range(max(0, position - max_distance), min(len(text), position + max_distance))
        if text[index] in SENTENCE_ENDINGS
    ]
    return min(candidates, key=lambda value: abs(value - position), default=position)


def normalize_with_boundaries(text: str) -> tuple[str, list[int]]:
    kept = []
    boundaries = [0]
    for index, character in enumerate(text):
        normalized = normalize_compare(character)
        if not normalized:
            continue
        kept.append(normalized)
        boundaries.append(index + 1)
    return "".join(kept), boundaries


def map_source_boundary(opcodes, source_index: int) -> int:
    previous_dest = 0
    for opcode in opcodes:
        if opcode.src_start <= source_index <= opcode.src_end and opcode.src_end > opcode.src_start:
            source_span = opcode.src_end - opcode.src_start
            dest_span = opcode.dest_end - opcode.dest_start
            fraction = (source_index - opcode.src_start) / source_span
            return round(opcode.dest_start + fraction * dest_span)
        if source_index < opcode.src_start:
            return previous_dest
        previous_dest = opcode.dest_end
    return previous_dest


def find_possible_terms(text: str, terms: list[str]) -> list[dict[str, Any]]:
    normalized = normalize_compare(text)
    found = []
    for term in terms:
        if len(term) < 4 or term in text:
            continue
        score = partial_ratio(normalize_compare(term), normalized)
        if score >= 84:
            found.append({"term": term, "score": round(score / 100, 2)})
    return sorted(found, key=lambda item: item["score"], reverse=True)[:8]


def canonicalize_terms(text: str, aliases: dict[str, tuple[str, ...]]) -> tuple[str, list[dict[str, str]]]:
    output = text
    applied = []
    replacements = {
        (variant, canonical)
        for canonical, variants in aliases.items()
        for variant in variants
        if variant
    }
    replacements.update(
        (canonical, canonical)
        for canonical in aliases
        if re.search(r"[A-Za-z]", canonical)
    )
    replacements = list(replacements)
    replacements.sort(key=lambda item: len(item[0]), reverse=True)
    for variant, canonical in replacements:
        flags = re.IGNORECASE if re.search(r"[A-Za-z]", variant) else 0
        escaped = re.escape(variant)
        if flags:
            escaped = rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])"
        pattern = re.compile(escaped, flags)
        matched_forms: list[str] = []
        def replace_match(match):
            if match.group(0) != canonical:
                matched_forms.append(match.group(0))
            return canonical

        output = pattern.sub(replace_match, output)
        applied.extend({"from": form, "to": canonical} for form in matched_forms)
    return output.strip(), applied


def find_identity_candidates(text: str, hints: tuple[dict, ...]) -> list[dict[str, Any]]:
    """Attach researched identity hints without adding unspoken names to the transcript."""
    matches = []
    for hint in hints:
        mentions = hint.get("mentions", [])
        matched = next((mention for mention in mentions if mention and mention in text), None)
        if not matched:
            continue
        matches.append({
            "mention": matched,
            "category": hint.get("category", "固有名詞"),
            "candidate": hint["candidate"],
            "confidence": hint.get("confidence", "確認"),
            "reason": hint.get("reason", ""),
            "sources": hint.get("sources", []),
            "transcript_policy": hint.get(
                "transcript_policy", "発言にない情報は本文へ追加しない"
            ),
        })
    return matches


def find_verification_notes(
    original: str, whisper_text: str, suggested_text: str, hints: tuple[dict, ...]
) -> list[dict[str, Any]]:
    """Record provenance and a researched keep/change judgment separately from text edits."""
    notes = []
    for hint in hints:
        term = hint.get("term", "")
        if not term or term not in suggested_text:
            continue
        origins = []
        if term in whisper_text and term not in original:
            origins.append("Whisper補完")
        if term in original:
            origins.append("Premiere記載")
        notes.append({
            "term": term,
            "origins": origins,
            "status": hint.get("status", "検証済み"),
            "judgment": hint.get("judgment", "表記を維持"),
            "reason": hint.get("reason", ""),
            "sources": hint.get("sources", []),
        })
    return notes


def canonicalize_saved_decisions(project: Project) -> dict[str, int]:
    if not project.correction_decisions_path.exists():
        return {"updated": 0}
    decisions = json.loads(project.correction_decisions_path.read_text(encoding="utf-8"))
    updated = 0
    for decision in decisions.values():
        text = decision.get("text")
        if not isinstance(text, str):
            continue
        normalized, _ = canonicalize_terms(text, project.glossary_aliases)
        if normalized != text:
            decision["text"] = normalized
            updated += 1
    project.correction_decisions_path.write_text(
        json.dumps(decisions, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"updated": updated}


def build_term_audit(project: Project) -> dict[str, Any]:
    candidates = json.loads(project.correction_candidates_path.read_text(encoding="utf-8"))
    items = candidates["items"]
    fields = ("original_text", "whisper_text", "suggested_text")
    canonicals = list(dict.fromkeys((*project.glossary_terms, *project.glossary_aliases.keys())))
    applied_counts = Counter(
        (change["to"], change["from"])
        for item in items
        for change in item.get("applied_reference_terms", [])
    )

    terms = []
    for canonical in canonicals:
        variants = project.glossary_aliases.get(canonical, ())
        terms.append({
            "canonical": canonical,
            "aliases": list(variants),
            "occurrences": {
                field: sum(item.get(field, "").count(canonical) for item in items)
                for field in fields
            },
            "alias_corrections": {
                variant: applied_counts[(canonical, variant)]
                for variant in variants
            },
        })

    candidate_counts = Counter()
    candidate_examples: dict[str, list[int]] = {}
    registered_forms = {
        normalize_compare(form)
        for canonical in canonicals
        for form in (canonical, *project.glossary_aliases.get(canonical, ()))
    }
    for item in items:
        for candidate in item.get("possible_reference_terms", []):
            if candidate["score"] < 0.9:
                continue
            term = candidate["term"]
            if normalize_compare(term) in registered_forms:
                continue
            candidate_counts[term] += 1
            candidate_examples.setdefault(term, []).append(item["segment_index"])

    result = {
        "schema_version": 1,
        "project_name": project.project_name,
        "terms": terms,
        "unresolved_candidates": [
            {
                "term": term,
                "count": count,
                "segment_indices": candidate_examples[term][:10],
            }
            for term, count in candidate_counts.most_common()
        ],
    }
    project.term_audit_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def export_corrected_csv(project: Project) -> dict[str, str]:
    base_candidates = json.loads(project.correction_candidates_path.read_text(encoding="utf-8"))
    from .segment_edits import load_edit_state, validate_structural_items
    edit_state = load_edit_state(project, base_candidates)
    ordered_items = sorted(
        edit_state["items"],
        key=lambda value: (float(value["start_seconds"]), int(value["segment_index"])),
    )
    candidates = {**base_candidates, "items": ordered_items}
    decisions = json.loads(project.correction_decisions_path.read_text(encoding="utf-8")) if project.correction_decisions_path.exists() else {}
    output_csv = project.analysis_dir / f"{project.transcript_json.stem}_修正版.csv"
    output_patch = project.analysis_dir / f"{project.transcript_json.stem}_修正パッチ.json"
    source_txt = project.transcript_json.with_suffix(".txt")
    output_txt = source_txt.with_name(f"{source_txt.stem}_修正版.txt")
    output_json = project.transcript_json.with_name(
        f"{project.transcript_json.stem}_修正版.json"
    )
    patches = []
    speaker_patches = []
    corrected_texts = []
    corrected_speakers = []
    with output_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["Speaker Name", "Start Time", "End Time", "Text"])
        writer.writeheader()
        for item in candidates["items"]:
            decision = decisions.get(str(item["segment_index"]), {})
            text = decision.get("text", item["original_text"]) if decision.get("status") == "approved" else item["original_text"]
            speaker_name = decision.get("speaker_name", item["speaker_name"])
            corrected_texts.append(text)
            corrected_speakers.append(speaker_name)
            writer.writerow({
                "Speaker Name": speaker_name,
                "Start Time": item["premiere_start"],
                "End Time": item["premiere_end"],
                "Text": text,
            })
            if text != item["original_text"]:
                patches.append({"segment_index": item["segment_index"], "before": item["original_text"], "after": text})
            if speaker_name != item["speaker_name"]:
                speaker_patches.append({
                    "segment_index": item["segment_index"],
                    "before": item["speaker_name"],
                    "after": speaker_name,
                })
    structural_changed = (
        len(ordered_items) != len(base_candidates.get("items", []))
        or any(
            item.get("segment_uid") != f"source-{item.get('segment_index')}"
            for item in ordered_items
        )
    )
    change_count = len(patches) + len(speaker_patches) + int(structural_changed)
    txt_result = {}
    try:
        output_txt.write_text(
            build_structural_transcript_text(candidates["items"], corrected_texts, corrected_speakers),
            encoding="utf-8",
        )
        txt_result["txt"] = str(output_txt)
    except (OSError, ValueError) as error:
        txt_result["txt_warning"] = str(error)
    json_result = {}
    try:
        transcript = json.loads(project.transcript_json.read_text(encoding="utf-8-sig"))
        validate_structural_items(transcript, candidates["items"])
        corrected_transcript = build_structurally_corrected_transcript_json(
            transcript, candidates["items"], corrected_texts, corrected_speakers
        )
        output_json.write_text(
            json.dumps(corrected_transcript, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        json_result["json"] = str(output_json)
    except ValueError as error:
        json_result["json_warning"] = str(error)
    output_patch.write_text(
        json.dumps(
            {
                "patches": patches,
                "speaker_patches": speaker_patches,
                "structural_changed": structural_changed,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    if json_result.get("json"):
        project.writeback_ready_path.write_text(
            json.dumps(
                {
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "corrected_json": json_result["json"],
                    "patch_count": change_count,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    return {
        "csv": str(output_csv),
        "patch": str(output_patch),
        "patch_count": str(change_count),
        **txt_result,
        **json_result,
    }


def build_structural_transcript_text(
    items: list[dict[str, Any]], corrected_texts: list[str], corrected_speakers: list[str]
) -> str:
    if not (len(items) == len(corrected_texts) == len(corrected_speakers)):
        raise ValueError("区間・本文・話者の件数が一致しません")
    blocks = []
    for item, text, speaker in zip(items, corrected_texts, corrected_speakers, strict=True):
        blocks.append("\n".join((
            f"{item['premiere_start']} - {item['premiere_end']}",
            speaker,
            text,
        )))
    return "\n\n".join(blocks) + "\n"


def build_structurally_corrected_transcript_json(
    transcript: dict[str, Any],
    items: list[dict[str, Any]],
    corrected_texts: list[str],
    corrected_speakers: list[str],
) -> dict[str, Any]:
    """Compile an edited segment topology without changing absolute word timing."""
    if not (len(items) == len(corrected_texts) == len(corrected_speakers)):
        raise ValueError("区間・本文・話者の件数が一致しません")
    output = json.loads(json.dumps(transcript, ensure_ascii=False))
    speaker_ids = {
        speaker.get("name"): speaker.get("id")
        for speaker in output.get("speakers", [])
        if speaker.get("name") and speaker.get("id")
    }
    compiled_segments = []
    ordered_rows = sorted(
        zip(items, corrected_texts, corrected_speakers, strict=True),
        key=lambda row: (float(row[0]["start_seconds"]), int(row[0]["segment_index"])),
    )
    for item, corrected_text, speaker_name in ordered_rows:
        words = json.loads(json.dumps(item.get("_source_words", []), ensure_ascii=False))
        if not words:
            raise ValueError(f"区間{item['segment_index']}に単語時刻情報がありません")
        original_text = "".join(str(word.get("text", "")) for word in words)
        if original_text != item.get("original_text", ""):
            raise ValueError(f"区間{item['segment_index']}の本文と単語情報が一致しません")
        if corrected_text != original_text:
            words = redistribute_corrected_text(words, original_text, corrected_text)
        segment = json.loads(json.dumps(item.get("_segment_template", {}), ensure_ascii=False))
        segment["start"] = float(item["start_seconds"])
        segment["duration"] = float(item["duration_seconds"])
        if speaker_name not in speaker_ids:
            speaker_id = str(uuid.uuid4())
            output.setdefault("speakers", []).append({"id": speaker_id, "name": speaker_name})
            speaker_ids[speaker_name] = speaker_id
        segment["speaker"] = speaker_ids[speaker_name]
        segment["words"] = words
        compiled_segments.append(segment)
    output["segments"] = compiled_segments
    return output


def build_corrected_transcript_text(source_text: str, corrected_texts: list[str]) -> str:
    blocks = re.split(r"\n{2,}", source_text.strip())
    if len(blocks) != len(corrected_texts):
        raise ValueError(
            f"TXT区間数({len(blocks)})と修正区間数({len(corrected_texts)})が一致しません"
        )

    output = []
    for index, (block, corrected_text) in enumerate(zip(blocks, corrected_texts, strict=True)):
        lines = block.splitlines()
        if len(lines) < 3:
            raise ValueError(f"TXTの区間{index}に必要な3行がありません")
        output.append("\n".join((lines[0], lines[1], corrected_text)))
    return "\n\n".join(output) + "\n"


def migrate_approved_decisions(source: Project, target: Project) -> dict[str, int]:
    """Carry approved edits to a new source-clip project only on an exact unique match."""
    source_candidates = json.loads(
        source.correction_candidates_path.read_text(encoding="utf-8")
    )["items"]
    source_decisions = json.loads(
        source.correction_decisions_path.read_text(encoding="utf-8")
    )
    target_candidates = json.loads(
        target.correction_candidates_path.read_text(encoding="utf-8")
    )["items"]
    target_decisions = (
        json.loads(target.correction_decisions_path.read_text(encoding="utf-8"))
        if target.correction_decisions_path.exists()
        else {}
    )

    matches: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in target_candidates:
        matches.setdefault((item["speaker_name"], item["original_text"]), []).append(item)

    migrated = ambiguous = missing = 0
    for item in source_candidates:
        decision = source_decisions.get(str(item["segment_index"]), {})
        if decision.get("status") != "approved":
            continue
        candidates = matches.get((item["speaker_name"], item["original_text"]), [])
        if not candidates:
            missing += 1
            continue
        if len(candidates) != 1:
            ambiguous += 1
            continue
        target_index = str(candidates[0]["segment_index"])
        if target_index in target_decisions:
            continue
        target_decisions[target_index] = {
            **decision,
            "segment_index": candidates[0]["segment_index"],
            "migrated_from": source.project_name,
        }
        migrated += 1

    target.correction_decisions_path.write_text(
        json.dumps(target_decisions, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"migrated": migrated, "ambiguous": ambiguous, "missing": missing}


def restore_original_selection(
    original_text: str, current_text: str, selection_start: int, selection_end: int
) -> dict[str, Any]:
    """Restore an original-text selection into its aligned range in an edited proposal."""
    start = max(0, min(int(selection_start), len(original_text)))
    end = max(start, min(int(selection_end), len(original_text)))
    if start == end:
        raise ValueError("選択範囲が空です")
    opcodes = difflib.SequenceMatcher(
        None, original_text, current_text, autojunk=False
    ).get_opcodes()
    mapped_start = _map_difflib_boundary(opcodes, start, prefer_after_insert=True)
    mapped_end = _map_difflib_boundary(opcodes, end, prefer_after_insert=False)
    mapped_start = max(0, min(mapped_start, len(current_text)))
    mapped_end = max(mapped_start, min(mapped_end, len(current_text)))
    selected_text = original_text[start:end]
    return {
        "text": current_text[:mapped_start] + selected_text + current_text[mapped_end:],
        "selected_text": selected_text,
        "proposal_start": mapped_start,
        "proposal_end": mapped_end,
    }


def _map_difflib_boundary(opcodes, position: int, prefer_after_insert: bool) -> int:
    insertions = [
        opcode
        for opcode in opcodes
        if opcode[0] == "insert" and opcode[1] == opcode[2] == position
    ]
    if insertions:
        return (
            max(opcode[4] for opcode in insertions)
            if prefer_after_insert
            else min(opcode[3] for opcode in insertions)
        )
    previous_destination = 0
    for tag, source_start, source_end, destination_start, destination_end in opcodes:
        if source_start == source_end == position and tag == "insert":
            return destination_end if prefer_after_insert else destination_start
        if source_start <= position <= source_end and source_end > source_start:
            if tag == "equal":
                return destination_start + (position - source_start)
            fraction = (position - source_start) / (source_end - source_start)
            return round(destination_start + fraction * (destination_end - destination_start))
        if position < source_start:
            return previous_destination
        previous_destination = destination_end
    return previous_destination


def build_corrected_transcript_json(
    transcript: dict[str, Any], corrected_texts: list[str], corrected_speakers: list[str] | None = None
) -> dict[str, Any]:
    """Replace transcript text while retaining speakers and word timing metadata."""
    segments = transcript.get("segments", [])
    if len(segments) != len(corrected_texts):
        raise ValueError(
            f"JSON区間数({len(segments)})と修正区間数({len(corrected_texts)})が一致しません"
        )

    # Work on a detached copy so the source export remains untouched.
    output = json.loads(json.dumps(transcript, ensure_ascii=False))
    speaker_ids = {
        speaker.get("name"): speaker.get("id")
        for speaker in output.get("speakers", [])
        if speaker.get("name") and speaker.get("id")
    }
    if corrected_speakers is not None and len(corrected_speakers) != len(segments):
        raise ValueError("話者名数とJSON区間数が一致しません")
    for index, (segment, corrected_text) in enumerate(
        zip(output["segments"], corrected_texts, strict=True)
    ):
        if corrected_speakers is not None:
            speaker_name = corrected_speakers[index].strip()
            if speaker_name:
                if speaker_name not in speaker_ids:
                    speaker_id = str(uuid.uuid4())
                    output.setdefault("speakers", []).append({"id": speaker_id, "name": speaker_name})
                    speaker_ids[speaker_name] = speaker_id
                segment["speaker"] = speaker_ids[speaker_name]
        words = segment.get("words", [])
        original_text = "".join(word.get("text", "") for word in words)
        if original_text == corrected_text:
            continue
        if not words:
            raise ValueError(f"JSONの区間{index}に単語時刻情報がありません")
        segment["words"] = redistribute_corrected_text(words, original_text, corrected_text)
    return output


def redistribute_corrected_text(
    words: list[dict[str, Any]], original_text: str, corrected_text: str
) -> list[dict[str, Any]]:
    """Apply character edits to the nearest original word without changing its timing."""
    owners: list[int] = []
    for word_index, word in enumerate(words):
        owners.extend([word_index] * len(word.get("text", "")))

    rebuilt = [""] * len(words)
    matcher = difflib.SequenceMatcher(None, original_text, corrected_text, autojunk=False)
    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        replacement = corrected_text[new_start:new_end]
        if tag == "equal":
            for offset, character in enumerate(replacement):
                rebuilt[owners[old_start + offset]] += character
        elif tag in {"replace", "insert"} and replacement:
            if old_start < len(owners):
                owner = owners[old_start]
            elif owners:
                owner = owners[-1]
            else:
                owner = 0
            rebuilt[owner] += replacement

    result = []
    for word, text in zip(words, rebuilt, strict=True):
        updated = dict(word)
        updated["text"] = text
        result.append(updated)
    if "eos" in result[-1] and "eos" in words[-1]:
        result[-1]["eos"] = words[-1]["eos"]
    if "duration" in result[-1]:
        segment_end = float(words[-1].get("start", 0)) + float(words[-1].get("duration", 0))
        result[-1]["duration"] = max(
            float(result[-1].get("duration", 0)),
            segment_end - float(result[-1].get("start", 0)),
        )
    if "".join(word.get("text", "") for word in result) != corrected_text:
        raise ValueError("修正文のJSON再配置に失敗しました")
    return result
