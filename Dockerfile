FROM ubuntu:focal
# We need an older Ubuntu as github3 depends on < Python 3.10 to avoid errors

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    python3-pip \
    git \
    ssh

COPY setup.py cfg.production.toml /src/
COPY homu/ /src/homu/
COPY requirements.txt /src/

# Pre-install dependencies from a lockfile
RUN pip3 install -r /src/requirements.txt

# Homu needs to be installed in "editable mode" (-e): when pip installs an
# application it resets the permissions of all source files to 644, but
# homu/git_helper.py needs to be executable (755). Installing in editable mode
# works around the issue since pip just symlinks the package to the source
# directory.
RUN pip3 install -e /src/

# Ensure the host SSH key for github.com is trusted by the container. If this
# is not run, homu will fail to authenticate SSH connections with GitHub.
RUN mkdir /root/.ssh && \
    ssh-keyscan github.com >> /root/.ssh/known_hosts

# Allow logs to show up timely on CloudWatch.
ENV PYTHONUNBUFFERED=1

CMD ["homu", "--verbose", "--config", "/src/cfg.production.toml"]
