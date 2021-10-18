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
    MAGENTA = c.Fore.MAGENTA
    BLUE = c.Fore.BLUE
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

def license_color(name):
    match name:
        case 'unknown'|'unreadable':
            return RED;
        case 'binary':
            # a binary file with no license specified
            return MAGENTA;
        case 'WITH'|'AND'|'OR':
            return '';
        case string if 'LGPL' in string:
            return GREEN
        case string if 'GPL' in string:
            return BRIGHT + GREEN
        case string if ('CC0-' in string or
                        'public-domain' in string or
                        'BSD' in string):
            return BLUE
        case 'MIT':
            return BRIGHT + BLUE
        case _:
            return BRIGHT;

def highlight_license(spec):
    paren = spec and spec[0] == '(' and spec[-1] == ')'
    if paren:
        spec = spec[1:-1]

    return ' '.join(f'{license_color(part)}{part}{RESET_ALL}' for part in spec.split())

def do_opts():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument('--repository', default='.', type=pathlib.Path)
    parser.add_argument('--branch')
    parser.add_argument('--glob-suffixes', action='store_true')
    parser.add_argument('--unknown', action='store_true')
    parser.add_argument('subpaths', type=pathlib.Path, nargs='*')

    opts = parser.parse_args()
    return opts

def find_license(path, file):
    n = 0
    for n, line in enumerate(file, start=1):
        line = line.strip()
        if m := re.search(r'SPDX-License-Identifier:\s*(.*)', line):
            text = m.group(1)
            if m := re.match(r'(.*?)(\*/|\*\}|#\}|-->)\s*', text):
                text = m.group(1)
            text = text.strip()
            return text

        if n > 20:
            break
    if n == 0:
        return None
    return 'unknown'

def generate_list(func):
    def wrapper(*args, **kwargs):
        return list(func(*args, **kwargs))
    return functools.update_wrapper(wrapper, func)

@dataclasses.dataclass
class File:
    opts: argparse.Namespace
    path: pathlib.Path

    @functools.cached_property
    def licenses(self):
        if self.path.is_symlink():
            raise ValueError('symlink in unexpected place')

        if self.opts._repo.get_attr(self.path, 'generated'):
            # File is marked as 'generated'. No license applies.
            return ()

        if self.opts._repo.get_attr(self.path, 'binary'):
            # File is marked as 'binary', i.e. unparsable for us.
            return ('binary',)

        with open(self.opts.repository / self.path) as f:
            try:
                lic = find_license(self.path, f)
            except UnicodeDecodeError as e:
                print(f'Cannot read {self.path}: {e}')
                return ('unreadable',)

        if lic is None:
            return ()
        if lic == 'unknown':
            name = self.path.name.removesuffix('.in')
            if any(fnmatch(name, p) for p in IGNORED_FILES):
                # print(f'{path}: no license, ignoring file')
                return ()

        # print(f'{path}: {lic}')
        return (lic,)

    @functools.cached_property
    def type(self):
        return 'file'

    @functools.cached_property
    def suffix(self):
        return ''

    @functools.cached_property
    def order(self):
        # files sort after other types
        return 1, self.licenses, self.path.name

    def walk(self):
        # don't print files without license by default
        if self.licenses:
            yield self

@dataclasses.dataclass
class Subtree:
    opts: argparse.Namespace
    path: pathlib.Path
    tree: pygit2.Object

    @functools.cached_property
    @generate_list
    def entries(self):
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

    @functools.cached_property
    def licenses(self):
        lics = itertools.chain.from_iterable(e.licenses for e in self.entries)
        return tuple(sorted(set(lics)))

    @functools.cached_property
    def type(self):
        return 'tree' if len(self.licenses) > 1 else 'monotree'

    @functools.cached_property
    def suffix(self):
        return '/'

    @functools.cached_property
    def order(self):
        # files sort after other types
        lics = self.licenses
        order = 1 if self.type == 'monotree' else 3
        return order, self.licenses, self.path.name

    def walk(self):
        if self.licenses:
            yield self

        if self.type == 'monotree':
            # The licenses are all identical, don't list individual items.
            return

        grouped = False
        if self.opts.glob_suffixes:
            grouped = GroupSuffixes(self.entries)
            yield from grouped.walk()
        else:
            for item in sorted(self.entries, key=lambda x:x.order):
                yield from item.walk()


@dataclasses.dataclass
class SuffixGlob:
    path: pathlib.Path
    licenses: tuple
    is_tree: bool

    @functools.cached_property
    def suffix(self):
        return '/' if self.is_tree else ''

    @functools.cached_property
    def order(self):
        # globs sort before files but after other types
        return 1, self.licenses, self.path.name

    @functools.cached_property
    def type(self):
        return 'tree-glob' if self.is_tree else 'file-glob'

    def walk(self):
        if self.licenses:
            yield self


class GroupSuffixes:
    def __init__(self, entries):
        self.by_suffix = collections.defaultdict(list)
        self.ungrouped = []

        for item in entries:
            if item.type == 'tree':
                # Cannot group (non-mono-)tree
                self.ungrouped.append(item)
            elif item.type == 'monotree':
                self.by_suffix['/'].append(item)
            else:
                self.by_suffix[item.path.suffix].append(item)

        if self.by_suffix['/'] and self.ungrouped:
            # We cannot group any subtrees if we can't group them all
            self.ungrouped += self.by_suffix['/']
            self.by_suffix['/'].clear()

    def _walk(self):
        for suffix, items in self.by_suffix.items():
            lics = set(item.licenses for item in items)
            types = set(item.type for item in items)

            # We group items if they all have the same license and type.
            # We don't want to "group" one item, because it's clearer to display it
            # without a glob.
            # We can't group files with an empty suffix, because the glob would
            # be confusing. We can only group them if the whole tree is grouped
            # as a monotree, which happens elsewhere.
            if len(lics) == 1 and len(types) == 1 and len(items) > 1 and suffix:
                # all the same
                glob = items[0].path.parent / f'*{suffix}'
                is_tree = types.pop() == 'monotree'
                yield SuffixGlob(glob, lics.pop(), is_tree)
            else:
                for item in items:
                    yield from item.walk()

    def walk(self):
        for item in sorted(self._walk(), key=lambda x:x.order):
            yield from item.walk()

        for item in sorted(self.ungrouped, key=lambda x:x.order):
            yield from item.walk()


def find_files_one_unknown(opts, tree, subpath):
    for item in Subtree(opts, subpath, tree).walk():
        if (item.type != 'tree' and
            item.licenses in {('binary',), ('unknown',)}):

            disp = highlight_license(*item.licenses)
            print(f'{item.path}{item.suffix} → {disp}')


def find_files_one_display(opts, tree, subpath):
    prev = ()
    for item in Subtree(opts, subpath, tree).walk():
        lics = item.licenses
        indent = '    ' * len(item.path.parts)
        if prev == (indent, lics):
            print(f'{indent}{item.path.name}{item.suffix}')
        else:
            disp = ', '.join(highlight_license(spec) for spec in lics)
            print(f'{indent}{item.path.name}{item.suffix}'
                  f' → {disp or "(none)"}')
            prev = (indent, lics)

def find_files_one(opts, tree, subpath):
    if opts.unknown:
        find_files_one_unknown(opts, tree, subpath)
    else:
        find_files_one_display(opts, tree, subpath)

def find_files(opts):
    repo = opts._repo = pygit2.Repository(opts.repository)

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
