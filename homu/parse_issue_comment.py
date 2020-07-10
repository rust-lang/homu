from itertools import chain
import re

WORDS_TO_ROLLUP = {
    'rollup-': 0,
    'rollup': 1,
    'rollup=maybe': 0,
    'rollup=never': -2,
    'rollup=iffy': -1,
    'rollup=always': 1,
}


class IssueCommentCommand:
    """
    A command that has been parsed out of a GitHub issue comment.

    E.g., `@bors r+` => an issue command with action == 'approve'
    """

    def __init__(self, action):
        self.action = action

    @classmethod
    def approve(cls, approver, commit):
        command = cls('approve')
        command.commit = commit
        command.actor = approver.lstrip('@')
        return command

    @classmethod
    def unapprove(cls):
        return cls('unapprove')

    @classmethod
    def prioritize(cls, priority):
        command = cls('prioritize')
        command.priority = priority
        return command

    @classmethod
    def delegate_author(cls):
        return cls('delegate-author')

    @classmethod
    def delegate(cls, delegate_to):
        command = cls('delegate')
        command.delegate_to = delegate_to
        return command

    @classmethod
    def undelegate(cls):
        return cls('undelegate')

    @classmethod
    def retry(cls):
        return cls('retry')

    @classmethod
    def try_(cls):
        return cls('try')

    @classmethod
    def untry(cls):
        return cls('untry')

    @classmethod
    def rollup(cls, rollup_value):
        command = cls('rollup')
        command.rollup_value = rollup_value
        return command

    @classmethod
    def force(cls):
        return cls('force')

    @classmethod
    def clean(cls):
        return cls('clean')

    @classmethod
    def ping(cls, ping_type='standard'):
        command = cls('ping')
        command.ping_type = ping_type
        return command

    @classmethod
    def treeclosed(cls, treeclosed_value):
        command = cls('treeclosed')
        command.treeclosed_value = treeclosed_value
        return command

    @classmethod
    def untreeclosed(cls):
        return cls('untreeclosed')

    @classmethod
    def hook(cls, hook_name, hook_extra=None):
        command = cls('hook')
        command.hook_name = hook_name
        command.hook_extra = hook_extra
        return command


def is_sha(sha):
    """
    Try to determine if the input is a git sha
    """
    return re.match(r'^[0-9a-f]{4,}$', sha)


def hook_with_extra_is_in_hooks(word, hooks):
    """
    Determine if the word given is the name of a valid hook, with extra data
    hanging off of it (e.g., `validhookname=extradata`).

       hook_with_extra_is_in_hooks(
         'validhookname=stuff',
         ['validhookname', 'other'])
       #=> True

       hook_with_extra_is_in_hooks(
         'invalidhookname=stuff',
         ['validhookname', 'other'])
       #=> False

       hook_with_extra_is_in_hooks(
         'validhookname',
         ['validhookname', 'other'])
       #=> False
    """
    for hook in hooks:
        if word.startswith('{}='.format(hook)):
            return True

    return False


def parse_issue_comment(username, body, sha, botname, hooks=[]):
    """
    Parse an issue comment looking for commands that Homu should handle

    Parameters:
    username: the username of the user that created the issue comment.
           This is without the leading @
    body: the full body of the comment (markdown)
    sha: the commit that the comment applies to
    botname: the name of bot. This is without the leading @.
           So if we should respond to `@bors {command}`, botname will be `bors`
    hooks: a list of strings that are valid hook names.
           E.g. `['hook1', 'hook2', 'hook3']`
    """

    botname_regex = re.compile(r'^.*(?=@' + botname + ')')

    # All of the 'words' after and including the botname
    words = list(chain.from_iterable(
                     re.findall(r'\S+', re.sub(botname_regex, '', x))
                 for x
                 in body.splitlines()
                 if '@' + botname in x))  # noqa

    commands = []

    if words[1:] == ["are", "you", "still", "there?"]:
        commands.append(IssueCommentCommand.ping('portal'))

    for i, word in enumerate(words):
        if word is None:
            # We already parsed the next word, and we set it to an empty string
            # to signify that we did.
            continue

        if word == '@' + botname:
            continue

        if word == '@' + botname + ':':
            continue

        if word == 'r+' or word.startswith('r='):
            approved_sha = sha

            if i + 1 < len(words) and is_sha(words[i + 1]):
                approved_sha = words[i + 1]
                words[i + 1] = None

            approver = word[len('r='):] if word.startswith('r=') else username

            # Ignore "r=me"
            if approver == 'me':
                continue

            commands.append(
                    IssueCommentCommand.approve(approver, approved_sha))

        elif word == 'r-':
            commands.append(IssueCommentCommand.unapprove())

        elif word.startswith('p='):
            try:
                pvalue = int(word[len('p='):])
            except ValueError:
                continue

            commands.append(IssueCommentCommand.prioritize(pvalue))

        elif word.startswith('delegate='):
            delegate = word[len('delegate='):]
            commands.append(IssueCommentCommand.delegate(delegate))

        elif word == 'delegate-':
            commands.append(IssueCommentCommand.undelegate())

        elif word == 'delegate+':
            commands.append(IssueCommentCommand.delegate_author())

        elif word == 'retry':
            commands.append(IssueCommentCommand.retry())

        elif word == 'try':
            commands.append(IssueCommentCommand.try_())

        elif word == 'try-':
            commands.append(IssueCommentCommand.untry())

        elif word in WORDS_TO_ROLLUP:
            rollup_value = WORDS_TO_ROLLUP[word]
            commands.append(IssueCommentCommand.rollup(rollup_value))

        elif word == 'force':
            commands.append(IssueCommentCommand.force())

        elif word == 'clean':
            commands.append(IssueCommentCommand.clean())

        elif (word == 'hello?' or word == 'ping'):
            commands.append(IssueCommentCommand.ping())

        elif word.startswith('treeclosed='):
            try:
                treeclosed = int(word[len('treeclosed='):])
                commands.append(IssueCommentCommand.treeclosed(treeclosed))
            except ValueError:
                pass

        elif word == 'treeclosed-':
            commands.append(IssueCommentCommand.untreeclosed())

        elif word in hooks:
            commands.append(IssueCommentCommand.hook(word))

        elif hook_with_extra_is_in_hooks(word, hooks):
            # word is like `somehook=data` and `somehook` is in our list of
            # potential hooks
            (hook_name, hook_extra) = word.split('=', 2)
            commands.append(IssueCommentCommand.hook(hook_name, hook_extra))

        else:
            # First time we reach an unknown word, stop parsing.
            break

    return commands
