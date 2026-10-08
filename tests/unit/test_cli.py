import subprocess

import pytest

from monplat import cli


def test_version_flag_prints_version_and_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(['--version'])
    assert exc.value.code == 0
    out, err = capsys.readouterr()
    # argparse on Python 2.7 writes the version to stderr
    assert (out + err).strip() == '0.1.0'


def test_unknown_subcommand_exits_two(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(['no-such-command'])
    assert exc.value.code == 2


def test_installed_console_script_prints_version():
    proc = subprocess.Popen(['mpctl', '--version'],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = proc.communicate()
    assert proc.returncode == 0, err
    assert (out + err).strip() == b'0.1.0'
