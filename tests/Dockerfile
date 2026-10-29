# Verifier image: the hidden cases (recorded from jq 1.7.1) and the scorer.
# Same Python as the agent image, nothing else installed, so the candidate is
# graded with exactly the standard library it was written against.
FROM python:3.12-slim-bookworm@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337

COPY . /tests/
RUN rm -rf /tests/tools \
    && mkdir -p /app \
    && chmod 700 /tests
