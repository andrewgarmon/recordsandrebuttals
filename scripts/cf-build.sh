#!/usr/bin/env sh
# Deploy command for Cloudflare Workers Builds.
#
#   Production deploy command:      sh scripts/cf-build.sh deploy
#   Non-production branch command:  sh scripts/cf-build.sh preview
#
# Python Workers need the `workers` SDK bundled with the code, which
# pywrangler does on deploy/preview (plain `wrangler deploy` doesn't, and the
# Worker fails at startup with "No module named 'workers'"). pywrangler needs
# uv >= 0.13.4, newer than the build image may have.
set -eu

command="${1:-deploy}"
[ "$#" -gt 0 ] && shift
case "$command" in
  deploy|preview) ;;
  *) echo "usage: $0 deploy|preview" >&2; exit 2 ;;
esac

python3 -m pip install --quiet --disable-pip-version-check "uv>=0.13.4"

# Call the uv binary directly and put it first on PATH. `python3 -m uv` would
# make every uv command pywrangler spawns target this python3 instead of the
# Worker's environments. pywrangler looks `uv` up on PATH.
uv_bin="$(python3 -c 'import uv; print(uv.find_uv_bin())')"
PATH="$(dirname "$uv_bin"):$PATH"
export PATH

# Python 3.14 to match the Workers runtime.
uv run --python 3.14 --group dev pywrangler "$command" "$@"
