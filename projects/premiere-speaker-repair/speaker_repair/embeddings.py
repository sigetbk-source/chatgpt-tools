from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import numpy as np

from .audio import WaveReader, chunk_ranges
from .project import Project
from .scoring import score_chunks


MODEL_SOURCE = "speechbrain/spkrec-ecapa-voxceleb"


def rescore(project: Project) -> dict[str, Any]:
    cached = np.load(project.embeddings_path, allow_pickle=False)
    chunks = []
    for metadata, embedding in zip(cached["metadata"], cached["embeddings"], strict=True):
        chunks.append({**json.loads(str(metadata)), "embedding": embedding})
    result = score_chunks(chunks, list(project.known_speakers))
    return _write_result(project, result)


def analyze(project: Project) -> dict[str, Any]:
    try:
        import torch
        from speechbrain.inference.classifiers import EncoderClassifier
    except ImportError as exc:
        raise RuntimeError(
            "音声モデルが未導入です。プロジェクトの依存関係をインストールしてください。"
        ) from exc

    manifest = json.loads(project.manifest_path.read_text(encoding="utf-8"))
    reader = WaveReader(project.wav_path)
    model_dir = project.analysis_dir / "models" / "spkrec-ecapa-voxceleb"
    classifier = EncoderClassifier.from_hparams(
        source=MODEL_SOURCE,
        savedir=str(model_dir),
        run_opts={"device": "cpu"},
    )

    chunks: list[dict[str, Any]] = []
    batch_samples = []
    batch_metadata: list[dict[str, Any]] = []

    def flush_batch() -> None:
        if not batch_samples:
            return
        lengths = torch.tensor([len(item) for item in batch_samples], dtype=torch.float32)
        max_length = int(lengths.max().item())
        padded = torch.zeros((len(batch_samples), max_length), dtype=torch.float32)
        for row, samples in enumerate(batch_samples):
            padded[row, : len(samples)] = torch.from_numpy(samples)
        relative_lengths = lengths / max_length
        with torch.no_grad():
            encoded = classifier.encode_batch(padded, wav_lens=relative_lengths)
        vectors = encoded.squeeze(1).cpu().numpy()
        for metadata, vector in zip(batch_metadata, vectors, strict=True):
            chunks.append({**metadata, "embedding": vector})
        batch_samples.clear()
        batch_metadata.clear()
        if len(chunks) % 40 < 8:
            print(f"音声特徴を抽出中: {len(chunks)}区間", flush=True)

    for segment in manifest["segments"]:
        for chunk_index, (start, duration) in enumerate(
            chunk_ranges(
                segment["start_seconds"],
                segment["duration_seconds"],
                project.minimum_chunk_seconds,
                project.maximum_chunk_seconds,
            )
            or []
        ):
            samples = reader.read(start, duration)
            if len(samples) < reader.sample_rate * project.minimum_chunk_seconds:
                continue
            batch_samples.append(samples)
            batch_metadata.append(
                {
                    "segment_index": segment["index"],
                    "chunk_index": chunk_index,
                    "speaker_id": segment["speaker_id"],
                    "speaker_name": segment["speaker_name"],
                    "locked": segment["locked"],
                    "start_seconds": start,
                    "duration_seconds": duration,
                }
            )
            if len(batch_samples) >= 8:
                flush_batch()
    flush_batch()

    np.savez_compressed(
        project.embeddings_path,
        embeddings=np.stack([item["embedding"] for item in chunks]),
        metadata=np.asarray(
            [json.dumps({k: v for k, v in item.items() if k != "embedding"}, ensure_ascii=False) for item in chunks]
        ),
    )
    result = score_chunks(chunks, list(project.known_speakers))
    return _write_result(project, result)


def _write_result(project: Project, result: dict[str, Any]) -> dict[str, Any]:
    result.update(
        {
            "schema_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "project_name": project.project_name,
            "model": MODEL_SOURCE,
        }
    )
    project.results_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result
