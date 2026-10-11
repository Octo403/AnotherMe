import os
from pathlib import Path

import pytest

from aitown.question_bank import codex_cli


@pytest.fixture
def isolated_install(tmp_path, monkeypatch):
    monkeypatch.delenv('AITOWN_CODEX_PATH', raising=False)
    monkeypatch.setattr(codex_cli, 'windows_path', lambda: '')
    monkeypatch.setattr(codex_cli, 'installation_directories', lambda: [tmp_path])
    monkeypatch.setenv('PATH', '')
    return tmp_path


@pytest.mark.skipif(os.name != 'nt', reason='Windows npm shim discovery')
def test_npm_install_found_outside_inherited_path(isolated_install):
    shim = isolated_install/'codex.cmd'; shim.touch()
    script = isolated_install/'node_modules/@openai/codex/bin/codex.js'
    script.parent.mkdir(parents=True); script.touch()
    node = isolated_install/'node.exe'; node.touch()
    assert codex_cli.codex_command() == [str(node), str(script)]


@pytest.mark.skipif(os.name != 'nt', reason='Fresh Windows registry PATH')
def test_custom_prefix_discovered_from_updated_path(isolated_install, monkeypatch):
    custom = isolated_install/'custom install'; custom.mkdir()
    binary = custom/'codex.exe'; binary.touch()
    monkeypatch.setattr(codex_cli, 'windows_path', lambda: str(custom))
    assert Path(codex_cli.codex_command()[0]) == binary


@pytest.mark.skipif(os.name != 'nt', reason='npm Windows native fallback')
@pytest.mark.parametrize('architecture,triple', [('AMD64','x86_64'), ('ARM64','aarch64')])
def test_native_npm_package_without_node(isolated_install, monkeypatch, architecture, triple):
    monkeypatch.setattr(codex_cli.platform, 'machine', lambda: architecture)
    (isolated_install/'codex.cmd').touch()
    package = 'codex-win32-'+('arm64' if architecture == 'ARM64' else 'x64')
    binary = isolated_install/f'node_modules/@openai/codex/node_modules/@openai/{package}/vendor/{triple}-pc-windows-msvc/bin/codex.exe'
    binary.parent.mkdir(parents=True); binary.touch()
    assert codex_cli.codex_command() == [str(binary)]


def test_explicit_path_and_environment_override(isolated_install, monkeypatch):
    binary = isolated_install/'custom codex.exe'; binary.touch()
    assert codex_cli.codex_command('"'+str(binary)+'"') == [str(binary)]
    monkeypatch.setenv('AITOWN_CODEX_PATH', str(binary))
    assert codex_cli.codex_command() == [str(binary)]
    with pytest.raises(ValueError, match='路径不存在'):
        codex_cli.codex_command(str(isolated_install/'missing.exe'))


def test_missing_cli_gives_path_recovery_steps(isolated_install):
    with pytest.raises(ValueError, match='where.exe codex') as error:
        codex_cli.codex_command()
    assert '未安装' not in str(error.value)


@pytest.mark.skipif(os.name != 'nt', reason='Incomplete npm shim')
def test_incomplete_install_distinguished_from_missing(isolated_install):
    (isolated_install/'codex.cmd').touch()
    with pytest.raises(ValueError, match='已找到 Codex 入口'):
        codex_cli.codex_command()


@pytest.mark.skipif(os.name != 'nt', reason='Windows registry environment')
def test_install_after_start_with_new_environment_variable(isolated_install,monkeypatch):
    import winreg
    from contextlib import nullcontext
    # The application process keeps its original PATH and has no NVM variable.
    monkeypatch.delenv('ANOTHERME_TEST_NODE_HOME',raising=False)
    monkeypatch.setattr(codex_cli,'windows_path',windows_path_original)
    registry=[]
    monkeypatch.setattr(winreg,'OpenKey',lambda *args:nullcontext('registry'))
    def value(key,index):
        if index>=len(registry):raise OSError('end')
        return registry[index]
    monkeypatch.setattr(winreg,'EnumValue',value)
    with pytest.raises(ValueError,match='当前未找到'):
        codex_cli.codex_command()
    custom=isolated_install/'installed after startup';custom.mkdir()
    binary=custom/'codex.exe';binary.touch()
    registry.extend([('ANOTHERME_TEST_NODE_HOME',str(custom),winreg.REG_SZ),
        ('Path','%anotherme_test_node_home%',winreg.REG_EXPAND_SZ)])
    assert Path(codex_cli.codex_command()[0])==binary
    assert 'ANOTHERME_TEST_NODE_HOME' not in os.environ


windows_path_original=codex_cli.windows_path
