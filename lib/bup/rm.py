
from __future__ import absolute_import
import sys

from bup import compat, git, vfs
from bup.client import ClientError
from bup.git import get_commit_items
from bup.helpers import add_error, die_if_errors, log, saved_errors


def append_commit(hash, parent, cp, writer):
    ci = get_commit_items(hash, cp)
    tree = ci.tree.decode('hex')
    author = '%s <%s>' % (ci.author_name, ci.author_mail)
    committer = '%s <%s>' % (ci.committer_name, ci.committer_mail)
    c = writer.new_commit(tree, parent,
                          author, ci.author_sec, ci.author_offset,
                          committer, ci.committer_sec, ci.committer_offset,
                          ci.message)
    return c, tree


def filter_branch(tip_commit_hex, exclude, writer):
    # May return None if everything is excluded.
    commits = [x.decode('hex') for x in git.rev_list(tip_commit_hex)]
    commits.reverse()
    last_c, tree = None, None
    # Rather than assert that we always find an exclusion here, we'll
    # just let the StopIteration signal the error.
    first_exclusion = next(i for i, c in enumerate(commits) if exclude(c))
    if first_exclusion != 0:
        last_c = commits[first_exclusion - 1]
        tree = get_commit_items(last_c.encode('hex'),
                                git.cp()).tree.decode('hex')
        commits = commits[first_exclusion:]
    for c in commits:
        if exclude(c):
            continue
        last_c, tree = append_commit(c.encode('hex'), last_c, git.cp(), writer)
    return last_c

def commit_oid(item):
    if isinstance(item, vfs.Commit):
        return item.coid
    assert isinstance(item, vfs.RevList)
    return item.oid

def rm_saves(saves, writer):
    assert(saves)
    first_branch_item = saves[0][1]
    for save, branch in saves: # Be certain they're all on the same branch
        assert(branch == first_branch_item)
    rm_commits = frozenset([commit_oid(save) for save, branch in saves])
    orig_tip = commit_oid(first_branch_item)
    new_tip = filter_branch(orig_tip.encode('hex'),
                            lambda x: x in rm_commits,
                            writer)
    assert(orig_tip)
    assert(new_tip != orig_tip)
    return orig_tip, new_tip


def dead_items(repo, paths):
    """Return an optimized set of removals, reporting errors via
    add_error, and if there are any errors, return None, None."""
    dead_branches = {}
    dead_saves = {}
    # Scan for bad requests, and opportunities to optimize
    for path in paths:
        try:
            resolved = vfs.resolve(repo, path, follow=False)
        except vfs.IOError as e:
            add_error(e)
            continue
        else:
            leaf_name, leaf_item = resolved[-1]
            if not leaf_item:
                add_error('error: cannot access %r in %r'
                          % ('/'.join(name for name, item in resolved),
                             path))
                continue
            if isinstance(leaf_item, vfs.RevList):  # rm /foo
                branchname = leaf_name
                dead_branches[branchname] = leaf_item
                dead_saves.pop(branchname, None)  # rm /foo obviates rm /foo/bar
            elif isinstance(leaf_item, vfs.Commit):  # rm /foo/bar
                if leaf_name == 'latest':
                    add_error("error: cannot delete 'latest' symlink")
                else:
                    branchname, branchitem = resolved[-2]
                    if branchname not in dead_branches:
                        dead = leaf_item, branchitem
                        dead_saves.setdefault(branchname, []).append(dead)
            else:
                add_error("don't know how to remove %r yet" % path)
    if saved_errors:
        return None, None
    return dead_branches, dead_saves


def bup_rm(repo, paths, compression=6, verbosity=None):
    dead_branches, dead_saves = dead_items(repo, paths)
    die_if_errors('not proceeding with any removals\n')

    updated_refs = {}  # ref_name -> (original_ref, tip_commit(bin))

    for branchname, branchitem in compat.items(dead_branches):
        ref = 'refs/heads/' + branchname
        assert(not ref in updated_refs)
        updated_refs[ref] = (branchitem.oid, None)

    if dead_saves:
        writer = git.PackWriter(compression_level=compression)
        try:
            for branch, saves in compat.items(dead_saves):
                assert(saves)
                updated_refs['refs/heads/' + branch] = rm_saves(saves, writer)
        except:
            if writer:
                writer.abort()
            raise
        else:
            if writer:
                # Must close before we can update the ref(s) below.
                writer.close()

    # Only update the refs here, at the very end, so that if something
    # goes wrong above, the old refs will be undisturbed.  Make an attempt
    # to update each ref.
    for ref_name, info in compat.items(updated_refs):
        orig_ref, new_ref = info
        try:
            if not new_ref:
                git.delete_ref(ref_name, orig_ref.encode('hex'))
            else:
                git.update_ref(ref_name, new_ref, orig_ref)
                if verbosity:
                    new_hex = new_ref.encode('hex')
                    if orig_ref:
                        orig_hex = orig_ref.encode('hex')
                        log('updated %r (%s -> %s)\n'
                            % (ref_name, orig_hex, new_hex))
                    else:
                        log('updated %r (%s)\n' % (ref_name, new_hex))
        except (git.GitError, ClientError) as ex:
            if new_ref:
                add_error('while trying to update %r (%s -> %s): %s'
                          % (ref_name, orig_ref, new_ref, ex))
            else:
                add_error('while trying to delete %r (%s): %s'
                          % (ref_name, orig_ref, ex))
