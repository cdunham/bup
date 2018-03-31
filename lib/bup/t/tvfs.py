
from __future__ import absolute_import, print_function
from collections import namedtuple
from errno import ELOOP, ENOTDIR
from io import BytesIO
from os import environ, symlink
from stat import S_IFDIR, S_IFREG, S_ISDIR, S_ISREG
from sys import stderr
from time import localtime, strftime

from wvtest import *

from bup import git, metadata, vfs
from bup.git import BUP_CHUNKED
from bup.metadata import Metadata
from bup.repo import LocalRepo
from bup.test.vfs import tree_dict
from buptest import ex, exo, no_lingering_errors, test_tempdir

top_dir = '../../..'
bup_tmp = os.path.realpath('../../../t/tmp')
bup_path = top_dir + '/bup'
start_dir = os.getcwd()

## The clear_cache() calls below are to make sure that the test starts
## from a known state since at the moment the cache entry for a given
## item (like a commit) can change.  For example, its meta value might
## be promoted from a mode to a Metadata instance once the tree it
## refers to is traversed.

def run_augment_item_meta_tests(repo,
                                file_path, file_size,
                                link_path, link_target):
    _, file_item = vfs.resolve(repo, file_path)[-1]
    _, link_item = vfs.resolve(repo, link_path, follow=False)[-1]
    wvpass(isinstance(file_item.meta, Metadata))
    wvpass(isinstance(link_item.meta, Metadata))
    # Note: normally, modifying item.meta values is forbidden
    file_item.meta.size = file_item.meta.size or vfs.item_size(repo, file_item)
    link_item.meta.size = link_item.meta.size or vfs.item_size(repo, link_item)

    ## Ensure a fully populated item is left alone
    augmented = vfs.augment_item_meta(repo, file_item)
    wvpass(augmented is file_item)
    wvpass(augmented.meta is file_item.meta)
    augmented = vfs.augment_item_meta(repo, file_item, include_size=True)
    wvpass(augmented is file_item)
    wvpass(augmented.meta is file_item.meta)

    ## Ensure a missing size is handled poperly
    file_item.meta.size = None
    augmented = vfs.augment_item_meta(repo, file_item)
    wvpass(augmented is file_item)
    wvpass(augmented.meta is file_item.meta)
    augmented = vfs.augment_item_meta(repo, file_item, include_size=True)
    wvpass(augmented is not file_item)
    wvpasseq(file_size, augmented.meta.size)

    ## Ensure a meta mode is handled properly
    mode_item = file_item._replace(meta=vfs.default_file_mode)
    augmented = vfs.augment_item_meta(repo, mode_item)
    augmented_w_size = vfs.augment_item_meta(repo, mode_item, include_size=True)
    for item in (augmented, augmented_w_size):
        meta = item.meta
        wvpass(item is not file_item)
        wvpass(isinstance(meta, Metadata))
        wvpasseq(vfs.default_file_mode, meta.mode)
        wvpasseq((0, 0, 0, 0, 0),
                 (meta.uid, meta.gid, meta.atime, meta.mtime, meta.ctime))
    wvpass(augmented.meta.size is None)
    wvpasseq(file_size, augmented_w_size.meta.size)

    ## Ensure symlinks are handled properly
    mode_item = link_item._replace(meta=vfs.default_symlink_mode)
    augmented = vfs.augment_item_meta(repo, mode_item)
    wvpass(augmented is not mode_item)
    wvpass(isinstance(augmented.meta, Metadata))
    wvpasseq(link_target, augmented.meta.symlink_target)
    wvpasseq(len(link_target), augmented.meta.size)
    augmented = vfs.augment_item_meta(repo, mode_item, include_size=True)
    wvpass(augmented is not mode_item)
    wvpass(isinstance(augmented.meta, Metadata))
    wvpasseq(link_target, augmented.meta.symlink_target)
    wvpasseq(len(link_target), augmented.meta.size)


@wvtest
def test_item_mode():
    with no_lingering_errors():
        mode = S_IFDIR | 0o755
        meta = metadata.from_path('.')
        oid = '\0' * 20
        wvpasseq(mode, vfs.item_mode(vfs.Item(oid=oid, meta=mode)))
        wvpasseq(meta.mode, vfs.item_mode(vfs.Item(oid=oid, meta=meta)))

@wvtest
def test_reverse_suffix_duplicates():
    suffix = lambda x: tuple(vfs._reverse_suffix_duplicates(x))
    wvpasseq(('x',), suffix(('x',)))
    wvpasseq(('x', 'y'), suffix(('x', 'y')))
    wvpasseq(('x-1', 'x-0'), suffix(('x',) * 2))
    wvpasseq(['x-%02d' % n for n in reversed(range(11))],
             list(suffix(('x',) * 11)))
    wvpasseq(('x-1', 'x-0', 'y'), suffix(('x', 'x', 'y')))
    wvpasseq(('x', 'y-1', 'y-0'), suffix(('x', 'y', 'y')))
    wvpasseq(('x', 'y-1', 'y-0', 'z'), suffix(('x', 'y', 'y', 'z')))

@wvtest
def test_misc():
    with no_lingering_errors():
        with test_tempdir('bup-tvfs-') as tmpdir:
            bup_dir = tmpdir + '/bup'
            environ['GIT_DIR'] = bup_dir
            environ['BUP_DIR'] = bup_dir
            git.repodir = bup_dir
            data_path = tmpdir + '/src'
            os.mkdir(data_path)
            with open(data_path + '/file', 'w+') as tmpfile:
                tmpfile.write(b'canary\n')
            symlink('file', data_path + '/symlink')
            ex((bup_path, 'init'))
            ex((bup_path, 'index', '-v', data_path))
            ex((bup_path, 'save', '-d', '100000', '-tvvn', 'test', '--strip',
                data_path))
            repo = LocalRepo()

            wvstart('readlink')
            ls_tree = exo(('git', 'ls-tree', 'test', 'symlink')).out
            mode, typ, oidx, name = ls_tree.strip().split(None, 3)
            assert name == 'symlink'
            link_item = vfs.Item(oid=oidx.decode('hex'), meta=int(mode, 8))
            wvpasseq('file', vfs.readlink(repo, link_item))

            ls_tree = exo(('git', 'ls-tree', 'test', 'file')).out
            mode, typ, oidx, name = ls_tree.strip().split(None, 3)
            assert name == 'file'
            file_item = vfs.Item(oid=oidx.decode('hex'), meta=int(mode, 8))
            wvexcept(Exception, vfs.readlink, repo, file_item)

            wvstart('item_size')
            wvpasseq(4, vfs.item_size(repo, link_item))
            wvpasseq(7, vfs.item_size(repo, file_item))
            meta = metadata.from_path(__file__)
            meta.size = 42
            fake_item = file_item._replace(meta=meta)
            wvpasseq(42, vfs.item_size(repo, fake_item))

            wvstart('augment_item_meta')
            run_augment_item_meta_tests(repo,
                                        '/test/latest/file', 7,
                                        '/test/latest/symlink', 'file')

            wvstart('copy_item')
            # FIXME: this caused StopIteration
            #_, file_item = vfs.resolve(repo, '/file')[-1]
            _, file_item = vfs.resolve(repo, '/test/latest/file')[-1]
            file_copy = vfs.copy_item(file_item)
            wvpass(file_copy is not file_item)
            wvpass(file_copy.meta is not file_item.meta)
            wvpass(isinstance(file_copy, tuple))
            wvpass(file_item.meta.user)
            wvpass(file_copy.meta.user)
            file_copy.meta.user = None
            wvpass(file_item.meta.user)

@wvtest
def test_contents_with_mismatched_bupm_git_ordering():
    with no_lingering_errors():
        with test_tempdir('bup-tvfs-') as tmpdir:
            bup_dir = tmpdir + '/bup'
            environ['GIT_DIR'] = bup_dir
            environ['BUP_DIR'] = bup_dir
            git.repodir = bup_dir
            data_path = tmpdir + '/src'
            os.mkdir(data_path)
            os.mkdir(data_path + '/foo')
            with open(data_path + '/foo.', 'w+') as tmpfile:
                tmpfile.write(b'canary\n')
            ex((bup_path, 'init'))
            ex((bup_path, 'index', '-v', data_path))
            ex((bup_path, 'save', '-tvvn', 'test', '--strip',
                data_path))
            repo = LocalRepo()
            tip_sref = exo(('git', 'show-ref', 'refs/heads/test')).out
            tip_oidx = tip_sref.strip().split()[0]
            tip_tree_oidx = exo(('git', 'log', '--pretty=%T', '-n1',
                                 tip_oidx)).out.strip()
            tip_tree_oid = tip_tree_oidx.decode('hex')
            tip_tree = tree_dict(repo, tip_tree_oid)

            name, item = vfs.resolve(repo, '/test/latest')[2]
            wvpasseq('latest', name)
            expected = frozenset((x.name, vfs.Item(oid=x.oid, meta=x.meta))
                                 for x in (tip_tree[name]
                                           for name in ('.', 'foo', 'foo.')))
            contents = tuple(vfs.contents(repo, item))
            wvpasseq(expected, frozenset(contents))
            # Spot check, in case tree_dict shares too much code with the vfs
            name, item = next(((n, i) for n, i in contents if n == 'foo'))
            wvpass(S_ISDIR(item.meta))
            name, item = next(((n, i) for n, i in contents if n == 'foo.'))
            wvpass(S_ISREG(item.meta.mode))

@wvtest
def test_duplicate_save_dates():
    with no_lingering_errors():
        with test_tempdir('bup-tvfs-') as tmpdir:
            bup_dir = tmpdir + '/bup'
            environ['GIT_DIR'] = bup_dir
            environ['BUP_DIR'] = bup_dir
            environ['TZ'] = 'UTC'
            git.repodir = bup_dir
            data_path = tmpdir + '/src'
            os.mkdir(data_path)
            with open(data_path + '/file', 'w+') as tmpfile:
                tmpfile.write(b'canary\n')
            ex((bup_path, 'init'))
            ex((bup_path, 'index', '-v', data_path))
            for i in range(11):
                ex((bup_path, 'save', '-d', '100000', '-n', 'test', data_path))
            repo = LocalRepo()
            res = vfs.resolve(repo, '/test')
            wvpasseq(2, len(res))
            name, revlist = res[-1]
            wvpasseq('test', name)
            wvpasseq(('.',
                      '1970-01-02-034640-00',
                      '1970-01-02-034640-01',
                      '1970-01-02-034640-02',
                      '1970-01-02-034640-03',
                      '1970-01-02-034640-04',
                      '1970-01-02-034640-05',
                      '1970-01-02-034640-06',
                      '1970-01-02-034640-07',
                      '1970-01-02-034640-08',
                      '1970-01-02-034640-09',
                      '1970-01-02-034640-10',
                      'latest'),
                     tuple(sorted(x[0] for x in vfs.contents(repo, revlist))))

# FIXME: add tests for the want_meta=False cases.
