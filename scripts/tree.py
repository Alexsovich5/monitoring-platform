#!/usr/bin/env python
"""Print a directory tree for a list of file paths read from stdin.

Usage: git ls-files | python scripts/tree.py

Directories are listed before files at each level, each group sorted by name,
so the same set of paths always gives the same output.
"""
import sys


def _insert(node, parts):
    head = parts[0]
    if len(parts) == 1:
        node.setdefault(head, None)
    else:
        child = node.get(head)
        if child is None:
            child = node[head] = {}
        _insert(child, parts[1:])


def _lines(node, prefix):
    dirs = sorted(k for k, v in node.items() if v is not None)
    files = sorted(k for k, v in node.items() if v is None)
    names = dirs + files
    out = []
    for i, name in enumerate(names):
        last = i == len(names) - 1
        out.append(prefix + ('`-- ' if last else '|-- ') + name)
        if node[name] is not None:
            out.extend(_lines(node[name], prefix + ('    ' if last else '|   ')))
    return out


def render(paths):
    """Return the tree for ``paths`` as a string without a trailing newline."""
    root = {}
    for path in paths:
        path = path.strip()
        if path:
            _insert(root, [p for p in path.split('/') if p])
    return '\n'.join(['.'] + _lines(root, ''))


def main():
    sys.stdout.write(render(sys.stdin.read().splitlines()) + '\n')


if __name__ == '__main__':
    main()
