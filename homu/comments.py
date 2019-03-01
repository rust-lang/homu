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
        return ":sunny: Try build successful - %s\nBuild commit: %s" % (
            urls, self.merge_sha,
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
