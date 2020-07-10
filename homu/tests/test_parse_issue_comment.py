from homu.parse_issue_comment import parse_issue_comment

# Random commit number. Just so that we don't need to come up with a new one
# for every test.
commit = "5ffafdb1e94fa87334d4851a57564425e11a569e"
other_commit = "4e4c9ddd781729173df2720d83e0f4d1b0102a94"


def test_r_plus():
    """
    @bors r+
    """

    author = "jack"
    body = "@bors r+"
    commands = parse_issue_comment(author, body, commit, "bors")

    assert len(commands) == 1
    command = commands[0]
    assert command.action == 'approve'
    assert command.actor == 'jack'


def test_r_plus_with_colon():
    """
    @bors: r+
    """

    author = "jack"
    body = "@bors: r+"
    commands = parse_issue_comment(author, body, commit, "bors")

    assert len(commands) == 1
    command = commands[0]
    assert command.action == 'approve'
    assert command.actor == 'jack'
    assert command.commit == commit


def test_r_plus_with_sha():
    """
    @bors r+ {sha}
    """

    author = "jack"
    body = "@bors r+ {}".format(other_commit)
    commands = parse_issue_comment(author, body, commit, "bors")

    assert len(commands) == 1
    command = commands[0]
    assert command.action == 'approve'
    assert command.actor == 'jack'
    assert command.commit == other_commit


def test_r_equals():
    """
    @bors r=jill
    """

    author = "jack"
    body = "@bors r=jill"
    commands = parse_issue_comment(author, body, commit, "bors")

    assert len(commands) == 1
    command = commands[0]
    assert command.action == 'approve'
    assert command.actor == 'jill'


def test_r_equals_at_user():
    """
    @bors r=@jill
    """

    author = "jack"
    body = "@bors r=@jill"
    commands = parse_issue_comment(author, body, commit, "bors")

    assert len(commands) == 1
    command = commands[0]
    assert command.action == 'approve'
    assert command.actor == 'jill'


def test_hidden_r_equals():
    author = "bors"
    body = """
    :pushpin: Commit {0} has been approved by `jack`
    <!-- @bors r=jack {0} -->
    """.format(commit)

    commands = parse_issue_comment(author, body, commit, "bors")

    assert len(commands) == 1
    command = commands[0]
    assert command.action == 'approve'
    assert command.actor == 'jack'
    assert command.commit == commit


def test_r_me():
    """
    Ignore r=me
    """

    author = "jack"
    body = "@bors r=me"
    commands = parse_issue_comment(author, body, commit, "bors")

    # r=me is not a valid command, so no valid commands.
    assert len(commands) == 0


def test_r_minus():
    """
    @bors r-
    """

    author = "jack"
    body = "@bors r-"
    commands = parse_issue_comment(author, body, commit, "bors")

    assert len(commands) == 1
    command = commands[0]
    assert command.action == 'unapprove'


def test_priority():
    """
    @bors p=5
    """

    author = "jack"
    body = "@bors p=5"
    commands = parse_issue_comment(author, body, commit, "bors")

    assert len(commands) == 1
    command = commands[0]
    assert command.action == 'prioritize'
    assert command.priority == 5


def test_approve_and_priority():
    """
    @bors r+ p=5
    """

    author = "jack"
    body = "@bors r+ p=5"
    commands = parse_issue_comment(author, body, commit, "bors")

    assert len(commands) == 2
    approve_commands = [command for command in commands
                        if command.action == 'approve']
    prioritize_commands = [command for command in commands
                           if command.action == 'prioritize']
    assert len(approve_commands) == 1
    assert len(prioritize_commands) == 1

    assert approve_commands[0].actor == 'jack'
    assert prioritize_commands[0].priority == 5


def test_approve_specific_and_priority():
    """
    @bors r+ {sha} p=5
    """

    author = "jack"
    body = "@bors r+ {} p=5".format(other_commit)
    commands = parse_issue_comment(author, body, commit, "bors")

    assert len(commands) == 2
    approve_commands = [command for command in commands
                        if command.action == 'approve']
    prioritize_commands = [command for command in commands
                           if command.action == 'prioritize']
    assert len(approve_commands) == 1
    assert len(prioritize_commands) == 1

    assert approve_commands[0].actor == 'jack'
    assert approve_commands[0].commit == other_commit
    assert prioritize_commands[0].priority == 5


def test_delegate_plus():
    """
    @bors delegate+
    """

    author = "jack"
    body = "@bors delegate+"
    commands = parse_issue_comment(author, body, commit, "bors")

    assert len(commands) == 1
    command = commands[0]
    assert command.action == 'delegate-author'


def test_delegate_equals():
    """
    @bors delegate={username}
    """

    author = "jack"
    body = "@bors delegate=jill"
    commands = parse_issue_comment(author, body, commit, "bors")

    assert len(commands) == 1
    command = commands[0]
    assert command.action == 'delegate'
    assert command.delegate_to == 'jill'


def test_delegate_minus():
    """
    @bors delegate-
    """

    author = "jack"
    body = "@bors delegate-"
    commands = parse_issue_comment(author, body, commit, "bors")

    assert len(commands) == 1
    command = commands[0]
    assert command.action == 'undelegate'


def test_retry():
    """
    @bors retry
    """

    author = "jack"
    body = "@bors retry"
    commands = parse_issue_comment(author, body, commit, "bors")

    assert len(commands) == 1
    command = commands[0]
    assert command.action == 'retry'


def test_try():
    """
    @bors try
    """

    author = "jack"
    body = "@bors try"
    commands = parse_issue_comment(author, body, commit, "bors")

    assert len(commands) == 1
    command = commands[0]
    assert command.action == 'try'


def test_try_minus():
    """
    @bors try-
    """

    author = "jack"
    body = "@bors try-"
    commands = parse_issue_comment(author, body, commit, "bors")

    assert len(commands) == 1
    command = commands[0]
    assert command.action == 'untry'


def test_rollup():
    """
    @bors rollup
    """

    author = "jack"
    body = "@bors rollup"
    commands = parse_issue_comment(author, body, commit, "bors")

    assert len(commands) == 1
    command = commands[0]
    assert command.action == 'rollup'
    assert command.rollup_value == 1


def test_rollup_minus():
    """
    @bors rollup-
    """

    author = "jack"
    body = "@bors rollup-"
    commands = parse_issue_comment(author, body, commit, "bors")

    assert len(commands) == 1
    command = commands[0]
    assert command.action == 'rollup'
    assert command.rollup_value == 0


def test_rollup_iffy():
    """
    @bors rollup=iffy
    """

    author = "manishearth"
    body = "@bors rollup=iffy"
    commands = parse_issue_comment(author, body, commit, "bors")

    assert len(commands) == 1
    command = commands[0]
    assert command.action == 'rollup'
    assert command.rollup_value == -1


def test_rollup_never():
    """
    @bors rollup=never
    """

    author = "jack"
    body = "@bors rollup=never"
    commands = parse_issue_comment(author, body, commit, "bors")

    assert len(commands) == 1
    command = commands[0]
    assert command.action == 'rollup'
    assert command.rollup_value == -2


def test_rollup_maybe():
    """
    @bors rollup=maybe
    """

    author = "jack"
    body = "@bors rollup=maybe"
    commands = parse_issue_comment(author, body, commit, "bors")

    assert len(commands) == 1
    command = commands[0]
    assert command.action == 'rollup'
    assert command.rollup_value == 0


def test_rollup_always():
    """
    @bors rollup=always
    """

    author = "jack"
    body = "@bors rollup=always"
    commands = parse_issue_comment(author, body, commit, "bors")

    assert len(commands) == 1
    command = commands[0]
    assert command.action == 'rollup'
    assert command.rollup_value == 1


def test_force():
    """
    @bors force
    """

    author = "jack"
    body = "@bors force"
    commands = parse_issue_comment(author, body, commit, "bors")

    assert len(commands) == 1
    command = commands[0]
    assert command.action == 'force'


def test_clean():
    """
    @bors clean
    """

    author = "jack"
    body = "@bors clean"
    commands = parse_issue_comment(author, body, commit, "bors")

    assert len(commands) == 1
    command = commands[0]
    assert command.action == 'clean'


def test_ping():
    """
    @bors ping
    """

    author = "jack"
    body = "@bors ping"
    commands = parse_issue_comment(author, body, commit, "bors")

    assert len(commands) == 1
    command = commands[0]
    assert command.action == 'ping'
    assert command.ping_type == 'standard'


def test_hello():
    """
    @bors hello?
    """

    author = "jack"
    body = "@bors hello?"
    commands = parse_issue_comment(author, body, commit, "bors")

    assert len(commands) == 1
    command = commands[0]
    assert command.action == 'ping'
    assert command.ping_type == 'standard'


def test_portal_ping():
    """
    @bors are you still there?
    """

    author = "jack"
    body = "@bors are you still there?"
    commands = parse_issue_comment(author, body, commit, "bors")

    assert len(commands) == 1
    command = commands[0]
    assert command.action == 'ping'
    assert command.ping_type == 'portal'


def test_treeclosed():
    """
    @bors treeclosed=50
    """

    author = "jack"
    body = "@bors treeclosed=50"
    commands = parse_issue_comment(author, body, commit, "bors")

    assert len(commands) == 1
    command = commands[0]
    assert command.action == 'treeclosed'
    assert command.treeclosed_value == 50


def test_treeclosed_minus():
    """
    @bors treeclosed-
    """

    author = "jack"
    body = "@bors treeclosed-"
    commands = parse_issue_comment(author, body, commit, "bors")

    assert len(commands) == 1
    command = commands[0]
    assert command.action == 'untreeclosed'


def test_hook():
    """
    Test hooks that are defined in the configuration

    @bors secondhook
    """

    author = "jack"
    body = "@bors secondhook"
    commands = parse_issue_comment(
            author, body, commit, "bors",
            ['firsthook', 'secondhook', 'thirdhook'])

    assert len(commands) == 1
    command = commands[0]
    assert command.action == 'hook'
    assert command.hook_name == 'secondhook'
    assert command.hook_extra is None


def test_hook_equals():
    """
    Test hooks that are defined in the configuration

    @bors secondhook=extra
    """

    author = "jack"
    body = "@bors secondhook=extra"
    commands = parse_issue_comment(
            author, body, commit, "bors",
            ['firsthook', 'secondhook', 'thirdhook'])

    assert len(commands) == 1
    command = commands[0]
    assert command.action == 'hook'
    assert command.hook_name == 'secondhook'
    assert command.hook_extra == 'extra'


def test_multiple_hooks():
    """
    Test hooks that are defined in the configuration

    @bors thirdhook secondhook=extra
    """

    author = "jack"
    body = "@bors thirdhook secondhook=extra"
    commands = parse_issue_comment(
            author, body, commit, "bors",
            ['firsthook', 'secondhook', 'thirdhook'])

    assert len(commands) == 2
    secondhook_commands = [command for command in commands
                           if command.action == 'hook'
                           and command.hook_name == 'secondhook']
    thirdhook_commands = [command for command in commands
                          if command.action == 'hook'
                          and command.hook_name == 'thirdhook']
    assert len(secondhook_commands) == 1
    assert len(thirdhook_commands) == 1
    assert secondhook_commands[0].hook_extra == 'extra'
    assert thirdhook_commands[0].hook_extra is None


def test_similar_name():
    """
    Test that a username that starts with 'bors' doesn't trigger.
    """

    author = "jack"
    body = """
    @bors-servo r+
    """
    commands = parse_issue_comment(author, body, commit, "bors")

    assert len(commands) == 0


def test_parse_up_to_first_unknown_word():
    """
    Test that when parsing, once we arrive at an unknown word, we stop parsing
    """

    author = "jack"
    body = """
    @bors retry -- yielding priority to the rollup
    """
    commands = parse_issue_comment(author, body, commit, "bors")

    assert len(commands) == 1
    command = commands[0]
    assert command.action == 'retry'

    body = """
    @bors retry (yielding priority to the rollup)
    """
    commands = parse_issue_comment(author, body, commit, "bors")

    assert len(commands) == 1
    command = commands[0]
    assert command.action == 'retry'


def test_ignore_commands_before_bors_line():
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

    assert len(commands) == 1
    command = commands[0]
    assert command.action == 'approve'
    assert command.actor == 'jack'


def test_ignore_commands_after_bors_line():
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

    assert len(commands) == 1
    command = commands[0]
    assert command.action == 'approve'
    assert command.actor == 'jack'
