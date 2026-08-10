#!/usr/bin/env bash
#
# Create (or replace) the ib-oauth-keys Kubernetes secret holding every IBKR
# Web API OAuth 1.0a credential: the four string values plus the two private
# PEMs the CronJob mounts at /etc/ibkr.
#
#   ./scripts/create_ib_oauth_secret.sh [key-dir]
#
# key-dir defaults to ~/ibkr-oauth-paper and must contain:
#   oauth.env                 KEY=VALUE per line (see README)
#   private_encryption.pem
#   private_signature.pem
#
# Why a script and not one kubectl invocation: `kubectl create secret generic`
# rejects --from-env-file combined with --from-file, so a secret that mixes
# string values and file content cannot be built in a single call. This assembles
# the manifest and pipes it to `apply`, which also makes re-running it a clean
# update instead of an "already exists" error.
#
# Nothing secret is echoed, and no plaintext is written anywhere: the manifest
# goes to kubectl over a pipe, never to disk.
set -euo pipefail

KEY_DIR="${1:-$HOME/ibkr-oauth-paper}"
CTX="${KUBE_CONTEXT:-luxvps}"
NS="${NAMESPACE:-tradingbots-2025}"
SECRET="${SECRET_NAME:-ib-oauth-keys}"

cd "$KEY_DIR"

for f in oauth.env private_encryption.pem private_signature.pem; do
    [ -f "$f" ] || { echo "missing $KEY_DIR/$f" >&2; exit 1; }
done

manifest() {
    SECRET="$SECRET" NS="$NS" python3 - <<'PY'
import base64, json, os, pathlib, re, sys

REQUIRED = [
    "IBIND_OAUTH1A_CONSUMER_KEY",
    "IBIND_OAUTH1A_ACCESS_TOKEN",
    "IBIND_OAUTH1A_ACCESS_TOKEN_SECRET",
    "IBIND_OAUTH1A_DH_PRIME",
    "IB_ACCOUNT_ID",
]

data = {}
for line in pathlib.Path("oauth.env").read_text().splitlines():
    line = line.strip()
    if not line or line.startswith("#"):
        continue
    if "=" not in line:
        sys.exit(f"malformed line in oauth.env: {line[:20]}...")
    k, v = line.split("=", 1)
    # A trailing newline or stray space inside a token fails IBKR auth with a
    # useless error, so strip here rather than debug it at 21:20 UTC.
    data[k.strip()] = v.strip()

missing = [k for k in REQUIRED if not data.get(k)]
if missing:
    sys.exit("empty or absent in oauth.env: " + ", ".join(missing))

ck = data["IBIND_OAUTH1A_CONSUMER_KEY"]
if len(ck) != 9:
    sys.exit(f"consumer key must be exactly 9 characters, got {len(ck)}")

for pem in ("private_encryption.pem", "private_signature.pem"):
    data[pem] = pathlib.Path(pem).read_text()

print(json.dumps({
    "apiVersion": "v1",
    "kind": "Secret",
    "type": "Opaque",
    "metadata": {"name": os.environ["SECRET"], "namespace": os.environ["NS"]},
    "data": {k: base64.b64encode(v.encode()).decode() for k, v in data.items()},
}))
PY
}

manifest | kubectl --context="$CTX" -n "$NS" apply -f - >/dev/null

echo "applied secret $SECRET in $NS (context $CTX):"
kubectl --context="$CTX" -n "$NS" get secret "$SECRET" -o json \
  | python3 -c "
import base64, json, sys
for k, v in sorted(json.load(sys.stdin)['data'].items()):
    print(f'  {k:38} {len(base64.b64decode(v))} bytes')
"
echo
echo "Values verified by length only — nothing printed. Now delete the plaintext:"
echo "  shred -u $KEY_DIR/oauth.env"
