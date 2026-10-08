"""The shared secret that authenticates alert notifications.

``mpctl provision`` creates the token file (``api.intake_token_file``, a
named volume in the compose stack, never part of the repository) with a
random token on first run.  The Zabbix alertscript reads it and sends it
in the ``X-Monplat-Token`` header; the API compares it in constant time
and refuses event intake when the file is missing.

The file is mode 0640 and takes the group of its directory, which the
zabbix container's entrypoint gives to the ``zabbix`` group, so the
alertscript can read it without making it world-readable.
"""
import binascii
import errno
import hmac
import os

HEADER = 'X-Monplat-Token'
DEFAULT_PATH = '/var/lib/monplat/secrets/intake.token'
TOKEN_BYTES = 32
FILE_MODE = 0o640


def token_path(cfg):
    """Return the configured token file path."""
    return (cfg.get('api') or {}).get('intake_token_file') or DEFAULT_PATH


def read_token(path):
    """Return the token stored at ``path``, or None when the file is
    missing or empty."""
    try:
        with open(path) as handle:
            token = handle.read().strip()
    except IOError as exc:
        if exc.errno == errno.ENOENT:
            return None
        raise
    return token or None


def _write_new(path, token):
    """Create ``path`` holding ``token`` unless it exists; return True when
    this call created it.  The token is written to a temporary file that
    is then hard-linked into place, so a reader never sees a partial file
    and two concurrent callers cannot both win."""
    directory = os.path.dirname(path) or '.'
    tmp = os.path.join(directory, '.%s.%d.tmp' % (os.path.basename(path),
                                                 os.getpid()))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, FILE_MODE)
    try:
        os.fchmod(fd, FILE_MODE)
        os.fchown(fd, -1, os.stat(directory).st_gid)
        os.write(fd, token + '\n')
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.link(tmp, path)
        return True
    except OSError as exc:
        if exc.errno != errno.EEXIST:
            raise
        return False
    finally:
        os.unlink(tmp)


def ensure_token(path):
    """Return ``(token, created)``: the token at ``path``, generating a
    random one first if there is none."""
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory, 0o750)
    existing = read_token(path)
    if existing:
        return existing, False
    token = binascii.hexlify(os.urandom(TOKEN_BYTES)).decode('ascii')
    if _write_new(str(path), str(token)):
        return token, True
    return read_token(path), False


def _bytes(value):
    if isinstance(value, bytes):
        return value
    return value.encode('utf-8')


def matches(expected, given):
    """Compare ``given`` with ``expected`` in constant time.  A missing or
    empty value never matches."""
    if not expected or not given:
        return False
    return hmac.compare_digest(_bytes(expected), _bytes(given))
