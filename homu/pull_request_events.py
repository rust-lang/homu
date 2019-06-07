import requests
import time


QUERY = """
query ($repoName: String!, $repoOwner: String!, $pull: Int!, $after: String) {
  repository(name: $repoName, owner: $repoOwner) {
    pullRequest(number: $pull) {
      author {
        login
      }
      title
      state
      headRefOid
      mergeable
      timelineItems(first: 100, after: $after) {
        pageInfo {
          hasNextPage
          endCursor
        }
        nodes {
          eventType: __typename
          ... on PullRequestCommit {
            commit {
              oid
            }
          }
          ... on AssignedEvent {
            actor {
              login
            }
            user {
              login
            }
          }
          ... on UnassignedEvent {
            actor {
              login
            }
            user {
              login
            }
          }
          ... on IssueComment {
            author {
              login
            }
            body
            publishedAt
          }
          ... on SubscribedEvent {
            actor {
              login
            }
          }
          ... on LabeledEvent {
            actor {
              login
            }
            label {
              name
            }
          }
          ... on UnlabeledEvent {
            actor {
              login
            }
            label {
              name
            }
          }
          ... on BaseRefChangedEvent {
            actor {
              login
            }
          }
          ... on HeadRefForcePushedEvent {
            actor {
              login
            }
            beforeCommit {
              oid
            }
            afterCommit {
              oid
            }
          }
          ... on RenamedTitleEvent {
            actor {
              login
            }
            previousTitle
            currentTitle
          }
          ... on MentionedEvent {
            actor {
              login
            }
          }
        }
      }
    }
  }
}
"""


class PullRequestResponse:
    def __init__(self):
        self.events = []

    @property
    def initial_title(self):
        if not hasattr(self, '_initial_title'):
            for event in self.events:
                if event.event_type == 'RenamedTitleEvent':
                    self._initial_title = event.data['previousTitle']
                    break

            # The title never changed. That means that the initial title is
            # the same as the current title.
            if not hasattr(self, '_initial_title'):
                self._initial_title = self.title

        return self._initial_title


class PullRequestEvent:
    def __init__(self, data):
        self.data = data

    def __getitem__(self, key):
        return self.data[key]

    @property
    def event_type(self):
        return self.data['eventType']

    @staticmethod
    def _actor(s):
        return "\x1b[1m@" + s + "\x1b[0m"

    @staticmethod
    def _label(s):
        return "\x1b[100m" + s + "\x1b[0m"

    @staticmethod
    def _commit(s):
        return "\x1b[93m" + s[0:7] + "\x1b[0m"

    @staticmethod
    def _comment_summary(comment):
        # line_1 = comment.splitlines()[0]
        # if len(line_1) > 40:
        #     return line_1[0:37] + '...'
        # else:
        #     return line_1
        return '\n'.join(['  \x1b[90m> \x1b[37m' + c + '\x1b[0m'
                          for c
                          in comment.splitlines()])

    def format(self):
        d = {
            'IssueComment': lambda e:
                "{} left a comment:\n{}".format(
                    self._actor(e['author']['login']),
                    self._comment_summary(e['body'])),
            'SubscribedEvent': lambda e:
                # "{} was subscribed".format(
                #     self._actor(e['actor']['login'])),
                None,
            'MentionedEvent': lambda e:
                # "{} was mentioned".format(
                #     self._actor(e['actor']['login'])),
                None,
            'RenamedTitleEvent': lambda e:
                "Renamed from '{}' to '{}' by {}".format(
                    e['previousTitle'],
                    e['currentTitle'],
                    self._actor(e['actor']['login'])),
            'LabeledEvent': lambda e:
                "Label {} added by {}".format(
                    self._label(e['label']['name']),
                    self._actor(e['actor']['login'])),
            'UnlabeledEvent': lambda e:
                "Label {} removed by {}".format(
                    self._label(e['label']['name']),
                    self._actor(e['actor']['login'])),
            'ReferencedEvent': lambda e:
                # "Referenced",
                None,
            'HeadRefForcePushedEvent': lambda e:
                "{} force-pushed from {} to {}".format(
                    self._actor(e['actor']['login']),
                    self._commit(e['beforeCommit']['oid']),
                    self._commit(e['afterCommit']['oid'])),
            'AssignedEvent': lambda e:
                "Assigned to {} by {}".format(
                    self._actor(e['user']['login']),
                    self._actor(e['actor']['login'])),
            'CrossReferencedEvent': lambda e:
                # "Cross referenced",
                None,
            'PullRequestReview': lambda e:
                "Reviewed",
            'PullRequestCommit': lambda e:
                "New commit {} pushed".format(
                    self._commit(self.data['commit']['oid'])),
            'MergedEvent': lambda e:
                "Merged!",
            'ClosedEvent': lambda e:
                "Closed.",
            'ReopenedEvent': lambda e:
                "Reopened.",
        }

        if self.event_type in d:
            r = d[self.event_type](self)
            if r:
                return r
            else:
                return None
        else:
            return None


def all(access_token, owner, repo, pull):
    after = None
    result = PullRequestResponse()
    result.owner = owner
    result.repo = repo
    result.pull = pull

    while True:
        response = one(access_token=access_token,
                       owner=owner,
                       repo=repo,
                       pull=pull,
                       after=after)
        if response.status_code == 502:
            # 502s happen sometimes when talking to GitHub. Try again.
            time.sleep(1)
            continue

        r = response.json()

        pull_request = r['data']['repository']['pullRequest']
        page_info = pull_request['timelineItems']['pageInfo']
        events = pull_request['timelineItems']['nodes']

        result.title = pull_request['title']
        result.author = pull_request['author']['login']
        result.state = pull_request['state']
        result.head_sha = pull_request['headRefOid']
        result.mergeable = pull_request['mergeable']

        result.events.extend([PullRequestEvent(e) for e in events])

        if not page_info['hasNextPage']:
            break
        after = page_info['endCursor']

    return result


def one(access_token, owner, repo, pull, after):
    headers = {
        'authorization': 'bearer ' + access_token,
        'accept': 'application/json',
    }
    json = {
        'query': QUERY,
        'variables': {
            'repoName': repo,
            'repoOwner': owner,
            'pull': int(pull),
            'after': after,
        }
    }
    result = requests.post('https://api.github.com/graphql',
                           headers=headers,
                           json=json)

    return result
