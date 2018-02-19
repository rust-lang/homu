import random
from enum import Enum


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
PORTAL_TURRET_IMAGE = "https://cloud.githubusercontent.com/assets/1617736/22222924/c07b2a1c-e16d-11e6-91b3-ac659550585c.png" # noqa


class Action:
    def get_portal_turret_dialog(self):
        return random.choice(PORTAL_TURRET_DIALOG)

    def still_here(self, state):
        state.add_comment(
            ":cake: {}\n\n![]({})".format(
                self.get_portal_turret_dialog(), PORTAL_TURRET_IMAGE)
            )

    def delegate_to(self, state, realtime, delegate):
        state.delegate = delegate
        state.save()

        if realtime:
            state.add_comment(
                ':v: @{} can now approve this pull request'
                .format(state.delegate)
            )

    def set_treeclosed(self, state, word):
        try:
            treeclosed = int(word[len('treeclosed='):])
            state.change_treeclosed(treeclosed)
        except ValueError:
            pass
        state.save()

    def treeclosed_negative(self, state):
        state.change_treeclosed(-1)
        state.save()

    def hello_or_ping(self, state):
        state.add_comment(":sleepy: I'm awake I'm awake")

    def rollup(self, state, word):
        state.rollup = word == 'rollup'
        state.save()

    def _try(self, state, word):
        state.try_ = word == 'try'
        state.merge_sha = ''
        state.init_build_res([])
        state.save()
        if state.try_:
            # `try-` just resets the `try` bit and doesn't correspond to
            # any meaningful labeling events.
            state.change_labels(LabelEvent.TRY)

    def clean(self, state):
        state.merge_sha = ''
        state.init_build_res([])
        state.save()

    def retry(self, state):
        state.set_status('')
        event = LabelEvent.TRY if state.try_ else LabelEvent.APPROVED
        state.change_labels(event)

    def delegate_negative(self, state):
        state.delegate = ''
        state.save()

    def review_rejected(self, state, realtime):
        state.approved_by = ''
        state.save()
        if realtime:
            state.change_labels(LabelEvent.REJECTED)

    def delegate_positive(self, state, delegate, realtime):
        state.delegate = delegate
        state.save()

        if realtime:
            state.add_comment(
                ':v: @{} can now approve this pull request'
                .format(state.delegate)
            )

    def set_priority(self, state, realtime, priority, cfg):
        try:
            pvalue = int(priority)
        except ValueError:
            return False

        if pvalue > cfg['max_priority']:
            if realtime:
                state.add_comment(
                    ':stop_sign: Priority higher than {} is ignored.'
                    .format(cfg['max_priority'])
                )
            return False
        state.priority = pvalue
        state.save()
        return True

    def review_approved(self, state, realtime, approver, username,
                        my_username, sha, states):
        # Ignore "r=me"
        if approver == 'me':
            return False

        # Ignore WIP PRs
        if any(map(state.title.startswith, [
            'WIP', 'TODO', '[WIP]', '[TODO]',
        ])):
            if realtime:
                state.add_comment(':clipboard: Looks like this PR is still in progress, ignoring approval')  # noqa
            return False

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

        if Action.sha_cmp(sha, state.head_sha):
            state.approved_by = approver
            state.try_ = False
            state.set_status('')

            state.save()
        elif realtime and username != my_username:
            if sha:
                msg = '`{}` is not a valid commit SHA.'.format(sha)
                state.add_comment(
                    ':scream_cat: {} Please try again with `{:.7}`.'
                    .format(msg, state.head_sha)
                )
            else:
                state.add_comment(
                    ':pushpin: Commit {:.7} has been approved by `{}`\n\n<!-- @{} r={} {} -->'  # noqa
                    .format(
                        state.head_sha,
                        approver,
                        my_username,
                        approver,
                        state.head_sha,
                ))
                treeclosed = state.blocked_by_closed_tree()
                if treeclosed:
                    state.add_comment(
                        ':evergreen_tree: The tree is currently closed for pull requests below priority {}, this pull request will be tested once the tree is reopened'  # noqa
                        .format(treeclosed)
                    )
                state.change_labels(LabelEvent.APPROVED)
        return True

    @staticmethod
    def sha_cmp(short, full):
        return len(short) >= 4 and short == full[:len(short)]
