#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later

"""Summarize SPDX license header status
"""

import argparse
import collections
import dataclasses
import functools
import itertools
import operator
import pathlib
import re
import typing
import pygit2
from fnmatch import fnmatch

try:
    import colorama as c
    GREEN = c.Fore.GREEN
    YELLOW = c.Fore.YELLOW
    RED = c.Fore.RED
    RESET_ALL = c.Style.RESET_ALL
    BRIGHT = c.Style.BRIGHT
except ImportError:
    GREEN = YELLOW = RED = RESET_ALL = BRIGHT = ''

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
    parser.add_argument('--glob-suffixes', action='store_true')
    parser.add_argument('subpaths', type=pathlib.Path, nargs='*')

    opts = parser.parse_args()
    return opts

def find_license(path, file):
    for n, line in enumerate(file):
        line = line.strip()
        if m := re.search(r'SPDX-License-Identifier:\s*(.*)', line):
            text = m.group(1)
            if m := re.match(r'(.*?)(\*/|\*\}|#\}|-->)\s*', text):
                text = m.group(1)
            text = text.strip()
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

        with open(self.opts.repository / self.path) as f:
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

    def type(self):
        return 'file'

    def suffix(self):
        return self.path.suffix

    def order(self):
        # files sort after other types
        return (1, self.licenses(), self.path.name)

    def walk(self):
        if lics := self.licenses():
            # don't print files without license by default
            yield self.path, self.type(), self.licenses()

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

    def type(self):
        return 'tree' if len(self.licenses()) > 1 else 'monotree'

    def suffix(self):
        return '/'

    def order(self):
        # files sort after other types
        lics = self.licenses()
        order = 2 if len(lics) <= 1 else 3
        return (order, self.licenses(), self.path.name)

    def walk(self):
        lics = self.licenses()
        typ = self.type()
        yield self.path, typ, lics

        if typ == 'monotree':
            # The licenses are all identical, don't list individual items.
            return

        if self.opts.glob_suffixes:
            try:
                grouped = GroupSuffixes(self.entries())
            except ValueError:
                pass
            else:
                # grouped
                yield from grouped.walk()
                return

        # ungrouped
        for item in sorted(self.entries(), key=lambda x:x.order()):
            yield from item.walk()


class GroupSuffixes:
    def __init__(self, entries):
        self.by_ext = collections.defaultdict(list)
        for item in entries:
            if item.type() == 'tree':
                raise ValueError('Cannot group (non-mono-)tree')
            self.by_ext[item.suffix()].append(item)

    def walk(self):
        yield from self._walk(0)
        yield from self._walk(1)
        yield from self._walk(2)

    def _walk(self, phase):
        for suffix, items in self.by_ext.items():
            lics = set(tuple(item.licenses()) for item in items)

            # We group items if they all have the same license.
            # We don't want to "group" one item, because it's clearer to display it
            # without a glob.
            # We can't group files with an empty suffix, because the glob would
            # be confusing. We can only group them if the whole tree is grouped
            # as a monotree, which happens elsewhere.
            if len(lics) == 1 and len(items) > 1 and suffix:
                # all the same
                if phase == 1:
                    glob = items[0].path.parent / f'*{suffix}'
                    type = 'tree-glob' if suffix == '/' else 'file-glob'
                    yield glob, type, lics.pop()
            elif items[0].type() == 'monotree':
                if phase == 0:
                    yield from items[0].walk()
            else:
                if phase == 2:
                    for item in sorted(items, key=lambda x:x.order()):
                        yield from item.walk()


TYPE_SUFFIXES = {'file':'', 'file-glob':'', 'tree':'/', 'tree-glob':'*/', 'monotree':'/*'}
def find_files_one(opts, tree, subpath):
    prev = ()
    for path, typ, lics in Subtree(opts, subpath, tree).walk():
        indent = '    ' * len(path.parts)
        if prev == (indent, lics):
            print(f'{indent}{path.name}{TYPE_SUFFIXES[typ]}')
        else:
            print(f'{indent}{path.name}{TYPE_SUFFIXES[typ]} → {BRIGHT}{", ".join(lics)}{RESET_ALL}')
            prev = (indent, lics)

def find_files(opts):
    repo = pygit2.Repository(opts.repository)

    branch = opts.branch or repo.head.name
    tree = repo.revparse_single(branch).tree

    for subpath in opts.subpaths or [pathlib.Path('')]:
        subtree = tree / subpath if subpath.name else tree
        find_files_one(opts, subtree, subpath)

def __main__():
    opts = do_opts()
    find_files(opts)

if __name__ == '__main__':
    try:
        __main__()
    except BrokenPipeError:
        # Don't fail if we are piped to a pager and the user exists before the end
        pass
