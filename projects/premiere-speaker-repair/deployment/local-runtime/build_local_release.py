"""Build a self-contained, local-only Premiere runtime without touching live installs.

The release is staged below --stage; --install-root controls paths embedded in
the UXP package. Deployment is a separate, explicitly reviewed operation.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

CORE_EXTRA_PACKAGES = (
    "rapidfuzz", "pykakasi", "jaconv", "deprecated", "wrapt", "imageio_ffmpeg",
)
SKIP = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store", "pkgconfig", "man")


def copy_tree(source: Path, target: Path) -> None:
    # Follow links during copy: a link back into Documents would defeat the fix.
    shutil.copytree(source, target, dirs_exist_ok=True, symlinks=False, ignore=SKIP)


def patch_plugin(plugin: Path, source: Path, install_root: Path) -> None:
    # Documents may resolve to Mobile Documents when Desktop/Documents sync is
    # enabled. Match the actual declared execution path, not resolve()'s spelling.
    original_index = (plugin / "index.js").read_text()
    match = re.search(r"const IPC_ROOT = '([^']+)';", original_index)
    if not match or not match.group(1).endswith("/premiere-speaker-repair/work/uxp/ipc"):
        raise ValueError("Unrecognized source IPC root; inspect before building")
    old_ipc = match.group(1)
    new_ipc = str(install_root / "app" / "work" / "uxp" / "ipc")
    for name in ("index.js", "diagnostics.js"):
        path = plugin / name
        text = path.read_text()
        if old_ipc not in text:
            raise ValueError(f"Expected IPC path was not found in {name}; review source before building")
        for suffix in ("/inbox", ""):
            text = text.replace("'" + old_ipc + suffix + "'", json.dumps(new_ipc + suffix, ensure_ascii=False))
        if name == "index.js":
            cep_export = Path.home() / "Library/Application Support/Adobe/CEP/extensions/jp.tsubaki.narration.timeline/data/current_narration_export.json"
            text, count = re.subn(r"const CEP_NARRATION_EXPORT = '[^']+';",
                                 lambda _: "const CEP_NARRATION_EXPORT = " + json.dumps(str(cep_export), ensure_ascii=False) + ";", text)
            if count != 1:
                raise ValueError("Expected CEP export declaration changed; inspect before building")
        path.write_text(text)
    # The UXP package may be installed anywhere by Creative Cloud. Its launcher
    # must use our local application root, not the package's parent directory.
    path = plugin / "start-bridge"
    text = path.read_text()
    text = text.replace('PROJECT_DIR="$(cd "$PLUGIN_DIR/.." && pwd)"',
                        "PROJECT_DIR=" + shell_quote(str(install_root / "app")))
    start = text.index('if [ -x "$PROJECT_DIR/.venv/bin/python" ]; then')
    end = text.index("\nfi", start) + len("\nfi")
    text = text[:start] + "PYTHON=" + shell_quote(str(install_root / "python" / "bin" / "python3")) + text[end:]
    # Health helper is bundled with the installed UXP, so PLUGIN_DIR is retained.
    path.write_text(text)
    path.chmod(0o755)


def shell_quote(text: str) -> str:
    return "'" + text.replace("'", "'\"'\"'") + "'"


def build(args: argparse.Namespace) -> dict:
    source = args.source.resolve()
    stage = args.stage.resolve()
    root = args.install_root.expanduser().absolute()
    if stage == root or root in stage.parents:
        raise ValueError("Build stage must be outside the live install root")
    if stage.exists() and any(stage.iterdir()):
        raise ValueError("Stage must be new or empty; existing releases are never overwritten")
    stage.mkdir(parents=True, exist_ok=True)
    copy_tree(args.python.resolve(), stage / "python")
    python = stage / "python" / "bin" / "python3"
    version = subprocess.check_output([str(python), "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"], text=True).strip()
    target_site = stage / "python" / "lib" / f"python{version}" / "site-packages"
    source_site = source / ".venv" / "lib" / f"python{version}" / "site-packages"
    if args.requirements_file:
        subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check",
                        "--only-binary=:all:", "--upgrade", "--target", str(target_site),
                        "--index-url", "https://pypi.org/simple", "-r", str(args.requirements_file.resolve())], check=True)
    elif args.asr:
        # Copy installed libraries, not venv/bin scripts or editable .pth files.
        # This can hydrate a large iCloud environment; opt in explicitly.
        for item in source_site.iterdir():
            if item.name.startswith("__editable__") or item.suffix == ".pth" or item.name == "__pycache__":
                continue
            if item.is_dir():
                copy_tree(item, target_site / item.name)
            else:
                shutil.copy2(item, target_site / item.name)
    else:
        for package in CORE_EXTRA_PACKAGES:
            found = sorted(source_site.glob(package + "*"))
            if not found:
                raise ValueError(f"Required runtime dependency missing: {package}")
            for item in found:
                if item.is_dir():
                    copy_tree(item, target_site / item.name)
                elif item.suffix != ".pyc":
                    shutil.copy2(item, target_site / item.name)
    return finalize(args, source, stage, root, python)


def finalize(args: argparse.Namespace, source: Path, stage: Path, root: Path, python: Path) -> dict:
    """Finalize an owned stage after dependencies have been successfully installed."""
    copy_tree(source / "speaker_repair", stage / "app" / "speaker_repair")
    copy_tree(source / "uxp-plugin", stage / "app" / "uxp-plugin")
    # Browser-approved transcript writeback uses this loader; it is execution
    # code and must be local even if the development repository is evicted.
    copy_tree(source / "scripts", stage / "app" / "scripts")
    global_glossary = source / "projects" / "global-glossary.json"
    if global_glossary.exists():
        (stage / "app" / "projects").mkdir(parents=True, exist_ok=True)
        shutil.copy2(global_glossary, stage / "app" / "projects" / global_glossary.name)
    copy_tree(args.narration.resolve(), stage / "narration")
    # Apply the versioned offline-history fix to the supplied narration template.
    narration_override = Path(__file__).with_name("narration_jobs.py")
    if narration_override.exists():
        shutil.copy2(narration_override, stage / "narration" / "narration_jobs.py")
    bridge = stage / "app" / "speaker_repair" / "uxp_bridge.py"
    text = bridge.read_text()
    old = 'NARRATION_RUNTIME = Path.home() / "Library" / "Application Support" / "ナレーション制作ツール" / "runtime"\nif not NARRATION_RUNTIME.is_dir():\n    NARRATION_RUNTIME = ROOT.parent / "ナレーションPremiereハブ_作業" / "runtime"'
    if old not in text:
        raise ValueError("Expected narration runtime declaration changed; inspect before building")
    bridge.write_text(text.replace(old, 'NARRATION_RUNTIME = ROOT.parent / "narration"'))
    patch_plugin(stage / "app" / "uxp-plugin", source, root)
    manifest_path = stage / "app" / "uxp-plugin" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["version"] = "0.4.1"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    # No copied editable pointer or symbolic link may take execution back to iCloud.
    for link in stage.rglob("*"):
        if link.is_symlink():
            raise ValueError(f"Unexpected runtime symlink: {link}")
    env = dict(os.environ, PYTHONNOUSERSITE="1", PYTHONPATH=str(stage / "app"))
    env.pop("PYTHONHOME", None)
    env.pop("VIRTUAL_ENV", None)
    probe = subprocess.run([str(python), "-c", "import json,sys,ssl,sqlite3,ctypes; import speaker_repair.uxp_bridge; print(json.dumps({'prefix':sys.prefix,'bridge':'ok'}))"],
                           env=env, cwd=stage, text=True, capture_output=True, timeout=60)
    if probe.returncode:
        raise RuntimeError("Relocated runtime import failed: " + probe.stderr[-6000:])
    report = {"stage": str(stage), "installRoot": str(root), "asrDependencies": args.asr,
              "runtimeProbe": json.loads(probe.stdout.strip().splitlines()[-1]),
              "sourceRepository": str(source), "mutableStateMigrated": False,
              "notes": ["Build only; no live folders changed", "Model weights and case files are not copied", "Existing state must be migrated separately before enabling the new plugin"]}
    (stage / "release-manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--narration", type=Path, required=True)
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument("--install-root", type=Path, default=Path.home() / "Library" / "Application Support" / "Premiere制作支援")
    parser.add_argument("--asr", action="store_true", help="Include existing ASR/diarization dependencies (may hydrate large iCloud files)")
    parser.add_argument("--requirements-file", type=Path, help="Install official PyPI binary wheels instead of reading iCloud dependencies")
    args = parser.parse_args()
    print(json.dumps(build(args), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
