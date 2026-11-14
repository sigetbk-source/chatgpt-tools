from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL_CACHE_DIR = PROJECT_ROOT / "work" / "model-cache"


class OfflineFileError(OSError):
    """The file exists as an iCloud placeholder; reading would block hydration."""


def read_local_text(path: Path) -> str:
    if getattr(path.stat(), "st_flags", 0) & 0x40000000:
        raise OfflineFileError(f"iCloud未ダウンロード: {path}")
    return path.read_text(encoding="utf-8")


@dataclass(frozen=True)
class Project:
    config_path: Path
    project_name: str
    source_dir: Path
    transcript_json: Path
    transcript_csv: Path | None
    native_transcript: Path | None
    audio: Path
    audio_stream_index: int | None
    audio_channel_index: int | None
    reference_documents: tuple[Path, ...]
    whisper_model: str
    glossary_terms: tuple[str, ...]
    glossary_aliases: dict[str, tuple[str, ...]]
    entity_hints: tuple[dict, ...]
    verification_hints: tuple[dict, ...]
    known_speakers: tuple[str, ...]
    speaker_registry_path: Path
    cast: dict[str, str]
    analysis_dir: Path
    model_cache_dir: Path
    transcript_alignment: str
    minimum_chunk_seconds: float
    maximum_chunk_seconds: float

    @classmethod
    def load(cls, config_path: str | Path) -> "Project":
        path = Path(config_path).expanduser().resolve()
        data = json.loads(read_local_text(path))
        shared = {}
        if data.get("global_glossary"):
            shared_path = Path(data["global_glossary"]).expanduser()
            if not shared_path.is_absolute():
                shared_path = path.parent / shared_path
            shared = json.loads(read_local_text(shared_path))
        glossary_terms = list(shared.get("glossary_terms", []))
        glossary_terms.extend(data.get("glossary_terms", []))
        glossary_aliases: dict[str, list[str]] = {}
        for source_aliases in (
            shared.get("glossary_aliases", {}),
            data.get("glossary_aliases", {}),
        ):
            for canonical, variants in source_aliases.items():
                glossary_aliases.setdefault(canonical, []).extend(variants)
        entity_hints = list(shared.get("entity_hints", []))
        entity_hints.extend(data.get("entity_hints", []))
        verification_hints = list(shared.get("verification_hints", []))
        verification_hints.extend(data.get("verification_hints", []))
        source = Path(data["source_dir"]).expanduser()
        analysis = Path(data.get("analysis_dir", "work"))
        if not analysis.is_absolute():
            analysis = (path.parent.parent / analysis).resolve()
        model_cache = Path(data.get("model_cache_dir", str(DEFAULT_MODEL_CACHE_DIR)))
        if not model_cache.is_absolute():
            model_cache = (PROJECT_ROOT / model_cache).resolve()
        return cls(
            config_path=path,
            project_name=data["project_name"],
            source_dir=source,
            transcript_json=source / data["transcript_json"],
            transcript_csv=(source / data["transcript_csv"]) if data.get("transcript_csv") else None,
            native_transcript=(source / data["native_transcript"]) if data.get("native_transcript") else None,
            audio=source / data.get("source_media", data.get("audio", "")),
            audio_stream_index=(
                int(data["audio_stream_index"])
                if data.get("audio_stream_index") is not None
                else None
            ),
            audio_channel_index=(
                int(data["audio_channel_index"])
                if data.get("audio_channel_index") is not None
                else None
            ),
            reference_documents=tuple(source / item for item in data.get("reference_documents", [])),
            whisper_model=data.get("whisper_model", "mlx-community/whisper-large-v3-turbo"),
            glossary_terms=tuple(dict.fromkeys(glossary_terms)),
            glossary_aliases={
                key: tuple(dict.fromkeys(value))
                for key, value in glossary_aliases.items()
            },
            entity_hints=tuple(entity_hints),
            verification_hints=tuple(verification_hints),
            known_speakers=tuple(data.get("known_speakers", [])),
            speaker_registry_path=Path(
                data.get("speaker_registry_path", str(analysis / "speaker-registry.json"))
            ).expanduser().resolve(),
            cast=dict(data.get("cast", {})),
            analysis_dir=analysis,
            model_cache_dir=model_cache,
            transcript_alignment=data.get("transcript_alignment", "semantic"),
            minimum_chunk_seconds=float(data.get("minimum_chunk_seconds", 2.5)),
            maximum_chunk_seconds=float(data.get("maximum_chunk_seconds", 10.0)),
        )

    @property
    def manifest_path(self) -> Path:
        return self.analysis_dir / "manifest.json"

    @property
    def wav_path(self) -> Path:
        return self.analysis_dir / "audio-16k-mono.wav"

    @property
    def review_video_path(self) -> Path:
        return self.analysis_dir / "review-proxy.mp4"

    @property
    def embeddings_path(self) -> Path:
        return self.analysis_dir / "embeddings.npz"

    @property
    def results_path(self) -> Path:
        return self.analysis_dir / "analysis.json"

    @property
    def decisions_path(self) -> Path:
        return self.analysis_dir / "decisions.json"

    @property
    def references_path(self) -> Path:
        return self.analysis_dir / "references.json"

    @property
    def whisper_path(self) -> Path:
        return self.analysis_dir / "whisper.json"

    @property
    def correction_candidates_path(self) -> Path:
        return self.analysis_dir / "correction-candidates.json"

    @property
    def correction_decisions_path(self) -> Path:
        return self.analysis_dir / "correction-decisions.json"

    @property
    def segment_edit_state_path(self) -> Path:
        return self.analysis_dir / "segment-edit-state.json"

    @property
    def writeback_ready_path(self) -> Path:
        return self.analysis_dir / "writeback-ready.json"

    @property
    def term_audit_path(self) -> Path:
        return self.analysis_dir / "term-audit.json"
