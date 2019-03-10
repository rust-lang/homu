def verify_level(username, repo_cfg, state, toml_keys):
    authorized = False
    if repo_cfg.get('auth_collaborators', False):
        authorized = state.get_repo().is_collaborator(username)
    if not authorized:
        authorized = username.lower() == state.delegate.lower()
    for toml_key in toml_keys:
        if not authorized:
            authorized = username in repo_cfg.get(toml_key, [])
    return authorized


def verify(username, repo_cfg, state, auth, realtime, my_username):
    # The import is inside the function to prevent circular imports: main.py
    # requires auth.py and auth.py requires main.py
    from .main import AuthState

    # In some cases (e.g. non-fully-qualified r+) we recursively talk to
    # ourself via a hidden markdown comment in the message. This is so that
    # when re-synchronizing after shutdown we can parse these comments and
    # still know the SHA for the approval.
    #
    # So comments from self should always be allowed
    if username == my_username:
        return True

    authorized = False
    if auth == AuthState.REVIEWER:
        authorized = verify_level(username, repo_cfg, state, ['reviewers'])
    elif auth == AuthState.TRY:
        authorized = verify_level(
            username, repo_cfg, state, ['reviewers', 'try_users'],
        )

    if authorized:
        return True
    else:
        if realtime:
            reply = '@{}: :key: Insufficient privileges: '.format(username)
            if auth == AuthState.REVIEWER:
                if repo_cfg.get('auth_collaborators', False):
                    reply += 'Collaborator required'
                else:
                    reply += 'Not in reviewers'
            elif auth == AuthState.TRY:
                reply += 'not in try users'
            state.add_comment(reply)
        return False
