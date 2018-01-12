import hmac
import json
import urllib.parse
from .main import (
    PullReqState,
    parse_commands,
    db_query,
    INTERRUPTED_BY_HOMU_RE,
    synchronize,
    LabelEvent,
)
from . import utils
from .utils import lazy_debug
import github3
import jinja2
import requests
import pkg_resources
from bottle import (
    get,
    post,
    run,
    request,
    redirect,
    abort,
    response,
)
from threading import Thread
import sys
import os
import traceback
from retrying import retry

import bottle
bottle.BaseRequest.MEMFILE_MAX = 1024 * 1024 * 10


class G:
    pass


g = G()


def find_state(sha):
    for repo_label, repo_states in g.states.items():
        for state in repo_states.values():
            if state.merge_sha == sha:
                return state, repo_label

    raise ValueError('Invalid SHA')


def get_repo(repo_label, repo_cfg):
    repo = g.repos[repo_label].gh
    if not repo:
        repo = g.gh.repository(repo_cfg['owner'], repo_cfg['name'])
        g.repos[repo_label] = repo
        assert repo.owner.login == repo_cfg['owner']
        assert repo.name == repo_cfg['name']
    return repo


@get('/')
def index():
    return g.tpls['index'].render(repos=[g.repos[label]
                                         for label in sorted(g.repos)])


@get('/queue/<repo_label:path>')
def queue(repo_label):
    logger = g.logger.getChild('queue')

    lazy_debug(logger, lambda: 'repo_label: {}'.format(repo_label))

    single_repo_closed = None
    if repo_label == 'all':
        labels = g.repos.keys()
        multiple = True
        repo_url = None
    else:
        labels = repo_label.split('+')
        multiple = len(labels) > 1
        if repo_label in g.repos and g.repos[repo_label].treeclosed >= 0:
            single_repo_closed = g.repos[repo_label].treeclosed
        repo_url = 'https://github.com/{}/{}'.format(
            g.cfg['repo'][repo_label]['owner'],
            g.cfg['repo'][repo_label]['name'])

    states = []
    for label in labels:
        try:
            states += g.states[label].values()
        except KeyError:
            abort(404, 'No such repository: {}'.format(label))

    pull_states = sorted(states)
    rows = []
    for state in pull_states:
        treeclosed = (single_repo_closed or
                      state.priority < g.repos[state.repo_label].treeclosed)
        status_ext = ''

        if state.try_:
            status_ext += ' (try)'

        if treeclosed:
            status_ext += ' [TREE CLOSED]'

        rows.append({
            'status': state.get_status(),
            'status_ext': status_ext,
            'priority': 'rollup' if state.rollup else state.priority,
            'url': 'https://github.com/{}/{}/pull/{}'.format(state.owner,
                                                             state.name,
                                                             state.num),
            'num': state.num,
            'approved_by': state.approved_by,
            'title': state.title,
            'head_ref': state.head_ref,
            'mergeable': ('yes' if state.mergeable is True else
                          'no' if state.mergeable is False else ''),
            'assignee': state.assignee,
            'repo_label': state.repo_label,
            'repo_url': 'https://github.com/{}/{}'.format(state.owner,
                                                          state.name),
            'greyed': "treeclosed" if treeclosed else "",
        })

    return g.tpls['queue'].render(
        repo_url=repo_url,
        repo_label=repo_label,
        treeclosed=single_repo_closed,
        states=rows,
        oauth_client_id=g.cfg['github']['app_client_id'],
        total=len(pull_states),
        approved=len([x for x in pull_states if x.approved_by]),
        rolled_up=len([x for x in pull_states if x.rollup]),
        failed=len([x for x in pull_states if x.status == 'failure' or
                   x.status == 'error']),
        multiple=multiple,
    )


@get('/callback')
def callback():
    logger = g.logger.getChild('callback')

    response.content_type = 'text/plain'

    code = request.query.code
    state = json.loads(request.query.state)

    lazy_debug(logger, lambda: 'state: {}'.format(state))
    oauth_url = 'https://github.com/login/oauth/access_token'

    try:
        res = requests.post(oauth_url, data={
            'client_id': g.cfg['github']['app_client_id'],
            'client_secret': g.cfg['github']['app_client_secret'],
            'code': code,
        })
    except Exception as ex:
        logger.warn('/callback encountered an error '
                    'during github oauth callback')
        # probably related to https://gitlab.com/pycqa/flake8/issues/42
        lazy_debug(logger, lambda: 'github oauth callback err: {}'.format(ex))  # noqa
        abort(502, 'Bad Gateway')

    args = urllib.parse.parse_qs(res.text)
    token = args['access_token'][0]

    repo_label = state['repo_label']
    repo_cfg = g.repo_cfgs[repo_label]
    repo = get_repo(repo_label, repo_cfg)

    user_gh = github3.login(token=token)

    if state['cmd'] == 'rollup':
        return rollup(user_gh, state, repo_label, repo_cfg, repo)
    elif state['cmd'] == 'synch':
        return synch(user_gh, state, repo_label, repo_cfg, repo)
    else:
        abort(400, 'Invalid command')


def rollup(user_gh, state, repo_label, repo_cfg, repo):
    user_repo = user_gh.repository(user_gh.user().login, repo.name)
    base_repo = user_gh.repository(repo.owner.login, repo.name)

    nums = state.get('nums', [])
    if nums:
        try:
            rollup_states = [g.states[repo_label][num] for num in nums]
        except KeyError as e:
            return 'Invalid PR number: {}'.format(e.args[0])
    else:
        rollup_states = [x for x in g.states[repo_label].values() if x.rollup]
    rollup_states = [x for x in rollup_states if x.approved_by]
    rollup_states.sort(key=lambda x: x.num)

    if not rollup_states:
        return 'No pull requests are marked as rollup'

    base_ref = rollup_states[0].base_ref

    base_sha = repo.ref('heads/' + base_ref).object.sha
    utils.github_set_ref(
        user_repo,
        'heads/' + repo_cfg.get('branch', {}).get('rollup', 'rollup'),
        base_sha,
        force=True,
    )

    successes = []
    failures = []

    for state in rollup_states:
        if base_ref != state.base_ref:
            failures.append(state.num)
            continue

        merge_msg = 'Rollup merge of #{} - {}, r={}\n\n{}\n\n{}'.format(
            state.num,
            state.head_ref,
            state.approved_by,
            state.title,
            state.body,
        )

        try:
            rollup = repo_cfg.get('branch', {}).get('rollup', 'rollup')
            user_repo.merge(rollup, state.head_sha, merge_msg)
        except github3.models.GitHubError as e:
            if e.code != 409:
                raise

            failures.append(state.num)
        else:
            successes.append(state.num)

    title = 'Rollup of {} pull requests'.format(len(successes))
    body = '- Successful merges: {}\n- Failed merges: {}'.format(
        ', '.join('#{}'.format(x) for x in successes),
        ', '.join('#{}'.format(x) for x in failures),
    )

    try:
        rollup = repo_cfg.get('branch', {}).get('rollup', 'rollup')
        pull = base_repo.create_pull(
            title,
            state.base_ref,
            user_repo.owner.login + ':' + rollup,
            body,
        )
    except github3.models.GitHubError as e:
        return e.response.text
    else:
        redirect(pull.html_url)


@post('/github')
def github():
    logger = g.logger.getChild('github')

    response.content_type = 'text/plain'

    payload = request.body.read()
    info = request.json

    lazy_debug(logger, lambda: 'info: {}'.format(utils.remove_url_keys_from_json(info)))  # noqa

    owner_info = info['repository']['owner']
    owner = owner_info.get('login') or owner_info['name']
    repo_label = g.repo_labels[owner, info['repository']['name']]
    repo_cfg = g.repo_cfgs[repo_label]

    hmac_method, hmac_sig = request.headers['X-Hub-Signature'].split('=')
    if hmac_sig != hmac.new(
        repo_cfg['github']['secret'].encode('utf-8'),
        payload,
        hmac_method,
    ).hexdigest():
        abort(400, 'Invalid signature')

    event_type = request.headers['X-Github-Event']

    if event_type == 'pull_request_review_comment':
        action = info['action']
        original_commit_id = info['comment']['original_commit_id']
        head_sha = info['pull_request']['head']['sha']

        if action == 'created' and original_commit_id == head_sha:
            pull_num = info['pull_request']['number']
            body = info['comment']['body']
            username = info['sender']['login']

            state = g.states[repo_label].get(pull_num)
            if state:
                state.title = info['pull_request']['title']
                state.body = info['pull_request']['body']

                if parse_commands(
                    body,
                    username,
                    repo_cfg,
                    state,
                    g.my_username,
                    g.db,
                    g.states,
                    realtime=True,
                    sha=original_commit_id,
                ):
                    state.save()

                    g.queue_handler()

    elif event_type == 'pull_request':
        action = info['action']
        pull_num = info['number']
        head_sha = info['pull_request']['head']['sha']

        if action == 'synchronize':
            state = g.states[repo_label][pull_num]
            state.head_advanced(head_sha)

            state.save()

        elif action in ['opened', 'reopened']:
            state = PullReqState(pull_num, head_sha, '', g.db, repo_label,
                                 g.mergeable_que, g.gh,
                                 info['repository']['owner']['login'],
                                 info['repository']['name'],
                                 repo_cfg.get('labels', {}),
                                 g.repos)
            state.title = info['pull_request']['title']
            state.body = info['pull_request']['body']
            state.head_ref = info['pull_request']['head']['repo']['owner']['login'] + ':' + info['pull_request']['head']['ref']  # noqa
            state.base_ref = info['pull_request']['base']['ref']
            state.set_mergeable(info['pull_request']['mergeable'])
            state.assignee = (info['pull_request']['assignee']['login'] if
                              info['pull_request']['assignee'] else '')

            found = False

            if action == 'reopened':
                # FIXME: Review comments are ignored here
                for c in state.get_repo().issue(pull_num).iter_comments():
                    found = parse_commands(
                        c.body,
                        c.user.login,
                        repo_cfg,
                        state,
                        g.my_username,
                        g.db,
                        g.states,
                    ) or found

                status = ''
                for info in utils.github_iter_statuses(state.get_repo(),
                                                       state.head_sha):
                    if info.context == 'homu':
                        status = info.state
                        break

                state.set_status(status)

            state.save()

            g.states[repo_label][pull_num] = state

            if found:
                g.queue_handler()

        elif action == 'closed':
            state = g.states[repo_label][pull_num]
            if hasattr(state, 'fake_merge_sha'):
                def inner():
                    utils.github_set_ref(
                        state.get_repo(),
                        'heads/' + state.base_ref,
                        state.merge_sha,
                        force=True,
                    )

                def fail(err):
                    state.add_comment(':boom: Failed to recover from the '
                                      'artificial commit. See {} for details.'
                                      ' ({})'.format(state.fake_merge_sha,
                                                     err))

                utils.retry_until(inner, fail, state)

            del g.states[repo_label][pull_num]

            db_query(g.db, 'DELETE FROM pull WHERE repo = ? AND num = ?',
                     [repo_label, pull_num])
            db_query(g.db, 'DELETE FROM build_res WHERE repo = ? AND num = ?',
                     [repo_label, pull_num])
            db_query(g.db, 'DELETE FROM mergeable WHERE repo = ? AND num = ?',
                     [repo_label, pull_num])

            g.queue_handler()

        elif action in ['assigned', 'unassigned']:
            state = g.states[repo_label][pull_num]
            state.assignee = (info['pull_request']['assignee']['login'] if
                              info['pull_request']['assignee'] else '')

            state.save()

        else:
            lazy_debug(logger, lambda: 'Invalid pull_request action: {}'.format(action))  # noqa

    elif event_type == 'push':
        ref = info['ref'][len('refs/heads/'):]

        for state in list(g.states[repo_label].values()):
            if state.base_ref == ref:
                state.set_mergeable(None, cause={
                    'sha': info['head_commit']['id'],
                    'title': info['head_commit']['message'].splitlines()[0],
                })

            if state.head_sha == info['before']:
                if state.status:
                    state.change_labels(LabelEvent.PUSHED)
                state.head_advanced(info['after'])

                state.save()

    elif event_type == 'issue_comment':
        body = info['comment']['body']
        username = info['comment']['user']['login']
        pull_num = info['issue']['number']

        state = g.states[repo_label].get(pull_num)

        if 'pull_request' in info['issue'] and state:
            state.title = info['issue']['title']
            state.body = info['issue']['body']

            if parse_commands(
                body,
                username,
                repo_cfg,
                state,
                g.my_username,
                g.db,
                g.states,
                realtime=True,
            ):
                state.save()

                g.queue_handler()

    elif event_type == 'status':
        try:
            state, repo_label = find_state(info['sha'])
        except ValueError:
            return 'OK'

        status_name = ""
        if 'status' in repo_cfg:
            for name, value in repo_cfg['status'].items():
                if 'context' in value and value['context'] == info['context']:
                    status_name = name
        if status_name is "":
            return 'OK'

        if info['state'] == 'pending':
            return 'OK'

        for row in info['branches']:
            if row['name'] == state.base_ref:
                return 'OK'

        report_build_res(info['state'] == 'success', info['target_url'],
                         'status-' + status_name, state, logger, repo_cfg)

    return 'OK'


def report_build_res(succ, url, builder, state, logger, repo_cfg):
    lazy_debug(logger,
               lambda: 'build result {}: builder = {}, succ = {}, current build_res = {}'  # noqa
                       .format(state, builder, succ,
                               state.build_res_summary()))

    state.set_build_res(builder, succ, url)

    if succ:
        if all(x['res'] for x in state.build_res.values()):
            state.set_status('success')
            desc = 'Test successful'
            utils.github_create_status(state.get_repo(), state.head_sha,
                                       'success', url, desc, context='homu')

            urls = ', '.join('[{}]({})'.format(builder, x['url']) for builder, x in sorted(state.build_res.items()))  # noqa
            test_comment = ':sunny: {} - {}'.format(desc, urls)

            if state.approved_by and not state.try_:
                comment = (test_comment + '\n' +
                           'Approved by: {}\nPushing {} to {}...'
                           ).format(state.approved_by, state.merge_sha,
                                    state.base_ref)
                state.add_comment(comment)
                state.change_labels(LabelEvent.SUCCEED)
                try:
                    try:
                        utils.github_set_ref(state.get_repo(), 'heads/' +
                                             state.base_ref, state.merge_sha)
                    except github3.models.GitHubError:
                        utils.github_create_status(
                            state.get_repo(),
                            state.merge_sha,
                            'success', '',
                            'Branch protection bypassed',
                            context='homu')
                        utils.github_set_ref(state.get_repo(), 'heads/' +
                                             state.base_ref, state.merge_sha)

                    state.fake_merge(repo_cfg)

                except github3.models.GitHubError as e:
                    state.set_status('error')
                    desc = ('Test was successful, but fast-forwarding failed:'
                            ' {}'.format(e))
                    utils.github_create_status(state.get_repo(),
                                               state.head_sha, 'error', url,
                                               desc, context='homu')

                    state.add_comment(':eyes: ' + desc)
            else:
                comment = (test_comment + '\n' +
                           'State: approved={} try={}'
                           ).format(state.approved_by, state.try_)
                state.add_comment(comment)
                state.change_labels(LabelEvent.TRY_SUCCEED)

    else:
        if state.status == 'pending':
            state.set_status('failure')
            desc = 'Test failed'
            utils.github_create_status(state.get_repo(), state.head_sha,
                                       'failure', url, desc, context='homu')

            state.add_comment(':broken_heart: {} - [{}]({})'.format(desc,
                                                                    builder,
                                                                    url))
            event = LabelEvent.TRY_FAILED if state.try_ else LabelEvent.FAILED
            state.change_labels(event)

    g.queue_handler()


@post('/buildbot')
def buildbot():
    logger = g.logger.getChild('buildbot')

    response.content_type = 'text/plain'

    for row in json.loads(request.forms.packets):
        if row['event'] == 'buildFinished':
            info = row['payload']['build']
            lazy_debug(logger, lambda: 'info: {}'.format(info))
            props = dict(x[:2] for x in info['properties'])

            if 'retry' in info['text']:
                continue

            if not props['revision']:
                continue

            try:
                state, repo_label = find_state(props['revision'])
            except ValueError:
                lazy_debug(logger,
                           lambda: 'Invalid commit ID from Buildbot: {}'.format(props['revision']))  # noqa
                continue

            lazy_debug(logger, lambda: 'state: {}, {}'.format(state, state.build_res_summary()))  # noqa

            if info['builderName'] not in state.build_res:
                lazy_debug(logger,
                           lambda: 'Invalid builder from Buildbot: {}'.format(info['builderName']))  # noqa
                continue

            repo_cfg = g.repo_cfgs[repo_label]

            if request.forms.secret != repo_cfg['buildbot']['secret']:
                abort(400, 'Invalid secret')

            build_succ = 'successful' in info['text'] or info['results'] == 0

            url = '{}/builders/{}/builds/{}'.format(
                repo_cfg['buildbot']['url'],
                info['builderName'],
                props['buildnumber'],
            )

            if 'interrupted' in info['text']:
                step_name = ''
                for step in reversed(info['steps']):
                    if 'interrupted' in step.get('text', []):
                        step_name = step['name']
                        break

                if step_name:
                    try:
                        url = ('{}/builders/{}/builds/{}/steps/{}/logs/interrupt'  # noqa
                               ).format(repo_cfg['buildbot']['url'],
                                        info['builderName'],
                                        props['buildnumber'],
                                        step_name,)
                        res = requests.get(url)
                    except Exception as ex:
                        logger.warn('/buildbot encountered an error during '
                                    'github logs request')
                        # probably related to
                        # https://gitlab.com/pycqa/flake8/issues/42
                        lazy_debug(logger, lambda: 'buildbot logs err: {}'.format(ex))  # noqa
                        abort(502, 'Bad Gateway')

                    mat = INTERRUPTED_BY_HOMU_RE.search(res.text)
                    if mat:
                        interrupt_token = mat.group(1)
                        if getattr(state, 'interrupt_token',
                                   '') != interrupt_token:
                            state.interrupt_token = interrupt_token

                            if state.status == 'pending':
                                state.set_status('')

                                desc = (':snowman: The build was interrupted '
                                        'to prioritize another pull request.')
                                state.add_comment(desc)
                                state.change_labels(LabelEvent.INTERRUPTED)
                                utils.github_create_status(state.get_repo(),
                                                           state.head_sha,
                                                           'error', url,
                                                           desc,
                                                           context='homu')

                                g.queue_handler()

                        continue

                else:
                    logger.error('Corrupt payload from Buildbot')

            report_build_res(build_succ, url, info['builderName'],
                             state, logger, repo_cfg)

        elif row['event'] == 'buildStarted':
            info = row['payload']['build']
            lazy_debug(logger, lambda: 'info: {}'.format(info))
            props = dict(x[:2] for x in info['properties'])

            if not props['revision']:
                continue

            try:
                state, repo_label = find_state(props['revision'])
            except ValueError:
                pass
            else:
                if info['builderName'] in state.build_res:
                    repo_cfg = g.repo_cfgs[repo_label]

                    if request.forms.secret != repo_cfg['buildbot']['secret']:
                        abort(400, 'Invalid secret')

                    url = '{}/builders/{}/builds/{}'.format(
                        repo_cfg['buildbot']['url'],
                        info['builderName'],
                        props['buildnumber'],
                    )

                    state.set_build_res(info['builderName'], None, url)

            if g.buildbot_slots[0] == props['revision']:
                g.buildbot_slots[0] = ''

                g.queue_handler()

    return 'OK'


def synch(user_gh, state, repo_label, repo_cfg, repo):
    if not repo.is_collaborator(user_gh.user().login):
        abort(400, 'You are not a collaborator')

    Thread(target=synchronize, args=[repo_label, repo_cfg, g.logger,
                                     g.gh, g.states, g.repos, g.db,
                                     g.mergeable_que, g.my_username,
                                     g.repo_labels]).start()

    return 'Synchronizing {}...'.format(repo_label)


def synch_all():
    @retry(wait_exponential_multiplier=1000, wait_exponential_max=600000)
    def sync_repo(repo_label, g):
        try:
            synchronize(repo_label, g.repo_cfgs[repo_label], g.logger, g.gh,
                        g.states, g.repos, g.db, g.mergeable_que,
                        g.my_username, g.repo_labels)
        except Exception:
            print('* Error while synchronizing {}'.format(repo_label))
            traceback.print_exc()
            raise

    for repo_label in g.repos:
        sync_repo(repo_label, g)
    print('* Done synchronizing all')


@post('/admin')
def admin():
    if request.json['secret'] != g.cfg['web']['secret']:
        return 'Authentication failure'

    if request.json['cmd'] == 'repo_new':
        repo_label = request.json['repo_label']
        repo_cfg = request.json['repo_cfg']

        g.states[repo_label] = {}
        g.repos[repo_label] = None
        g.repo_cfgs[repo_label] = repo_cfg
        g.repo_labels[repo_cfg['owner'], repo_cfg['name']] = repo_label

        Thread(target=synchronize, args=[repo_label, repo_cfg, g.logger,
                                         g.gh, g.states, g.repos, g.db,
                                         g.mergeable_que, g.my_username,
                                         g.repo_labels]).start()
        return 'OK'

    elif request.json['cmd'] == 'repo_del':
        repo_label = request.json['repo_label']
        repo_cfg = g.repo_cfgs[repo_label]

        db_query(g.db, 'DELETE FROM pull WHERE repo = ?', [repo_label])
        db_query(g.db, 'DELETE FROM build_res WHERE repo = ?', [repo_label])
        db_query(g.db, 'DELETE FROM mergeable WHERE repo = ?', [repo_label])

        del g.states[repo_label]
        del g.repos[repo_label]
        del g.repo_cfgs[repo_label]
        del g.repo_labels[repo_cfg['owner'], repo_cfg['name']]

        return 'OK'

    elif request.json['cmd'] == 'repo_edit':
        repo_label = request.json['repo_label']
        repo_cfg = request.json['repo_cfg']

        assert repo_cfg['owner'] == g.repo_cfgs[repo_label]['owner']
        assert repo_cfg['name'] == g.repo_cfgs[repo_label]['name']

        g.repo_cfgs[repo_label] = repo_cfg

        return 'OK'

    elif request.json['cmd'] == 'sync_all':
        Thread(target=synch_all).start()

        return 'OK'

    return 'Unrecognized command'


def start(cfg, states, queue_handler, repo_cfgs, repos, logger,
          buildbot_slots, my_username, db, repo_labels, mergeable_que, gh):
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(pkg_resources.resource_filename(__name__, 'html')),  # noqa
        autoescape=True,
    )
    tpls = {}
    tpls['index'] = env.get_template('index.html')
    tpls['queue'] = env.get_template('queue.html')

    g.cfg = cfg
    g.states = states
    g.queue_handler = queue_handler
    g.repo_cfgs = repo_cfgs
    g.repos = repos
    g.logger = logger.getChild('server')
    g.buildbot_slots = buildbot_slots
    g.tpls = tpls
    g.my_username = my_username
    g.db = db
    g.repo_labels = repo_labels
    g.mergeable_que = mergeable_que
    g.gh = gh

    # Synchronize all PR data on startup
    if cfg['web'].get('sync_on_start', False):
        Thread(target=synch_all).start()

    try:
        run(host=cfg['web'].get('host', '0.0.0.0'),
            port=cfg['web']['port'],
            server='waitress')
    except OSError as e:
        print(e, file=sys.stderr)
        os._exit(1)
