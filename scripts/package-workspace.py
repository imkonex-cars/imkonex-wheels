"""Package the complete editable source workspace without nested source ZIPs."""
from pathlib import Path
import json
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIRS = ('backend', 'data', 'deploy', 'docs', 'frontend', 'scripts', 'tests', '.github')
ROOT_FILES = ('BUILD_RENDER.mjs', 'package.json', 'package-lock.json', 'requirements.txt',
              'requirements-dev.txt', 'compose.yaml', 'Dockerfile', 'render.yaml',
              'render-api.yaml', 'README.md', 'START_HERE_PC1.txt', '.env.example', '.gitignore')
EXCLUDED_DIRS = {'__pycache__', '.pytest_cache', 'node_modules', '.venv', '.check-venv', 'results'}


def main():
    subprocess.run(['node', 'BUILD_RENDER.mjs'], cwd=ROOT, check=True)
    version = json.loads((ROOT / 'package.json').read_text())['version']
    target = ROOT / 'artifacts' / 'workspace'
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    chosen = {ROOT / name for name in ROOT_FILES}
    for directory in SOURCE_DIRS:
        for file in (ROOT / directory).rglob('*'):
            if not file.is_file() or EXCLUDED_DIRS.intersection(file.relative_to(ROOT).parts):
                continue
            if 'REPORT' in file.name or file.suffix.lower() in {'.log', '.pyc', '.sqlite', '.dump', '.zip'}:
                continue
            if file.name.startswith('.env') and file.name != '.env.example':
                continue
            chosen.add(file)
    for file in sorted(chosen):
        destination = target / file.relative_to(ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(file, destination)
    names = sorted([file.relative_to(ROOT).as_posix() for file in chosen] + ['FILES_MANIFEST.txt'])
    manifest = (f'IMKONEX CARS — полная рабочая область {version}\n'
                f'Всего файлов: {len(names)}. Все пути указаны от корня GitHub-репозитория.\n'
                'Загрузите содержимое архива с сохранением папок.\n'
                'Render: Static Site; Root Directory пусто; Build Command node BUILD_RENDER.mjs; Publish Directory dist.\n'
                'dist и node_modules создаются локально/на сервере и в этот список не входят.\n\n'
                + '\n'.join(names) + '\n')
    (target / 'FILES_MANIFEST.txt').write_text(manifest, encoding='utf-8-sig')
    archive = ROOT / 'artifacts' / f'IMKONEX_WHEELS_WORKSPACE_v{version}.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as output:
        for name in names:
            output.write(target / name, name)
    print(f'Workspace archive: {archive}; {len(names)} files; no nested source archive')


if __name__ == '__main__':
    main()
