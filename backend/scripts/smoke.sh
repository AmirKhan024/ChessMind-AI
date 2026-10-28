#!/bin/sh
# End-to-end: upload real games, wait until Stockfish analysis finishes.
set -e
API=${API:-http://localhost:8000}
FX=$(dirname "$0")/../tests/fixtures/games.ndjson
head -3 "$FX" | ${PYTHON:-python3} -c "import sys,json; print('\n\n'.join(json.loads(l)['pgn'] for l in sys.stdin))" > "${TMPDIR:-/tmp}/smoke.pgn"
JOB=$(curl -fsS -F username=thibault -F file=@"${TMPDIR:-/tmp}/smoke.pgn" "$API/upload" | ${PYTHON:-python3} -c "import sys,json; print(json.load(sys.stdin)['job_id'])")
for i in $(seq 1 60); do
  S=$(curl -fsS "$API/jobs/$JOB")
  echo "$S"
  echo "$S" | grep -q '"status":"done"' && exit 0
  echo "$S" | grep -q '"status":"failed"' && exit 1
  sleep 5
done
exit 1
