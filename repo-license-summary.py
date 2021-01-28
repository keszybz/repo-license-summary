#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later

"""Summarize SPDX license header status
"""

import itertools
import operator
import argparse
import pathlib
import pygit2
import re
from fnmatch import fnmatch

IGNORED_FILES = [
    'README',
    'README.*',
    'LICENSE*',
    'LINGUAS',    # translation language list
    'POTFILES.*', # translation file list
    '.gitignore',
    '.gitattributes',
    '*.conf',
    '*.options',
    '*.list',
    '*.sym',
    '*.txt',
    '*.example',
    '*.rules',
    '*.pkla',
    '*.gpg',
    '*-map',
    'RFCs',
]

def do_opts():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument('--repository', default='.', type=pathlib.Path)
    parser.add_argument('--branch')

    opts = parser.parse_args()
    return opts

def find_license(path, file):
    for n, line in enumerate(file):
        line = line.strip()
        if m := re.search(r'SPDX-License-Identifier:\s*(.*)', line):
            text = m.group(1)
            text = text.removesuffix('*/').removesuffix('*}').removesuffix('-->').strip()
            return text

        if n > 20:
            break
    return 'unknown'

def analyze(path):
    if path.is_symlink():
        raise ValueError('symlink in unexpected place')

    with open(path) as f:
        try:
            lic = find_license(path, f)
        except UnicodeDecodeError as e:
            print(f'Cannot read {path}: {e}')
            return path, 'file', ['unreadable']

    if lic == 'unknown':
        name = path.name.removesuffix('.in')
        if any(fnmatch(name, p) for p in IGNORED_FILES):
            # print(f'{path}: no license, ignoring file')
            return None

    # print(f'{path}: {lic}')
    return path, 'file', [lic]

def same_license(items):
    if all(it[1] == items[0][1] for it in items):
        return items[0][1]

def walk(path, tree):
    dirs = []
    files = []
    for item in tree:
        itempath = path / item.name
        # print(f'{itempath} {item.type_str}' )
        #if not str(itempath).startswith('src'):
        #    continue

        if item.type_str == 'tree':
            dirs.append((itempath, item))
        elif item.filemode == pygit2.GIT_FILEMODE_LINK:
            # print(f'{path}: symlink')
            continue
        else:
            # We open the file from disk instead of using staged or committed
            # content so it's easy to have up-to-date output when editing.
            if entry := analyze(itempath):
                files.append(entry)

    # Sort file children by license
    yield from sorted(files, key=operator.itemgetter(2))

    # Process all subdirectories after files, so it's nice to list things.
    for itempath, item in dirs:
        subtree = list(walk(itempath, item))
        if not subtree:
            # empty or all-ignored subtree
            continue

        lics = sorted(set(itertools.chain.from_iterable(e[2] for e in subtree)))
        typ = 'tree' if len(lics) > 1 else 'monotree'
        yield itempath, typ, lics
        if typ == 'tree':
            # The licenses are all not identical, list subtree.
            # yield from sorted(subtree, key=operator.itemgetter(2))
            yield from subtree

def find_files(opts):
    repo = pygit2.Repository(opts.repository)

    branch = opts.branch or repo.head.name
    tree = repo.revparse_single(branch).tree
    prev = ()

    for path, typ, lics in walk(pathlib.Path(''), tree):
        indent = '    ' * len(path.parent.parts)
        suffix = {'file':'', 'tree':'/', 'monotree':'/*'}[typ]
        if prev == (indent, lics):
            print(f'{indent}{path.name}{suffix}')
        else:
            print(f'{indent}{path.name}{suffix} → {", ".join(lics)}')
            prev = (indent, lics)

if __name__ == '__main__':
    opts = do_opts()
    find_files(opts)
