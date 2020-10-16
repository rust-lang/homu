FROM ubuntu:focal

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    python3-pip \
    git

COPY setup.py cfg.production.toml /src/
COPY homu/ /src/homu/

RUN pip3 install /src/

# Allow logs to show up timely on CloudWatch.
ENV PYTHONUNBUFFERED=1

CMD ["homu", "--config", "/src/cfg.production.toml"]
