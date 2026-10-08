import os
import subprocess
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
SCRIPT = os.path.join(ROOT, 'scripts', 'tree.py')
sys.path.insert(0, os.path.join(ROOT, 'scripts'))

import tree  # noqa: E402


def test_render_nests_paths_with_directories_before_files():
    paths = ['b.txt', 'a/z.py', 'a/c/d.py', 'Makefile']
    assert tree.render(paths) == '\n'.join([
        '.',
        '|-- a',
        '|   |-- c',
        '|   |   `-- d.py',
        '|   `-- z.py',
        '|-- Makefile',
        '`-- b.txt',
    ])


def test_render_is_independent_of_input_order():
    paths = ['x/2', 'x/1', 'y', 'w/v/u']
    assert tree.render(paths) == tree.render(list(reversed(paths)))


def test_render_ignores_blank_lines_and_duplicates():
    assert tree.render(['a/b', '', 'a/b', '  ']) == '.\n`-- a\n    `-- b'


def test_render_of_nothing_is_the_root_only():
    assert tree.render([]) == '.'


def test_script_reads_paths_from_stdin():
    proc = subprocess.Popen([sys.executable, SCRIPT], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE)
    out, _ = proc.communicate(b'docs/SPEC.md\nREADME.md\n')
    assert proc.returncode == 0
    assert out.decode('utf-8') == '.\n|-- docs\n|   `-- SPEC.md\n`-- README.md\n'
