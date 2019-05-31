import weakref
from threading import Timer
import time
from . import utils
from . import comments
from .consts import (
    STATUS_TO_PRIORITY,
    WORDS_TO_ROLLUP,
    LabelEvent,
)


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
                 gh, owner, name, label_events, repos):
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
            # Sort rollup=always to the bottom of the queue, but treat all
            # other rollup statuses as equivalent
            1 if WORDS_TO_ROLLUP['rollup=always'] == self.rollup else 0,
            -self.priority,
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

        self.db.execute(
            'UPDATE pull SET status = ? WHERE repo = ? AND num = ?',
            [self.status, self.repo_label, self.num]
        )

        # FIXME: self.try_ should also be saved in the database
        if not self.try_:
            self.db.execute(
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

            self.db.execute(
                'INSERT OR REPLACE INTO mergeable (repo, num, mergeable) VALUES (?, ?, ?)',  # noqa
                [self.repo_label, self.num, self.mergeable]
            )
        else:
            if que:
                self.mergeable_que.put([self, cause])
            else:
                self.mergeable = None

            self.db.execute(
                'DELETE FROM mergeable WHERE repo = ? AND num = ?',
                [self.repo_label, self.num]
            )

    def init_build_res(self, builders, *, use_db=True):
        self.build_res = {x: {
            'res': None,
            'url': '',
        } for x in builders}

        if use_db:
            self.db.execute(
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

        self.db.execute(
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

    def save(self):
        self.db.execute(
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
        self.body = issue.body

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

    def record_retry_log(self, src, body, cfg):
        # destroy ancient records
        self.db.execute(
            "DELETE FROM retry_log WHERE repo = ? AND time < date('now', ?)",
            [self.repo_label, cfg.get('retry_log_expire', '-42 days')],
        )
        self.db.execute(
            'INSERT INTO retry_log (repo, num, src, msg) VALUES (?, ?, ?, ?)',
            [self.repo_label, self.num, src, body],
        )

    @property
    def author(self):
        """
        Get the GitHub login name of the author of the pull request
        """
        return self.get_issue().user.login
