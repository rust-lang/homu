import argparse
import github3
import toml
import json
import re
import functools
from . import comments
from . import utils
from .parse_issue_comment import parse_issue_comment
from .auth import verify as verify_auth
from .utils import lazy_debug
import logging
from threading import Thread, Lock, Timer
import time
import traceback
import sqlite3
import requests
from contextlib import contextmanager
from queue import Queue
import os
import sys
from enum import IntEnum, Enum
import subprocess
from .git_helper import SSH_KEY_FILE
import shlex
import random
import weakref

STATUS_TO_PRIORITY = {
    'pending': 1,
    'approved': 2,
    '': 3,
    'error': 4,
    'failure': 5,
    'success': 6,
}

INTERRUPTED_BY_HOMU_FMT = 'Interrupted by Homu ({})'
INTERRUPTED_BY_HOMU_RE = re.compile(r'Interrupted by Homu \((.+?)\)')
DEFAULT_TEST_TIMEOUT = 3600 * 10

VARIABLES_RE = re.compile(r'\${([a-zA-Z_]+)}')

global_cfg = {}


# Replace @mention with `@mention` to suppress pings in merge commits.
# Note: Don't replace non-mentions like "email@gmail.com".
def suppress_pings(text):
    return re.sub(r'\B(@\S+)', r'`\g<1>`', text)  # noqa


@contextmanager
def buildbot_sess(repo_cfg):
    sess = requests.Session()

    sess.post(
        repo_cfg['buildbot']['url'] + '/login',
        allow_redirects=False,
        data={
            'username': repo_cfg['buildbot']['username'],
            'passwd': repo_cfg['buildbot']['password'],
        })

    yield sess

    sess.get(repo_cfg['buildbot']['url'] + '/logout', allow_redirects=False)


db_query_lock = Lock()


def db_query(db, *args):
    with db_query_lock:
        db.execute(*args)


class Repository:
    treeclosed = -1
    treeclosed_src = None
    gh = None
    gh_test_on_fork = None
    label = None
    db = None

    def __init__(self, gh, repo_label, db):
        self.gh = gh
        self.repo_label = repo_label
        self.db = db
        db_query(
            db,
            'SELECT treeclosed, treeclosed_src FROM repos WHERE repo = ?',
            [repo_label]
        )
        row = db.fetchone()
        if row:
            self.treeclosed = row[0]
            self.treeclosed_src = row[1]
        else:
            self.treeclosed = -1
            self.treeclosed_src = None

    def update_treeclosed(self, value, src):
        self.treeclosed = value
        self.treeclosed_src = src
        db_query(
            self.db,
            'DELETE FROM repos where repo = ?',
            [self.repo_label]
        )
        if value > 0:
            db_query(
                self.db,
                '''
                    INSERT INTO repos (repo, treeclosed, treeclosed_src)
                    VALUES (?, ?, ?)
                ''',
                [self.repo_label, value, src]
            )

    def __lt__(self, other):
        return self.gh < other.gh


class PullReqState:
    num = 0
    priority = 0
    rollup = 0
    title = ''
    body = ''
    head_ref = ''
    base_ref = ''
    assignee = ''
    delegate = ''

    def __init__(self, num, head_sha, status, db, repo_label, mergeable_que,
                 gh, owner, name, label_events, repos, test_on_fork):
        self.head_advanced('', use_db=False)

        self.num = num
        self.head_sha = head_sha
        self.status = status
        self.db = db
        self.repo_label = repo_label
        self.mergeable_que = mergeable_que
        self.gh = gh
        self.owner = owner
        self.name = name
        self.repos = repos
        self.timeout_timer = None
        self.test_started = time.time()
        self.label_events = label_events
        self.test_on_fork = test_on_fork

    def head_advanced(self, head_sha, *, use_db=True):
        self.head_sha = head_sha
        self.approved_by = ''
        self.status = ''
        self.merge_sha = ''
        self.build_res = {}
        self.try_ = False
        self.mergeable = None

        if use_db:
            self.set_status('')
            self.set_mergeable(None)
            self.init_build_res([])

    def __repr__(self):
        fmt = 'PullReqState:{}/{}#{}(approved_by={}, priority={}, status={})'
        return fmt.format(
            self.owner,
            self.name,
            self.num,
            self.approved_by,
            self.priority,
            self.status,
        )

    def sort_key(self):
        return [
            STATUS_TO_PRIORITY.get(self.get_status(), -1),
            1 if self.mergeable is False else 0,
            0 if self.approved_by else 1,
            -self.priority,
            self.rollup,
            self.num,
        ]

    def __lt__(self, other):
        return self.sort_key() < other.sort_key()

    def get_issue(self):
        issue = getattr(self, 'issue', None)
        if not issue:
            issue = self.issue = self.get_repo().issue(self.num)
        return issue

    def add_comment(self, comment):
        if isinstance(comment, comments.Comment):
            comment = "%s\n<!-- homu: %s -->" % (
                comment.render(), comment.jsonify(),
            )
        self.get_issue().create_comment(comment)

    def change_labels(self, event):
        event = self.label_events.get(event.value, {})
        removes = event.get('remove', [])
        adds = event.get('add', [])
        unless = event.get('unless', [])
        if not removes and not adds:
            return

        issue = self.get_issue()
        labels = {label.name for label in issue.iter_labels()}
        if labels.isdisjoint(unless):
            labels.difference_update(removes)
            labels.update(adds)
            issue.replace_labels(list(labels))

    def set_status(self, status):
        self.status = status
        if self.timeout_timer:
            self.timeout_timer.cancel()
            self.timeout_timer = None

        db_query(
            self.db,
            'UPDATE pull SET status = ? WHERE repo = ? AND num = ?',
            [self.status, self.repo_label, self.num]
        )

        # FIXME: self.try_ should also be saved in the database
        if not self.try_:
            db_query(
                self.db,
                'UPDATE pull SET merge_sha = ? WHERE repo = ? AND num = ?',
                [self.merge_sha, self.repo_label, self.num]
            )

    def get_status(self):
        if self.status == '' and self.approved_by:
            if self.mergeable is not False:
                return 'approved'
        return self.status

    def set_mergeable(self, mergeable, *, cause=None, que=True):
        if mergeable is not None:
            self.mergeable = mergeable

            db_query(
                self.db,
                'INSERT OR REPLACE INTO mergeable (repo, num, mergeable) VALUES (?, ?, ?)',  # noqa
                [self.repo_label, self.num, self.mergeable]
            )
        else:
            if que:
                self.mergeable_que.put([self, cause])
            else:
                self.mergeable = None

            db_query(
                self.db,
                'DELETE FROM mergeable WHERE repo = ? AND num = ?',
                [self.repo_label, self.num]
            )

    def init_build_res(self, builders, *, use_db=True):
        self.build_res = {x: {
            'res': None,
            'url': '',
        } for x in builders}

        if use_db:
            db_query(
                self.db,
                'DELETE FROM build_res WHERE repo = ? AND num = ?',
                [self.repo_label, self.num]
            )

    def set_build_res(self, builder, res, url):
        if builder not in self.build_res:
            raise Exception('Invalid builder: {}'.format(builder))

        self.build_res[builder] = {
            'res': res,
            'url': url,
        }

        db_query(
            self.db,
            'INSERT OR REPLACE INTO build_res (repo, num, builder, res, url, merge_sha) VALUES (?, ?, ?, ?, ?, ?)',  # noqa
            [
                self.repo_label,
                self.num,
                builder,
                res,
                url,
                self.merge_sha,
            ])

    def build_res_summary(self):
        return ', '.join('{}: {}'.format(builder, data['res'])
                         for builder, data in self.build_res.items())

    def get_repo(self):
        repo = self.repos[self.repo_label].gh
        if not repo:
            repo = self.gh.repository(self.owner, self.name)
            self.repos[self.repo_label].gh = repo

            assert repo.owner.login == self.owner
            assert repo.name == self.name
        return repo

    def get_test_on_fork_repo(self):
        if not self.test_on_fork:
            return None

        repo = self.repos[self.repo_label].gh_test_on_fork
        if not repo:
            repo = self.gh.repository(
                self.test_on_fork['owner'],
                self.test_on_fork['name'],
            )
            self.repos[self.repo_label].gh_test_on_fork = repo

            assert repo.owner.login == self.test_on_fork['owner']
            assert repo.name == self.test_on_fork['name']
        return repo

    def save(self):
        db_query(
            self.db,
            'INSERT OR REPLACE INTO pull (repo, num, status, merge_sha, title, body, head_sha, head_ref, base_ref, assignee, approved_by, priority, try_, rollup, delegate) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',  # noqa
            [
                self.repo_label,
                self.num,
                self.status,
                self.merge_sha,
                self.title,
                self.body,
                self.head_sha,
                self.head_ref,
                self.base_ref,
                self.assignee,
                self.approved_by,
                self.priority,
                self.try_,
                self.rollup,
                self.delegate,
            ])

    def refresh(self):
        issue = self.get_repo().issue(self.num)

        self.title = issue.title
        self.body = suppress_pings(issue.body)

    def fake_merge(self, repo_cfg):
        if not repo_cfg.get('linear', False):
            return
        if repo_cfg.get('autosquash', False):
            return

        issue = self.get_issue()
        title = issue.title
        # We tell github to close the PR via the commit message, but it
        # doesn't know that constitutes a merge.  Edit the title so that it's
        # clearer.
        merged_prefix = '[merged] '
        if not title.startswith(merged_prefix):
            title = merged_prefix + title
            issue.edit(title=title)

    def change_treeclosed(self, value, src):
        self.repos[self.repo_label].update_treeclosed(value, src)

    def blocked_by_closed_tree(self):
        treeclosed = self.repos[self.repo_label].treeclosed
        return treeclosed if self.priority < treeclosed else None

    def start_testing(self, timeout):
        self.test_started = time.time()     # FIXME: Save in the local database
        self.set_status('pending')

        wm = weakref.WeakMethod(self.timed_out)

        def timed_out():
            m = wm()
            if m:
                m()
        timer = Timer(timeout, timed_out)
        timer.start()
        self.timeout_timer = timer

    def timed_out(self):
        print('* Test timed out: {}'.format(self))

        self.merge_sha = ''
        self.save()
        self.set_status('failure')

        utils.github_create_status(
            self.get_repo(),
            self.head_sha,
            'failure',
            '',
            'Test timed out',
            context='homu')
        self.add_comment(comments.TimedOut())
        self.change_labels(LabelEvent.TIMED_OUT)

    def record_retry_log(self, src, body):
        # destroy ancient records
        db_query(
            self.db,
            "DELETE FROM retry_log WHERE repo = ? AND time < date('now', ?)",
            [self.repo_label, global_cfg.get('retry_log_expire', '-42 days')],
        )
        db_query(
            self.db,
            'INSERT INTO retry_log (repo, num, src, msg) VALUES (?, ?, ?, ?)',
            [self.repo_label, self.num, src, body],
        )

    @property
    def author(self):
        """
        Get the GitHub login name of the author of the pull request
        """
        return self.get_issue().user.login


def sha_cmp(short, full):
    return len(short) >= 4 and short == full[:len(short)]


def sha_or_blank(sha):
    return sha if re.match(r'^[0-9a-f]+$', sha) else ''


class AuthState(IntEnum):
    # Higher is more privileged
    REVIEWER = 3
    TRY = 2
    NONE = 1


class LabelEvent(Enum):
    APPROVED = 'approved'
    REJECTED = 'rejected'
    CONFLICT = 'conflict'
    SUCCEED = 'succeed'
    FAILED = 'failed'
    TRY = 'try'
    TRY_SUCCEED = 'try_succeed'
    TRY_FAILED = 'try_failed'
    EXEMPTED = 'exempted'
    TIMED_OUT = 'timed_out'
    INTERRUPTED = 'interrupted'
    PUSHED = 'pushed'


PORTAL_TURRET_DIALOG = ["Target acquired", "Activated", "There you are"]
PORTAL_TURRET_IMAGE = "https://cloud.githubusercontent.com/assets/1617736/22222924/c07b2a1c-e16d-11e6-91b3-ac659550585c.png"  # noqa


def parse_commands(body, username, user_id, repo_label, repo_cfg, state,
                   my_username, db, states, *, realtime=False, sha='',
                   command_src=''):
    global global_cfg
    state_changed = False

    _reviewer_auth_verified = functools.partial(
        verify_auth,
        username,
        user_id,
        repo_label,
        repo_cfg,
        state,
        AuthState.REVIEWER,
        realtime,
        my_username,
    )
    _try_auth_verified = functools.partial(
        verify_auth,
        username,
        user_id,
        repo_label,
        repo_cfg,
        state,
        AuthState.TRY,
        realtime,
        my_username,
    )

    hooks = []
    if 'hooks' in global_cfg:
        hooks = list(global_cfg['hooks'].keys())

    commands = parse_issue_comment(username, body, sha, my_username, hooks)

    for command in commands:
        found = True
        if command.action == 'approve':
            if not _reviewer_auth_verified():
                continue

            approver = command.actor
            cur_sha = command.commit

            # Ignore WIP PRs
            is_wip = False
            for wip_kw in ['WIP', 'TODO', '[WIP]', '[TODO]', '[DO NOT MERGE]']:
                if state.title.upper().startswith(wip_kw):
                    if realtime:
                        state.add_comment(comments.ApprovalIgnoredWip(
                            sha=state.head_sha,
                            wip_keyword=wip_kw,
                        ))
                    is_wip = True
                    break
            if is_wip:
                continue

            # Sometimes, GitHub sends the head SHA of a PR as 0000000
            # through the webhook. This is called a "null commit", and
            # seems to happen when GitHub internally encounters a race
            # condition. Last time, it happened when squashing commits
            # in a PR. In this case, we just try to retrieve the head
            # SHA manually.
            if all(x == '0' for x in state.head_sha):
                if realtime:
                    state.add_comment(
                        ':bangbang: Invalid head SHA found, retrying: `{}`'
                        .format(state.head_sha)
                    )

                state.head_sha = state.get_repo().pull_request(state.num).head.sha  # noqa
                state.save()

                assert any(x != '0' for x in state.head_sha)

            if state.approved_by and realtime and username != my_username:
                for _state in states[state.repo_label].values():
                    if _state.status == 'pending':
                        break
                else:
                    _state = None

                lines = []

                if state.status in ['failure', 'error']:
                    lines.append('- This pull request previously failed. You should add more commits to fix the bug, or use `retry` to trigger a build again.')  # noqa

                if _state:
                    if state == _state:
                        lines.append('- This pull request is currently being tested. If there\'s no response from the continuous integration service, you may use `retry` to trigger a build again.')  # noqa
                    else:
                        lines.append('- There\'s another pull request that is currently being tested, blocking this pull request: #{}'.format(_state.num))  # noqa

                if lines:
                    lines.insert(0, '')
                lines.insert(0, ':bulb: This pull request was already approved, no need to approve it again.')  # noqa

                state.add_comment('\n'.join(lines))

            if sha_cmp(cur_sha, state.head_sha):
                state.approved_by = approver
                state.try_ = False
                state.set_status('')

                state.save()
            elif realtime and username != my_username:
                if cur_sha:
                    msg = '`{}` is not a valid commit SHA.'.format(cur_sha)
                    state.add_comment(
                        ':scream_cat: {} Please try again with `{}`.'
                        .format(msg, state.head_sha)
                    )
                else:
                    state.add_comment(comments.Approved(
                        sha=state.head_sha,
                        approver=approver,
                        bot=my_username,
                    ))
                    treeclosed = state.blocked_by_closed_tree()
                    if treeclosed:
                        state.add_comment(
                            ':evergreen_tree: The tree is currently closed for pull requests below priority {}, this pull request will be tested once the tree is reopened'  # noqa
                            .format(treeclosed)
                        )
                    state.change_labels(LabelEvent.APPROVED)

        elif command.action == 'unapprove':
            # Allow the author of a pull request to unapprove their own PR. The
            # author can already perform other actions that effectively
            # unapprove the PR (change the target branch, push more commits,
            # etc.) so allowing them to directly unapprove it is also allowed.

            # Because verify_auth has side-effects (especially, it may leave a
            # comment on the pull request if the user is not authorized), we
            # need to do the author check BEFORE the verify_auth check.
            if state.author != username:
                if not verify_auth(username, user_id, repo_label, repo_cfg,
                                   state, AuthState.REVIEWER, realtime,
                                   my_username):
                    continue

            state.approved_by = ''
            state.save()
            if realtime:
                state.change_labels(LabelEvent.REJECTED)

        elif command.action == 'prioritize':
            if not verify_auth(username, user_id, repo_label, repo_cfg, state,
                               AuthState.TRY, realtime, my_username):
                continue

            pvalue = command.priority

            if pvalue > global_cfg['max_priority']:
                if realtime:
                    state.add_comment(
                        ':stop_sign: Priority higher than {} is ignored.'
                        .format(global_cfg['max_priority'])
                    )
                continue
            state.priority = pvalue
            state.save()

        elif command.action == 'delegate':
            if not verify_auth(username, user_id, repo_label, repo_cfg, state,
                               AuthState.REVIEWER, realtime, my_username):
                continue

            state.delegate = command.delegate_to
            state.save()

            if realtime:
                state.add_comment(comments.Delegated(
                    delegator=username,
                    delegate=state.delegate
                ))

        elif command.action == 'undelegate':
            # TODO: why is this a TRY?
            if not _try_auth_verified():
                continue
            state.delegate = ''
            state.save()

        elif command.action == 'delegate-author':
            if not _reviewer_auth_verified():
                continue

            state.delegate = state.get_repo().pull_request(state.num).user.login  # noqa
            state.save()

            if realtime:
                state.add_comment(comments.Delegated(
                    delegator=username,
                    delegate=state.delegate
                ))

        elif command.action == 'retry' and realtime:
            if not _try_auth_verified():
                continue
            state.set_status('')
            if realtime:
                event = LabelEvent.TRY if state.try_ else LabelEvent.APPROVED
                state.record_retry_log(command_src, body)
                state.change_labels(event)

        elif command.action in ['try', 'untry'] and realtime:
            if not _try_auth_verified():
                continue
            if state.status == '' and state.approved_by:
                state.add_comment(
                    ':no_good: '
                    'Please do not `try` after a pull request has been `r+`ed.'
                    ' If you need to `try`, unapprove (`r-`) it first.'
                )
                continue

            state.try_ = command.action == 'try'

            state.merge_sha = ''
            state.init_build_res([])

            state.save()
            if realtime and state.try_:
                # If we've tried before, the status will be 'success', and this
                # new try will not be picked up. Set the status back to ''
                # so the try will be run again.
                state.set_status('')
                # `try-` just resets the `try` bit and doesn't correspond to
                # any meaningful labeling events.
                state.change_labels(LabelEvent.TRY)

        elif command.action == 'rollup':
            if not _try_auth_verified():
                continue
            state.rollup = command.rollup_value

            state.save()

        elif command.action == 'force' and realtime:
            if not _try_auth_verified():
                continue
            if 'buildbot' in repo_cfg:
                with buildbot_sess(repo_cfg) as sess:
                    res = sess.post(
                        repo_cfg['buildbot']['url'] + '/builders/_selected/stopselected',   # noqa
                        allow_redirects=False,
                        data={
                            'selected': repo_cfg['buildbot']['builders'],
                            'comments': INTERRUPTED_BY_HOMU_FMT.format(int(time.time())),  # noqa
                        }
                    )

            if 'authzfail' in res.text:
                err = 'Authorization failed'
            else:
                mat = re.search('(?s)<div class="error">(.*?)</div>', res.text)
                if mat:
                    err = mat.group(1).strip()
                    if not err:
                        err = 'Unknown error'
                else:
                    err = ''

            if err:
                state.add_comment(
                    ':bomb: Buildbot returned an error: `{}`'.format(err)
                )

        elif command.action == 'clean' and realtime:
            if not _try_auth_verified():
                continue
            state.merge_sha = ''
            state.init_build_res([])

            state.save()

        elif command.action == 'ping' and realtime:
            if command.ping_type == 'portal':
                state.add_comment(
                    ":cake: {}\n\n![]({})".format(
                        random.choice(PORTAL_TURRET_DIALOG),
                        PORTAL_TURRET_IMAGE)
                    )
            else:
                state.add_comment(":sleepy: I'm awake I'm awake")

        elif command.action == 'treeclosed':
            if not _reviewer_auth_verified():
                continue
            state.change_treeclosed(command.treeclosed_value, command_src)
            state.save()

        elif command.action == 'untreeclosed':
            if not _reviewer_auth_verified():
                continue
            state.change_treeclosed(-1, None)
            state.save()

        elif command.action == 'hook':
            hook = command.hook_name
            hook_cfg = global_cfg['hooks'][hook]
            if hook_cfg['realtime'] and not realtime:
                continue
            if hook_cfg['access'] == "reviewer":
                if not _reviewer_auth_verified():
                    continue
            else:
                if not _try_auth_verified():
                    continue
            Thread(
                target=handle_hook_response,
                args=[state, hook_cfg, body, command.hook_extra]
            ).start()

        else:
            found = False

        if found:
            state_changed = True

    return state_changed


def handle_hook_response(state, hook_cfg, body, extra_data):
    post_data = {}
    post_data["pull"] = state.num
    post_data["body"] = body
    post_data["extra_data"] = extra_data
    print(post_data)
    response = requests.post(hook_cfg['endpoint'], json=post_data)
    print(response.text)

    # We only post a response if we're configured to have a response
    # non-realtime hooks cannot post
    if hook_cfg['has_response'] and hook_cfg['realtime']:
        state.add_comment(response.text)


def git_push(git_cmd, branch, state):
    merge_sha = subprocess.check_output(git_cmd('rev-parse', 'HEAD')).decode('ascii').strip()  # noqa

    if utils.silent_call(git_cmd('push', '-f', 'test-origin', branch)):
        utils.logged_call(git_cmd('branch', '-f', 'homu-tmp', branch))
        utils.logged_call(git_cmd('push', '-f', 'test-origin', 'homu-tmp'))

        def inner():
            utils.github_create_status(
                state.get_repo(),
                merge_sha,
                'success',
                '',
                'Branch protection bypassed',
                context='homu',
            )

        def fail(err):
            state.add_comment(
                ':boom: Unable to create a status for {} ({})'
                .format(merge_sha, err)
            )

        utils.retry_until(inner, fail, state)

        utils.logged_call(git_cmd('push', '-f', 'test-origin', branch))

    return merge_sha


def init_local_git_cmds(repo_cfg, git_cfg):
    fpath = 'cache/{}/{}'.format(repo_cfg['owner'], repo_cfg['name'])
    genurl = lambda cfg: 'git@github.com:{}/{}.git'.format(cfg['owner'], cfg['name'])  # noqa

    if not os.path.exists(SSH_KEY_FILE):
        os.makedirs(os.path.dirname(SSH_KEY_FILE), exist_ok=True)
        with open(SSH_KEY_FILE, 'w') as fp:
            fp.write(git_cfg['ssh_key'])
        os.chmod(SSH_KEY_FILE, 0o600)

    if not os.path.exists(fpath):
        utils.logged_call(['git', 'init', fpath])

    remotes = {
        'origin': genurl(repo_cfg),
        'test-origin': genurl(repo_cfg.get('test-on-fork', repo_cfg)),
    }

    for remote, url in remotes.items():
        try:
            utils.logged_call(['git', '-C', fpath, 'remote', 'set-url', remote, url])  # noqa
            utils.logged_call(['git', '-C', fpath, 'remote', 'set-url', '--push', remote, url])  # noqa
        except subprocess.CalledProcessError:
            utils.logged_call(['git', '-C', fpath, 'remote', 'add', remote, url])  # noqa

    return lambda *args: ['git', '-C', fpath] + list(args)


def branch_equal_to_merge(git_cmd, state, branch):
    utils.logged_call(git_cmd('fetch', 'origin',
                              'pull/{}/merge'.format(state.num)))
    return utils.silent_call(git_cmd('diff', '--quiet', 'FETCH_HEAD', branch)) == 0  # noqa


def create_merge(state, repo_cfg, branch, logger, git_cfg,
                 ensure_merge_equal=False):
    base_sha = state.get_repo().ref('heads/' + state.base_ref).object.sha

    state.refresh()

    lazy_debug(logger,
               lambda: "create_merge: attempting merge {} into {} on {!r}"
               .format(state.head_sha, branch, state.get_repo()))

    merge_msg = 'Auto merge of #{} - {}, r={}\n\n{}\n\n{}'.format(
        state.num,
        state.head_ref,
        '<try>' if state.try_ else state.approved_by,
        state.title,
        state.body)

    desc = 'Merge conflict'
    comment = (
        'This pull request and the master branch diverged in a way that cannot'
        ' be automatically merged. Please rebase on top of the latest master'
        ' branch, and let the reviewer approve again.\n'
        '\n'
        '<details><summary>How do I rebase?</summary>\n\n'
        'Assuming `self` is your fork and `upstream` is this repository,'
        ' you can resolve the conflict following these steps:\n\n'
        '1. `git checkout {branch}` *(switch to your branch)*\n'
        '2. `git fetch upstream master` *(retrieve the latest master)*\n'
        '3. `git rebase upstream/master -p` *(rebase on top of it)*\n'
        '4. Follow the on-screen instruction to resolve conflicts'
        ' (check `git status` if you got lost).\n'
        '5. `git push self {branch} --force-with-lease` *(update this PR)*\n\n'
        'You may also read'
        ' [*Git Rebasing to Resolve Conflicts* by Drew Blessing](http://blessing.io/git/git-rebase/open-source/2015/08/23/git-rebasing-to-resolve-conflicts.html)' # noqa
        ' for a short tutorial.\n\n'
        'Please avoid the ["**Resolve conflicts**" button](https://help.github.com/articles/resolving-a-merge-conflict-on-github/) on GitHub.' # noqa
        ' It uses `git merge` instead of `git rebase` which makes the PR commit' # noqa
        ' history more difficult to read.\n\n'
        'Sometimes step 4 will complete without asking for resolution. This is'
        ' usually due to difference between how `Cargo.lock` conflict is'
        ' handled during merge and rebase. This is normal, and you should still' # noqa
        ' perform step 5 to update this PR.\n\n'
        '</details>\n\n'
    ).format(branch=state.head_ref.split(':', 1)[1])

    if git_cfg['local_git']:

        git_cmd = init_local_git_cmds(repo_cfg, git_cfg)

        utils.logged_call(git_cmd('fetch', 'origin', state.base_ref,
                                  'pull/{}/head'.format(state.num)))
        utils.silent_call(git_cmd('rebase', '--abort'))
        utils.silent_call(git_cmd('merge', '--abort'))

        if repo_cfg.get('linear', False):
            utils.logged_call(
                git_cmd('checkout', '-B', branch, state.head_sha))
            try:
                args = [base_sha]
                if repo_cfg.get('autosquash', False):
                    args += ['-i', '--autosquash']
                utils.logged_call(git_cmd('-c',
                                          'user.name=' + git_cfg['name'],
                                          '-c',
                                          'user.email=' + git_cfg['email'],
                                          'rebase',
                                          *args))
            except subprocess.CalledProcessError:
                if repo_cfg.get('autosquash', False):
                    utils.silent_call(git_cmd('rebase', '--abort'))
                    if utils.silent_call(git_cmd('rebase', base_sha)) == 0:
                        desc = 'Auto-squashing failed'
                        comment = ''
            else:
                ap = '<try>' if state.try_ else state.approved_by
                text = '\nCloses: #{}\nApproved by: {}'.format(state.num, ap)
                msg_code = 'cat && echo {}'.format(shlex.quote(text))
                env_code = 'export GIT_COMMITTER_NAME={} && export GIT_COMMITTER_EMAIL={} && unset GIT_COMMITTER_DATE'.format(shlex.quote(git_cfg['name']), shlex.quote(git_cfg['email']))  # noqa
                utils.logged_call(git_cmd('filter-branch', '-f',
                                          '--msg-filter', msg_code,
                                          '--env-filter', env_code,
                                          '{}..'.format(base_sha)))

                if ensure_merge_equal:
                    if not branch_equal_to_merge(git_cmd, state, branch):
                        return ''

                return git_push(git_cmd, branch, state)
        else:
            utils.logged_call(git_cmd(
                'checkout',
                '-B',
                'homu-tmp',
                state.head_sha))

            ok = True
            if repo_cfg.get('autosquash', False):
                try:
                    merge_base_sha = subprocess.check_output(
                        git_cmd(
                            'merge-base',
                            base_sha,
                            state.head_sha)).decode('ascii').strip()
                    utils.logged_call(git_cmd(
                        '-c',
                        'user.name=' + git_cfg['name'],
                        '-c',
                        'user.email=' + git_cfg['email'],
                        'rebase',
                        '-i',
                        '--autosquash',
                        '--onto',
                        merge_base_sha, base_sha))
                except subprocess.CalledProcessError:
                    desc = 'Auto-squashing failed'
                    comment = ''
                    ok = False

            if ok:
                utils.logged_call(git_cmd('checkout', '-B', branch, base_sha))
                try:
                    subprocess.check_output(
                        git_cmd(
                            '-c',
                            'user.name=' + git_cfg['name'],
                            '-c',
                            'user.email=' + git_cfg['email'],
                            'merge',
                            'heads/homu-tmp',
                            '--no-ff',
                            '-m',
                            merge_msg),
                        stderr=subprocess.STDOUT,
                        universal_newlines=True)
                except subprocess.CalledProcessError as e:
                    comment += '<details><summary>Error message</summary>\n\n```text\n' # noqa
                    comment += e.output
                    comment += '\n```\n\n</details>'
                    pass
                else:
                    if ensure_merge_equal:
                        if not branch_equal_to_merge(git_cmd, state, branch):
                            return ''

                    return git_push(git_cmd, branch, state)
    else:
        if repo_cfg.get('linear', False) or repo_cfg.get('autosquash', False):
            raise RuntimeError('local_git must be turned on to use this feature')  # noqa

        # if we're merging using the GitHub API, we have no way to predict
        # with certainty what the final result will be so make sure the caller
        # isn't asking us to keep any promises (see also discussions at
        # https://github.com/servo/homu/pull/57)
        assert ensure_merge_equal is False

        if branch != state.base_ref:
            utils.github_set_ref(
                state.get_repo(),
                'heads/' + branch,
                base_sha,
                force=True,
            )

        try:
            merge_commit = state.get_repo().merge(
                branch,
                state.head_sha,
                merge_msg)
        except github3.models.GitHubError as e:
            if e.code != 409:
                raise
        else:
            return merge_commit.sha if merge_commit else ''

    state.set_status('error')
    utils.github_create_status(
        state.get_repo(),
        state.head_sha,
        'error',
        '',
        desc,
        context='homu')

    state.add_comment(':lock: {}\n\n{}'.format(desc, comment))
    state.change_labels(LabelEvent.CONFLICT)

    return ''


def pull_is_rebased(state, repo_cfg, git_cfg, base_sha):
    assert git_cfg['local_git']
    git_cmd = init_local_git_cmds(repo_cfg, git_cfg)

    utils.logged_call(git_cmd('fetch', 'origin', state.base_ref,
                              'pull/{}/head'.format(state.num)))

    return utils.silent_call(git_cmd('merge-base', '--is-ancestor',
                                     base_sha, state.head_sha)) == 0


# We could fetch this from GitHub instead, but that API is being deprecated:
# https://developer.github.com/changes/2013-04-25-deprecating-merge-commit-sha/
def get_github_merge_sha(state, repo_cfg, git_cfg):
    assert git_cfg['local_git']
    git_cmd = init_local_git_cmds(repo_cfg, git_cfg)

    if state.mergeable is not True:
        return None

    utils.logged_call(git_cmd('fetch', 'origin',
                              'pull/{}/merge'.format(state.num)))

    return subprocess.check_output(git_cmd('rev-parse', 'FETCH_HEAD')).decode('ascii').strip()  # noqa


def do_exemption_merge(state, logger, repo_cfg, git_cfg, url, check_merge,
                       reason):

    try:
        merge_sha = create_merge(
            state,
            repo_cfg,
            state.base_ref,
            logger,
            git_cfg,
            check_merge)
    except subprocess.CalledProcessError:
        print('* Unable to create a merge commit for the exempted PR: {}'.format(state))  # noqa
        traceback.print_exc()
        return False

    if not merge_sha:
        return False

    desc = 'Test exempted'

    state.set_status('success')
    utils.github_create_status(state.get_repo(), state.head_sha, 'success',
                               url, desc, context='homu')
    state.add_comment(':zap: {}: {}.'.format(desc, reason))
    state.change_labels(LabelEvent.EXEMPTED)

    state.merge_sha = merge_sha
    state.save()

    state.fake_merge(repo_cfg)
    return True


def try_travis_exemption(state, logger, repo_cfg, git_cfg):

    travis_info = None
    for info in utils.github_iter_statuses(state.get_repo(), state.head_sha):
        if info.context == 'continuous-integration/travis-ci/pr':
            travis_info = info
            break

    if travis_info is None or travis_info.state != 'success':
        return False

    mat = re.search('/builds/([0-9]+)$', travis_info.target_url)
    if not mat:
        return False

    url = 'https://api.travis-ci.org/{}/{}/builds/{}'.format(state.owner,
                                                             state.name,
                                                             mat.group(1))
    try:
        res = requests.get(url)
    except Exception as ex:
        print('* Unable to gather build info from Travis CI: {}'.format(ex))
        return False

    travis_sha = json.loads(res.text)['commit']
    travis_commit = state.get_repo().commit(travis_sha)

    if not travis_commit:
        return False

    base_sha = state.get_repo().ref('heads/' + state.base_ref).object.sha

    if (travis_commit.parents[0]['sha'] == base_sha and
            travis_commit.parents[1]['sha'] == state.head_sha):
        # make sure we check against the github merge sha before pushing
        return do_exemption_merge(state, logger, repo_cfg, git_cfg,
                                  travis_info.target_url, True,
                                  "merge already tested by Travis CI")

    return False


def try_status_exemption(state, logger, repo_cfg, git_cfg):

    # If all the builders are status-based, then we can do some checks to
    # exempt testing under the following cases:
    #   1. The PR head commit has the equivalent statuses set to 'success' and
    #      it is fully rebased on the HEAD of the target base ref.
    #   2. The PR head and merge commits have the equivalent statuses set to
    #      state 'success' and the merge commit's first parent is the HEAD of
    #      the target base ref.

    if not git_cfg['local_git']:
        raise RuntimeError('local_git is required to use status exemption')

    statuses_all = set()

    # equivalence dict: pr context --> auto context
    status_equivalences = {}

    for key, value in repo_cfg['status'].items():
        context = value.get('context')
        pr_context = value.get('pr_context', context)
        if context is not None:
            statuses_all.add(context)
            status_equivalences[pr_context] = context

    assert len(statuses_all) > 0

    # let's first check that all the statuses we want are set to success
    statuses_pass = set()
    for info in utils.github_iter_statuses(state.get_repo(), state.head_sha):
        if info.context in status_equivalences and info.state == 'success':
            statuses_pass.add(status_equivalences[info.context])

    if statuses_all != statuses_pass:
        return False

    # is the PR fully rebased?
    base_sha = state.get_repo().ref('heads/' + state.base_ref).object.sha
    if pull_is_rebased(state, repo_cfg, git_cfg, base_sha):
        return do_exemption_merge(state, logger, repo_cfg, git_cfg, '', False,
                                  "pull fully rebased and already tested")

    # check if we can use the github merge sha as proof
    merge_sha = get_github_merge_sha(state, repo_cfg, git_cfg)
    if merge_sha is None:
        return False

    statuses_merge_pass = set()
    for info in utils.github_iter_statuses(state.get_repo(), merge_sha):
        if info.context in status_equivalences and info.state == 'success':
            statuses_merge_pass.add(status_equivalences[info.context])

    merge_commit = state.get_repo().commit(merge_sha)
    if (statuses_all == statuses_merge_pass and
            merge_commit.parents[0]['sha'] == base_sha and
            merge_commit.parents[1]['sha'] == state.head_sha):
        # make sure we check against the github merge sha before pushing
        return do_exemption_merge(state, logger, repo_cfg, git_cfg, '', True,
                                  "merge already tested")

    return False


def start_build(state, repo_cfgs, buildbot_slots, logger, db, git_cfg):
    if buildbot_slots[0]:
        return True

    lazy_debug(logger, lambda: "start_build on {!r}".format(state.get_repo()))

    pr = state.get_repo().pull_request(state.num)
    assert state.head_sha == pr.head.sha
    assert state.base_ref == pr.base.ref

    repo_cfg = repo_cfgs[state.repo_label]

    builders = []
    branch = 'try' if state.try_ else 'auto'
    branch = repo_cfg.get('branch', {}).get(branch, branch)
    can_try_travis_exemption = False

    only_status_builders = True
    if 'buildbot' in repo_cfg:
        if state.try_:
            builders += repo_cfg['buildbot']['try_builders']
        else:
            builders += repo_cfg['buildbot']['builders']
        only_status_builders = False
    if 'travis' in repo_cfg:
        builders += ['travis']
        only_status_builders = False
    if 'status' in repo_cfg:
        found_travis_context = False
        for key, value in repo_cfg['status'].items():
            context = value.get('context')
            if context is not None:
                if state.try_ and not value.get('try', True):
                    # Skip this builder for tries.
                    continue
                builders += ['status-' + key]
                # We have an optional fast path if the Travis test passed
                # for a given commit and master is unchanged, we can do
                # a direct push.
                if context == 'continuous-integration/travis-ci/push':
                    found_travis_context = True

        if found_travis_context and len(builders) == 1:
            can_try_travis_exemption = True
    if 'checks' in repo_cfg:
        builders += [
            'checks-' + key
            for key, value in repo_cfg['checks'].items()
            if 'name' in value or (state.try_ and 'try_name' in value)
        ]
        only_status_builders = False

    if len(builders) == 0:
        raise RuntimeError('Invalid configuration')

    lazy_debug(logger, lambda: "start_build: builders={!r}".format(builders))

    if (only_status_builders and state.approved_by and
            repo_cfg.get('status_based_exemption', False)):
        if can_try_travis_exemption:
            if try_travis_exemption(state, logger, repo_cfg, git_cfg):
                return True
        if try_status_exemption(state, logger, repo_cfg, git_cfg):
            return True

    merge_sha = create_merge(state, repo_cfg, branch, logger, git_cfg)
    lazy_debug(logger, lambda: "start_build: merge_sha={}".format(merge_sha))
    if not merge_sha:
        return False

    state.init_build_res(builders)
    state.merge_sha = merge_sha

    state.save()

    if 'buildbot' in repo_cfg:
        buildbot_slots[0] = state.merge_sha

    logger.info('Starting build of {}/{}#{} on {}: {}'.format(
        state.owner,
        state.name,
        state.num,
        branch,
        state.merge_sha))

    timeout = repo_cfg.get('timeout', DEFAULT_TEST_TIMEOUT)
    state.start_testing(timeout)

    desc = '{} commit {} with merge {}...'.format(
        'Trying' if state.try_ else 'Testing',
        state.head_sha,
        state.merge_sha,
    )
    utils.github_create_status(
        state.get_repo(),
        state.head_sha,
        'pending',
        '',
        desc,
        context='homu')

    if state.try_:
        state.add_comment(comments.TryBuildStarted(
            head_sha=state.head_sha,
            merge_sha=state.merge_sha,
        ))
    else:
        state.add_comment(comments.BuildStarted(
            head_sha=state.head_sha,
            merge_sha=state.merge_sha,
        ))

    return True


def start_rebuild(state, repo_cfgs):
    repo_cfg = repo_cfgs[state.repo_label]

    if 'buildbot' not in repo_cfg or not state.build_res:
        return False

    builders = []
    succ_builders = []

    for builder, info in state.build_res.items():
        if not info['url']:
            return False

        if info['res']:
            succ_builders.append([builder, info['url']])
        else:
            builders.append([builder, info['url']])

    if not builders or not succ_builders:
        return False

    base_sha = state.get_repo().ref('heads/' + state.base_ref).object.sha
    _parents = state.get_repo().commit(state.merge_sha).parents
    parent_shas = [x['sha'] for x in _parents]

    if base_sha not in parent_shas:
        return False

    utils.github_set_ref(
        state.get_repo(),
        'tags/homu-tmp',
        state.merge_sha,
        force=True)

    builders.sort()
    succ_builders.sort()

    with buildbot_sess(repo_cfg) as sess:
        for builder, url in builders:
            res = sess.post(url + '/rebuild', allow_redirects=False, data={
                'useSourcestamp': 'exact',
                'comments': 'Initiated by Homu',
            })

            if 'authzfail' in res.text:
                err = 'Authorization failed'
            elif builder in res.text:
                err = ''
            else:
                mat = re.search('<title>(.+?)</title>', res.text)
                err = mat.group(1) if mat else 'Unknown error'

            if err:
                state.add_comment(':bomb: Failed to start rebuilding: `{}`'.format(err))  # noqa
                return False

    timeout = repo_cfg.get('timeout', DEFAULT_TEST_TIMEOUT)
    state.start_testing(timeout)

    msg_1 = 'Previous build results'
    msg_2 = ' for {}'.format(', '.join('[{}]({})'.format(builder, url) for builder, url in succ_builders))  # noqa
    msg_3 = ' are reusable. Rebuilding'
    msg_4 = ' only {}'.format(', '.join('[{}]({})'.format(builder, url) for builder, url in builders))  # noqa

    utils.github_create_status(
        state.get_repo(),
        state.head_sha,
        'pending',
        '',
        '{}{}...'.format(msg_1, msg_3),
        context='homu')

    state.add_comment(':zap: {}{}{}{}...'.format(msg_1, msg_2, msg_3, msg_4))

    return True


def start_build_or_rebuild(state, repo_cfgs, *args):
    if start_rebuild(state, repo_cfgs):
        return True

    return start_build(state, repo_cfgs, *args)


def process_queue(states, repos, repo_cfgs, logger, buildbot_slots, db,
                  git_cfg):
    for repo_label, repo in repos.items():
        repo_states = sorted(states[repo_label].values())

        for state in repo_states:
            lazy_debug(logger, lambda: "process_queue: state={!r}, building {}"
                       .format(state, repo_label))
            if state.priority < repo.treeclosed:
                continue
            if state.status == 'pending' and not state.try_:
                break

            elif state.status == 'success' and hasattr(state, 'fake_merge_sha'):  # noqa
                break

            elif state.status == '' and state.approved_by:
                if start_build_or_rebuild(state, repo_cfgs, buildbot_slots,
                                          logger, db, git_cfg):
                    return

            elif state.status == 'success' and state.try_ and state.approved_by:  # noqa
                state.try_ = False

                state.save()

                if start_build(state, repo_cfgs, buildbot_slots, logger, db,
                               git_cfg):
                    return

        for state in repo_states:
            if state.status == '' and state.try_:
                if start_build(state, repo_cfgs, buildbot_slots, logger, db,
                               git_cfg):
                    return


def fetch_mergeability(mergeable_que):
    re_pull_num = re.compile('(?i)merge (?:of|pull request) #([0-9]+)')

    while True:
        try:
            state, cause = mergeable_que.get()

            if state.status == 'success':
                continue

            pull_request = state.get_repo().pull_request(state.num)
            if pull_request is None or pull_request.mergeable is None:
                time.sleep(5)
                pull_request = state.get_repo().pull_request(state.num)
            mergeable = pull_request is not None and pull_request.mergeable

            if state.mergeable is True and mergeable is False:
                if cause:
                    mat = re_pull_num.search(cause['title'])

                    if mat:
                        issue_or_commit = '#' + mat.group(1)
                    else:
                        issue_or_commit = cause['sha']
                else:
                    issue_or_commit = ''

                _blame = ''
                if issue_or_commit:
                    _blame = ' (presumably {})'.format(issue_or_commit)
                state.add_comment(
                    ':umbrella: The latest upstream changes{} made this '
                    'pull request unmergeable. Please [resolve the merge conflicts]'  # noqa
                    '(https://rustc-dev-guide.rust-lang.org/git.html#conflicts).\n\n'  # noqa
                    '*Note that reviewers usually do not review pull requests '
                    'until merge conflicts are resolved!* Once you resolve '
                    'the conflicts, you should change the labels applied by '
                    'bors to indicate that your PR is ready for review. '
                    'Post this as a comment to change the labels:\n'
                    '```\n'
                    '@rustbot modify labels: +S-waiting-on-review -S-waiting-on-author\n'  # noqa
                    '```'
                    .format(_blame)
                )
                state.change_labels(LabelEvent.CONFLICT)

            state.set_mergeable(mergeable, que=False)

        except Exception:
            print('* Error while fetching mergeability')
            traceback.print_exc()

        finally:
            mergeable_que.task_done()


def synchronize(repo_label, repo_cfg, logger, gh, states, repos, db, mergeable_que, my_username, repo_labels):  # noqa
    logger.info('Synchronizing {}...'.format(repo_label))

    repo = gh.repository(repo_cfg['owner'], repo_cfg['name'])

    db_query(db, 'DELETE FROM pull WHERE repo = ?', [repo_label])
    db_query(db, 'DELETE FROM build_res WHERE repo = ?', [repo_label])
    db_query(db, 'DELETE FROM mergeable WHERE repo = ?', [repo_label])

    saved_states = {}
    for num, state in states[repo_label].items():
        saved_states[num] = {
            'merge_sha': state.merge_sha,
            'build_res': state.build_res,
        }

    states[repo_label] = {}
    repos[repo_label] = Repository(repo, repo_label, db)

    for pull in repo.iter_pulls(state='open'):
        db_query(
            db,
            'SELECT status FROM pull WHERE repo = ? AND num = ?',
            [repo_label, pull.number])
        row = db.fetchone()
        if row:
            status = row[0]
        else:
            status = ''
            for info in utils.github_iter_statuses(repo, pull.head.sha):
                if info.context == 'homu':
                    status = info.state
                    break

        state = PullReqState(pull.number, pull.head.sha, status, db, repo_label, mergeable_que, gh, repo_cfg['owner'], repo_cfg['name'], repo_cfg.get('labels', {}), repos, repo_cfg.get('test-on-fork'))  # noqa
        state.title = pull.title
        state.body = suppress_pings(pull.body)
        state.head_ref = pull.head.repo[0] + ':' + pull.head.ref
        state.base_ref = pull.base.ref
        state.set_mergeable(None)
        state.assignee = pull.assignee.login if pull.assignee else ''

        for comment in pull.iter_comments():
            if comment.original_commit_id == pull.head.sha:
                parse_commands(
                    comment.body,
                    comment.user.login,
                    comment.user.id,
                    repo_label,
                    repo_cfg,
                    state,
                    my_username,
                    db,
                    states,
                    sha=comment.original_commit_id,
                    command_src=comment.to_json()['html_url'],
                    # FIXME switch to `comment.html_url`
                    #       after updating github3 to 1.3.0+
                )

        for comment in pull.iter_issue_comments():
            parse_commands(
                comment.body,
                comment.user.login,
                comment.user.id,
                repo_label,
                repo_cfg,
                state,
                my_username,
                db,
                states,
                command_src=comment.to_json()['html_url'],
                # FIXME switch to `comment.html_url`
                #       after updating github3 to 1.3.0+
            )

        saved_state = saved_states.get(pull.number)
        if saved_state:
            for key, val in saved_state.items():
                setattr(state, key, val)

        state.save()

        states[repo_label][pull.number] = state

    logger.info('Done synchronizing {}!'.format(repo_label))


def process_config(config):
    # Replace environment variables
    if type(config) == str:
        for var in VARIABLES_RE.findall(config):
            try:
                config = config.replace("${"+var+"}", os.environ[var])
            except KeyError:
                raise RuntimeError(
                    f"missing environment variable ${var} "
                    f"(requested in the configuration file)"
                ) from None

        return config
    # Recursively apply the processing
    elif type(config) == list:
        return [process_config(item) for item in config]
    elif type(config) == dict:
        return {key: process_config(value) for key, value in config.items()}
    # All other values should be returned as-is
    else:
        return config


def arguments():
    parser = argparse.ArgumentParser(
        description='A bot that integrates with GitHub and your favorite '
                    'continuous integration service')
    parser.add_argument(
        '-v',
        '--verbose',
        action='store_true',
        help='Enable more verbose logging')
    parser.add_argument(
        '-c',
        '--config',
        action='store',
        help='Path to cfg.toml',
        default='cfg.toml')

    return parser.parse_args()


def main():
    global global_cfg
    args = arguments()

    logger = logging.getLogger('homu')
    logger.setLevel(logging.DEBUG if args.verbose else logging.INFO)
    logger.addHandler(logging.StreamHandler())

    if sys.getfilesystemencoding() == 'ascii':
        logger.info('You need to set a locale compatible with unicode or homu will choke on Unicode in PR descriptions/titles. See http://stackoverflow.com/a/27931669')  # noqa

    try:
        with open(args.config) as fp:
            cfg = toml.loads(fp.read())
    except FileNotFoundError:
        # Fall back to cfg.json only if we're using the defaults
        if args.config == 'cfg.toml':
            with open('cfg.json') as fp:
                cfg = json.loads(fp.read())
        else:
            raise
    cfg = process_config(cfg)
    global_cfg = cfg

    gh = github3.login(token=cfg['github']['access_token'])
    user = gh.user()
    cfg_git = cfg.get('git', {})
    user_email = cfg_git.get('email')
    if user_email is None:
        try:
            user_email = [x for x in gh.iter_emails() if x['primary']][0]['email']  # noqa
        except IndexError:
            raise RuntimeError('Primary email not set, or "user" scope not granted')  # noqa
    user_name = cfg_git.get('name', user.name if user.name else user.login)

    states = {}
    repos = {}
    repo_cfgs = {}
    buildbot_slots = ['']
    my_username = user.login
    repo_labels = {}
    mergeable_que = Queue()
    git_cfg = {
        'name': user_name,
        'email': user_email,
        'ssh_key': cfg_git.get('ssh_key', ''),
        'local_git': cfg_git.get('local_git', False),
    }

    db_file = cfg.get('db', {}).get('file', 'main.db')
    db_conn = sqlite3.connect(db_file,
                              check_same_thread=False,
                              isolation_level=None)
    db = db_conn.cursor()

    db_query(db, '''CREATE TABLE IF NOT EXISTS pull (
        repo TEXT NOT NULL,
        num INTEGER NOT NULL,
        status TEXT NOT NULL,
        merge_sha TEXT,
        title TEXT,
        body TEXT,
        head_sha TEXT,
        head_ref TEXT,
        base_ref TEXT,
        assignee TEXT,
        approved_by TEXT,
        priority INTEGER,
        try_ INTEGER,
        rollup INTEGER,
        delegate TEXT,
        UNIQUE (repo, num)
    )''')

    db_query(db, '''CREATE TABLE IF NOT EXISTS build_res (
        repo TEXT NOT NULL,
        num INTEGER NOT NULL,
        builder TEXT NOT NULL,
        res INTEGER,
        url TEXT NOT NULL,
        merge_sha TEXT NOT NULL,
        UNIQUE (repo, num, builder)
    )''')

    db_query(db, '''CREATE TABLE IF NOT EXISTS mergeable (
        repo TEXT NOT NULL,
        num INTEGER NOT NULL,
        mergeable INTEGER NOT NULL,
        UNIQUE (repo, num)
    )''')
    db_query(db, '''CREATE TABLE IF NOT EXISTS repos (
        repo TEXT NOT NULL,
        treeclosed INTEGER NOT NULL,
        treeclosed_src TEXT,
        UNIQUE (repo)
    )''')

    db_query(db, '''CREATE TABLE IF NOT EXISTS retry_log (
        repo TEXT NOT NULL,
        num INTEGER NOT NULL,
        time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        src TEXT NOT NULL,
        msg TEXT NOT NULL
    )''')
    db_query(db, '''
        CREATE INDEX IF NOT EXISTS retry_log_time_index ON retry_log
        (repo, time DESC)
    ''')

    # manual DB migration :/
    try:
        db_query(db, 'SELECT treeclosed_src FROM repos LIMIT 0')
    except sqlite3.OperationalError:
        db_query(db, 'ALTER TABLE repos ADD COLUMN treeclosed_src TEXT')

    for repo_label, repo_cfg in cfg['repo'].items():
        repo_cfgs[repo_label] = repo_cfg
        repo_labels[repo_cfg['owner'], repo_cfg['name']] = repo_label

        # If test-on-fork is enabled point both the main repo and the fork to
        # the same homu "repository". This will allow events coming from both
        # GitHub repositories to be processed the same way.
        if 'test-on-fork' in repo_cfg:
            tof = repo_cfg['test-on-fork']
            repo_labels[tof['owner'], tof['name']] = repo_label

        repo_states = {}
        repos[repo_label] = Repository(None, repo_label, db)

        db_query(
            db,
            'SELECT num, head_sha, status, title, body, head_ref, base_ref, assignee, approved_by, priority, try_, rollup, delegate, merge_sha FROM pull WHERE repo = ?',   # noqa
            [repo_label])
        for num, head_sha, status, title, body, head_ref, base_ref, assignee, approved_by, priority, try_, rollup, delegate, merge_sha in db.fetchall():  # noqa
            state = PullReqState(num, head_sha, status, db, repo_label, mergeable_que, gh, repo_cfg['owner'], repo_cfg['name'], repo_cfg.get('labels', {}), repos, repo_cfg.get('test-on-fork'))  # noqa
            state.title = title
            state.body = body
            state.head_ref = head_ref
            state.base_ref = base_ref
            state.assignee = assignee

            state.approved_by = approved_by
            state.priority = int(priority)
            state.try_ = bool(try_)
            state.rollup = rollup
            state.delegate = delegate
            builders = []
            if merge_sha:
                if 'buildbot' in repo_cfg:
                    builders += repo_cfg['buildbot']['builders']
                if 'travis' in repo_cfg:
                    builders += ['travis']
                if 'status' in repo_cfg:
                    builders += ['status-' + key for key, value in repo_cfg['status'].items() if 'context' in value]  # noqa
                if 'checks' in repo_cfg:
                    builders += ['checks-' + key for key, value in repo_cfg['checks'].items() if 'name' in value]  # noqa
                if len(builders) == 0:
                    raise RuntimeError('Invalid configuration')

                state.init_build_res(builders, use_db=False)
                state.merge_sha = merge_sha

            elif state.status == 'pending':
                # FIXME: There might be a better solution
                state.status = ''

                state.save()

            repo_states[num] = state

        states[repo_label] = repo_states

    db_query(
        db,
        'SELECT repo, num, builder, res, url, merge_sha FROM build_res')
    for repo_label, num, builder, res, url, merge_sha in db.fetchall():
        try:
            state = states[repo_label][num]
            if builder not in state.build_res:
                raise KeyError
            if state.merge_sha != merge_sha:
                raise KeyError
        except KeyError:
            db_query(
                db,
                'DELETE FROM build_res WHERE repo = ? AND num = ? AND builder = ?',   # noqa
                [repo_label, num, builder])
            continue

        state.build_res[builder] = {
            'res': bool(res) if res is not None else None,
            'url': url,
        }

    db_query(db, 'SELECT repo, num, mergeable FROM mergeable')
    for repo_label, num, mergeable in db.fetchall():
        try:
            state = states[repo_label][num]
        except KeyError:
            db_query(
                db,
                'DELETE FROM mergeable WHERE repo = ? AND num = ?',
                [repo_label, num])
            continue

        state.mergeable = bool(mergeable) if mergeable is not None else None

    db_query(db, 'SELECT repo FROM pull GROUP BY repo')
    for repo_label, in db.fetchall():
        if repo_label not in repos:
            db_query(db, 'DELETE FROM pull WHERE repo = ?', [repo_label])

    queue_handler_lock = Lock()

    def queue_handler():
        with queue_handler_lock:
            return process_queue(states, repos, repo_cfgs, logger, buildbot_slots, db, git_cfg)  # noqa

    os.environ['GIT_SSH'] = os.path.join(os.path.dirname(__file__), 'git_helper.py')  # noqa
    os.environ['GIT_EDITOR'] = 'cat'

    from . import server
    Thread(
        target=server.start,
        args=[
            cfg,
            states,
            queue_handler,
            repo_cfgs,
            repos,
            logger,
            buildbot_slots,
            my_username,
            db,
            repo_labels,
            mergeable_que,
            gh,
        ]).start()

    Thread(target=fetch_mergeability, args=[mergeable_que]).start()

    queue_handler()


if __name__ == '__main__':
    main()
