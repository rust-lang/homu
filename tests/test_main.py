import unittest
from unittest.mock import patch, Mock, MagicMock, call
from homu.main import sha_or_blank, force, parse_commands, \
get_words

class TestMain(unittest.TestCase):

    def call_parse_commands(self, cfg={}, body='', username='user', repo_cfg={},
                            state=None, my_username='my_user', db=None,
                            states=[], realtime=False, sha=''):
        return parse_commands(cfg, body, username, repo_cfg, state, my_username, db,
                       states, realtime=realtime, sha=sha)

    def test_get_words_no_username(self):
        self.assertEqual(get_words("Hi, I'm a test message.", ''), [])

    def test_get_words_incorrect_username(self):
        self.assertEqual(get_words("@user I'm a message", 'username'), [])

    def test_get_words_correct_username(self):
        self.assertEqual(get_words("@user I'm a message", 'user'), ['@user', "I'm", 'a', 'message'])

    def test_sha_or_blank_return_sha(self):
        self.assertEqual(sha_or_blank('f5d42200481'), 'f5d42200481')

    def test_sha_or_blank_return_blank(self):
        self.assertEqual(sha_or_blank('f5d@12'), '')

    @patch('homu.main.get_words', return_value=["@bot", "are", "you", "still", "there?"])
    @patch('homu.main.verify_auth', return_value=True)
    @patch('homu.main.PullReqState')
    @patch('homu.action.still_here')
    def test_parse_commands_still_here_realtime(self, mock_still_here, MockPullReqState, mock_auth, mock_words):
        state = MockPullReqState()
        self.assertFalse(self.call_parse_commands(state=state, realtime=True))
        mock_still_here.assert_called_once_with(state)


    @patch('homu.main.get_words', return_value=["@bot", "are", "you", "still", "there?"])
    @patch('homu.main.verify_auth', return_value=True)
    @patch('homu.main.PullReqState')
    @patch('homu.action.still_here')
    def test_parse_commands_still_here_not_realtime(self, mock_still_here, MockPullReqState, mock_auth, mock_words):
        state = MockPullReqState()
        self.assertFalse(self.call_parse_commands(state=state))
        assert not mock_still_here.called, 'still_here was called and should never be.'


    @patch('homu.main.get_words', return_value=["r+"])
    @patch('homu.main.verify_auth', return_value=True)
    @patch('homu.main.PullReqState')
    @patch('homu.action.review_approved')
    def test_parse_commands_review_approved_verified(self, mock_review_approved, MockPullReqState, mock_auth, mock_words):
        state = MockPullReqState()
        self.assertTrue(self.call_parse_commands(state=state, sha='abc123'))
        mock_review_approved.assert_called_once_with(state, False, 'user', 'user', 'my_user', 'abc123', [])

    @patch('homu.main.get_words', return_value=["r+"])
    @patch('homu.main.verify_auth', return_value=False)
    @patch('homu.main.PullReqState')
    @patch('homu.action.review_approved')
    def test_parse_commands_review_approved_not_verified(self, mock_review_approved, MockPullReqState, mock_auth, mock_words):
        state = MockPullReqState()
        self.assertFalse(self.call_parse_commands(state=state, sha='abc123'))
        assert not mock_review_approved.called, 'mock_review_approved was called and should never be.'

    @patch('homu.main.get_words', return_value=["r=user2"])
    @patch('homu.main.verify_auth', return_value=True)
    @patch('homu.main.PullReqState')
    @patch('homu.action.review_approved')
    def test_parse_commands_review_approved_verified_different_approver(self, mock_review_approved, MockPullReqState, mock_auth, mock_words):
        state = MockPullReqState()
        self.assertTrue(self.call_parse_commands(state=state, sha='abc123'))
        mock_review_approved.assert_called_once_with(state, False, 'user2', 'user', 'my_user', 'abc123', [])

    @patch('homu.main.get_words', return_value=["r-"])
    @patch('homu.main.verify_auth', return_value=True)
    @patch('homu.main.PullReqState')
    @patch('homu.action.review_rejected')
    def test_parse_commands_review_rejected(self, mock_review_rejected, MockPullReqState, mock_auth, mock_words):
        state = MockPullReqState()
        self.assertTrue(self.call_parse_commands(state=state, sha='abc123'))
        mock_review_rejected.assert_called_once_with(state, False)

    @patch('homu.main.get_words', return_value=["p=1"])
    @patch('homu.main.verify_auth', return_value=True)
    @patch('homu.main.PullReqState')
    @patch('homu.action.set_priority')
    def test_parse_commands_set_priority(self, mock_set_priority, MockPullReqState, mock_auth, mock_words):
        state = MockPullReqState()
        self.assertTrue(self.call_parse_commands(state=state, sha='abc123'))
        mock_set_priority.assert_called_once_with(state, False, '1', {})

    @patch('homu.main.get_words', return_value=["delegate=user2"])
    @patch('homu.main.verify_auth', return_value=True)
    @patch('homu.main.PullReqState')
    @patch('homu.action.delegate_to')
    def test_parse_commands_delegate_to(self, mock_delegate_to, MockPullReqState, mock_auth, mock_words):
        state = MockPullReqState()
        self.assertTrue(self.call_parse_commands(state=state, sha='abc123'))
        mock_delegate_to.assert_called_once_with(state, False, 'user2')

    @patch('homu.main.get_words', return_value=["delegate-"])
    @patch('homu.main.verify_auth', return_value=True)
    @patch('homu.main.PullReqState')
    @patch('homu.action.delegate_negative')
    def test_parse_commands_delegate_negative(self, mock_delegate_negative, MockPullReqState, mock_auth, mock_words):
        state = MockPullReqState()
        self.assertTrue(self.call_parse_commands(state=state, sha='abc123'))
        mock_delegate_negative.assert_called_once_with(state)

    @patch('homu.main.get_words', return_value=["delegate+"])
    @patch('homu.main.verify_auth', return_value=True)
    @patch('homu.main.PullReqState')
    @patch('homu.action.delegate_positive')
    def test_parse_commands_delegate_positive(self, mock_delegate_positive, MockPullReqState, mock_auth, mock_words):
        state = MockPullReqState()
        state.num = 2
        state.get_repo().pull_request(state.num).user.login = 'delegate'
        self.assertTrue(self.call_parse_commands(state=state, sha='abc123'))
        mock_delegate_positive.assert_called_once_with(state, 'delegate', False)

    @patch('homu.main.get_words', return_value=["retry"])
    @patch('homu.main.verify_auth', return_value=True)
    @patch('homu.main.PullReqState')
    @patch('homu.action.retry')
    def test_parse_commands_retry_realtime(self, mock_retry, MockPullReqState, mock_auth, mock_words):
        state = MockPullReqState()
        self.assertTrue(self.call_parse_commands(state=state, realtime=True, sha='abc123'))
        mock_retry.assert_called_once_with(state)

    @patch('homu.main.get_words', return_value=["retry"])
    @patch('homu.main.verify_auth', return_value=True)
    @patch('homu.main.PullReqState')
    @patch('homu.action.retry')
    def test_parse_commands_retry_not_realtime(self, mock_retry, MockPullReqState, mock_auth, mock_words):
        state = MockPullReqState()
        self.assertFalse(self.call_parse_commands(state=state, sha='abc123'))
        assert not mock_retry.called, 'retry was called and should never be.'

    @patch('homu.main.get_words', return_value=["try"])
    @patch('homu.main.verify_auth', return_value=True)
    @patch('homu.main.PullReqState')
    @patch('homu.action._try')
    def test_parse_commands_try_realtime(self, mock_try, MockPullReqState, mock_auth, mock_words):
        state = MockPullReqState()
        self.assertTrue(self.call_parse_commands(state=state, realtime=True, sha='abc123'))
        mock_try.assert_called_once_with(state, 'try')

    @patch('homu.main.get_words', return_value=["try"])
    @patch('homu.main.verify_auth', return_value=True)
    @patch('homu.main.PullReqState')
    @patch('homu.action._try')
    def test_parse_commands_try_not_realtime(self, mock_try, MockPullReqState, mock_auth, mock_words):
        state = MockPullReqState()
        self.assertFalse(self.call_parse_commands(state=state, sha='abc123'))
        assert not mock_try.called, '_try was called and should never be.'

    @patch('homu.main.get_words', return_value=["rollup"])
    @patch('homu.main.verify_auth', return_value=True)
    @patch('homu.main.PullReqState')
    @patch('homu.action.rollup')
    def test_parse_commands_rollup(self, mock_rollup, MockPullReqState, mock_auth, mock_words):
        state = MockPullReqState()
        self.assertTrue(self.call_parse_commands(state=state, realtime=True, sha='abc123'))
        mock_rollup.assert_called_once_with(state, 'rollup')

    @patch('homu.main.get_words', return_value=["clean"])
    @patch('homu.main.verify_auth', return_value=True)
    @patch('homu.main.PullReqState')
    @patch('homu.action.clean')
    def test_parse_commands_clean_realtime(self, mock_clean, MockPullReqState, mock_auth, mock_words):
        state = MockPullReqState()
        self.assertTrue(self.call_parse_commands(state=state, realtime=True, sha='abc123'))
        mock_clean.assert_called_once_with(state)

    @patch('homu.main.get_words', return_value=["clean"])
    @patch('homu.main.verify_auth', return_value=True)
    @patch('homu.main.PullReqState')
    @patch('homu.action.clean')
    def test_parse_commands_clean_not_realtime(self, mock_clean, MockPullReqState, mock_auth, mock_words):
        state = MockPullReqState()
        self.assertFalse(self.call_parse_commands(state=state, sha='abc123'))
        assert not mock_clean.called, 'clean was called and should never be.'

    @patch('homu.main.get_words', return_value=["hello?"])
    @patch('homu.main.verify_auth', return_value=True)
    @patch('homu.main.PullReqState')
    @patch('homu.action.hello_or_ping')
    def test_parse_commands_hello_or_ping_realtime(self, mock_hello_or_ping, MockPullReqState, mock_auth, mock_words):
        state = MockPullReqState()
        self.assertTrue(self.call_parse_commands(state=state, realtime=True, sha='abc123'))
        mock_hello_or_ping.assert_called_once_with(state)

    @patch('homu.main.get_words', return_value=["hello?"])
    @patch('homu.main.verify_auth', return_value=True)
    @patch('homu.main.PullReqState')
    @patch('homu.action.hello_or_ping')
    def test_parse_commands_hello_or_ping_not_realtime(self, mock_hello_or_ping, MockPullReqState, mock_auth, mock_words):
        state = MockPullReqState()
        self.assertFalse(self.call_parse_commands(state=state, sha='abc123'))
        assert not mock_hello_or_ping.called, 'hello_or_ping was called and should never be.'

    @patch('homu.main.get_words', return_value=["treeclosed=1"])
    @patch('homu.main.verify_auth', return_value=True)
    @patch('homu.main.PullReqState')
    @patch('homu.action.set_treeclosed')
    def test_parse_commands_set_treeclosed(self, mock_set_treeclosed, MockPullReqState, mock_auth, mock_words):
        state = MockPullReqState()
        self.assertTrue(self.call_parse_commands(state=state, realtime=True, sha='abc123'))
        mock_set_treeclosed.assert_called_once_with(state, 'treeclosed=1')

    @patch('homu.main.get_words', return_value=["treeclosed-"])
    @patch('homu.main.verify_auth', return_value=True)
    @patch('homu.main.PullReqState')
    @patch('homu.action.treeclosed_negative')
    def test_parse_commands_treeclosed_negative(self, mock_treeclosed_negative, MockPullReqState, mock_auth, mock_words):
        state = MockPullReqState()
        self.assertTrue(self.call_parse_commands(state=state, realtime=True, sha='abc123'))
        mock_treeclosed_negative.assert_called_once_with(state)


if __name__ == '__main__':
    unittest.main()
