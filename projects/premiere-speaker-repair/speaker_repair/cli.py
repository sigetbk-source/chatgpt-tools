from __future__ import annotations

import argparse
import json

from .audio import convert_audio
from .corrections import build_correction_candidates, build_term_audit, canonicalize_saved_decisions, export_corrected_csv, migrate_approved_decisions
from .embeddings import analyze, rescore
from .manifest import write_manifest
from .project import Project
from .references import build_references
from .whisper_transcribe import transcribe


def main() -> None:
    parser = argparse.ArgumentParser(description="Premiere話者修復ツール")
    parser.add_argument("command", choices=[
        "prepare", "convert-audio", "analyze", "rescore",
        "prepare-references", "transcribe", "align", "audit-terms", "export-corrections",
        "migrate-decisions",
        "normalize-decisions",
    ])
    parser.add_argument("project")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--test-seconds", type=float)
    parser.add_argument("--from-project")
    args = parser.parse_args()
    project = Project.load(args.project)

    if args.command == "prepare":
        result = write_manifest(project)
        print(json.dumps({
            "manifest": str(project.manifest_path),
            "segments": result["segment_count"],
            "mismatches": result["csv_json_mismatch_indices"],
        }, ensure_ascii=False, indent=2))
    elif args.command == "convert-audio":
        print(convert_audio(project, force=args.force))
    elif args.command == "analyze":
        result = analyze(project)
        print(json.dumps({
            "results": str(project.results_path),
            "chunks": len(result["chunks"]),
            "seed_counts": result["seed_counts"],
        }, ensure_ascii=False, indent=2))
    elif args.command == "rescore":
        result = rescore(project)
        print(json.dumps({
            "results": str(project.results_path),
            "chunks": len(result["chunks"]),
            "seed_counts": result["seed_counts"],
        }, ensure_ascii=False, indent=2))
    elif args.command == "prepare-references":
        result = build_references(project)
        print(json.dumps({"references": str(project.references_path), "terms": len(result["terms"]), "sources": len(result["sources"])}, ensure_ascii=False, indent=2))
    elif args.command == "transcribe":
        result = transcribe(project, test_seconds=args.test_seconds)
        print(json.dumps({"segments": len(result.get("segments", [])), "output": str(project.whisper_path if not args.test_seconds else project.analysis_dir / "whisper-test.json")}, ensure_ascii=False, indent=2))
    elif args.command == "align":
        result = build_correction_candidates(project)
        audit = build_term_audit(project)
        print(json.dumps({
            "output": str(project.correction_candidates_path),
            "items": len(result["items"]),
            "needs_review": sum(item["needs_review"] for item in result["items"]),
            "term_audit": str(project.term_audit_path),
            "unresolved_candidates": len(audit["unresolved_candidates"]),
        }, ensure_ascii=False, indent=2))
    elif args.command == "audit-terms":
        result = build_term_audit(project)
        print(json.dumps({
            "output": str(project.term_audit_path),
            "terms": len(result["terms"]),
            "unresolved_candidates": len(result["unresolved_candidates"]),
        }, ensure_ascii=False, indent=2))
    elif args.command == "export-corrections":
        print(json.dumps(export_corrected_csv(project), ensure_ascii=False, indent=2))
    elif args.command == "migrate-decisions":
        if not args.from_project:
            raise SystemExit("migrate-decisions には --from-project が必要です")
        source = Project.load(args.from_project)
        print(json.dumps(migrate_approved_decisions(source, project), ensure_ascii=False, indent=2))
    elif args.command == "normalize-decisions":
        print(json.dumps(canonicalize_saved_decisions(project), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
