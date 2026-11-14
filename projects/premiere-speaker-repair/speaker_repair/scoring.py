from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np


def normalize(vector: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(vector)
    return vector / norm if norm else vector


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(normalize(a), normalize(b)))


def robust_centroid(vectors: list[np.ndarray]) -> np.ndarray:
    if not vectors:
        raise ValueError("基準音声がありません")
    matrix = np.stack([normalize(item) for item in vectors])
    initial = normalize(np.mean(matrix, axis=0))
    if len(matrix) < 4:
        return initial
    similarities = matrix @ initial
    cutoff = np.quantile(similarities, 0.2)
    kept = matrix[similarities >= cutoff]
    return normalize(np.mean(kept, axis=0))


def score_chunks(
    chunks: list[dict[str, Any]], known_speakers: list[str]
) -> dict[str, Any]:
    seeds: dict[str, list[np.ndarray]] = defaultdict(list)
    for chunk in chunks:
        if chunk.get("locked") and chunk["speaker_name"] in known_speakers:
            seeds[chunk["speaker_name"]].append(np.asarray(chunk["embedding"]))

    centroids = {
        name: robust_centroid(seeds[name]) for name in known_speakers if seeds.get(name)
    }
    scored: list[dict[str, Any]] = []
    for chunk in chunks:
        embedding = np.asarray(chunk["embedding"])
        scores = sorted(
            ((name, cosine(embedding, center)) for name, center in centroids.items()),
            key=lambda item: item[1],
            reverse=True,
        )
        best_name, best_score = scores[0] if scores else (None, None)
        second_score = scores[1][1] if len(scores) > 1 else None
        margin = best_score - second_score if second_score is not None else None
        scored.append(
            {
                **{key: value for key, value in chunk.items() if key != "embedding"},
                "candidate": best_name,
                "similarity": best_score,
                "margin": margin,
                "scores": {name: score for name, score in scores},
            }
        )

    return {
        "known_speakers": list(centroids),
        "seed_counts": {name: len(seeds[name]) for name in centroids},
        "chunks": scored,
        "segment_suggestions": aggregate_segments(scored),
        "label_diagnostics": diagnose_labels(scored),
    }


def aggregate_segments(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for chunk in chunks:
        grouped[int(chunk["segment_index"])].append(chunk)
    suggestions = []
    for segment_index, items in sorted(grouped.items()):
        candidate_scores: dict[str, list[float]] = defaultdict(list)
        for item in items:
            if item.get("candidate") is not None:
                candidate_scores[item["candidate"]].append(float(item["similarity"]))
        ranked = sorted(
            ((name, len(scores), float(np.median(scores))) for name, scores in candidate_scores.items()),
            key=lambda item: (item[1], item[2]),
            reverse=True,
        )
        best_name, best_count, best_similarity = ranked[0] if ranked else (None, 0, None)
        share = best_count / len(items) if items else 0.0
        if best_similarity is not None and best_similarity >= 0.55 and share == 1.0:
            confidence = "strong"
        elif best_similarity is not None and best_similarity >= 0.35 and share >= 0.75:
            confidence = "review"
        else:
            confidence = "uncertain"
        suggestions.append(
            {
                "segment_index": segment_index,
                "speaker_id": items[0]["speaker_id"],
                "original_speaker": items[0]["speaker_name"],
                "locked": bool(items[0]["locked"]),
                "candidate": best_name,
                "similarity": best_similarity,
                "candidate_share": share,
                "confidence": confidence,
                "chunk_candidates": [item.get("candidate") for item in items],
                "possible_internal_change": len({item.get("candidate") for item in items}) > 1,
            }
        )
    return suggestions


def diagnose_labels(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for chunk in chunks:
        grouped[chunk["speaker_id"]].append(chunk)
    output = []
    for speaker_id, items in grouped.items():
        votes = CounterLike(item.get("candidate") for item in items if item.get("candidate"))
        dominant_name, dominant_count = max(votes.items(), key=lambda item: item[1]) if votes else (None, 0)
        dominant_share = dominant_count / sum(votes.values()) if votes else 0.0
        output.append(
            {
                "speaker_id": speaker_id,
                "speaker_name": items[0]["speaker_name"],
                "chunk_count": len(items),
                "candidate_votes": votes,
                "dominant_candidate": dominant_name,
                "dominant_share": dominant_share,
                "outlier_count": sum(votes.values()) - dominant_count,
                "possible_mixture": len(votes) > 1 and dominant_share < 0.98,
            }
        )
    return output


def CounterLike(values):
    result: dict[str, int] = {}
    for value in values:
        result[value] = result.get(value, 0) + 1
    return result
