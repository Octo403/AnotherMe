"""Locate the locally installed CLI without a shell or access to credentials."""
import os
import platform
import shutil
from pathlib import Path


def windows_path():
    """Explorer and already running apps can retain PATH from before an install."""
    import winreg
    paths = [os.environ.get('PATH', '')]
    for hive, key in ((winreg.HKEY_CURRENT_USER, r'Environment'),
                      (winreg.HKEY_LOCAL_MACHINE, r'SYSTEM\CurrentControlSet\Control\Session Manager\Environment')):
        try:
            with winreg.OpenKey(hive, key) as registry:
                value, _ = winreg.QueryValueEx(registry, 'Path')
            if isinstance(value, str):
                paths.append(os.path.expandvars(value))
        except OSError:
            continue
    return os.pathsep.join(paths)


def installation_directories():
    home = Path.home()
    directories = [home/'.local/bin', home/'.bun/bin']
    for variable, children in (
        ('APPDATA', ('npm',)),
        ('LOCALAPPDATA', ('Microsoft/WinGet/Links', 'pnpm')),
        ('ProgramFiles', ('nodejs',)),
    ):
        if os.environ.get(variable):
            directories.extend(Path(os.environ[variable])/child for child in children)
    return directories


def _command(path, search_path):
    path = Path(path)
    if not path.is_file():
        raise ValueError(f'Codex CLI 路径不存在：{path}。请填写 codex.exe 或 codex.cmd 的完整路径。')
    if os.name != 'nt' or path.suffix.lower() not in ('.cmd', '.ps1', '.bat'):
        return [str(path)]
    # npm shims require a shell. Invoke the JS entry with an absolute Node path
    # instead; paths with spaces remain individual arguments, never shell code.
    root = path.parent/'node_modules/@openai/codex'
    script = root/'bin/codex.js'
    node = path.parent/'node.exe'
    if not node.is_file():
        node = shutil.which('node', path=search_path)
    if script.is_file() and node:
        return [str(node), str(script)]
    # npm's optional platform package also works if Node is unavailable to the
    # portable app. Support both current and earlier vendor directory layouts.
    architecture = 'arm64' if platform.machine().lower() in ('arm64', 'aarch64') else 'x64'
    triple = 'aarch64' if architecture == 'arm64' else 'x86_64'
    triple += '-pc-windows-msvc'
    package = '@openai/codex-win32-'+architecture
    for vendor in (root/'node_modules'/package/'vendor', path.parent/'node_modules'/package/'vendor', root/'vendor'):
        for directory in ('bin', 'codex'):
            binary = vendor/triple/directory/'codex.exe'
            if binary.is_file():
                return [str(binary)]
    raise ValueError(f'已找到 Codex 入口 {path}，但缺少可用的 Node.js 或 Codex 程序文件。'
                     '请在新 PowerShell 中确认 codex --version 能运行，或选择可用的 codex.exe。')


def codex_command(cli_path=''):
    directories = installation_directories() if os.name == 'nt' else []
    search_path = os.pathsep.join([windows_path() if os.name == 'nt' else os.environ.get('PATH', ''),
                                  *(str(path) for path in directories)])
    explicit = (cli_path or os.environ.get('AITOWN_CODEX_PATH', '')).strip().strip('"')
    if explicit:
        return _command(Path(os.path.expandvars(explicit)).expanduser(), search_path)
    candidates = []
    found = shutil.which('codex', path=search_path)
    if found:
        candidates.append(Path(found))
    for directory in directories:
        candidates.extend(directory/name for name in ('codex.exe', 'codex.cmd', 'codex.ps1'))
    errors = []
    for path in dict.fromkeys(candidates):
        if not path.is_file():
            continue
        try:
            return _command(path, search_path)
        except ValueError as exc:
            errors.append(str(exc))
    if errors:
        raise ValueError(errors[0])
    raise ValueError('当前未找到 Windows 可用的 Codex CLI。若已安装，请在新 PowerShell 中运行 '
                     'where.exe codex，把结果填入设置中的“Codex CLI 路径”，再刷新型号。'
                     '仅在 WSL、编辑器插件或其他 Windows 账户中安装／登录，当前程序无法直接使用。')
