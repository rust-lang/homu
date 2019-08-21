import unittest.mock
import re
from homu.comments import Comment
from homu.consts import (
    LabelEvent,
)

from homu.pull_req_state import (
    PullReqState,
    ApprovalState,
    BuildState,
    # ProcessEventResult,
)
from homu.github_v4 import (
    PullRequestEvent
)


def new_state(num=1, head_sha='abcdef', status='', title='A change'):
    repo = unittest.mock.Mock()
    repo.treeclosed = False
    repo.repo_label = 'test'
    repo.owner = 'test-org'
    repo.name = 'test-repo'
    repo.gh = None
    repo.github_repo = None

    state = PullReqState(
            repository=repo,
            num=num,
            head_sha=head_sha,
            status=status,
            db=None,
            mergeable_que=None,
            author='ferris')

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


global_cursor = 1


def create_event(event):
    global global_cursor
    global_cursor += 1
    cursor = "{0:010d}".format(global_cursor)
    return PullRequestEvent(cursor, event)


def test_baseline():
    """
    Test that a new pull request does not have any state
    """

    state = new_state()
    assert state.get_status() == ''
    assert state.approval_state == ApprovalState.UNAPPROVED
    assert state.build_state == BuildState.NONE
    assert state.try_state == BuildState.NONE


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
    assert state.approval_state == ApprovalState.APPROVED

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
    assert state.approval_state == ApprovalState.APPROVED

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
    assert state.approval_state == ApprovalState.APPROVED

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
    assert state.approval_state == ApprovalState.APPROVED

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
    assert state.approval_state == ApprovalState.UNAPPROVED

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
    assert state.approval_state == ApprovalState.UNAPPROVED

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
    assert state.approval_state == ApprovalState.APPROVED
    result2 = state.process_event(event2)
    assert result2.changed is False
    assert assert_comment(r'already approved', result2.comments)
    assert state.get_status() == 'approved'
    assert state.approval_state == ApprovalState.APPROVED


@unittest.mock.patch('homu.pull_req_state.assert_authorized',
                     side_effect=return_true)
def test_homu_state_approval(_):
    state = new_state(head_sha='abcdef')
    event = create_event({
        'eventType': 'IssueComment',
        'author': {
            'login': 'bors',
        },
        'body': '''
            Commit abcdef has been approved

            <!-- homu: {"type":"Approved","sha":"012345","approver":"ferris"} -->
        ''', # noqa
        'publishedAt': '1985-04-21T00:00:00Z',
    })
    result = state.process_event(event)
    assert result.changed is True
    assert len(result.comments) == 0
    assert state.get_status() == 'approved'
    assert state.approved_by == 'ferris'
    assert state.approval_state == ApprovalState.APPROVED

    # Nobody but bors can use homu state
    state = new_state(head_sha='abcdef')
    event = create_event({
        'eventType': 'IssueComment',
        'author': {
            'login': 'ferris',
        },
        'body': '''
            Commit abcdef has been approved

            <!-- homu: {"type":"Approved","sha":"012345","approver":"ferris"} -->
        ''', # noqa
        'publishedAt': '1985-04-21T00:00:00Z',
    })
    result = state.process_event(event)
    assert result.changed is False
    assert len(result.comments) == 0
    assert state.get_status() == ''
    assert state.approved_by == ''
    assert state.approval_state == ApprovalState.UNAPPROVED


@unittest.mock.patch('homu.pull_req_state.assert_authorized',
                     side_effect=return_true)
def test_tried(_):
    """
    Test that a pull request that has been tried shows up as tried
    """

    state = new_state(head_sha='065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe')
    result = state.process_event(create_event({
        'eventType': 'IssueComment',
        'author': {
            'login': 'bors',
        },
        'body': '''
            :hourglass: Trying commit 065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe with merge 330c85d9270b32d7703ebefc337eb37ae959f741...
            <!-- homu: {"type":"TryBuildStarted","head_sha":"065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe","merge_sha":"330c85d9270b32d7703ebefc337eb37ae959f741"} -->
        ''', # noqa
        'publishedAt': '1985-04-21T00:00:00Z',
    }))

    assert result.changed is True
    assert state.try_ is True
    assert state.get_status() == 'pending'
    assert state.build_state == BuildState.NONE
    assert state.try_state == BuildState.PENDING
    assert state.last_try.state == BuildState.PENDING

    result = state.process_event(create_event({
        'eventType': 'IssueComment',
        'author': {
            'login': 'bors',
        },
        'body': '''
            :sunny: Try build successful - [checks-travis](https://travis-ci.com/rust-lang/rust/builds/115542062) Build commit: 330c85d9270b32d7703ebefc337eb37ae959f741
            <!-- homu: {"type":"TryBuildCompleted","builders":{"checks-travis":"https://travis-ci.com/rust-lang/rust/builds/115542062"},"merge_sha":"330c85d9270b32d7703ebefc337eb37ae959f741"} -->
        ''', # noqa
        'publishedAt': '1985-04-21T00:01:00Z',
    }))

    assert result.changed is True
    assert state.try_ is True
    assert state.get_status() == 'success'
    assert state.build_state == BuildState.NONE
    assert state.try_state == BuildState.SUCCESS
    assert state.last_try.state == BuildState.SUCCESS


@unittest.mock.patch('homu.pull_req_state.assert_authorized',
                     side_effect=return_true)
def test_try_failed(_):
    """
    Test that a pull request that has been tried shows up as tried
    """

    state = new_state(head_sha='065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe')
    result = state.process_event(create_event({
        'eventType': 'IssueComment',
        'author': {
            'login': 'bors',
        },
        'body': '''
            :hourglass: Trying commit 065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe with merge 330c85d9270b32d7703ebefc337eb37ae959f741...
            <!-- homu: {"type":"TryBuildStarted","head_sha":"065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe","merge_sha":"330c85d9270b32d7703ebefc337eb37ae959f741"} -->
        ''', # noqa
        'publishedAt': '1985-04-21T00:00:00Z',
    }))

    assert result.changed is True
    assert state.try_ is True
    assert state.get_status() == 'pending'
    assert state.build_state == BuildState.NONE
    assert state.try_state == BuildState.PENDING
    assert state.last_try.state == BuildState.PENDING

    result = state.process_event(create_event({
        'eventType': 'IssueComment',
        'author': {
            'login': 'bors',
        },
        'body': '''
            :sunny: Try build successful - [checks-travis](https://travis-ci.com/rust-lang/rust/builds/115542062) Build commit: 330c85d9270b32d7703ebefc337eb37ae959f741
            <!-- homu: {"type":"TryBuildFailed","builders":{"checks-travis":"https://travis-ci.com/rust-lang/rust/builds/115542062"},"merge_sha":"330c85d9270b32d7703ebefc337eb37ae959f741"} -->
        ''', # noqa
        'publishedAt': '1985-04-21T00:01:00Z',
    }))

    assert result.changed is True
    assert state.try_ is True
    assert state.get_status() == 'failure'
    assert state.build_state == BuildState.NONE
    assert state.try_state == BuildState.FAILURE
    assert state.last_try.state == BuildState.FAILURE


@unittest.mock.patch('homu.pull_req_state.assert_authorized',
                     side_effect=return_true)
def test_try_timed_out(_):
    """
    Test that a pull request that has been tried shows up as tried
    """

    state = new_state(head_sha='065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe')
    result = state.process_event(create_event({
        'eventType': 'IssueComment',
        'author': {
            'login': 'bors',
        },
        'body': '''
            :hourglass: Trying commit 065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe with merge 330c85d9270b32d7703ebefc337eb37ae959f741...
            <!-- homu: {"type":"TryBuildStarted","head_sha":"065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe","merge_sha":"330c85d9270b32d7703ebefc337eb37ae959f741"} -->
        ''', # noqa
        'publishedAt': '1985-04-21T00:00:00Z',
    }))

    assert result.changed is True
    assert state.try_ is True
    assert state.get_status() == 'pending'
    assert state.build_state == BuildState.NONE
    assert state.try_state == BuildState.PENDING
    assert state.last_try.state == BuildState.PENDING

    result = state.process_event(create_event({
        'eventType': 'IssueComment',
        'author': {
            'login': 'bors',
        },
        'body': '''
            :boom: Test timed out
            <!-- homu: {"type":"TimedOut"} -->
        ''', # noqa
        'publishedAt': '1985-04-21T00:01:00Z',
    }))

    assert result.changed is True
    assert state.try_ is True
    assert state.get_status() == 'failure'
    assert state.build_state == BuildState.NONE
    assert state.try_state == BuildState.FAILURE
    assert state.last_try.state == BuildState.TIMEDOUT


@unittest.mock.patch('homu.pull_req_state.assert_authorized',
                     side_effect=return_true)
def test_try_reset_by_push(_):
    """
    Test that a pull request that has been tried, and new commits pushed, does
    not show up as tried
    """

    state = new_state(head_sha="065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe")
    result = state.process_event(create_event({
        'eventType': 'IssueComment',
        'author': {
            'login': 'bors',
        },
        'body': '''
            :hourglass: Trying commit 065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe with merge 330c85d9270b32d7703ebefc337eb37ae959f741...
            <!-- homu: {"type":"TryBuildStarted","head_sha":"065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe","merge_sha":"330c85d9270b32d7703ebefc337eb37ae959f741"} -->
        ''', # noqa
        'publishedAt': '1985-04-21T00:00:00Z',
    }))

    assert result.changed is True
    assert state.try_ is True
    assert state.get_status() == 'pending'
    assert state.try_state == BuildState.PENDING
    assert state.last_try.state == BuildState.PENDING

    result = state.process_event(create_event({
        'eventType': 'IssueComment',
        'author': {
            'login': 'bors',
        },
        'body': '''
            :sunny: Try build successful - [checks-travis](https://travis-ci.com/rust-lang/rust/builds/115542062) Build commit: 330c85d9270b32d7703ebefc337eb37ae959f741
            <!-- homu: {"type":"TryBuildCompleted","builders":{"checks-travis":"https://travis-ci.com/rust-lang/rust/builds/115542062"},"merge_sha":"330c85d9270b32d7703ebefc337eb37ae959f741"} -->
        ''', # noqa
        'publishedAt': '1985-04-21T00:01:00Z',
    }))

    assert result.changed is True
    assert state.try_ is True
    assert state.get_status() == 'success'
    assert state.try_state == BuildState.SUCCESS
    assert state.last_try.state == BuildState.SUCCESS

    result = state.process_event(create_event({
        'eventType': 'PullRequestCommit',
        'commit': {
            'oid': '012345',
        }
    }))

    assert result.changed is True
    assert state.try_ is False
    assert state.get_status() == ''
    assert state.try_state == BuildState.NONE
    assert state.last_try.state == BuildState.SUCCESS


@unittest.mock.patch('homu.pull_req_state.assert_authorized',
                     side_effect=return_true)
def test_build(_):
    """
    Test that a pull request that has been built shows up as built. This is
    maybe a bad test because a PR that has been built and succeeds will likely
    be merged and removed.
    """

    state = new_state(head_sha='065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe')
    state.process_event(create_event({
        'eventType': 'IssueComment',
        'author': {
            'login': 'ferris',
        },
        'body': '@bors r+',
        'publishedAt': '1985-04-21T00:00:00Z',
    }))
    assert state.get_status() == 'approved'
    assert state.approval_state == ApprovalState.APPROVED
    assert state.build_state == BuildState.NONE
    assert state.try_state == BuildState.NONE

    result = state.process_event(create_event({
        'eventType': 'IssueComment',
        'author': {
            'login': 'bors',
        },
        'body': '''
            :hourglass: Building commit 065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe with merge 330c85d9270b32d7703ebefc337eb37ae959f741...
            <!-- homu: {"type":"BuildStarted","head_sha":"065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe","merge_sha":"330c85d9270b32d7703ebefc337eb37ae959f741"} -->
        ''', # noqa
        'publishedAt': '1985-04-21T00:00:00Z',
    }))

    assert result.changed is True
    assert state.try_ is False
    assert state.get_status() == 'pending'
    assert state.build_state == BuildState.PENDING
    assert state.try_state == BuildState.NONE
    assert state.last_build.state == BuildState.PENDING

    result = state.process_event(create_event({
        'eventType': 'IssueComment',
        'author': {
            'login': 'bors',
        },
        'body': '''
            :sunny: Build successful - [checks-travis](https://travis-ci.com/rust-lang/rust/builds/115542062) Build commit: 330c85d9270b32d7703ebefc337eb37ae959f741
            <!-- homu: {"type":"BuildCompleted","builders":{"checks-travis":"https://travis-ci.com/rust-lang/rust/builds/115542062"},"merge_sha":"330c85d9270b32d7703ebefc337eb37ae959f741"} -->
        ''', # noqa
        'publishedAt': '1985-04-21T00:01:00Z',
    }))

    assert result.changed is True
    assert state.try_ is False
    assert state.get_status() == 'completed'
    assert state.build_state == BuildState.SUCCESS
    assert state.try_state == BuildState.NONE
    assert state.last_build.state == BuildState.SUCCESS


@unittest.mock.patch('homu.pull_req_state.assert_authorized',
                     side_effect=return_true)
def test_build_failed(_):
    """
    Test that a pull request that has been built and failed shows up that way.
    """

    state = new_state(head_sha='065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe')
    state.process_event(create_event({
        'eventType': 'IssueComment',
        'author': {
            'login': 'ferris',
        },
        'body': '@bors r+',
        'publishedAt': '1985-04-21T00:00:00Z',
    }))
    assert state.get_status() == 'approved'
    assert state.approval_state == ApprovalState.APPROVED
    assert state.build_state == BuildState.NONE
    assert state.try_state == BuildState.NONE

    result = state.process_event(create_event({
        'eventType': 'IssueComment',
        'author': {
            'login': 'bors',
        },
        'body': '''
            :hourglass: Building commit 065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe with merge 330c85d9270b32d7703ebefc337eb37ae959f741...
            <!-- homu: {"type":"BuildStarted","head_sha":"065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe","merge_sha":"330c85d9270b32d7703ebefc337eb37ae959f741"} -->
        ''', # noqa
        'publishedAt': '1985-04-21T00:00:00Z',
    }))

    assert result.changed is True
    assert state.try_ is False
    assert state.get_status() == 'pending'
    assert state.build_state == BuildState.PENDING
    assert state.try_state == BuildState.NONE
    assert state.last_build.state == BuildState.PENDING

    result = state.process_event(create_event({
        'eventType': 'IssueComment',
        'author': {
            'login': 'bors',
        },
        'body': '''
            :sunny: Build failed - [checks-travis](https://travis-ci.com/rust-lang/rust/builds/115542062) Build commit: 330c85d9270b32d7703ebefc337eb37ae959f741
            <!-- homu: {"type":"BuildFailed","builders":{"checks-travis":"https://travis-ci.com/rust-lang/rust/builds/115542062"},"merge_sha":"330c85d9270b32d7703ebefc337eb37ae959f741"} -->
        ''', # noqa
        'publishedAt': '1985-04-21T00:01:00Z',
    }))

    assert result.changed is True
    assert state.try_ is False
    assert state.get_status() == 'failure'
    assert state.build_state == BuildState.FAILURE
    assert state.try_state == BuildState.NONE
    assert state.last_build.state == BuildState.FAILURE


@unittest.mock.patch('homu.pull_req_state.assert_authorized',
                     side_effect=return_true)
def test_build_retry_cancels(_):
    """
    Test that a pull request that has started a build and then gets a 'retry'
    command cancels the build.
    """

    state = new_state(head_sha='065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe')
    result = state.process_event(create_event({
        'eventType': 'IssueComment',
        'author': {
            'login': 'bors',
        },
        'body': '''
            :hourglass: Building commit 065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe with merge 330c85d9270b32d7703ebefc337eb37ae959f741...
            <!-- homu: {"type":"BuildStarted","head_sha":"065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe","merge_sha":"330c85d9270b32d7703ebefc337eb37ae959f741"} -->
        ''', # noqa
        'publishedAt': '1985-04-21T00:00:00Z',
    }))

    assert result.changed is True
    assert state.try_ is False
    assert state.get_status() == 'pending'
    assert state.build_state == BuildState.PENDING
    assert state.try_state == BuildState.NONE
    assert state.last_build.state == BuildState.PENDING

    result = state.process_event(create_event({
        'eventType': 'IssueComment',
        'author': {
            'login': 'ferris',
        },
        'body': '''
            @bors retry
        ''',
        'publishedAt': '1985-04-21T00:01:00Z',
    }))

    assert result.changed is True
    assert state.try_ is False
    assert state.get_status() == ''
    assert state.build_state == BuildState.NONE
    assert state.try_state == BuildState.NONE
    assert state.last_build.state == BuildState.CANCELLED
    # TODO: does issuing this retry emit label events?


@unittest.mock.patch('homu.pull_req_state.assert_authorized',
                     side_effect=return_true)
def test_tried_and_approved(_):
    """
    Test that a pull request that has been approved AND tried shows up as
    approved AND tried
    """

    state = new_state(head_sha='065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe')
    result = state.process_event(create_event({
        'eventType': 'IssueComment',
        'author': {
            'login': 'bors',
        },
        'body': '''
            :hourglass: Trying commit 065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe with merge 330c85d9270b32d7703ebefc337eb37ae959f741...
            <!-- homu: {"type":"TryBuildStarted","head_sha":"065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe","merge_sha":"330c85d9270b32d7703ebefc337eb37ae959f741"} -->
        ''', # noqa
        'publishedAt': '1985-04-21T00:00:00Z',
    }))

    assert result.changed is True
    assert state.try_ is True
    assert state.get_status() == 'pending'
    assert state.build_state == BuildState.NONE
    assert state.try_state == BuildState.PENDING
    assert state.last_try.state == BuildState.PENDING

    result = state.process_event(create_event({
        'eventType': 'IssueComment',
        'author': {
            'login': 'bors',
        },
        'body': '''
            :sunny: Try build successful - [checks-travis](https://travis-ci.com/rust-lang/rust/builds/115542062) Build commit: 330c85d9270b32d7703ebefc337eb37ae959f741
            <!-- homu: {"type":"TryBuildCompleted","builders":{"checks-travis":"https://travis-ci.com/rust-lang/rust/builds/115542062"},"merge_sha":"330c85d9270b32d7703ebefc337eb37ae959f741"} -->
        ''', # noqa
        'publishedAt': '1985-04-21T00:01:00Z',
    }))

    assert result.changed is True
    assert state.try_ is True
    assert state.get_status() == 'success'
    assert state.build_state == BuildState.NONE
    assert state.try_state == BuildState.SUCCESS
    assert state.last_try.state == BuildState.SUCCESS

    state.process_event(create_event({
        'eventType': 'IssueComment',
        'author': {
            'login': 'ferris',
        },
        'body': '@bors r+',
        'publishedAt': '1985-04-21T00:00:00Z',
    }))
    assert state.get_status() == 'approved'
    assert state.approval_state == ApprovalState.APPROVED
    assert state.build_state == BuildState.NONE
    assert state.try_state == BuildState.SUCCESS
    assert state.last_try.state == BuildState.SUCCESS


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
    assert state.approval_state == ApprovalState.APPROVED

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
    assert state.approval_state == ApprovalState.UNAPPROVED
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
    assert state.approval_state == ApprovalState.APPROVED
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
    assert state.approval_state == ApprovalState.UNAPPROVED


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
    assert state.approval_state == ApprovalState.APPROVED
    state.process_event(create_event({
        'eventType': 'BaseRefChangedEvent',
        'actor': {
            'login': 'ferris',
        },
    }))

    assert state.get_status() == ''
    assert state.approval_state == ApprovalState.UNAPPROVED


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
