#!/bin/bash
# Sets up the Sealed Vault (AR4) on this Mac. Run once, as your normal user, with sudo:
#
#   sudo bash scripts/vault/setup_vault.sh --bundle ~/Downloads/tanishi-vault.enc --commit <main commit sha>
#
# It asks for the bundle's passphrase once (typed, never echoed, never on a command line). Steps:
#   1. a hidden OS user `tanishi-vault` whose home is 0700 (no other user can read it)
#   2. that user's own runtime: a clone of the public repo at <commit>, a venv, tanishi installed (not editable)
#   3. the sealed tasks and checks, decrypted straight into the vault's home (plaintext never touches your folders)
#   4. root-owned tools in /usr/local/libexec/tanishi-vault: the wrapper `vault-run` and the canary scanner
#   5. one sudo rule: you may run `vault-run` as tanishi-vault, nothing else
#   6. the shared folder /Users/Shared/tanishi-vault (scores and scan reports, readable by you)
#   7. a nightly canary scan (root LaunchDaemon, 03:17) that alerts on Telegram and locks the vault on a hit
#   8. a first scan and the selftest
# Safe to run again: every step checks what is already there.
set -euo pipefail

VAULT=tanishi-vault
VHOME=/Users/$VAULT
LIBEXEC=/usr/local/libexec/tanishi-vault
ETC=/usr/local/etc/tanishi-vault
SHARED=/Users/Shared/tanishi-vault
PLIST=/Library/LaunchDaemons/com.tanishi.canary-scan.plist
REPO_URL=https://github.com/Jyotiraditya0709/tanishi-ai.git
BUNDLE=""
COMMIT=""

while [ $# -gt 0 ]; do
  case "$1" in
    --bundle) BUNDLE="$2"; shift 2 ;;
    --commit) COMMIT="$2"; shift 2 ;;
    *) echo "unknown option: $1"; exit 2 ;;
  esac
done

say() { printf '\n== %s\n' "$*"; }
die() { printf 'STOP: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run with sudo"
OWNER="${SUDO_USER:-}"
[ -n "$OWNER" ] && [ "$OWNER" != root ] || die "run with sudo from your normal user, not as root directly"
OWNER_HOME=$(dscl . -read "/Users/$OWNER" NFSHomeDirectory | awk '{print $2}')
[ -n "$COMMIT" ] || die "--commit <sha of main that holds tanishi/arena/sealed.py> is required"

PY=""
for v in 3.13 3.12 3.11; do
  if [ -x "/Library/Frameworks/Python.framework/Versions/$v/bin/python3" ]; then
    PY="/Library/Frameworks/Python.framework/Versions/$v/bin/python3"; break
  fi
done
[ -n "$PY" ] || die "no python.org Python 3.11+ in /Library/Frameworks (the vault cannot use your own Homebrew)"
as_vault() { sudo -H -u "$VAULT" "$@"; }

say "1. the vault user"
if ! dscl . -read "/Users/$VAULT" >/dev/null 2>&1; then
  uid=""
  for n in $(seq 401 499); do
    if ! dscl . -search /Users UniqueID "$n" | grep -q .; then uid=$n; break; fi
  done
  [ -n "$uid" ] || die "no free hidden user id between 401 and 499"
  dscl . -create "/Users/$VAULT"
  dscl . -create "/Users/$VAULT" UserShell /usr/bin/false
  dscl . -create "/Users/$VAULT" RealName "Tanishi Vault"
  dscl . -create "/Users/$VAULT" UniqueID "$uid"
  dscl . -create "/Users/$VAULT" PrimaryGroupID 20
  dscl . -create "/Users/$VAULT" NFSHomeDirectory "$VHOME"
  dscl . -create "/Users/$VAULT" IsHidden 1
  dscl . -create "/Users/$VAULT" Password '*'
  echo "created $VAULT (uid $uid, hidden, no password, no login shell)"
else
  echo "$VAULT already exists"
fi
mkdir -p "$VHOME"
chown "$VAULT":staff "$VHOME"
chmod 700 "$VHOME"
echo "home $VHOME: $(stat -f '%Su %Lp' "$VHOME")"

say "2. the vault's own runtime at $COMMIT"
if [ ! -d "$VHOME/runtime/src/.git" ]; then
  as_vault mkdir -p "$VHOME/runtime"
  as_vault git clone --quiet "$REPO_URL" "$VHOME/runtime/src"
fi
as_vault git -C "$VHOME/runtime/src" fetch --quiet origin
as_vault git -C "$VHOME/runtime/src" -c advice.detachedHead=false checkout --quiet "$COMMIT"
[ -x "$VHOME/runtime/venv/bin/python" ] || as_vault "$PY" -m venv "$VHOME/runtime/venv"
as_vault "$VHOME/runtime/venv/bin/pip" install --quiet --upgrade pip
as_vault "$VHOME/runtime/venv/bin/pip" install --quiet --no-cache-dir --force-reinstall --no-deps "$VHOME/runtime/src"
as_vault "$VHOME/runtime/venv/bin/pip" install --quiet "$VHOME/runtime/src" certifi
as_vault "$VHOME/runtime/venv/bin/python" -I -c "import tanishi.arena.sealed as s; print('runtime ok:', s.__file__)"

say "3. the sealed tasks"
if [ -n "$BUNDLE" ]; then
  [ -f "$BUNDLE" ] || die "bundle not found: $BUNDLE"
  [ ! -e "$VHOME/sealed/tasks" ] || die "$VHOME/sealed already holds tasks; refusing to overwrite (rotate by hand)"
  echo "Type the bundle passphrase (it is not shown):"
  openssl enc -d -aes-256-cbc -md sha256 -in "$BUNDLE" | as_vault tar -xzf - -C "$VHOME"
  as_vault /bin/sh -c 'chmod 700 ~/sealed ~/sealed/tasks ~/selftest && chmod 600 ~/sealed/checks.py ~/sealed/tasks/*.yaml ~/selftest/*.py'
  echo "tasks installed: $(as_vault /bin/sh -c 'ls ~/sealed/tasks/*.yaml | wc -l')"
else
  echo "no --bundle given; leaving $VHOME/sealed as it is"
fi

say "4. root-owned tools"
mkdir -p "$LIBEXEC" "$ETC"
chown root:wheel "$LIBEXEC" "$ETC"
chmod 755 "$LIBEXEC"
chmod 700 "$ETC"
install -m 755 -o root -g wheel "$VHOME/runtime/src/tanishi/arena/canary_scan.py" "$LIBEXEC/canary_scan.py"
install -m 755 -o root -g wheel "$VHOME/runtime/src/scripts/vault/vault-run" "$LIBEXEC/vault-run"
echo "installed $LIBEXEC/vault-run and $LIBEXEC/canary_scan.py"

say "5. the sudo rule"
RULE=/etc/sudoers.d/tanishi-vault
TMP=$(mktemp)
printf '%s ALL=(%s) NOPASSWD: %s/vault-run\n' "$OWNER" "$VAULT" "$LIBEXEC" > "$TMP"
visudo -cf "$TMP" >/dev/null || die "the sudo rule did not validate"
install -m 440 -o root -g wheel "$TMP" "$RULE"
rm -f "$TMP"
echo "$OWNER may run only $LIBEXEC/vault-run as $VAULT"

say "6. the shared folder"
mkdir -p "$SHARED/reads" "$SHARED/scan"
chown root:wheel "$SHARED" "$SHARED/scan"
chown "$VAULT":staff "$SHARED/reads"
chmod 755 "$SHARED" "$SHARED/reads" "$SHARED/scan"
echo "$SHARED ready"

say "7. the nightly canary scan"
if [ -f "$OWNER_HOME/.tanishi-warden/telegram.env" ]; then
  install -m 600 -o root -g wheel "$OWNER_HOME/.tanishi-warden/telegram.env" "$ETC/telegram.env"
  echo "Telegram alerts: on (settings copied into a root-only file)"
else
  echo "Telegram alerts: off (no $OWNER_HOME/.tanishi-warden/telegram.env)"
fi
args=""
for t in "$OWNER_HOME/Personal/tanishi-ai" "$OWNER_HOME/Personal/tanishi-worktrees" "$OWNER_HOME/.tanishi" \
         "$OWNER_HOME/.claude/projects"; do
  args="$args<string>--target</string><string>$t</string>"
done
cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.tanishi.canary-scan</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string><string>$LIBEXEC/canary_scan.py</string>
    $args
    <string>--git</string><string>$OWNER_HOME/Personal/tanishi-ai</string>
    <string>--git-as</string><string>$OWNER</string>
    <string>--report</string><string>$SHARED/scan/last.json</string>
    <string>--leak-file</string><string>$SHARED/LEAK</string>
    <string>--telegram-env</string><string>$ETC/telegram.env</string>
  </array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>3</integer><key>Minute</key><integer>17</integer></dict>
  <key>StandardOutPath</key><string>$SHARED/scan/launchd.log</string>
  <key>StandardErrorPath</key><string>$SHARED/scan/launchd.log</string>
</dict>
</plist>
PLISTEOF
chown root:wheel "$PLIST"
chmod 644 "$PLIST"
plutil -lint "$PLIST" >/dev/null
launchctl bootout system "$PLIST" 2>/dev/null || true
launchctl bootstrap system "$PLIST"
echo "nightly scan loaded (03:17 every day)"

say "8. first scan and selftest"
/usr/bin/python3 "$LIBEXEC/canary_scan.py" \
  --target "$OWNER_HOME/Personal/tanishi-ai" --target "$OWNER_HOME/Personal/tanishi-worktrees" \
  --target "$OWNER_HOME/.tanishi" --target "$OWNER_HOME/.claude/projects" \
  --git "$OWNER_HOME/Personal/tanishi-ai" --git-as "$OWNER" \
  --report "$SHARED/scan/last.json" --leak-file "$SHARED/LEAK" || echo "SCAN DID NOT COME BACK CLEAN: read $SHARED/scan/last.json"
if as_vault test -f "$VHOME/selftest/selftest.py"; then
  (cd "$VHOME" && as_vault "$VHOME/runtime/venv/bin/python" -I "$VHOME/selftest/selftest.py" --home "$VHOME") \
    || echo "SELFTEST FAILED"
fi

say "done"
echo "Now run, as yourself (no sudo):  bash scripts/vault/check_vault.sh"
