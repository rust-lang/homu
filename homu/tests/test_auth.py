import pytest
import re
import unittest.mock
import httpretty
from homu.auth import (
    AuthorizationException,
    AuthState,
    assert_authorized,
)


class TestBasic:
    """
    Test basic authorization using `reviewers` and `try_users`
    """

    def test_reviewers(self):
        state = unittest.mock.Mock()
        state.delegate = 'david'
        auth = AuthState.REVIEWER
        repo_configuration = {
            'reviewers': ['alice'],
            'try_users': ['bob'],
        }

        # The bot is successful
        result = assert_authorized(
                'bors',
                'test',
                repo_configuration,
                state,
                auth,
                'bors')

        assert result is True

        # A reviewer is successful
        result = assert_authorized(
                'alice',
                'test',
                repo_configuration,
                state,
                auth,
                'bors')

        assert result is True

        # A try user gets rejected
        with pytest.raises(AuthorizationException) as context:
            assert_authorized(
                'bob',
                'test',
                repo_configuration,
                state,
                auth,
                'bors')

        assert re.search(
                r'Insufficient privileges.*reviewer',
                str(context.value.comment)) is not None

        # An unauthorized user gets rejected
        with pytest.raises(AuthorizationException) as context:
            assert_authorized(
                'eve',
                'test',
                repo_configuration,
                state,
                auth,
                'bors')

        assert re.search(
                r'Insufficient privileges.*reviewers',
                str(context.value.comment)) is not None

        # The delegated user is successful
        result = assert_authorized(
                'david',
                'test',
                repo_configuration,
                state,
                auth,
                'bors')

        assert result is True

    def test_try(self):
        state = unittest.mock.Mock()
        state.delegate = 'david'
        auth = AuthState.TRY
        repo_configuration = {
            'reviewers': ['alice'],
            'try_users': ['bob'],
        }

        # The bot is successful
        result = assert_authorized(
                'bors',
                'test',
                repo_configuration,
                state,
                auth,
                'bors')

        assert result is True

        # A reviewer is successful
        result = assert_authorized(
                'alice',
                'test',
                repo_configuration,
                state,
                auth,
                'bors')

        assert result is True

        # A try user is successful
        result = assert_authorized(
                'bob',
                'test',
                repo_configuration,
                state,
                auth,
                'bors')

        assert result is True

        # An unauthorized user gets rejected
        with pytest.raises(AuthorizationException) as context:
            assert_authorized(
                'eve',
                'test',
                repo_configuration,
                state,
                auth,
                'bors')

        assert re.search(
                r'Insufficient privileges.*try users',
                str(context.value.comment)) is not None

        # The delegated user is successful
        result = assert_authorized(
                'david',
                'test',
                repo_configuration,
                state,
                auth,
                'bors')

        assert result is True


class TestCollaborator:
    """
    Test situations when auth_collaborators is set
    """

    def test_reviewers(self):
        repo = unittest.mock.Mock()
        repo.is_collaborator = unittest.mock.Mock()
        repo.is_collaborator.return_value = False

        state = unittest.mock.Mock()
        state.delegate = 'david'
        state.get_repo = unittest.mock.Mock(return_value=repo)

        auth = AuthState.REVIEWER
        repo_configuration = {
            'auth_collaborators': True,
            'reviewers': [],
            'try_users': [],
        }

        # The bot is successful
        repo.is_collaborator.return_value = False
        result = assert_authorized(
                'bors',
                'test',
                repo_configuration,
                state,
                auth,
                'bors')

        assert result is True

        # A collaborator is successful
        repo.is_collaborator.return_value = True
        result = assert_authorized(
                'alice',
                'test',
                repo_configuration,
                state,
                auth,
                'bors')

        assert result is True

        # A non-collaborator is not successful
        repo.is_collaborator.return_value = False
        with pytest.raises(AuthorizationException) as context:
            assert_authorized(
                'bob',
                'test',
                repo_configuration,
                state,
                auth,
                'bors')

        assert re.search(
                r'Insufficient privileges.*Collaborator required',
                str(context.value.comment)) is not None

        # The delegated user is successful
        repo.is_collaborator.return_value = False
        result = assert_authorized(
                'david',
                'test',
                repo_configuration,
                state,
                auth,
                'bors')

        assert result is True

    def test_try(self):
        repo = unittest.mock.Mock()
        repo.is_collaborator = unittest.mock.Mock()
        repo.is_collaborator.return_value = False

        state = unittest.mock.Mock()
        state.delegate = 'david'
        state.get_repo = unittest.mock.Mock(return_value=repo)

        auth = AuthState.TRY
        repo_configuration = {
            'auth_collaborators': True,
            'reviewers': [],
            'try_users': [],
        }

        # The bot is successful
        repo.is_collaborator.return_value = False
        result = assert_authorized(
                'bors',
                'test',
                repo_configuration,
                state,
                auth,
                'bors')

        assert result is True

        # A collaborator is successful
        repo.is_collaborator.return_value = True
        result = assert_authorized(
                'alice',
                'test',
                repo_configuration,
                state,
                auth,
                'bors')

        assert result is True

        # A non-collaborator is not successful
        repo.is_collaborator.return_value = False
        with pytest.raises(AuthorizationException) as context:
            assert_authorized(
                'bob',
                'test',
                repo_configuration,
                state,
                auth,
                'bors')

        assert re.search(
                r'Insufficient privileges.*try users',
                str(context.value.comment)) is not None

        # The delegated user is successful
        repo.is_collaborator.return_value = False
        result = assert_authorized(
                'david',
                'test',
                repo_configuration,
                state,
                auth,
                'bors')

        assert result is True


class TestAuthRustTeam:
    """
    Test situations where rust_team authorization is set
    """

    @httpretty.activate
    def test_reviewers(self):
        httpretty.register_uri(
            httpretty.GET,
            'https://team-api.infra.rust-lang.org/v1/permissions/bors.test.review.json', # noqa
            body="""
            {
                "github_users": [
                    "alice"
                ]
            }
            """)

        state = unittest.mock.Mock()
        state.delegate = 'david'
        auth = AuthState.REVIEWER
        repo_configuration = {
            'rust_team': True,
            'reviewers': ['alice'],
            'try_users': ['bob'],
        }

        # The bot is successful
        result = assert_authorized(
                'bors',
                'test',
                repo_configuration,
                state,
                auth,
                'bors')

        assert result is True

        # A reviewer is successful
        result = assert_authorized(
                'alice',
                'test',
                repo_configuration,
                state,
                auth,
                'bors')

        assert result is True

        # A try user gets rejected
        with pytest.raises(AuthorizationException) as context:
            assert_authorized(
                'bob',
                'test',
                repo_configuration,
                state,
                auth,
                'bors')

        assert re.search(
                r'Insufficient privileges.*reviewer',
                str(context.value.comment)) is not None

        # An unauthorized user gets rejected
        with pytest.raises(AuthorizationException) as context:
            assert_authorized(
                'eve',
                'test',
                repo_configuration,
                state,
                auth,
                'bors')

        assert re.search(
                r'Insufficient privileges.*reviewer',
                str(context.value.comment)) is not None

        # The delegated user is successful
        result = assert_authorized(
                'david',
                'test',
                repo_configuration,
                state,
                auth,
                'bors')

        assert result is True

    @httpretty.activate
    def test_try(self):
        httpretty.register_uri(
            httpretty.GET,
            'https://team-api.infra.rust-lang.org/v1/permissions/bors.test.try.json', # noqa
            body="""
            {
                "github_users": [
                    "alice",
                    "bob"
                ]
            }
            """)

        state = unittest.mock.Mock()
        state.delegate = 'david'
        auth = AuthState.TRY
        repo_configuration = {
            'rust_team': True,
            'reviewers': ['alice'],
            'try_users': ['bob'],
        }

        # The bot is successful
        result = assert_authorized(
                'bors',
                'test',
                repo_configuration,
                state,
                auth,
                'bors')

        assert result is True

        # A reviewer is successful
        result = assert_authorized(
                'alice',
                'test',
                repo_configuration,
                state,
                auth,
                'bors')

        assert result is True

        # A try user is successful
        result = assert_authorized(
                'bob',
                'test',
                repo_configuration,
                state,
                auth,
                'bors')

        assert result is True

        # An unauthorized user gets rejected
        with pytest.raises(AuthorizationException) as context:
            assert_authorized(
                'eve',
                'test',
                repo_configuration,
                state,
                auth,
                'bors')

        assert re.search(
                r'Insufficient privileges.*try users',
                str(context.value.comment)) is not None

        # The delegated user is successful
        result = assert_authorized(
                'david',
                'test',
                repo_configuration,
                state,
                auth,
                'bors')

        assert result is True
