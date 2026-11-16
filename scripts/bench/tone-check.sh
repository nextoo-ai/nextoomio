#!/bin/sh
# scripts/bench/tone-check.sh: Remote bench audio tone check runner
# Implements ARCHITECTURE.md section 4.1, SPECIFICATION.md section 2.1, P0-F6.2, D-012

set -u

BENCH_HOST="${NEXTOOMIO_BENCH_HOST:-nextoomio-bench.local}"
SSH_PORT="22"
SSH_USER="root"
SSH_KEY=""
TIMEOUT="10"
RUN_LOCAL=0
FORWARD_ARGS=""

usage() {
    cat << EOF
Usage: scripts/bench/tone-check.sh [OPTIONS] [HOST]

Executes the remote audio tone check on the bench device over SSH
and reports the observed stream state and hardware frame advancement.

Options:
  --host <host>       Bench device hostname or IP (default: \$NEXTOOMIO_BENCH_HOST or nextoomio-bench.local)
  -p, --port <port>   SSH port (default: 22)
  -u, --user <user>   SSH user (default: root)
  -i, --identity <k>  SSH private key file
  --timeout <sec>     SSH connection timeout in seconds (default: 10)
  --json              Request structured JSON output from remote check
  --quiet             Suppress progress output
  --local             Run tone check locally on workstation (for testing)
  -h, --help          Show this help message
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --host)
            BENCH_HOST="$2"
            shift 2
            ;;
        -p|--port)
            SSH_PORT="$2"
            shift 2
            ;;
        -u|--user)
            SSH_USER="$2"
            shift 2
            ;;
        -i|--identity)
            SSH_KEY="$2"
            shift 2
            ;;
        --timeout)
            TIMEOUT="$2"
            shift 2
            ;;
        --json)
            FORWARD_ARGS="${FORWARD_ARGS} --json"
            shift
            ;;
        --quiet)
            FORWARD_ARGS="${FORWARD_ARGS} --quiet"
            shift
            ;;
        --local)
            RUN_LOCAL=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        -*)
            FORWARD_ARGS="${FORWARD_ARGS} $1"
            shift
            ;;
        *)
            BENCH_HOST="$1"
            shift
            ;;
    esac
done

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(cd "${SCRIPT_DIR}/../.." && pwd)

if [ "$RUN_LOCAL" -eq 1 ]; then
    # Run locally using script from recipe tree
    LOCAL_BIN="${REPO_ROOT}/meta-nextoomio/recipes-nextoomio/nextoomio-tone-check/files/nextoomio-tone-check.sh"
    if [ ! -f "$LOCAL_BIN" ]; then
        echo "scripts/bench/tone-check.sh: Local binary '$LOCAL_BIN' not found." >&2
        exit 1
    fi
    exec /bin/sh "$LOCAL_BIN" ${FORWARD_ARGS}
fi

# Build SSH command line
SSH_OPTS="-o BatchMode=yes -o ConnectTimeout=${TIMEOUT} -p ${SSH_PORT}"
if [ -n "$SSH_KEY" ]; then
    SSH_OPTS="${SSH_OPTS} -i ${SSH_KEY}"
fi

# Execute remote tone check over SSH
ssh ${SSH_OPTS} "${SSH_USER}@${BENCH_HOST}" "nextoomio-tone-check ${FORWARD_ARGS}"
EXIT_CODE=$?

exit "$EXIT_CODE"
