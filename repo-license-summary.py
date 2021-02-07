#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later

"""Summarize SPDX license header status
"""

import argparse
import dataclasses
import functools
import itertools
import operator
import pathlib
import re
import typing
import pygit2
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
    parser.add_argument('subpaths', type=pathlib.Path, nargs='*')

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

@dataclasses.dataclass
class File:
    opts: argparse.Namespace
    path: pathlib.Path
    _licenses_cache: typing.Optional[list] = dataclasses.field(default=None, init=False)

    def licenses(self):
        if self._licenses_cache is None:
            self._licenses_cache = self._licenses()
        return self._licenses_cache

    def _licenses(self):
        if self.path.is_symlink():
            raise ValueError('symlink in unexpected place')

        with open(opts.repository / self.path) as f:
            try:
                lic = find_license(self.path, f)
            except UnicodeDecodeError as e:
                print(f'Cannot read {self.path}: {e}')
                return ['unreadable']

        if lic == 'unknown':
            name = self.path.name.removesuffix('.in')
            if any(fnmatch(name, p) for p in IGNORED_FILES):
                # print(f'{path}: no license, ignoring file')
                return []

        # print(f'{path}: {lic}')
        return [lic]

    def order(self):
        # files sort after other types
        return (1, self.licenses(), self.path.name)

    def walk(self):
        if lics := self.licenses():
            # don't print files without license by default
            yield self.path, 'file', self.licenses()

@dataclasses.dataclass
class Subtree:
    opts: argparse.Namespace
    path: pathlib.Path
    tree: pygit2.Object
    _entries_cache: typing.Optional[list] =  dataclasses.field(default=None, init=False)
    _licenses_cache: typing.Optional[list] = dataclasses.field(default=None, init=False)

    def _entries(self):
        "Generate an unsorted sequence of items underneath this Subtree"

        for item in self.tree:
            itempath = self.path / item.name
            # print(f'{itempath} {item.type_str}' )
            #if not str(itempath).startswith('src'):
            #    continue

            if item.type_str == 'tree':
                yield Subtree(self.opts, itempath, item)
            elif item.filemode == pygit2.GIT_FILEMODE_LINK:
                # print(f'{path}: symlink')
                continue
            else:
                # We open the file from disk instead of using staged or committed
                # content so it's easy to have up-to-date output when editing.
                yield File(self.opts, itempath)

    def entries(self):
        if self._entries_cache is None:
            self._entries_cache = list(self._entries())
        return self._entries_cache

    def licenses(self):
        if self._licenses_cache is None:
            lics = itertools.chain.from_iterable(e.licenses() for e in self.entries())
            self._licenses_cache = sorted(set(lics))
        return self._licenses_cache

    def order(self):
        # files sort after other types
        lics = self.licenses()
        order = 2 if len(lics) <= 1 else 3
        return (order, self.licenses(), self.path.name)

    def walk(self):
        lics = self.licenses()
        typ = 'tree' if len(lics) > 1 else 'monotree'
        yield self.path, typ, lics

        if typ == 'monotree':
            # The licenses are all identical, don't list individual items.
            # yield from sorted(subtree, key=operator.itemgetter(2))
            return

        for item in sorted(self.entries(), key=lambda x:x.order()):
            yield from item.walk()

TYPE_SUFFIXES = {'file':'', 'tree':'/', 'monotree':'/*'}
def find_files_one(opts, tree, subpath):
    prev = ()
    for path, typ, lics in Subtree(opts, subpath, tree).walk():
        indent = '    ' * len(path.parts)
        if prev == (indent, lics):
            print(f'{indent}{path.name}{TYPE_SUFFIXES[typ]}')
        else:
            print(f'{indent}{path.name}{TYPE_SUFFIXES[typ]} → {", ".join(lics)}')
            prev = (indent, lics)

def find_files(opts):
    repo = pygit2.Repository(opts.repository)

    branch = opts.branch or repo.head.name
    tree = repo.revparse_single(branch).tree

    for subpath in opts.subpaths or [pathlib.Path('')]:
        subtree = tree / subpath if subpath.name else tree
        find_files_one(opts, subtree, subpath)

if __name__ == '__main__':
    opts = do_opts()
    find_files(opts)
