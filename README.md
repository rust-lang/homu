# Homu

[![Hommando]][Akemi Homura]

Homu is a bot that integrates with GitHub and your favorite continuous
integration service such as [Travis CI], [Appveyor] or [Buildbot].

[Hommando]: https://i.imgur.com/j0jNvHF.png
[Akemi Homura]: https://wiki.puella-magi.net/Homura_Akemi
[Buildbot]: http://buildbot.net/
[Travis CI]: https://travis-ci.org/
[Appveyor]: https://www.appveyor.com/

## Why is it needed?

Let's take Travis CI as an example. If you send a pull request to a repository,
Travis CI instantly shows you the test result, which is great. However, after
several other pull requests are merged into the `master` branch, your pull
request can *still* break things after being merged into `master`. The
traditional continuous integration solutions don't protect you from this.

In fact, that's why they provide the build status badges. If anything pushed to
`master` is completely free from any breakage, those badges will **not** be
necessary, as they will always be green. The badges themselves prove that there
can still be some breakages, even when continuous integration services are used.

To solve this problem, the test procedure should be executed *just before the
merge*, not just after the pull request is received. You can manually click the
"restart build" button each time before you merge a pull request, but Homu can
automate this process. It listens to the pull request comments, waiting for an
approval comment from one of the configured reviewers. When the pull request is
approved, Homu tests it using your favorite continuous integration service, and
only when it passes all the tests, it is merged into `master`.

Note that Homu is **not** a replacement of Travis CI, Buildbot or Appveyor. It
works on top of them. Homu itself doesn't have the ability to test pull
requests.

## Influences of bors

Homu is largely inspired by [bors]. The concept of "tests should be done just
before the merge" came from bors. However, there are also some differences:

1. Stateful: Unlike bors, which intends to be stateless, Homu is stateful. It
   means that Homu does not need to retrieve all the information again and again
   from GitHub at every run. This is essential because of the GitHub's rate
   limiting. Once it downloads the initial state, the following changes are
   delivered with the [Webhooks] API.
2. Pushing over polling: Homu prefers pushing wherever possible. The pull
   requests from GitHub are retrieved using Webhooks, as stated above. The test
   results from Buildbot are pushed back to Homu with the [HttpStatusPush]
   feature. This approach improves the overall performance and the response
   time, because the bot is informed about the status changes immediately.

And also, Homu has more features, such as `rollup`, `try`, and the Travis CI &
Appveyor support.

[bors]: https://github.com/graydon/bors
[Webhooks]: https://developer.github.com/webhooks/
[HttpStatusPush]: http://docs.buildbot.net/current/manual/cfg-statustargets.html#httpstatuspush

## Usage

### How to install

```sh
$ sudo apt-get install python3-venv python3-wheel
$ python3 -m venv .venv
$ . .venv/bin/activate
$ pip install -U pip
$ git clone https://github.com/rust-ops/homu.git
$ pip install -e homu
```

### How to configure

In the following instructions, `HOST` refers to the hostname (or IP address)
where you are running your custom homu instance. `PORT` is the port the service
is listening to and is configured in `web.port` in `cfg.toml`. `NAME` refers to
the name of the repository you are configuring homu for.

1. Copy `cfg.sample.toml` to `cfg.toml`. You'll need to edit this file to set up
   your configuration. The following steps explain where you can find important
   config values. 

2. Create a GitHub account that will be used by Homu. You can also use an
   existing account. In the [account settings][settings], go to "OAuth
   applications" and create a new application:
   - Make note of the "Client ID" and "Client Secret"; you will need to put them in
   your `cgf.toml`.
   - The OAuth Callback URL should be `http://HOST:PORT/callback`.
   - The homepage URL isn't necessary; you could set `http://HOST:PORT/`.
   
3. Go to the user settings of the GitHub account you created/used in the
   previous step. Go to "Personal access tokens". Click "Generate new token" and
   choose the "repo" and "user" scopes. Put the token value in your `cfg.toml`.
   
4. Add your new GitHub account as a Collaborator to the GitHub repo you are
   setting up homu for. This can be done in repo (NOT user) "Settings", then
   "Collaborators".
   
     4.1. Make sure you login as the new GitHub account and that you **accept 
          the collaborator invitation** you just sent! 

5. Add a Webhook to your repository. This is done under repo (NOT user)
   "Settings", then "Webhooks". Click "Add webhook", the set:
   - Payload URL: `http://HOST:PORT/github`
   - Content type: `application/json`
   - Secret: The same as `repo.NAME.github.secret` in `cfg.toml`
   - Events: `Issue Comment`, `Pull Request`, `Push`, `Status`, `Check runs`

6. Add a Webhook to your continuous integration service, if necessary. You don't
   need this if using Travis/Appveyor.
   - Buildbot 

     Insert the following code to the `master.cfg` file:

     ```python
     from buildbot.status.status_push import HttpStatusPush

     c['status'].append(HttpStatusPush(
        serverUrl='http://HOST:PORT/buildbot',
        extra_post_params={'secret': 'repo.NAME.buildbot.secret in cfg.toml'},
     ))
     ```

7. Go through the rest of your `cfg.toml` and uncomment (and change, if needed)
   parts of the config you'll need.

[settings]: https://github.com/settings/applications
[travis]: https://travis-ci.org/profile/info

### How to run

```sh
$ . .venv/bin/activate
$ homu
```
