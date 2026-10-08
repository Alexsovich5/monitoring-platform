import os
import re
import stat

import pytest

from monplat import intake


def test_ensure_token_creates_a_random_private_token(tmpdir):
    path = str(tmpdir.join('intake.token'))
    token, created = intake.ensure_token(path)
    assert created is True
    assert re.match(r'^[0-9a-f]{64}$', token)
    with open(path) as handle:
        assert handle.read().strip() == token
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o640
    assert os.listdir(str(tmpdir)) == ['intake.token']


def test_ensure_token_keeps_an_existing_token(tmpdir):
    path = str(tmpdir.join('intake.token'))
    first, _ = intake.ensure_token(path)
    second, created = intake.ensure_token(path)
    assert (second, created) == (first, False)


def test_tokens_differ_between_installs(tmpdir):
    one, _ = intake.ensure_token(str(tmpdir.join('a')))
    two, _ = intake.ensure_token(str(tmpdir.join('b')))
    assert one != two


def test_ensure_token_creates_the_directory(tmpdir):
    path = str(tmpdir.join('secrets', 'intake.token'))
    token, _ = intake.ensure_token(path)
    assert intake.read_token(path) == token


@pytest.mark.skipif(os.getuid() != 0, reason='chown needs root')
def test_token_file_takes_the_directory_group(tmpdir):
    os.chown(str(tmpdir), -1, 4321)
    path = str(tmpdir.join('intake.token'))
    intake.ensure_token(path)
    assert os.stat(path).st_gid == 4321


def test_read_token_is_none_for_a_missing_or_empty_file(tmpdir):
    assert intake.read_token(str(tmpdir.join('missing'))) is None
    empty = tmpdir.join('empty')
    empty.write('\n')
    assert intake.read_token(str(empty)) is None


@pytest.mark.parametrize('given, ok', [
    ('s3cret-token', True),
    (u's3cret-token', True),
    ('s3cret-tokeN', False),
    ('s3cret', False),
    ('', False),
    (None, False),
    (u'\u00e9', False),
])
def test_matches_compares_tokens(given, ok):
    assert intake.matches('s3cret-token', given) is ok


def test_matches_never_accepts_a_missing_expected_token():
    assert intake.matches(None, '') is False
    assert intake.matches('', '') is False
