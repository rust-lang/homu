import unittest.mock
import re
from homu.comments import Comment
from homu.consts import (
    LabelEvent,
)

from homu.pull_req_state import (
    PullReqState,
    # ProcessEventResult,
)
from homu.pull_request_events import (
    PullRequestEvent
)


def new_state(num=1, head_sha='abcdef', status='', title='A change'):
    repo = unittest.mock.Mock()
    repo.treeclosed = False

    state = PullReqState(
            num=num,
            head_sha=head_sha,
            status=status,
            db=None,
            repo_label='test',
            mergeable_que=None,
            gh=None,
            owner='test-org',
            name='test-repo',
            label_events=[],
            repos={
                'test': repo,
            })

    state.title = title
    state.cfg = {}

    return state


def render_comment(comment):
    if isinstance(comment, Comment):
        return comment.render()
    else:
        return comment


def assert_comment(pattern, comments):
    for comment in comments:
        if re.search(pattern, render_comment(comment)) is not None:
            return True

    return False


def create_event(event):
    return PullRequestEvent(event)


def test_baseline():
    """
    Test that a new pull request does not have any state
    """

    state = new_state()
    assert state.get_status() == ''


def test_current_sha():
    """
    Test that a pull request gets the current head sha
    """

    state = new_state()
    event = create_event({
        'eventType': 'PullRequestCommit',
        'commit': {
            'oid': '012345',
        }
    })
    result = state.process_event(event)
    assert result.changed is True
    assert state.head_sha == '012345'

    state = new_state()
    event = create_event({
        'eventType': 'HeadRefForcePushedEvent',
        'actor': {
            'login': 'ferris',
        },
        'beforeCommit': {
            'oid': 'abcdef',
        },
        'afterCommit': {
            'oid': '012345',
        },
    })
    result = state.process_event(event)
    assert result.changed is True
    assert state.head_sha == '012345'


def return_true(a, b, c, d, e, f):
    return True


@unittest.mock.patch('homu.pull_req_state.assert_authorized',
                     side_effect=return_true)
def test_approved(_):
    """
    Test that a pull request that has been approved is still approved
    """

    # Typical approval
    state = new_state(head_sha='abcdef')
    event = create_event({
        'eventType': 'IssueComment',
        'author': {
            'login': 'ferris',
        },
        'body': '@bors r+',
        'publishedAt': '1985-04-21T00:00:00Z',
    })
    result = state.process_event(event)
    assert result.changed is True
    assert assert_comment(r'Commit abcdef has been approved', result.comments)
    assert state.approved_by == 'ferris'
    assert state.get_status() == 'approved'

    # Approval by someone else
    state = new_state(head_sha='abcdef')
    event = create_event({
        'eventType': 'IssueComment',
        'author': {
            'login': 'ferris',
        },
        'body': '@bors r=someone',
        'publishedAt': '1985-04-21T00:00:00Z',
    })
    result = state.process_event(event)
    assert result.changed is True
    assert assert_comment(r'Commit abcdef has been approved', result.comments)
    assert state.approved_by == 'someone'
    assert state.get_status() == 'approved'

    # Approval with commit sha
    state = new_state(head_sha='abcdef')
    event = create_event({
        'eventType': 'IssueComment',
        'author': {
            'login': 'ferris',
        },
        'body': '@bors r+ abcdef',
        'publishedAt': '1985-04-21T00:00:00Z',
    })
    result = state.process_event(event)
    assert result.changed is True
    assert assert_comment(r'Commit abcdef has been approved', result.comments)
    assert state.get_status() == 'approved'

    # Approval with commit sha by bors
    state = new_state(head_sha='abcdef')
    event = create_event({
        'eventType': 'IssueComment',
        'author': {
            'login': 'bors',
        },
        'body': '''
            Commit abcdef has been approved

            <!-- @bors r=ferris abcdef -->
        ''',
        'publishedAt': '1985-04-21T00:00:00Z',
    })
    result = state.process_event(event)
    assert result.changed is True
    for comment in result.comments:
        print(render_comment(comment))
    assert len(result.comments) == 0
    assert state.get_status() == 'approved'

    # Approval of WIP
    state = new_state(head_sha='abcdef', title="[WIP] A change")
    event = create_event({
        'eventType': 'IssueComment',
        'author': {
            'login': 'ferris',
        },
        'body': '@bors r+',
        'publishedAt': '1985-04-21T00:00:00Z',
    })
    result = state.process_event(event)
    assert result.changed is False
    assert assert_comment(r'still in progress', result.comments)
    assert state.get_status() == ''

    # Approval with invalid commit sha
    state = new_state(head_sha='abcdef')
    event = create_event({
        'eventType': 'IssueComment',
        'author': {
            'login': 'ferris',
        },
        'body': '@bors r+ 012345',
        'publishedAt': '1985-04-21T00:00:00Z',
    })
    result = state.process_event(event)
    assert result.changed is False
    assert assert_comment(r'`012345` is not a valid commit SHA',
                          result.comments)
    assert state.get_status() == ''

    # Approval of already approved state
    state = new_state(head_sha='abcdef')
    event1 = create_event({
        'eventType': 'IssueComment',
        'author': {
            'login': 'ferris',
        },
        'body': '@bors r+',
        'publishedAt': '1985-04-21T00:00:00Z',
    })
    event2 = create_event({
        'eventType': 'IssueComment',
        'author': {
            'login': 'bill',
        },
        'body': '@bors r+',
        'publishedAt': '1985-04-21T00:01:00Z',
    })
    result1 = state.process_event(event1)
    assert result1.changed is True
    assert state.get_status() == 'approved'
    result2 = state.process_event(event2)
    assert result2.changed is False
    assert assert_comment(r'already approved', result2.comments)
    assert state.get_status() == 'approved'


#def test_tried():
#    """
#    Test that a pull request that has been tried shows up as tried
#    """
#
#
#def test_tried_and_approved():
#    """
#    Test that a pull request that has been approved AND tried shows up as
#    approved AND tried
#    """


@unittest.mock.patch('homu.pull_req_state.assert_authorized',
                     side_effect=return_true)
def test_approved_unapproved(_):
    """
    Test that a pull request that was r+'ed, but then r-'ed shows up as
    unapproved. I.e., there isn't a bug that allows an unapproved item to
    all of a sudden be approved after a restart.
    """

    state = new_state()
    state.process_event(create_event({
        'eventType': 'IssueComment',
        'author': {
            'login': 'ferris',
        },
        'body': '@bors r+',
        'publishedAt': '1985-04-21T00:00:00Z',
    }))
    assert state.get_status() != ''

    result = state.process_event(create_event({
        'eventType': 'IssueComment',
        'author': {
            'login': 'ferris',
        },
        'body': '@bors r-',
        'publishedAt': '1985-04-21T00:01:00Z',
    }))
    assert state.get_status() == ''
    assert state.approved_by == ''
    assert result.changed is True
    assert len(result.label_events) == 1
    assert result.label_events[0] == LabelEvent.REJECTED


@unittest.mock.patch('homu.pull_req_state.assert_authorized',
                     side_effect=return_true)
def test_approved_changed_push(_):
    """
    Test that a pull request that was r+'ed, but then had more commits
    pushed is not listed as approved.
    """

    # Regular push
    state = new_state()
    state.process_event(create_event({
        'eventType': 'IssueComment',
        'author': {
            'login': 'ferris',
        },
        'body': '@bors r+',
        'publishedAt': '1985-04-21T00:00:00Z',
    }))
    state.process_event(create_event({
        'eventType': 'PullRequestCommit',
        'commit': {
            'oid': '012345',
        },
    }))

    assert state.get_status() == ''
    assert state.head_sha == '012345'

    # Force push
    state = new_state()
    state.process_event(create_event({
        'eventType': 'IssueComment',
        'author': {
            'login': 'ferris',
        },
        'body': '@bors r+',
        'publishedAt': '1985-04-21T00:00:00Z',
    }))
    state.process_event(create_event({
        'eventType': 'HeadRefForcePushedEvent',
        'actor': {
            'login': 'ferris',
        },
        'beforeCommit': {
            'oid': 'abcdef',
        },
        'afterCommit': {
            'oid': '012345',
        },
    }))

    assert state.get_status() == ''
    assert state.head_sha == '012345'


@unittest.mock.patch('homu.pull_req_state.assert_authorized',
                     side_effect=return_true)
def test_approved_changed_base(_):
    """
    Test that a pull request that was r+'ed, but then changed its base is
    not listed as approved.
    """

    state = new_state()
    state.process_event(create_event({
        'eventType': 'IssueComment',
        'author': {
            'login': 'ferris',
        },
        'body': '@bors r+',
        'publishedAt': '1985-04-21T00:00:00Z',
    }))
    state.process_event(create_event({
        'eventType': 'BaseRefChangedEvent',
        'actor': {
            'login': 'ferris',
        },
    }))

    assert state.get_status() == ''


#def test_pending():
#    """
#    Test that a pull request that started before the service was restarted
#    but didn't finish is still marked as pending.
#
#    Currently we don't reach out to see if the build is still running or if
#    it finished while we were off.
#    """


@unittest.mock.patch('homu.pull_req_state.assert_authorized',
                     side_effect=return_true)
def test_priority(_):
    """
    Test that priority values stick
    """

    state = new_state()
    result = state.process_event(create_event({
        'eventType': 'IssueComment',
        'author': {
            'login': 'ferris',
        },
        'body': '@bors p=20',
        'publishedAt': '1985-04-21T00:00:00Z',
    }))
    assert state.priority == 20
    assert result.changed is True
    assert len(result.comments) == 0

    state = new_state()
    result = state.process_event(create_event({
        'eventType': 'IssueComment',
        'author': {
            'login': 'ferris',
        },
        'body': '@bors p=9002',
        'publishedAt': '1985-04-21T00:00:00Z',
    }))
    assert state.priority == 0
    assert result.changed is False
    assert assert_comment(r'Priority.*is ignored', result.comments)


@unittest.mock.patch('homu.pull_req_state.assert_authorized',
                     side_effect=return_true)
def test_rollup(_):
    """
    Test that rollup values stick
    """
    state = new_state()
    result = state.process_event(create_event({
        'eventType': 'IssueComment',
        'author': {
            'login': 'ferris',
        },
        'body': '@bors rollup=always',
        'publishedAt': '1985-04-21T00:00:00Z',
    }))
    assert state.rollup == 1
    assert result.changed is True
    assert len(result.comments) == 0
