from .parse_issue_comment import parse_issue_comment
import datetime


class PullRequest:
    def __init__(self, pull):
        self.owner = pull.owner
        self.repo = pull.repo
        self.number = pull.pull
        self.title = pull.initial_title
        self.author = pull.author
        self.assignee = None
        self.approver = None

        # Before we start going through the events, the state is 'open'. We'll
        # let the history of the pull request tell us differently.
        self.github_state = 'open'

        # Before we start going through the events, this is not approved We'll
        # let the history of the pull request tell us differently.
        self.approval_state = 'unapproved'
        self.build_state = 'none'

        # The way GitHub's timeline events work, one of the first events will
        # be a PullRequestCommit event that we can get the current SHA from.
        # However, if this is pull request that has existed for a bit, and it
        # has had a force push in it at some point, we may not get the initial
        # sha. So we'll have to handle that as well. To start, we'll set this
        # to None to represent that we don't know the initial sha, and if we
        # get an early PullRequestCommit, we'll update it.
        self.head_sha = None

        self.tries = []

    @property
    def state(self):
        if self.github_state == 'open':
            if self.build_state != 'none':
                return self.build_state
            return self.approval_state
        return self.github_state

    def __str__(self):
        output = """
PullRequest: {owner}/{repo}#{number}
  title: {title}
  author: {author}
  assignee: {assignee}
  approver: {approver}
  head: {head}
  state: {state}
  tries: {tries}
""".format(
            owner=self.owner,
            repo=self.repo,
            number=self.number,
            title=self.title,
            author=self.author,
            assignee=self.assignee if self.assignee is not None else 'None',
            approver=self.approver if self.approver is not None else 'None',
            head=self.head_sha[0:7] if self.head_sha is not None else 'None',
            state=self.state,
            tries=len(self.tries)
        )

        for try_ in self.tries:
            output += "    " + str(try_) + "\n"

        return output.strip()

    def process_event(self, event):
        changed = False
        if event.event_type == 'PullRequestCommit':
            changed = self.head_sha != event['commit']['oid']
            self.head_sha = event['commit']['oid']

        elif event.event_type == 'HeadRefForcePushedEvent':
            changed = self.head_sha != event['afterCommit']['oid']
            self.head_sha = event['afterCommit']['oid']

        elif event.event_type == 'IssueComment':
            comments = parse_issue_comment(
                    username=event['author']['login'],
                    body=event['body'],
                    sha=self.head_sha,
                    botname='bors',
                    hooks=[])

            for comment in comments:
                (subchanged,) = self.process_issue_comment(event, comment)
                changed = changed or subchanged

        elif event.event_type == 'RenamedTitleEvent':
            changed = self.title != event['currentTitle']
            self.title = event['currentTitle']

        elif event.event_type == 'AssignedEvent':
            changed = self.assignee != event['user']['login']
            self.assignee = event['user']['login']

        elif event.event_type == 'PullRequestReview':
            # TODO: Pull commands from review comments
            pass

        elif event.event_type == 'MergedEvent':
            changed = self.github_state != 'merged'
            self.github_state = 'merged'

        elif event.event_type == 'ClosedEvent':
            if self.github_state != 'merged':
                changed = self.github_state != 'closed'
                self.github_state = 'closed'

        elif event.event_type == 'ReopenedEvent':
            changed = self.github_state != 'open'
            self.github_state = 'open'

        elif event.event_type in [
                'SubscribedEvent',
                'MentionedEvent',
                'LabeledEvent',
                'UnlabeledEvent',
                'ReferencedEvent',
                'CrossReferencedEvent']:
            # We don't care about any of these events.
            pass
        else:
            # Ooops, did we miss this event type? Or is it new?
            print("Unknown event type: {}".format(event.event_type))

        return (changed,)

    def process_issue_comment(self, event, command):
        changed = False
        if command.action == 'homu-state':
            return self.process_homu_state(event, command)

        if command.action == 'approve':
            changed = self.approval_state != 'approved'
            changed = changed or self.approver != command.actor
            self.approval_state = 'approved'
            self.approver = command.actor

        if command.action == 'unapprove':
            changed = self.approval_state != 'unapproved'
            changed = changed or self.approver is not None
            self.approval_state = 'unapproved'
            self.approver = None

        # if command.action == 'try':
        #    changed = True
        #    self.tries.append(PullRequestTry(1, self.head_sha, None))
        return (changed,)

    def process_homu_state(self, event, command):
        changed = False
        state = command.homu_state

        if state['type'] == 'Approved':
            changed = self.approval_state != 'approved'
            changed = changed or self.approver != state['approver']
            self.approval_state = 'approved'
            self.approver = state['approver']

        elif state['type'] == 'BuildStarted':
            changed = True
            self.build_state = 'pending'

        elif state['type'] == 'BuildCompleted':
            changed = True
            self.build_state = 'completed'

        elif state['type'] == 'BuildFailed':
            changed = True
            self.build_state = 'failure'

        elif state['type'] == 'TryBuildStarted':
            changed = True
            self.tries.append(PullRequestTry(
                len(self.tries) + 1,
                state['head_sha'],
                state['merge_sha'],
                event['publishedAt'])
            )

        elif state['type'] == 'TryBuildCompleted':
            item = next((try_
                         for try_ in self.tries
                         if try_.state == 'pending'
                         and try_.merge_sha == state['merge_sha']),
                        None)

            if item:
                changed = True
                item.ended_at = event['publishedAt']
                item.state = 'completed'
                item.builders = state['builders']

        return (changed,)


class PullRequestTry:
    def __init__(self, number, head_sha, merge_sha, started_at):
        self.number = number
        self.head_sha = head_sha
        self.merge_sha = merge_sha
        self.state = 'pending'
        self.started_at = started_at

    def __str__(self):
        return "Try #{} for {}: {}".format(
                self.number,
                self.head_sha[0:7],
                self.expanded_state)

    @property
    def expanded_state(self):
        if self.state == 'completed' and self.started_at and self.ended_at:
            start = datetime.datetime.strptime(
                    self.started_at,
                    "%Y-%m-%dT%H:%M:%S%z")
            end = datetime.datetime.strptime(
                    self.ended_at,
                    "%Y-%m-%dT%H:%M:%S%z")
            duration = end - start
            return "{} after {}s".format(self.state, duration.total_seconds())
        return self.state
