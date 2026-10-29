#!/usr/bin/env bash
# Verifier entry point. Runs as root. The candidate program only ever runs as
# nobody (see verify.py), and the reward directory stays root-only until every
# nobody process is gone.
set -u

mkdir -p /logs/verifier
owner="$(stat -c '%u:%g' /logs/verifier)"
mode="$(stat -c '%a' /logs/verifier)"
chown root:root /logs/verifier
chmod 700 /logs/verifier
chmod 700 /tests

python3 /tests/verify.py
if [ ! -s /logs/verifier/reward.txt ]; then
  echo 0 > /logs/verifier/reward.txt
fi

chown -R "$owner" /logs/verifier
chmod "$mode" /logs/verifier
exit 0
