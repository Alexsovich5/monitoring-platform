"""Fixtures shared by the unit tests."""
import pytest

TOKEN = 'a' * 63 + 'f'


class Intake(object):
    """An intake token file in a temporary directory."""

    def __init__(self, path, token):
        self.path = path
        self.token = token
        self.headers = {'X-Monplat-Token': token}

    def cfg(self, base):
        cfg = dict(base)
        cfg['api'] = dict(base.get('api') or {}, intake_token_file=self.path)
        return cfg


@pytest.fixture
def intake(tmpdir):
    path = tmpdir.join('intake.token')
    path.write(TOKEN + '\n')
    return Intake(str(path), TOKEN)


@pytest.fixture(autouse=True)
def private_token_file(tmpdir, monkeypatch):
    """Point the configured token file at a temporary directory, so no
    test reads or writes the stack's real secret."""
    monkeypatch.setenv('MONPLAT_API_INTAKE_TOKEN_FILE',
                       str(tmpdir.join('secrets', 'intake.token')))
