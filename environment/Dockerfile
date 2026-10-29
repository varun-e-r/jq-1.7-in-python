# Agent image: Python 3.12 with the standard library only, the starter entry
# point, the recorded examples and the example runner. No jq anywhere.
FROM python:3.12-slim-bookworm@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337

ENV PYTHONDONTWRITEBYTECODE=1 LANG=C.UTF-8
WORKDIR /app
COPY workspace/ /app/
RUN chmod +x /app/jq /app/run_examples.py
