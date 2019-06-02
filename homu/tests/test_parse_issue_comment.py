import unittest
from homu.parse_issue_comment import parse_issue_comment

# Random commit number. Just so that we don't need to come up with a new one
# for every test.
commit = "5ffafdb1e94fa87334d4851a57564425e11a569e"
other_commit = "4e4c9ddd781729173df2720d83e0f4d1b0102a94"


class TestParseIssueComment(unittest.TestCase):
    def test_r_plus(self):
        """
        @bors r+
        """

        author = "jack"
        body = "@bors r+"
        commands = parse_issue_comment(author, body, commit, "bors")

        self.assertEqual(len(commands), 1)
        command = commands[0]
        self.assertEqual(command.action, 'approve')
        self.assertEqual(command.actor, 'jack')

    def test_r_plus_with_sha(self):
        """
        @bors r+ {sha}
        """

        author = "jack"
        body = "@bors r+ {}".format(other_commit)
        commands = parse_issue_comment(author, body, commit, "bors")

        self.assertEqual(len(commands), 1)
        command = commands[0]
        self.assertEqual(command.action, 'approve')
        self.assertEqual(command.actor, 'jack')
        self.assertEqual(command.commit, other_commit)

    def test_r_equals(self):
        """
        @bors r=jill
        """

        author = "jack"
        body = "@bors r=jill"
        commands = parse_issue_comment(author, body, commit, "bors")

        self.assertEqual(len(commands), 1)
        command = commands[0]
        self.assertEqual(command.action, 'approve')
        self.assertEqual(command.actor, 'jill')

    def test_r_me(self):
        """
        Ignore r=me
        """

        author = "jack"
        body = "@bors r=me"
        commands = parse_issue_comment(author, body, commit, "bors")

        # r=me is not a valid command, so no valid commands.
        self.assertEqual(len(commands), 0)

    def test_r_minus(self):
        """
        @bors r-
        """

        author = "jack"
        body = "@bors r-"
        commands = parse_issue_comment(author, body, commit, "bors")

        self.assertEqual(len(commands), 1)
        command = commands[0]
        self.assertEqual(command.action, 'unapprove')

    def test_priority(self):
        """
        @bors p=5
        """

        author = "jack"
        body = "@bors p=5"
        commands = parse_issue_comment(author, body, commit, "bors")

        self.assertEqual(len(commands), 1)
        command = commands[0]
        self.assertEqual(command.action, 'prioritize')
        self.assertEqual(command.priority, 5)

    def test_approve_and_priority(self):
        """
        @bors r+ p=5
        """

        author = "jack"
        body = "@bors r+ p=5"
        commands = parse_issue_comment(author, body, commit, "bors")

        self.assertEqual(len(commands), 2)
        approve_commands = [command for command in commands
                            if command.action == 'approve']
        prioritize_commands = [command for command in commands
                               if command.action == 'prioritize']
        self.assertEqual(len(approve_commands), 1)
        self.assertEqual(len(prioritize_commands), 1)

        self.assertEqual(approve_commands[0].actor, 'jack')
        self.assertEqual(prioritize_commands[0].priority, 5)

    def test_approve_specific_and_priority(self):
        """
        @bors r+ {sha} p=5
        """

        author = "jack"
        body = "@bors r+ {} p=5".format(other_commit)
        commands = parse_issue_comment(author, body, commit, "bors")

        self.assertEqual(len(commands), 2)
        approve_commands = [command for command in commands
                            if command.action == 'approve']
        prioritize_commands = [command for command in commands
                               if command.action == 'prioritize']
        self.assertEqual(len(approve_commands), 1)
        self.assertEqual(len(prioritize_commands), 1)

        self.assertEqual(approve_commands[0].actor, 'jack')
        self.assertEqual(approve_commands[0].commit, other_commit)
        self.assertEqual(prioritize_commands[0].priority, 5)

    def test_delegate_plus(self):
        """
        @bors delegate+
        """

        author = "jack"
        body = "@bors delegate+"
        commands = parse_issue_comment(author, body, commit, "bors")

        self.assertEqual(len(commands), 1)
        command = commands[0]
        self.assertEqual(command.action, 'delegate-author')

    def test_delegate_equals(self):
        """
        @bors delegate={username}
        """

        author = "jack"
        body = "@bors delegate=jill"
        commands = parse_issue_comment(author, body, commit, "bors")

        self.assertEqual(len(commands), 1)
        command = commands[0]
        self.assertEqual(command.action, 'delegate')
        self.assertEqual(command.delegate_to, 'jill')

    def test_delegate_minus(self):
        """
        @bors delegate-
        """

        author = "jack"
        body = "@bors delegate-"
        commands = parse_issue_comment(author, body, commit, "bors")

        self.assertEqual(len(commands), 1)
        command = commands[0]
        self.assertEqual(command.action, 'undelegate')

    def test_retry(self):
        """
        @bors retry
        """

        author = "jack"
        body = "@bors retry"
        commands = parse_issue_comment(author, body, commit, "bors")

        self.assertEqual(len(commands), 1)
        command = commands[0]
        self.assertEqual(command.action, 'retry')

    def test_try(self):
        """
        @bors try
        """

        author = "jack"
        body = "@bors try"
        commands = parse_issue_comment(author, body, commit, "bors")

        self.assertEqual(len(commands), 1)
        command = commands[0]
        self.assertEqual(command.action, 'try')

    def test_try_minus(self):
        """
        @bors try-
        """

        author = "jack"
        body = "@bors try-"
        commands = parse_issue_comment(author, body, commit, "bors")

        self.assertEqual(len(commands), 1)
        command = commands[0]
        self.assertEqual(command.action, 'untry')

    def test_rollup(self):
        """
        @bors rollup
        """

        author = "jack"
        body = "@bors rollup"
        commands = parse_issue_comment(author, body, commit, "bors")

        self.assertEqual(len(commands), 1)
        command = commands[0]
        self.assertEqual(command.action, 'rollup')
        self.assertEqual(command.rollup_value, 1)

    def test_rollup_minus(self):
        """
        @bors rollup-
        """

        author = "jack"
        body = "@bors rollup-"
        commands = parse_issue_comment(author, body, commit, "bors")

        self.assertEqual(len(commands), 1)
        command = commands[0]
        self.assertEqual(command.action, 'rollup')
        self.assertEqual(command.rollup_value, 0)

    def test_rollup_never(self):
        """
        @bors rollup=never
        """

        author = "jack"
        body = "@bors rollup=never"
        commands = parse_issue_comment(author, body, commit, "bors")

        self.assertEqual(len(commands), 1)
        command = commands[0]
        self.assertEqual(command.action, 'rollup')
        self.assertEqual(command.rollup_value, -1)

    def test_rollup_maybe(self):
        """
        @bors rollup=maybe
        """

        author = "jack"
        body = "@bors rollup=maybe"
        commands = parse_issue_comment(author, body, commit, "bors")

        self.assertEqual(len(commands), 1)
        command = commands[0]
        self.assertEqual(command.action, 'rollup')
        self.assertEqual(command.rollup_value, 0)

    def test_rollup_always(self):
        """
        @bors rollup=always
        """

        author = "jack"
        body = "@bors rollup=always"
        commands = parse_issue_comment(author, body, commit, "bors")

        self.assertEqual(len(commands), 1)
        command = commands[0]
        self.assertEqual(command.action, 'rollup')
        self.assertEqual(command.rollup_value, 1)

    def test_force(self):
        """
        @bors force
        """

        author = "jack"
        body = "@bors force"
        commands = parse_issue_comment(author, body, commit, "bors")

        self.assertEqual(len(commands), 1)
        command = commands[0]
        self.assertEqual(command.action, 'force')

    def test_clean(self):
        """
        @bors clean
        """

        author = "jack"
        body = "@bors clean"
        commands = parse_issue_comment(author, body, commit, "bors")

        self.assertEqual(len(commands), 1)
        command = commands[0]
        self.assertEqual(command.action, 'clean')

    def test_ping(self):
        """
        @bors ping
        """

        author = "jack"
        body = "@bors ping"
        commands = parse_issue_comment(author, body, commit, "bors")

        self.assertEqual(len(commands), 1)
        command = commands[0]
        self.assertEqual(command.action, 'ping')
        self.assertEqual(command.ping_type, 'standard')

    def test_hello(self):
        """
        @bors hello?
        """

        author = "jack"
        body = "@bors hello?"
        commands = parse_issue_comment(author, body, commit, "bors")

        self.assertEqual(len(commands), 1)
        command = commands[0]
        self.assertEqual(command.action, 'ping')
        self.assertEqual(command.ping_type, 'standard')

    def test_portal_ping(self):
        """
        @bors are you still there?
        """

        author = "jack"
        body = "@bors are you still there?"
        commands = parse_issue_comment(author, body, commit, "bors")

        self.assertEqual(len(commands), 1)
        command = commands[0]
        self.assertEqual(command.action, 'ping')
        self.assertEqual(command.ping_type, 'portal')

    def test_treeclosed(self):
        """
        @bors treeclosed=50
        """

        author = "jack"
        body = "@bors treeclosed=50"
        commands = parse_issue_comment(author, body, commit, "bors")

        self.assertEqual(len(commands), 1)
        command = commands[0]
        self.assertEqual(command.action, 'treeclosed')
        self.assertEqual(command.treeclosed_value, 50)

    def test_treeclosed_minus(self):
        """
        @bors treeclosed-
        """

        author = "jack"
        body = "@bors treeclosed-"
        commands = parse_issue_comment(author, body, commit, "bors")

        self.assertEqual(len(commands), 1)
        command = commands[0]
        self.assertEqual(command.action, 'untreeclosed')

    def test_hook(self):
        """
        Test hooks that are defined in the configuration

        @bors secondhook
        """

        author = "jack"
        body = "@bors secondhook"
        commands = parse_issue_comment(
                author, body, commit, "bors",
                ['firsthook', 'secondhook', 'thirdhook'])

        self.assertEqual(len(commands), 1)
        command = commands[0]
        self.assertEqual(command.action, 'hook')
        self.assertEqual(command.hook_name, 'secondhook')
        self.assertEqual(command.hook_extra, None)

    def test_hook_equals(self):
        """
        Test hooks that are defined in the configuration

        @bors secondhook=extra
        """

        author = "jack"
        body = "@bors secondhook=extra"
        commands = parse_issue_comment(
                author, body, commit, "bors",
                ['firsthook', 'secondhook', 'thirdhook'])

        self.assertEqual(len(commands), 1)
        command = commands[0]
        self.assertEqual(command.action, 'hook')
        self.assertEqual(command.hook_name, 'secondhook')
        self.assertEqual(command.hook_extra, 'extra')

    def test_multiple_hooks(self):
        """
        Test hooks that are defined in the configuration

        @bors thirdhook secondhook=extra
        """

        author = "jack"
        body = "@bors thirdhook secondhook=extra"
        commands = parse_issue_comment(
                author, body, commit, "bors",
                ['firsthook', 'secondhook', 'thirdhook'])

        self.assertEqual(len(commands), 2)
        secondhook_commands = [command for command in commands
                               if command.action == 'hook'
                               and command.hook_name == 'secondhook']
        thirdhook_commands = [command for command in commands
                              if command.action == 'hook'
                              and command.hook_name == 'thirdhook']
        self.assertEqual(len(secondhook_commands), 1)
        self.assertEqual(len(thirdhook_commands), 1)
        self.assertEqual(secondhook_commands[0].hook_extra, 'extra')
        self.assertEqual(thirdhook_commands[0].hook_extra, None)

    def test_ignore_commands_before_bors_line(self):
        """
        Test that when command-like statements appear before the @bors part,
        they don't get parsed
        """

        author = "jack"
        body = """
        A sentence that includes command-like statements, like r- or ping or delegate+ or the like.

        @bors r+
        """ # noqa
        commands = parse_issue_comment(author, body, commit, "bors")

        self.assertEqual(len(commands), 1)
        command = commands[0]
        self.assertEqual(command.action, 'approve')
        self.assertEqual(command.actor, 'jack')

    def test_ignore_commands_after_bors_line(self):
        """
        Test that when command-like statements appear after the @bors part,
        they don't get parsed
        """

        author = "jack"
        body = """
        @bors r+

        A sentence that includes command-like statements, like r- or ping or delegate+ or the like.
        """ # noqa
        commands = parse_issue_comment(author, body, commit, "bors")

        self.assertEqual(len(commands), 1)
        command = commands[0]
        self.assertEqual(command.action, 'approve')
        self.assertEqual(command.actor, 'jack')


if __name__ == '__main__':
    unittest.main()
