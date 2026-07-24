# gigactl-cli-lib.sh — the plumbing gfan and gkbd share.
#
# Sourced, not executed. The caller sets TOOL (its own name, for messages) and
# `set -euo pipefail` before sourcing. The file lives next to the scripts in a
# checkout and in /usr/lib/gigactl once packaged; each script resolves it in that
# order. It exists because this plumbing drifted the moment it was duplicated —
# gfan grew a prop() helper while gkbd inlined the identical busctl call, within
# a single commit.

BUS=io.github.smairio.gigactl
OBJ=/io/github/smairio/gigactl
IFACE=$BUS.Control

# Overridable only so cli/ can exercise both modes as an ordinary user in a tmp
# directory; the defaults are the real locations. LOCK is the same path the
# daemon's ec.py takes — a different one would serialise nothing.
LOCK=${GIGACTL_EC_LOCK:-/run/lock/gigactl-ec.lock}
LOCK_WAIT=${GIGACTL_LOCK_WAIT:-10}

die() { echo "$TOOL: $*" >&2; exit 1; }

# --- which mode ---------------------------------------------------------------

# Sets MODE to daemon or direct. Anything short of a definite "the daemon is not
# running" refuses instead of falling back: the daemon is the single writer of
# the EC, and "could not tell" must never quietly become "wrote behind its back".
detect_mode() {
  command -v busctl >/dev/null 2>&1 || \
    die "busctl not found, so there is no way to tell whether the gigactl daemon is running — refusing to touch the EC behind its back. (busctl ships with systemd.)"
  local answer
  if ! answer=$(busctl --system call org.freedesktop.DBus /org/freedesktop/DBus \
                  org.freedesktop.DBus NameHasOwner s "$BUS" 2>&1); then
    die "cannot tell whether the gigactl daemon is running ($answer) — refusing to touch the EC behind its back."
  fi
  case "$answer" in
    "b true")  MODE=daemon ;;
    "b false") MODE=direct ;;
    *) die "cannot tell whether the gigactl daemon is running (busctl answered: $answer) — refusing to touch the EC behind its back." ;;
  esac
}

# Both modes implement the same verbs as <mode>_<verb>; this is the only place
# the choice is acted on, so no command can reach for the wrong backend.
op() { "${MODE}_$1" "${@:2}"; }

# --- daemon mode ----------------------------------------------------------------

# Call a control method. On failure CALL_ERROR holds the daemon's message and the
# return is non-zero — for the one caller (gfan's split-duty path) that has a
# recovery of its own. Everything else uses call(), which dies.
try_call() {
  local method=$1; shift
  if CALL_ERROR=$(busctl --system call "$BUS" "$OBJ" "$IFACE" "$method" "$@" 2>&1); then
    return 0
  fi
  CALL_ERROR=${CALL_ERROR#Call failed: }
  return 1
}

call() {
  try_call "$@" && return 0
  case "${CALL_ERROR,,}" in
    *authoriz*|*authenticat*)
      # polkit lets an active local session do this with no password; a remote
      # (SSH) session has no polkit agent to ask — but root is authorized
      # outright, so sudo is the remedy there.
      die "the daemon refused $1: $CALL_ERROR (from SSH or another remote session, re-run with sudo)" ;;
    *)
      die "the daemon refused $1: $CALL_ERROR" ;;
  esac
}

prop() { busctl --system get-property "$BUS" "$OBJ" "$IFACE" "$1" 2>/dev/null; }

# --- direct mode ----------------------------------------------------------------

# Hold the daemon's own EC lock for a whole write-and-verify sequence, so
# concurrent direct invocations serialise instead of interleaving three-write
# mailbox commands.
lock_ec() {
  exec 9>"$LOCK" || die "cannot open the EC lock $LOCK"
  flock -w "$LOCK_WAIT" 9 || \
    die "another gigactl EC writer is holding the lock $LOCK — try again."
}
