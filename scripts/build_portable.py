"""Build a clean Windows ZIP from app sources, standalone Python and converters.

Run with the development venv. Only the builder needs uv/network access.
The resulting archive never reads .env, data/, models or user preferences.
"""
import argparse
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]


def archive_bundle(output, archive=None):
    output = Path(output).resolve()
    archive = Path(archive).resolve() if archive else output.parent/(output.name+'.zip')
    if archive.exists():
        raise FileExistsError(archive)
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=1) as package:
        for path in sorted(output.rglob('*')):
            if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc':
                package.write(path, str(Path(output.name)/path.relative_to(output)))
    return archive


def build(output, python_home, libreoffice, sevenzip=None):
    if os.name != 'nt' or platform.machine().lower() not in ('amd64', 'x86_64'):
        raise ValueError('当前打包脚本用于 Windows x64，请在 Windows x64 上构建')
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    runtime = output/'runtime'
    python_home = Path(python_home).resolve()
    if not (python_home/'python.exe').is_file() or (python_home/'pyvenv.cfg').exists():
        raise ValueError('--python-home 需要独立 Python 安装目录，不能使用虚拟环境目录')
    def ignored(directory, names):
        excluded = {'__pycache__', '*.pyc'}
        if Path(directory) == python_home/'Lib':
            excluded.add('site-packages')
        return shutil.ignore_patterns(*excluded)(directory, names)
    shutil.copytree(python_home, runtime, ignore=ignored)
    uv = shutil.which('uv')
    if not uv:
        raise ValueError('构建机需要 uv；体验者无需安装')
    site = runtime/'Lib/site-packages'
    subprocess.run([uv, 'pip', 'install', '--python', str(runtime/'python.exe'),
        '--target', str(site), '--link-mode', 'copy', str(PROJECT)+'[question-bank]'], check=True)
    # Include the native helper in the installed package, not a developer path.
    shutil.copy2(PROJECT/'scripts/windows_ocr.ps1', site/'aitown/question_bank/windows_ocr.ps1')
    libreoffice = Path(libreoffice).resolve()
    if not (libreoffice/'program/soffice.com').is_file():
        raise ValueError('--libreoffice-dir 必须包含 program/soffice.com；完整版不依赖系统 Word')
    office_target = output/'tools/libreoffice'
    shutil.copytree(libreoffice, office_target, ignore=shutil.ignore_patterns('*.msi', 'extract.log'))
    # MSI administrative extraction leaves the VC runtime in System64. A portable
    # converter needs these beside its executable rather than in system folders.
    if (libreoffice/'System64').is_dir():
        for library in (libreoffice/'System64').glob('*.dll'):
            if not (office_target/'program'/library.name).exists():
                shutil.copy2(library, office_target/'program'/library.name)
    if sevenzip:
        sevenzip = Path(sevenzip).resolve()
        target = output/'tools/7zip'
        target.mkdir(parents=True)
        for name in ('7z.exe', '7z.dll', '7-zip.dll', 'License.txt', 'license.txt', 'readme.txt'):
            if (sevenzip/name).is_file() and not (target/name).exists():
                shutil.copy2(sevenzip/name, target/name)
    for name, arguments in [('Start.bat', ''), ('Stop.bat', '--stop')]:
        (output/name).write_bytes(('@echo off\r\nchcp 65001 >nul\r\n'
            '"%~dp0runtime\\python.exe" -I -m aitown.portable --home "%~dp0." '+arguments+' %*\r\n'
            'if errorlevel 1 pause\r\n').encode('ascii'))
    shutil.copy2(PROJECT/'docs/PORTABLE_QUICKSTART.txt', output/'使用说明.txt')
    (output/'示例资料').mkdir()
    shutil.copy2(PROJECT/'examples/portable/示例课程-期末试题.md', output/'示例资料/示例课程-期末试题.md')
    # Installed metadata is a record of this artifact, not an input lockfile.
    components = []
    for distribution in importlib.metadata.distributions(path=[str(site)]):
        components.append({'name':distribution.metadata['Name'], 'version':distribution.version})
    (output/'components.json').write_text(json.dumps(sorted(components, key=lambda c:c['name'].lower()),
        ensure_ascii=False, indent=2), encoding='utf-8')
    shutil.copy2(PROJECT/'docs/PORTABLE_NOTICES.txt', output/'第三方组件说明.txt')
    # Check real imports and packaged static resources before creating the ZIP.
    subprocess.run([str(runtime/'python.exe'), '-I', '-c',
        "import aitown.portable, aitown.ui.server, pymupdf, faiss; from pathlib import Path; "
        "assert (Path(aitown.ui.server.__file__).parent/'static/setup.js').is_file(); print('Runtime imports OK')"], check=True)
    archive = archive_bundle(output)
    print(json.dumps({'archive':str(archive), 'bytes':archive.stat().st_size,
        'directory':str(output), 'python':sys.version.split()[0], 'libreoffice':True}, ensure_ascii=False), flush=True)
    return archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=PROJECT/'dist/AnotherMe-0.1.0-windows-x64')
    parser.add_argument('--python-home', type=Path, default=Path(sys.base_prefix))
    parser.add_argument('--libreoffice-dir', type=Path, required=True)
    parser.add_argument('--sevenzip-dir', type=Path)
    args = parser.parse_args()
    build(args.output, args.python_home, args.libreoffice_dir, args.sevenzip_dir)


if __name__ == '__main__':
    main()
