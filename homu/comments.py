import json


class Comment:
    def __init__(self, **args):
        if len(args) != len(self.params):
            raise KeyError("different number of params")
        for key, value in args.items():
            if key in self.params:
                setattr(self, key, value)
            else:
                raise KeyError("unknown attribute: %s" % key)

    def jsonify(self):
        out = {"type": self.__class__.__name__}
        for param in self.params:
            out[param] = getattr(self, param)
        return json.dumps(out, separators=(',', ':'))


class Approved(Comment):
    def __init__(self, bot=None, **args):
        # Because homu needs to leave a comment for itself to kick off a build,
        # we need to know the correct botname to use. However, we don't want to
        # save that botname in our state JSON. So we need a custom constructor
        # to grab the botname and delegate the rest of the keyword args to the
        # Comment constructor.
        super().__init__(**args)
        self.bot = bot

    params = ["sha", "approver"]

    def render(self):
        # The comment here is required because Homu wants a full, unambiguous,
        # pinned commit hash to kick off the build, and this note-to-self is
        # how it gets it. This is to safeguard against situations where Homu
        # reloads and another commit has been pushed since the approval.
        message = ":pushpin: Commit {sha} has been " + \
            "approved by `{approver}`\n\n" + \
            "<!-- @{bot} r={approver} {sha} -->"
        return message.format(
            sha=self.sha,
            approver=self.approver,
            bot=self.bot
        )


class ApprovalIgnoredWip(Comment):
    def __init__(self, wip_keyword=None, **args):
        # We want to use the wip keyword in the message, but not in the json
        # blob.
        super().__init__(**args)
        self.wip_keyword = wip_keyword

    params = ["sha"]

    def render(self):
        message = ':clipboard:' + \
            ' Looks like this PR is still in progress,' + \
            ' ignoring approval.\n\n' + \
            'Hint: Remove **{wip_keyword}** from this PR\'s title when' + \
            ' it is ready for review.'
        return message.format(wip_keyword=self.wip_keyword)


class Delegated(Comment):
    params = ["delegator", "delegate"]

    def render(self):
        message = ':v: @{} can now approve this pull request'
        return message.format(self.delegate)


class BuildStarted(Comment):
    params = ["head_sha", "merge_sha"]

    def render(self):
        return ":hourglass: Testing commit %s with merge %s..." % (
            self.head_sha, self.merge_sha,
        )


class TryBuildStarted(Comment):
    params = ["head_sha", "merge_sha"]

    def render(self):
        return ":hourglass: Trying commit %s with merge %s..." % (
            self.head_sha, self.merge_sha,
        )


class BuildCompleted(Comment):
    params = ["approved_by", "base_ref", "builders", "merge_sha"]

    def render(self):
        urls = ", ".join(
            "[%s](%s)" % kv for kv in sorted(self.builders.items())
        )
        return (
            ":sunny: Test successful - %s\n"
            "Approved by: %s\n"
            "Pushing %s to %s..."
            % (
                urls, self.approved_by, self.merge_sha, self.base_ref,
            )
        )


class TryBuildCompleted(Comment):
    params = ["builders", "merge_sha"]

    def render(self):
        urls = ", ".join(
            "[%s](%s)" % kv for kv in sorted(self.builders.items())
        )
        return ":sunny: Try build successful - %s\nBuild commit: %s (`%s`)" % (
            urls, self.merge_sha, self.merge_sha,
        )


class BuildFailed(Comment):
    params = ["builder_url", "builder_name"]

    def render(self):
        return ":broken_heart: Test failed - [%s](%s)" % (
            self.builder_name, self.builder_url
        )


class TryBuildFailed(Comment):
    params = ["builder_url", "builder_name"]

    def render(self):
        return ":broken_heart: Test failed - [%s](%s)" % (
            self.builder_name, self.builder_url
        )


class TimedOut(Comment):
    params = []

    def render(self):
        return ":boom: Test timed out"
