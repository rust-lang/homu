import requests
import time

PULL_REQUESTS_QUERY = """
query ($repoName: String!, $repoOwner: String!, $after: String) {
  repository(name: $repoName, owner: $repoOwner) {
    pullRequests(first: 100, after: $after, states: OPEN) {
      pageInfo {
        hasNextPage
        endCursor
      }
      nodes {
        author {
          login
        }
        number
        title
        state
        baseRefName
        headRepositoryOwner {
          login
        }
        body
        headRefName
        headRefOid
        mergeable
        timelineItems(last: 1) {
          pageInfo {
            endCursor
          }
        }
      }
    }
  }
  rateLimit {
    limit
    cost
    remaining
    resetAt
  }
}
"""

PULL_REQUEST_QUERY = """
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
        edges {
          cursor
          node {
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
  rateLimit {
    limit
    cost
    remaining
    resetAt
  }
}
"""


class PullRequestsItem:
    def __init__(self, data):
        self.number = data['number']
        self.body = data['body']
        self.author = data['author']['login']
        self.head_ref = "{}:{}".format(data['headRepositoryOwner']['login'], data['headRefName']) # noqa
        self.head_sha = data['headRefOid']
        self.base_ref = data['baseRefName']
        self.timeline_cursor = data['timelineItems']['pageInfo']['endCursor']


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
    def __init__(self, cursor, data):
        self.cursor = cursor
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


class GitHubV4:
    def __init__(self, access_token):
        self.access_token = access_token

    def _update_rate_limit(self, rate_limit):
        self.rate_limit = rate_limit['limit']
        self.rate_remaining = rate_limit['remaining']
        self.rate_reset = rate_limit['resetAt']

    def pull_requests(self, owner, repo, after=None):
        results = []

        attempt = 1

        while True:
            response = self._pull_requests_one(
                           owner=owner,
                           repo=repo,
                           after=after)
            if response.status_code == 502:
                # 502s happen sometimes when talking to GitHub. Try again.
                time.sleep(1)
                continue

            r = response.json()

            if 'errors' in r:
                if attempt == 10:
                    raise Exception("Too many errors")
                attempt += 1
#                print("GraphQL query failed:")
#                for error in r['errors']:
#                    print(" * {}".format(error['message']))
                time.sleep(1)
                continue

            if 'data' not in r:
                print("response.status_code = {}".format(response.status_code))
                print("r = {}".format(r))

            rate_limit = r['data']['rateLimit']
            self._update_rate_limit(rate_limit)

            page_info = r['data']['repository']['pullRequests']['pageInfo']
            pull_requests = r['data']['repository']['pullRequests']['nodes']

            results.extend([PullRequestsItem(e)
                            for e
                            in pull_requests])

#            print("Page info: hasNextPage={0} endCursor={1}"
#                  .format(
#                    page_info['hasNextPage'],
#                    page_info['endCursor']))
            if not page_info['hasNextPage']:
                break
            after = page_info['endCursor']

        return results

    def _pull_requests_one(self, owner, repo, after):
        headers = {
            'authorization': 'bearer ' + self.access_token,
            'accept': 'application/json',
        }
        json = {
            'query': PULL_REQUESTS_QUERY,
            'variables': {
                'repoName': repo,
                'repoOwner': owner,
                'after': after,
            }
        }
        result = requests.post('https://api.github.com/graphql',
                               headers=headers,
                               json=json)

        return result

    def pull_request(self, owner, repo, pull, after=None):
        result = PullRequestResponse()
        result.owner = owner
        result.repo = repo
        result.pull = pull

        attempt = 1

        while True:
            response = self._pull_request_one(
                           owner=owner,
                           repo=repo,
                           pull=pull,
                           after=after)
            if response.status_code == 502:
                # 502s happen sometimes when talking to GitHub. Try again.
                time.sleep(1)
                continue

            r = response.json()

            if 'errors' in r:
                if attempt == 3:
                    raise Exception("Too many errors")
                attempt += 1
#                print("GraphQL query failed:")
#                for error in r['errors']:
#                    print(" * {}".format(error['message']))
                time.sleep(1)
                continue

            if 'data' not in r:
                print("response.status_code = {}".format(response.status_code))
                print("r = {}".format(r))

            rate_limit = r['data']['rateLimit']
            self._update_rate_limit(rate_limit)
#            print("Rate limit: limit={0} cost={1} remaining={2} resetAt={3}"
#                  .format(
#                    rate_limit['limit'],
#                    rate_limit['cost'],
#                    rate_limit['remaining'],
#                    rate_limit['resetAt']))
            pull_request = r['data']['repository']['pullRequest']
            page_info = pull_request['timelineItems']['pageInfo']
            events = pull_request['timelineItems']['edges']

            result.title = pull_request['title']
            result.author = pull_request['author']['login']
            result.state = pull_request['state']
            result.head_sha = pull_request['headRefOid']
            result.mergeable = pull_request['mergeable']

            result.events.extend([PullRequestEvent(e['cursor'], e['node'])
                                  for e
                                  in events])

#            print("Page info: hasNextPage={0} endCursor={1}"
#                  .format(
#                    page_info['hasNextPage'],
#                    page_info['endCursor']))
            if not page_info['hasNextPage']:
                break
            after = page_info['endCursor']

        return result

    def _pull_request_one(self, owner, repo, pull, after):
        headers = {
            'authorization': 'bearer ' + self.access_token,
            'accept': 'application/json',
        }
        json = {
            'query': PULL_REQUEST_QUERY,
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
