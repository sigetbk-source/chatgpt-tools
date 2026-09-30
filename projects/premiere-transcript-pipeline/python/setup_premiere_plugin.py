"""Stage the UXP panel with a local IPC directory for UXP Developer Tool."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil


FILES = ('manifest.json', 'index.html', 'index.js', 'workflow-launcher.js', 'workflow-bridge.js',
         'safe-writeback.js', 'import-transcript.js')
PLUGIN_ID = 'jp.tbk.premiere-transcript-workflow'


def stage(destination: Path, ipc_root: Path, workspace: Path | None = None) -> Path:
    source = Path(__file__).resolve().parents[1] / 'uxp'
    destination = destination.expanduser().resolve()
    ipc_root = ipc_root.expanduser().resolve()
    if destination == source or source in destination.parents:
        raise ValueError('Stage outside the source plugin folder')
    manifest = destination / 'manifest.json'
    if manifest.exists() and json.loads(manifest.read_text(encoding='utf-8')).get('id') != PLUGIN_ID:
        raise ValueError('Destination contains another plugin')
    destination.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        shutil.copy2(source / name, destination / name)
    for name in ('inbox', 'outbox', 'backups', 'claims',
                 'launcher-inbox', 'launcher-outbox', 'launcher-claims',
                 'caption-inbox', 'caption-outbox', 'caption-claims', 'caption-backups'):
        (ipc_root / name).mkdir(parents=True, exist_ok=True)
    config = destination / 'bridge-config.json'
    settings = {'root': str(ipc_root)}
    if workspace is not None:
        workspace = workspace.expanduser().resolve(strict=True)
        if not workspace.is_dir():
            raise ValueError('Workspace must be an existing folder')
        settings['workspace'] = str(workspace)
    config.write_text(json.dumps(settings, ensure_ascii=False), encoding='utf-8')
    config.chmod(0o600)
    return manifest


def stage_caption(destination: Path, ipc_root: Path) -> Path:
    source = Path(__file__).resolve().parents[1] / 'cep'
    destination = destination.expanduser().resolve()
    ipc_root = ipc_root.expanduser().resolve()
    if destination == source or source in destination.parents:
        raise ValueError('Stage outside the source CEP folder')
    manifest = destination / 'CSXS' / 'manifest.xml'
    if manifest.exists() and 'jp.tbk.transcript.caption' not in manifest.read_text(encoding='utf-8'):
        raise ValueError('Destination contains another CEP extension')
    for relative in ('CSXS/manifest.xml', 'index.html', 'panel.js', 'jsx/host.jsx'):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, target)
    (destination / 'bridge-config.js').write_text(
        'window.CAPTION_BRIDGE_ROOT = ' + json.dumps(str(ipc_root), ensure_ascii=False) + ';\n',
        encoding='utf-8')
    for name in ('caption-inbox', 'caption-outbox', 'caption-claims', 'caption-backups'):
        (ipc_root / name).mkdir(parents=True, exist_ok=True)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--ipc-root', type=Path, required=True)
    parser.add_argument('--workspace', type=Path, help='Preselect one exact review workspace in the UXP panel')
    parser.add_argument('--cep-destination', type=Path)
    args = parser.parse_args()
    print(stage(args.destination, args.ipc_root, args.workspace))
    if args.cep_destination:
        print(stage_caption(args.cep_destination, args.ipc_root))


if __name__ == '__main__':
    main()
