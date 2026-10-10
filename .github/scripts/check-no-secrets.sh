#!/usr/bin/env bash
# Fails if an env or key file is committed, or if a tracked file holds a real
# database password. Placeholders ($VAR, PASSWORD, ...) and the CI test login are allowed.
set -euo pipefail
status=0
bad_files=$(git ls-files | grep -E '(^|/)(\.env[^/]*|[^/]*\.env|[^/]*\.env\.[^/]*|[^/]*\.(pem|key))$' | grep -v '\.example$' || true)
if [ -n "$bad_files" ]; then
  echo "Env or key files must not be committed:"; echo "$bad_files"; status=1
fi
bad_urls=$(git grep -n -E 'postgres(ql)?://[A-Za-z0-9_]+:[^@[:space:]]+@' -- . \
  | grep -v -E '://[A-Za-z0-9_]+:(\$[A-Za-z_{]|PASSWORD|\.\.\.|postgres@localhost)' || true)
if [ -n "$bad_urls" ]; then
  echo "Possible database passwords in tracked files:"; echo "$bad_urls"; status=1
fi
[ "$status" -eq 0 ] && echo "No env files or passwords tracked."
exit "$status"
