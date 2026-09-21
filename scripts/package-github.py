"""Produce a flat GitHub/Render release with full development sources attached.

Run from the development project: python scripts/package-github.py
Requires Node.js 22+ and Python 3.12; packaging never calls the supplier API.
"""
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parent.parent


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'Expected one release template marker: {old[:60]}')
    return text.replace(old, new, 1)


def main():
    package = json.loads((ROOT / 'package.json').read_text())
    version = package['version']
    release = {'version': version, 'id': f'IMKONEX-WHEELS-{version}-FULL-1', 'products': 6}
    env = dict(os.environ, CATALOG_MODE='snapshot')
    subprocess.run(['node', 'scripts/build.mjs'], cwd=ROOT, env=env, check=True)
    subprocess.run(['node', 'scripts/standalone.mjs'], cwd=ROOT, env=env, check=True)
    target = ROOT / 'artifacts' / 'github-full'
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    html = (ROOT / 'artifacts/IMKONEX_WHEELS_REAL_DATA.html').read_text(encoding='utf-8')
    html = replace_once(html, '<body>', f'<body data-imkonex-release="{release["id"]}">')
    html = replace_once(html,
        '<span id="catalog-notice">Демоверсия. Характеристики, цены и наличие — тестовые. Покупка недоступна.</span>',
        '<span id="catalog-notice">Реальная выборка · 6 товаров. Цены и остатки на дату проверки. Покупка недоступна.</span>')
    html = replace_once(html, '<span id="footer-notice">', f'<small id="release-label">Сборка {version}</small><span id="footer-notice">')
    html = html.replace('\r\n', '\n').replace('\r', '\n')
    (target / 'index.html').write_text(html, encoding='utf-8', newline='\n')
    builder = (ROOT / 'scripts/flat-build.template.mjs').read_text(encoding='utf-8')
    validator = (ROOT / 'scripts/public-catalog.mjs').read_text(encoding='utf-8').replace('export function ', 'function ')
    builder = replace_once(builder, '__RELEASE_METADATA__', json.dumps(release))
    builder = replace_once(builder, '__EXPECTED_HTML_SHA256__', json.dumps(sha256(html.encode()).hexdigest()))
    builder = replace_once(builder, '__PUBLIC_VALIDATOR__', validator)
    (target / 'BUILD_RENDER.mjs').write_text(builder, encoding='utf-8', newline='\n')
    runtime_package = {
        'name': 'imkonex-wheels-full', 'version': version, 'private': True, 'type': 'module',
        'engines': {'node': '>=22'},
        'scripts': {'build': 'node BUILD_RENDER.mjs', 'check': 'node BUILD_RENDER.mjs --check',
                    'test': 'node BUILD_RENDER.mjs --check'},
    }
    (target / 'package.json').write_text(json.dumps(runtime_package, indent=2) + '\n')
    lock = {'name': runtime_package['name'], 'version': version, 'lockfileVersion': 3,
            'requires': True, 'packages': {'': {'name': runtime_package['name'], 'version': version,
                                              'engines': runtime_package['engines']}}}
    (target / 'package-lock.json').write_text(json.dumps(lock, indent=2) + '\n')
    render = (ROOT / 'render.yaml').read_text().replace('buildCommand: npm run build', 'buildCommand: node BUILD_RENDER.mjs')
    (target / 'render.yaml').write_text(render, encoding='utf-8')
    shutil.copyfile(ROOT / 'docs/GITHUB_FULL_README.md', target / 'README.md')
    shutil.copyfile(ROOT / 'docs/GITHUB_FULL_PC1.txt', target / 'START_HERE_PC1.txt')
    source_dirs = ['backend', 'data', 'deploy', 'docs', 'frontend', 'scripts', 'tests', '.github']
    source_files = ['BUILD_RENDER.mjs', 'package.json', 'package-lock.json', 'requirements.txt', 'requirements-dev.txt',
                    'compose.yaml', 'Dockerfile', 'render.yaml', 'render-api.yaml', 'README.md',
                    'START_HERE_PC1.txt', '.env.example', '.gitignore']
    skipped = {'__pycache__', '.pytest_cache', 'node_modules', '.venv', '.check-venv', 'results'}
    chosen = {ROOT / name for name in source_files if (ROOT / name).is_file()}
    for directory in source_dirs:
        for file in (ROOT / directory).rglob('*'):
            relative = file.relative_to(ROOT)
            if not file.is_file() or skipped.intersection(relative.parts):
                continue
            if 'REPORT' in file.name or file.name.endswith(('.log', '.pyc', '.sqlite', '.dump')):
                continue
            if file.name.startswith('.env') and file.name != '.env.example':
                continue
            chosen.add(file)
    with zipfile.ZipFile(target / 'SOURCE_CODE.zip', 'w', zipfile.ZIP_DEFLATED) as source_zip:
        for file in sorted(chosen):
            source_zip.write(file, file.relative_to(ROOT))
    subprocess.run(['node', 'BUILD_RENDER.mjs'], cwd=target, check=True)
    # The outer ZIP contains only regular files at its root: no folder uploads needed.
    archive = ROOT / 'artifacts' / f'IMKONEX_WHEELS_FULL_GITHUB_v{version}.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as outer:
        for file in sorted(target.iterdir()):
            if file.is_file():
                outer.write(file, file.name)
    print(f'Full release: {archive} ({archive.stat().st_size} bytes)')


if __name__ == '__main__':
    main()
