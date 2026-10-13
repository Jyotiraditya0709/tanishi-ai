#!/bin/bash
# G0 check for the Sealed Vault (AR4). Run as your normal user, WITHOUT sudo:  bash scripts/vault/check_vault.sh
# Every line must say PASS. It reads nothing from the vault: each "cannot" line expects a refusal.
set -u
VHOME=/Users/tanishi-vault
WRAP=/usr/local/libexec/tanishi-vault/vault-run
SHARED=/Users/Shared/tanishi-vault
fails=0
pass() { printf 'PASS  %s\n' "$1"; }
fail() { printf 'FAIL  %s\n' "$1"; fails=$((fails + 1)); }

[ "$(id -u)" -ne 0 ] || { echo "run this as your normal user, not with sudo"; exit 2; }

[ "$(stat -f '%Su %Lp' "$VHOME" 2>/dev/null)" = "tanishi-vault 700" ] \
  && pass "vault home is owned by tanishi-vault, mode 700" || fail "vault home owner or mode is wrong"
ls "$VHOME" >/dev/null 2>&1 && fail "you can list the vault home" || pass "you cannot list the vault home"
cat "$VHOME/sealed/checks.py" >/dev/null 2>&1 && fail "you can read the checks" || pass "you cannot read the checks"
ls "$VHOME/sealed/tasks" >/dev/null 2>&1 && fail "you can list the tasks" || pass "you cannot list the tasks"
sudo -n -u tanishi-vault /bin/cat "$VHOME/sealed/checks.py" >/dev/null 2>&1 \
  && fail "sudo lets you run other commands as the vault" || pass "sudo allows only the wrapper"
[ "$(stat -f '%Su' "$WRAP" 2>/dev/null)" = "root" ] && pass "the wrapper is root-owned" || fail "the wrapper is not root-owned"

status=$(sudo -n -u tanishi-vault "$WRAP" status 2>/dev/null)
echo "$status" | grep -q '"installed": true' && pass "the vault answers: $status" || fail "the vault did not answer status"
echo "$status" | grep -q '"leak": false' && pass "no leak is flagged" || fail "a leak is flagged (read $SHARED/scan/last.json)"

if [ -f "$SHARED/scan/last.json" ] && grep -q '"clean": true' "$SHARED/scan/last.json"; then
  pass "last canary scan is clean ($(grep -o '"started": "[^"]*"' "$SHARED/scan/last.json"))"
else
  fail "last canary scan is missing or not clean"
fi
launchctl print system/com.tanishi.canary-scan >/dev/null 2>&1 \
  && pass "the nightly scan is loaded" || fail "the nightly scan is not loaded"

echo
[ "$fails" -eq 0 ] && echo "ALL PASS" || echo "$fails FAIL"
exit "$fails"
