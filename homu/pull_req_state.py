import weakref
from threading import Timer
import time
import functools
from . import utils
from . import comments
from .consts import (
    STATUS_TO_PRIORITY,
    WORDS_TO_ROLLUP,
    LabelEvent,
)
from .parse_issue_comment import parse_issue_comment
from .auth import (
    assert_authorized,
    AuthorizationException,
    AuthState,
)
from enum import Enum


class ProcessEventResult:
    """
    The object returned from PullReqState::process_event that contains
    information about what changed and what needs to happen.
    """
    def __init__(self):
        self.changed = False
        self.comments = []
        self.label_events = []

    def __repr__(self):
        return 'ProcessEventResult<changed={}, comments={}, label_events={}>'.format(
                self.changed, self.comments, self.label_events)


def sha_cmp(short, full):
    return len(short) >= 4 and short == full[:len(short)]


def sha_or_blank(sha):
    return sha if re.match(r'^[0-9a-f]+$', sha) else ''



class BuildState(Enum):
    """
    The state of a merge build or a try build
    """
    NONE = 'none'
    PENDING = 'pending'
    SUCCESS = 'success'
    FAILURE = 'failure'
    ERROR = 'error'


class ApprovalState(Enum):
    """
    The approval state for a pull request.
    """
    UNAPPROVED = 'unapproved'
    APPROVED = 'approved'


class GitHubPullRequestState(Enum):
    CLOSED = 'closed'
    MERGED = 'merged'
    OPEN = 'open'


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
    last_github_cursor = None
    build_state = BuildState.NONE
    try_state = BuildState.NONE
    github_pr_state = GitHubPullRequestState.OPEN

    def __init__(self, repository, num, head_sha, status, db, mergeable_que, author):
        self.head_advanced('', use_db=False)

        self.repository = repository
        self.num = num
        self.head_sha = head_sha
        self.status = status
        self.db = db
        self.mergeable_que = mergeable_que
        self.author = author
        self.timeout_timer = None
        self.test_started = time.time()

    @property
    def repo_label(self):
        return self.repository.repo_label

    @property
    def owner(self):
        return self.repository.owner

    @property
    def name(self):
        return self.repository.name

    @property
    def gh(self):
        return self.repository.gh

    @property
    def label_events(self):
        return self.repository.cfg.get('labels', {})

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

    @property
    def approval_state(self):
        if self.approved_by != '':
            return ApprovalState.APPROVED
        else:
            return ApprovalState.UNAPPROVED

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
        return self.repository.github_repo

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
        self.repository.update_treeclosed(value, src)

    def blocked_by_closed_tree(self):
        treeclosed = self.repository.treeclosed
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

    def process_event(self, event):
        """
        Process a GitHub event (in the form of either a Timeline Event from
        GitHub's Timeline API or an Event from GitHub's Webhooks) and update
        the state of the pull request accordingly.

        Returns an object that contains information about the change, with at
        least the following properties:

            changed: bool -- Whether or not the state of the pull request was
            affected by this event

            comments: [string] -- Comments that can be made on the pull request
            as a result of this event. In realtime mode, these should then be
            applied to the pull request. In synchronization mode, they may be
            dropped. (In testing mode, they should be tested.)

            label_events: [LabelEvent] -- Any label events that need to be
            applied as a result of this event.
        """

        self.last_github_cursor = event.cursor

        # TODO: Don't hardcode botname!
        botname = 'bors'
        # TODO: Don't hardcode hooks!
        hooks = []

        result = ProcessEventResult()
        if event.event_type == 'PullRequestCommit':
            result.changed = self.head_sha != event['commit']['oid']
            self.head_sha = event['commit']['oid']
            # New commits come in: no longer approved
            result.changed = result.changed or self.approved_by != ''
            self.approved_by = ''
            result.changed = result.changed or self.try_ != False
            self.try_ = False
            # TODO: Do we *always* reset the state?
            result.changed = result.changed or self.status != ''
            self.status = ''
            result.changed = result.changed or self.try_state != BuildState.NONE
            self.try_state = BuildState.NONE
            result.changed = result.changed or self.build_state != BuildState.NONE
            self.build_state = BuildState.NONE

        elif event.event_type == 'HeadRefForcePushedEvent':
            result.changed = self.head_sha != event['afterCommit']['oid']
            self.head_sha = event['afterCommit']['oid']
            # New commits come in: no longer approved
            result.changed = result.changed or self.approved_by != ''
            self.approved_by = ''
            # TODO: Do we need to reset the state here?

        elif event.event_type == 'BaseRefChangedEvent':
            # Base ref changed: no longer approved
            result.changed = self.approved_by != ''
            self.approved_by = ''


        elif event.event_type == 'IssueComment':
            comments = parse_issue_comment(
                    username=event['author']['login'],
                    body=event['body'],
                    sha=self.head_sha,
                    botname=botname,
                    hooks=[])

            for comment in comments:
                subresult = self.process_issue_comment(event, comment)
                result.changed = result.changed or subresult.changed
                result.comments.extend(subresult.comments)
                result.label_events.extend(subresult.label_events)

        elif event.event_type == 'RenamedTitleEvent':
            result.changed = self.title != event['currentTitle']
            self.title = event['currentTitle']

        elif event.event_type == 'AssignedEvent':
            result.changed = self.assignee != event['user']['login']
            self.assignee = event['user']['login']

        elif event.event_type == 'PullRequestReview':
            # TODO: Pull commands from review comments
            pass

        elif event.event_type == 'MergedEvent':
            # TODO: Test.
            changed = self.github_pr_state != GitHubPullRequestState.MERGED
            self.github_pr_state = GitHubPullRequestState.MERGED

        elif event.event_type == 'ClosedEvent':
            # TODO: Test.
            if self.github_pr_state != GitHubPullRequestState.MERGED:
                changed = self.github_pr_state != GitHubPullRequestState.CLOSED
                self.github_pr_state = GitHubPullRequestState.CLOSED

        elif event.event_type == 'ReopenedEvent':
            # TODO: Test.
            changed = self.github_pr_state != GitHubPullRequestState.OPEN
            self.github_pr_state = GitHubPullRequestState.OPEN

        elif event.event_type in [
                'SubscribedEvent',
                'UnsubscribedEvent',
                'MentionedEvent',
                'LabeledEvent',
                'UnlabeledEvent',
                'ReferencedEvent',
                'CrossReferencedEvent']:
            # We don't care about any of these events.
            pass

        elif event.event_type in [
                'UnassignedEvent',
                'MilestonedEvent',
                'DemilestonedEvent',
                'ReviewRequestedEvent',
                'ReviewDismissedEvent',
                'CommentDeletedEvent',
                'PullRequestCommitCommentThread']:
            # TODO! Review these events to see if we care about any of them.
            # These events were seen as "Unknown event type: {}" when doing initial testing.
            pass

        else:
            # Ooops, did we miss this event type? Or is it new?
            print("Unknown event type: {}".format(event.event_type))

        return result

#    def process_issue_comment(self, event, command):
#        result = ProcessEventResult()
#        if command.action == 'homu-state':
#            return self.process_homu_state(event, command)
#
#        if command.action == 'approve':
#            # TODO: Something with states
#            result.changed = self.approved_by != command.actor
#            self.approved_by = command.actor
#
#        if command.action == 'unapprove':
#            # TODO: Something with states
#            result.changed = self.approved_by != ''
#            self.approved_by = None
#
#        # if command.action == 'try':
#        #    changed = True
#        #    self.tries.append(PullRequestTry(1, self.head_sha, None))
#        return result

    def process_issue_comment(self, event, command):
        # TODO: Don't hardcode botname
        botname = 'bors'
        username = event['author']['login']
        # TODO: Don't hardcode repo_cfg
        #repo_cfg = {}
        repo_cfg = self.cfg

        _assert_reviewer_auth_verified = functools.partial(
            assert_authorized,
            username,
            self.repo_label,
            repo_cfg,
            self,
            AuthState.REVIEWER,
            botname,
        )
        _assert_try_auth_verified = functools.partial(
            assert_authorized,
            username,
            self.repo_label,
            repo_cfg,
            self,
            AuthState.TRY,
            botname,
        )
        result = ProcessEventResult()
        try:
            found = True
            if command.action == 'approve':
                _assert_reviewer_auth_verified()

                approver = command.actor
                cur_sha = command.commit

                # Ignore WIP PRs
                is_wip = False
                for wip_kw in ['WIP', 'TODO', '[WIP]', '[TODO]',
                               '[DO NOT MERGE]']:
                    if self.title.upper().startswith(wip_kw):
                        result.comments.append(comments.ApprovalIgnoredWip(
                            sha=self.head_sha,
                            wip_keyword=wip_kw,
                        ))
                        is_wip = True
                        break
                if is_wip:
                    return result

#                # Sometimes, GitHub sends the head SHA of a PR as 0000000
#                # through the webhook. This is called a "null commit", and
#                # seems to happen when GitHub internally encounters a race
#                # condition. Last time, it happened when squashing commits
#                # in a PR. In this case, we just try to retrieve the head
#                # SHA manually.
#                if all(x == '0' for x in self.head_sha):
#                    result.commens.append(
#                        ':bangbang: Invalid head SHA found, retrying: `{}`'
#                        .format(self.head_sha)
#                    )
#
#                    state.head_sha = state.get_repo().pull_request(state.num).head.sha  # noqa
#                    state.save()
#
#                    assert any(x != '0' for x in state.head_sha)

                if self.approved_by and username != botname:
                    lines = []

                    if self.status in ['failure', 'error']:
                        lines.append('- This pull request previously failed. You should add more commits to fix the bug, or use `retry` to trigger a build again.')  # noqa

                    if lines:
                        lines.insert(0, '')
                    lines.insert(0, ':bulb: This pull request was already approved, no need to approve it again.')  # noqa

                    result.comments.append('\n'.join(lines))

                elif not sha_cmp(cur_sha, self.head_sha):
                    if username != botname:
                        msg = '`{}` is not a valid commit SHA.'.format(cur_sha)
                        result.comments.append(
                            ':scream_cat: {} Please try again with `{}`.'
                            .format(msg, self.head_sha)
                        )
                    else:
                        # Somehow, the bot specified an invalid sha?
                        pass

                else:
                    self.approved_by = approver
                    self.try_ = False
                    self.status = ''
                    result.changed = True
                    result.label_events.append(LabelEvent.APPROVED)

                    if username != botname:
                        result.comments.append(comments.Approved(
                            sha=self.head_sha,
                            approver=approver,
                            bot=botname,
                        ))
                        treeclosed = self.blocked_by_closed_tree()
                        if treeclosed:
                            result.comments.append(
                                ':evergreen_tree: The tree is currently closed for pull requests below priority {}, this pull request will be tested once the tree is reopened'  # noqa
                                .format(treeclosed)
                            )

            elif command.action == 'unapprove':
                # Allow the author of a pull request to unapprove their own PR.
                # The author can already perform other actions that effectively
                # unapprove the PR (change the target branch, push more
                # commits, etc.) so allowing them to directly unapprove it is
                # also allowed.
                if self.author != username:
                    assert_authorized(username, self.repo_label, repo_cfg, self,
                                      AuthState.REVIEWER, botname)

                self.approved_by = ''
                result.changed = True
                result.label_events.append(LabelEvent.REJECTED)

            elif command.action == 'prioritize':
                assert_authorized(username, self.repo_label, repo_cfg, self,
                                  AuthState.TRY, botname)

                pvalue = command.priority

                # TODO: Don't hardcode max_priority
                # global_cfg['max_priority']
                max_priority = 9001
                if pvalue > max_priority:
                    result.comments.append(
                        ':stop_sign: Priority higher than {} is ignored.'
                        .format(max_priority)
                    )
                    return result
                result.changed = self.priority != pvalue
                self.priority = pvalue

#            elif command.action == 'delegate':
#                assert_authorized(username, repo_label, repo_cfg, state,
#                                  AuthState.REVIEWER, my_username)
#
#                state.delegate = command.delegate_to
#                state.save()
#
#                if realtime:
#                    state.add_comment(comments.Delegated(
#                        delegator=username,
#                        delegate=state.delegate
#                    ))
#
#            elif command.action == 'undelegate':
#                # TODO: why is this a TRY?
#                _assert_try_auth_verified()
#
#                state.delegate = ''
#                state.save()
#
#            elif command.action == 'delegate-author':
#                _assert_reviewer_auth_verified()
#
#                state.delegate = state.get_repo().pull_request(state.num).user.login  # noqa
#                state.save()
#
#                if realtime:
#                    state.add_comment(comments.Delegated(
#                        delegator=username,
#                        delegate=state.delegate
#                    ))
#
            elif command.action == 'retry':
                _assert_try_auth_verified()

                self.status = ''
                if self.try_:
                    event = LabelEvent.TRY
                    self.try_state = BuildState.NONE
                else:
                    event = LabelEvent.APPROVED
                    self.build_state = BuildState.NONE
                # TODO: re-enable the retry log!
                #self.record_retry_log(command_src, body, global_cfg)
                result.label_events.append(event)
                result.changed = True

#            elif command.action in ['try', 'untry'] and realtime:
#                _assert_try_auth_verified()
#
#                if state.status == '' and state.approved_by:
#                    state.add_comment(
#                        ':no_good: '
#                        'Please do not `try` after a pull request has'
#                        ' been `r+`ed.'
#                        ' If you need to `try`, unapprove (`r-`) it first.'
#                    )
#                    #continue
#
#                state.try_ = command.action == 'try'
#
#                state.merge_sha = ''
#                state.init_build_res([])
#
#                state.save()
#                if realtime and state.try_:
#                    # If we've tried before, the status will be 'success', and
#                    # this new try will not be picked up. Set the status back
#                    # to '' so the try will be run again.
#                    state.set_status('')
#                    # `try-` just resets the `try` bit and doesn't correspond
#                    # to any meaningful labeling events.
#                    state.change_labels(LabelEvent.TRY)
#
            elif command.action == 'rollup':
                _assert_try_auth_verified()

                result.changed = self.rollup != command.rollup_value
                self.rollup = command.rollup_value

#            elif command.action == 'force' and realtime:
#                _assert_try_auth_verified()
#
#                if 'buildbot' in repo_cfg:
#                    with buildbot_sess(repo_cfg) as sess:
#                        res = sess.post(
#                            repo_cfg['buildbot']['url'] + '/builders/_selected/stopselected',   # noqa
#                            allow_redirects=False,
#                            data={
#                                'selected': repo_cfg['buildbot']['builders'],
#                                'comments': INTERRUPTED_BY_HOMU_FMT.format(int(time.time())),  # noqa
#                        })
#
#                if 'authzfail' in res.text:
#                    err = 'Authorization failed'
#                else:
#                    mat = re.search('(?s)<div class="error">(.*?)</div>', res.text) # noqa
#                    if mat:
#                        err = mat.group(1).strip()
#                        if not err:
#                            err = 'Unknown error'
#                    else:
#                        err = ''
#
#                if err:
#                    state.add_comment(
#                        ':bomb: Buildbot returned an error: `{}`'.format(err)
#                    )
#
#            elif command.action == 'clean' and realtime:
#                _assert_try_auth_verified()
#
#                state.merge_sha = ''
#                state.init_build_res([])
#
#                state.save()
#
#            elif command.action == 'ping' and realtime:
#                if command.ping_type == 'portal':
#                    state.add_comment(
#                        ":cake: {}\n\n![]({})".format(
#                            random.choice(PORTAL_TURRET_DIALOG),
#                            PORTAL_TURRET_IMAGE)
#                        )
#                else:
#                    state.add_comment(":sleepy: I'm awake I'm awake")
#
#            elif command.action == 'treeclosed':
#                _assert_reviewer_auth_verified()
#
#                state.change_treeclosed(command.treeclosed_value, command_src)
#                state.save()
#
#            elif command.action == 'untreeclosed':
#                _assert_reviewer_auth_verified()
#
#                state.change_treeclosed(-1, None)
#                state.save()
#
#            elif command.action == 'hook':
#                hook = command.hook_name
#                hook_cfg = global_cfg['hooks'][hook]
#                if hook_cfg['realtime'] and not realtime:
#                    #continue
#                    pass
#                if hook_cfg['access'] == "reviewer":
#                    _assert_reviewer_auth_verified()
#                else:
#                    _assert_try_auth_verified()
#
#                Thread(
#                    target=handle_hook_response,
#                    args=[state, hook_cfg, body, command.hook_extra]
#                ).start()

            elif command.action == 'homu-state' and username == botname:
                subresult = self.process_homu_state(event, command)
                result.comments.extend(subresult.comments)
                result.label_events.extend(subresult.label_events)
                result.changed = subresult.changed

            else:
                found = False

            if found:
                state_changed = True

        except AuthorizationException as e:
            print("{} is unauthorized".format(event['author']['login']))
            result.comments.append(e.comment)

        return result

    def process_homu_state(self, event, command):
        result = ProcessEventResult()
        state = command.homu_state


        if state['type'] == 'Approved':
            result.changed = self.approved_by != state['approver']
            self.approved_by = state['approver']

        elif state['type'] == 'BuildStarted':
            result.changed = True
            self.try_ = False
            self.status = 'pending'
            self.build_state = BuildState.PENDING

        elif state['type'] == 'BuildCompleted':
            result.changed = True
            self.try_ = False
            self.status = 'completed'
            self.build_state = BuildState.SUCCESS

        elif state['type'] == 'BuildFailed':
            result.changed = True
            self.try_ = False
            self.status = 'failure'
            self.build_state = BuildState.FAILURE

        elif state['type'] == 'TryBuildStarted':
            result.changed = True
            self.try_ = True
            self.status = 'pending'
            self.try_state = BuildState.PENDING
            # TODO: Multiple tries?
            # result.changed = True
            # self.tries.append(PullRequestTry(
            #     len(self.tries) + 1,
            #     state['head_sha'],
            #     state['merge_sha'],
            #     event['publishedAt'])
            # )

        elif state['type'] == 'TryBuildCompleted':
            result.changed = True
            self.status = 'success'
            self.try_state = BuildState.SUCCESS
            # TODO: Multiple tries?
            # item = next((try_
            #              for try_ in self.tries
            #              if try_.state == 'pending'
            #              and try_.merge_sha == state['merge_sha']),
            #             None)
            #
            # if item:
            #     result.changed = True
            #     # TODO: Multiple tries?
            #     item.ended_at = event['publishedAt']
            #     item.state = 'completed'
            #     item.builders = state['builders']

        elif state['type'] == 'TryBuildFailed':
            result.changed = True
            self.status = 'failure'
            self.try_state = BuildState.FAILURE

        elif state['type'] == 'TimedOut':
            result.changed = True
            self.status = 'failure'
            # TODO: Do we need to determine if a try or a build failed?
            self.try_state = BuildState.FAILURE

        return result
