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


def return_true(a, b, c, d, e, f):
    return True


@unittest.mock.patch('homu.pull_req_state.assert_authorized',
                     side_effect=return_true)
def test_tried_multiple_times(_):
    """
    Test that a pull request that has been tried multiple times has a history
    """

    state = new_state()
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
    assert len(state.try_history) == 1
    assert state.try_history[0].head_sha == "065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe" # noqa
    assert state.try_history[0].merge_sha == "330c85d9270b32d7703ebefc337eb37ae959f741" # noqa
    assert state.try_history[0].state == BuildState.PENDING

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
    assert len(state.try_history) == 1
    assert state.try_history[0].head_sha == "065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe" # noqa
    assert state.try_history[0].merge_sha == "330c85d9270b32d7703ebefc337eb37ae959f741" # noqa
    assert state.try_history[0].state == BuildState.SUCCESS

    result = state.process_event(create_event({
        'eventType': 'IssueComment',
        'author': {
            'login': 'bors',
        },
        'body': '''
            :hourglass: Trying commit 065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe with merge 330c85d9270b32d7703ebefc337eb37ae959f741...
            <!-- homu: {"type":"TryBuildStarted","head_sha":"065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe","merge_sha":"dba7673010f19a94af4345453005933fd511bea9"} -->
        ''', # noqa
        'publishedAt': '1985-04-21T00:02:00Z',
    }))

    assert result.changed is True
    assert state.try_ is True
    assert state.get_status() == 'pending'
    assert state.build_state == BuildState.NONE
    assert state.try_state == BuildState.PENDING
    assert len(state.try_history) == 2
    assert state.try_history[0].head_sha == "065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe" # noqa
    assert state.try_history[0].merge_sha == "330c85d9270b32d7703ebefc337eb37ae959f741" # noqa
    assert state.try_history[0].state == BuildState.SUCCESS
    assert state.try_history[1].head_sha == "065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe" # noqa
    assert state.try_history[1].merge_sha == "dba7673010f19a94af4345453005933fd511bea9" # noqa
    assert state.try_history[1].state == BuildState.PENDING

    result = state.process_event(create_event({
        'eventType': 'IssueComment',
        'author': {
            'login': 'bors',
        },
        'body': '''
            :sunny: Try build successful - [checks-travis](https://travis-ci.com/rust-lang/rust/builds/115542062) Build commit: 330c85d9270b32d7703ebefc337eb37ae959f741
            <!-- homu: {"type":"TryBuildCompleted","builders":{"checks-travis":"https://travis-ci.com/rust-lang/rust/builds/115542062"},"merge_sha":"dba7673010f19a94af4345453005933fd511bea9"} -->
        ''', # noqa
        'publishedAt': '1985-04-21T00:03:00Z',
    }))

    assert result.changed is True
    assert state.try_ is True
    assert state.get_status() == 'success'
    assert state.build_state == BuildState.NONE
    assert state.try_state == BuildState.SUCCESS
    assert len(state.try_history) == 2
    assert state.try_history[0].head_sha == "065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe" # noqa
    assert state.try_history[0].merge_sha == "330c85d9270b32d7703ebefc337eb37ae959f741" # noqa
    assert state.try_history[0].state == BuildState.SUCCESS
    assert state.try_history[1].head_sha == "065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe" # noqa
    assert state.try_history[1].merge_sha == "dba7673010f19a94af4345453005933fd511bea9" # noqa
    assert state.try_history[1].state == BuildState.SUCCESS

@unittest.mock.patch('homu.pull_req_state.assert_authorized',
                     side_effect=return_true)
def test_tried_multiple_times_failed_then_succeeded(_):
    """
    Test that a pull request that has been tried multiple times has a history
    """

    state = new_state()
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
    assert len(state.try_history) == 1
    assert state.try_history[0].head_sha == "065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe" # noqa
    assert state.try_history[0].merge_sha == "330c85d9270b32d7703ebefc337eb37ae959f741" # noqa
    assert state.try_history[0].state == BuildState.PENDING

    result = state.process_event(create_event({
        'eventType': 'IssueComment',
        'author': {
            'login': 'bors',
        },
        'body': '''
            :sunny: Try build successful - [checks-travis](https://travis-ci.com/rust-lang/rust/builds/115542062) Build commit: 330c85d9270b32d7703ebefc337eb37ae959f741
            <!-- homu: {"type":"TryBuildFailed","builder_name":"checks-travis","builder_url":"https://travis-ci.com/rust-lang/rust/builds/115542062"} -->
        ''', # noqa
        'publishedAt': '1985-04-21T00:01:00Z',
    }))

    assert result.changed is True
    assert state.try_ is True
    assert state.get_status() == 'failure'
    assert state.build_state == BuildState.NONE
    assert state.try_state == BuildState.FAILURE
    assert len(state.try_history) == 1
    assert state.try_history[0].head_sha == "065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe" # noqa
    assert state.try_history[0].merge_sha == "330c85d9270b32d7703ebefc337eb37ae959f741" # noqa
    assert state.try_history[0].state == BuildState.FAILURE

    result = state.process_event(create_event({
        'eventType': 'IssueComment',
        'author': {
            'login': 'bors',
        },
        'body': '''
            :hourglass: Trying commit 065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe with merge 330c85d9270b32d7703ebefc337eb37ae959f741...
            <!-- homu: {"type":"TryBuildStarted","head_sha":"065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe","merge_sha":"dba7673010f19a94af4345453005933fd511bea9"} -->
        ''', # noqa
        'publishedAt': '1985-04-21T00:02:00Z',
    }))

    assert result.changed is True
    assert state.try_ is True
    assert state.get_status() == 'pending'
    assert state.build_state == BuildState.NONE
    assert state.try_state == BuildState.PENDING
    assert len(state.try_history) == 2
    assert state.try_history[0].head_sha == "065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe" # noqa
    assert state.try_history[0].merge_sha == "330c85d9270b32d7703ebefc337eb37ae959f741" # noqa
    assert state.try_history[0].state == BuildState.FAILURE
    assert state.try_history[1].head_sha == "065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe" # noqa
    assert state.try_history[1].merge_sha == "dba7673010f19a94af4345453005933fd511bea9" # noqa
    assert state.try_history[1].state == BuildState.PENDING

    result = state.process_event(create_event({
        'eventType': 'IssueComment',
        'author': {
            'login': 'bors',
        },
        'body': '''
            :sunny: Try build successful - [checks-travis](https://travis-ci.com/rust-lang/rust/builds/115542062) Build commit: 330c85d9270b32d7703ebefc337eb37ae959f741
            <!-- homu: {"type":"TryBuildCompleted","builders":{"checks-travis":"https://travis-ci.com/rust-lang/rust/builds/115542062"},"merge_sha":"dba7673010f19a94af4345453005933fd511bea9"} -->
        ''', # noqa
        'publishedAt': '1985-04-21T00:03:00Z',
    }))

    assert result.changed is True
    assert state.try_ is True
    assert state.get_status() == 'success'
    assert state.build_state == BuildState.NONE
    assert state.try_state == BuildState.SUCCESS
    assert len(state.try_history) == 2
    assert state.try_history[0].head_sha == "065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe" # noqa
    assert state.try_history[0].merge_sha == "330c85d9270b32d7703ebefc337eb37ae959f741" # noqa
    assert state.try_history[0].state == BuildState.FAILURE
    assert state.try_history[1].head_sha == "065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe" # noqa
    assert state.try_history[1].merge_sha == "dba7673010f19a94af4345453005933fd511bea9" # noqa
    assert state.try_history[1].state == BuildState.SUCCESS


@unittest.mock.patch('homu.pull_req_state.assert_authorized',
                     side_effect=return_true)
def test_tried_multiple_times_failed_then_succeeded(_):
    """
    Test that a pull request that has been tried shows up as tried
    """

    state = new_state()
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
    assert len(state.try_history) == 1
    assert state.try_history[0].head_sha == "065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe" # noqa
    assert state.try_history[0].merge_sha == "330c85d9270b32d7703ebefc337eb37ae959f741" # noqa
    assert state.try_history[0].state == BuildState.PENDING

    result = state.process_event(create_event({
        'eventType': 'IssueComment',
        'author': {
            'login': 'bors',
        },
        'body': '''
            :sunny: Try build successful - [checks-travis](https://travis-ci.com/rust-lang/rust/builds/115542062) Build commit: 330c85d9270b32d7703ebefc337eb37ae959f741
            <!-- homu: {"type":"TryBuildFailed","builder_name":"checks-travis","builder_url":"https://travis-ci.com/rust-lang/rust/builds/115542062"} -->
        ''', # noqa
        'publishedAt': '1985-04-21T00:01:00Z',
    }))

    assert result.changed is True
    assert state.try_ is True
    assert state.get_status() == 'failure'
    assert state.build_state == BuildState.NONE
    assert state.try_state == BuildState.FAILURE
    assert len(state.try_history) == 1
    assert state.try_history[0].head_sha == "065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe" # noqa
    assert state.try_history[0].merge_sha == "330c85d9270b32d7703ebefc337eb37ae959f741" # noqa
    assert state.try_history[0].state == BuildState.FAILURE

    result = state.process_event(create_event({
        'eventType': 'IssueComment',
        'author': {
            'login': 'bors',
        },
        'body': '''
            :hourglass: Trying commit 065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe with merge 330c85d9270b32d7703ebefc337eb37ae959f741...
            <!-- homu: {"type":"TryBuildStarted","head_sha":"065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe","merge_sha":"dba7673010f19a94af4345453005933fd511bea9"} -->
        ''', # noqa
        'publishedAt': '1985-04-21T00:02:00Z',
    }))

    assert result.changed is True
    assert state.try_ is True
    assert state.get_status() == 'pending'
    assert state.build_state == BuildState.NONE
    assert state.try_state == BuildState.PENDING
    assert len(state.try_history) == 2
    assert state.try_history[0].head_sha == "065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe" # noqa
    assert state.try_history[0].merge_sha == "330c85d9270b32d7703ebefc337eb37ae959f741" # noqa
    assert state.try_history[0].state == BuildState.FAILURE
    assert state.try_history[1].head_sha == "065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe" # noqa
    assert state.try_history[1].merge_sha == "dba7673010f19a94af4345453005933fd511bea9" # noqa
    assert state.try_history[1].state == BuildState.PENDING

    result = state.process_event(create_event({
        'eventType': 'IssueComment',
        'author': {
            'login': 'bors',
        },
        'body': '''
            :sunny: Try build successful - [checks-travis](https://travis-ci.com/rust-lang/rust/builds/115542062) Build commit: 330c85d9270b32d7703ebefc337eb37ae959f741
            <!-- homu: {"type":"TryBuildCompleted","builders":{"checks-travis":"https://travis-ci.com/rust-lang/rust/builds/115542062"},"merge_sha":"dba7673010f19a94af4345453005933fd511bea9"} -->
        ''', # noqa
        'publishedAt': '1985-04-21T00:03:00Z',
    }))

    assert result.changed is True
    assert state.try_ is True
    assert state.get_status() == 'success'
    assert state.build_state == BuildState.NONE
    assert state.try_state == BuildState.SUCCESS
    assert len(state.try_history) == 2
    assert state.try_history[0].head_sha == "065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe" # noqa
    assert state.try_history[0].merge_sha == "330c85d9270b32d7703ebefc337eb37ae959f741" # noqa
    assert state.try_history[0].state == BuildState.FAILURE
    assert state.try_history[1].head_sha == "065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe" # noqa
    assert state.try_history[1].merge_sha == "dba7673010f19a94af4345453005933fd511bea9" # noqa
    assert state.try_history[1].state == BuildState.SUCCESS

@unittest.mock.patch('homu.pull_req_state.assert_authorized',
                     side_effect=return_true)
def test_built_multiple_times(_):
    """
    Test that a pull request that has been built multiple times has a history
    """

    state = new_state()
    result = state.process_event(create_event({
        'eventType': 'IssueComment',
        'author': {
            'login': 'bors',
        },
        'body': '''
            :hourglass: Trying commit 065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe with merge 330c85d9270b32d7703ebefc337eb37ae959f741...
            <!-- homu: {"type":"BuildStarted","head_sha":"065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe","merge_sha":"330c85d9270b32d7703ebefc337eb37ae959f741"} -->
        ''', # noqa
        'publishedAt': '1985-04-21T00:00:00Z',
    }))

    assert result.changed is True
    assert state.try_ is False
    assert state.get_status() == 'pending'
    assert state.build_state == BuildState.PENDING
    assert state.try_state == BuildState.NONE
    assert len(state.build_history) == 1
    assert state.build_history[0].head_sha == "065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe" # noqa
    assert state.build_history[0].merge_sha == "330c85d9270b32d7703ebefc337eb37ae959f741" # noqa
    assert state.build_history[0].state == BuildState.PENDING

    result = state.process_event(create_event({
        'eventType': 'IssueComment',
        'author': {
            'login': 'bors',
        },
        'body': '''
            :sunny: Try build successful - [checks-travis](https://travis-ci.com/rust-lang/rust/builds/115542062) Build commit: 330c85d9270b32d7703ebefc337eb37ae959f741
            <!-- homu: {"type":"BuildFailed","builder_name":"checks-travis","builder_url":"https://travis-ci.com/rust-lang/rust/builds/115542062"} -->
        ''', # noqa
        'publishedAt': '1985-04-21T00:01:00Z',
    }))

    assert result.changed is True
    assert state.try_ is False
    assert state.get_status() == 'failure'
    assert state.build_state == BuildState.FAILURE
    assert state.try_state == BuildState.NONE
    assert len(state.build_history) == 1
    assert state.build_history[0].head_sha == "065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe" # noqa
    assert state.build_history[0].merge_sha == "330c85d9270b32d7703ebefc337eb37ae959f741" # noqa
    assert state.build_history[0].state == BuildState.FAILURE

    result = state.process_event(create_event({
        'eventType': 'IssueComment',
        'author': {
            'login': 'bors',
        },
        'body': '''
            :hourglass: Trying commit 065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe with merge 330c85d9270b32d7703ebefc337eb37ae959f741...
            <!-- homu: {"type":"BuildStarted","head_sha":"065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe","merge_sha":"dba7673010f19a94af4345453005933fd511bea9"} -->
        ''', # noqa
        'publishedAt': '1985-04-21T00:02:00Z',
    }))

    assert result.changed is True
    assert state.try_ is False
    assert state.get_status() == 'pending'
    assert state.build_state == BuildState.PENDING
    assert state.try_state == BuildState.NONE
    assert len(state.build_history) == 2
    assert state.build_history[0].head_sha == "065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe" # noqa
    assert state.build_history[0].merge_sha == "330c85d9270b32d7703ebefc337eb37ae959f741" # noqa
    assert state.build_history[0].state == BuildState.FAILURE
    assert state.build_history[1].head_sha == "065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe" # noqa
    assert state.build_history[1].merge_sha == "dba7673010f19a94af4345453005933fd511bea9" # noqa
    assert state.build_history[1].state == BuildState.PENDING

    result = state.process_event(create_event({
        'eventType': 'IssueComment',
        'author': {
            'login': 'bors',
        },
        'body': '''
            :sunny: Try build successful - [checks-travis](https://travis-ci.com/rust-lang/rust/builds/115542062) Build commit: 330c85d9270b32d7703ebefc337eb37ae959f741
            <!-- homu: {"type":"BuildFailed","builder_name":"checks-travis","builder_url":"https://travis-ci.com/rust-lang/rust/builds/115542062"} -->
        ''', # noqa
        'publishedAt': '1985-04-21T00:03:00Z',
    }))

    assert result.changed is True
    assert state.try_ is False
    assert state.get_status() == 'failure'
    assert state.build_state == BuildState.FAILURE
    assert state.try_state == BuildState.NONE
    assert len(state.build_history) == 2
    assert state.build_history[0].head_sha == "065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe" # noqa
    assert state.build_history[0].merge_sha == "330c85d9270b32d7703ebefc337eb37ae959f741" # noqa
    assert state.build_history[0].state == BuildState.FAILURE
    assert state.build_history[1].head_sha == "065151f8b2c31d9e4ddd34aaf8d3263a997f5cfe" # noqa
    assert state.build_history[1].merge_sha == "dba7673010f19a94af4345453005933fd511bea9" # noqa
    assert state.build_history[1].state == BuildState.FAILURE
