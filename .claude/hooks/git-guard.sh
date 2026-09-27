#!/usr/bin/env bash
# PreToolUse guard for Bash calls that invoke git.
#
# PURPOSE AND LIMITS
# This is a guardrail against mistakes, and a reminder of the workflow in
# .claude/rules/git-workflow.md. It is NOT a security boundary. It inspects the
# text of a Bash command, and arbitrary shell has unlimited ways to spell the
# same action (bash -c, eval, aliases, absolute paths, xargs). Do not try to
# close that class of gap here.
#
# The real enforcement lives in two places that do not care how git was invoked:
#   - .githooks/pre-commit and .githooks/pre-push  (git itself enforces)
#   - GitHub branch protection on main             (server side, unbypassable)
#
# Fails open by design: any unexpected condition allows the call.

set -uo pipefail

PROTECTED='^(main|master)$'

INPUT=$(cat)
command -v jq >/dev/null 2>&1 || exit 0

TOOL=$(jq -r '.tool_name // empty' <<<"$INPUT" 2>/dev/null) || exit 0
[ "$TOOL" = "Bash" ] || exit 0
CMD=$(jq -r '.tool_input.command // empty' <<<"$INPUT" 2>/dev/null) || exit 0
[ -n "$CMD" ] || exit 0

deny() {
  jq -n --arg r "$1" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
  exit 0
}

# ---------------------------------------------------------------------------
# Strip heredoc BODIES only. Commands after the heredoc terminator still count.
# ---------------------------------------------------------------------------
SCAN=""
DELIM=""
IN_HD=0
while IFS= read -r LINE || [ -n "$LINE" ]; do
  if [ "$IN_HD" -eq 1 ]; then
    TRIM="${LINE#"${LINE%%[![:space:]]*}"}"
    TRIM="${TRIM%"${TRIM##*[![:space:]]}"}"
    [ "$TRIM" = "$DELIM" ] && IN_HD=0
    continue
  fi
  if [[ "$LINE" =~ \<\<-?[[:space:]]*[\'\"]?([A-Za-z_][A-Za-z0-9_]*)[\'\"]? ]]; then
    DELIM="${BASH_REMATCH[1]}"
    IN_HD=1
    SCAN+="${LINE%%<<*}"$'\n'
    continue
  fi
  SCAN+="$LINE"$'\n'
done <<< "$CMD"

# ---------------------------------------------------------------------------
# Split into candidate commands. Avoid mapfile (bash 3.2 lacks it).
# ---------------------------------------------------------------------------
SEGMENTS=()
while IFS= read -r S; do SEGMENTS+=("$S"); done < <(printf '%s\n' "$SCAN" | tr ';|&\n' '\n\n\n\n')

WD="."
branch_of() {
  local d="$1" b
  b=$(git -C "$d" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
  if [ "$b" = "HEAD" ] && ! git -C "$d" rev-parse HEAD >/dev/null 2>&1; then
    b=$(git -C "$d" symbolic-ref --short HEAD 2>/dev/null || echo "")
  fi
  printf '%s' "$b"
}

# Does a short-flag cluster (e.g. -fu) contain this letter?
has_short() {
  local letter="$1"; shift
  local t
  for t in "$@"; do
    case "$t" in
      --*) continue ;;
      -*) [[ "${t:1}" == *"$letter"* ]] && return 0 ;;
    esac
  done
  return 1
}

for SEG in "${SEGMENTS[@]}"; do
  SEG="${SEG#"${SEG%%[![:space:]]*}"}"
  [ -n "$SEG" ] || continue

  while [[ "$SEG" =~ ^[A-Za-z_][A-Za-z0-9_]*=[^[:space:]]*[[:space:]]+(.*)$ ]]; do SEG="${BASH_REMATCH[1]}"; done
  [[ "$SEG" =~ ^sudo[[:space:]]+(.*)$ ]] && SEG="${BASH_REMATCH[1]}"

  # Track `cd` so a later git in the same chain is judged in the right repo.
  if [[ "$SEG" =~ ^cd[[:space:]]+([^[:space:]]+) ]]; then
    NEW="${BASH_REMATCH[1]//\"/}"; NEW="${NEW//\'/}"
    [ -d "$NEW" ] && WD="$NEW"
    continue
  fi

  [[ "$SEG" =~ ^git([[:space:]]|$) ]] || continue

  # shellcheck disable=SC2206
  TOKENS=($SEG)
  SUB=""; GITDIR="$WD"; NOHOOKS=0; i=1
  while [ $i -lt ${#TOKENS[@]} ]; do
    case "${TOKENS[$i]}" in
      -C) GITDIR="${TOKENS[$((i+1))]:-$WD}"; i=$((i+2)) ;;
      # `-c core.hooksPath=...` disables layer 2 the same way --no-verify does.
      -c) [[ "${TOKENS[$((i+1))]:-}" == core.hooksPath=* ]] && NOHOOKS=1; i=$((i+2)) ;;
      --git-dir|--work-tree|--namespace) i=$((i+2)) ;;
      -*) i=$((i+1)) ;;
      *) SUB="${TOKENS[$i]}"; break ;;
    esac
  done
  [ -n "$SUB" ] || continue

  ARGS=("${TOKENS[@]:$((i+1))}")
  # `git <sub> --help` runs nothing.
  [[ " ${ARGS[*]} " == *" --help "* ]] && continue
  BRANCH=$(branch_of "$GITDIR")

  case "$SUB" in
    commit)
      if [ "$NOHOOKS" -eq 1 ] || has_short n "${ARGS[@]}" || [[ " ${ARGS[*]} " == *" --no-verify "* ]]; then
        deny "Blocked: --no-verify (or overriding core.hooksPath) skips the repository's own pre-commit hook. Fix what the hook is complaining about instead."
      fi
      [[ "$BRANCH" =~ $PROTECTED ]] && deny "Blocked: you are on '$BRANCH', which is protected. Per .claude/rules/git-workflow.md, branch first — git switch -c <type>/<short-description> — then commit there and open a PR."
      ;;

    push)
      if has_short f "${ARGS[@]}" || [[ " ${ARGS[*]} " == *" --force "* ]] || [[ " ${ARGS[*]} " == *" --force-with-lease "* ]]; then
        deny "Blocked: force push. A branch under review must never be force-pushed. Push a follow-up commit and squash at merge time. If a rewrite is genuinely required, ask Robert first."
      fi
      if [ "$NOHOOKS" -eq 1 ] || has_short n "${ARGS[@]}" || [[ " ${ARGS[*]} " == *" --no-verify "* ]]; then
        deny "Blocked: --no-verify (or overriding core.hooksPath) skips the repository's own pre-push hook. Fix the underlying problem instead."
      fi
      if [[ " ${ARGS[*]} " == *" --delete "* ]] || has_short d "${ARGS[@]}"; then
        deny "Blocked: deleting a remote branch. Ask Robert first."
      fi
      # Examine every refspec's DESTINATION. Covers main, :main, HEAD:main,
      # +main:main and feat/x:main in one rule. A bare HEAD resolves to the
      # current branch, so `git push -u origin HEAD` on main is caught too.
      SAW_REF=0
      for A in "${ARGS[@]}"; do
        # Unwrap a fully quoted token ("main" -> main). A dangling quote is
        # left alone: it means the segment came from inside a string.
        [[ "$A" =~ ^[\"\'](.*)[\"\']$ ]] && A="${BASH_REMATCH[1]}"
        case "$A" in -*|"") continue ;; esac
        [ "$A" = "origin" ] && continue
        SAW_REF=1
        DEST="${A##*:}"; DEST="${DEST#+}"; DEST="${DEST#refs/heads/}"
        [ "$DEST" = "HEAD" ] && DEST="$BRANCH"
        [[ "$DEST" =~ $PROTECTED ]] && deny "Blocked: this push targets a protected branch ('$DEST'). Push your feature branch and open a pull request instead: git push -u origin HEAD && gh pr create"
      done
      if [ "$SAW_REF" -eq 0 ] && [[ "$BRANCH" =~ $PROTECTED ]]; then
        deny "Blocked: you are on '$BRANCH' and a bare push would go to it. Move the work to a feature branch and open a pull request."
      fi
      ;;

    reset)
      [[ " ${ARGS[*]} " == *" --hard "* ]] && deny "Blocked: git reset --hard discards work irrecoverably. Say what you are trying to achieve and ask Robert before running it."
      ;;

    filter-branch)
      deny "Blocked: git filter-branch rewrites history. Ask Robert first."
      ;;

    branch)
      has_short D "${ARGS[@]}" && deny "Blocked: force-deleting a branch can discard unmerged commits. Use -d, or ask Robert if the branch really should go."
      ;;

    reflog)
      [ "${ARGS[0]:-}" = "delete" ] && deny "Blocked: deleting reflog entries removes the last safety net for recovering lost commits. Ask Robert first."
      ;;
  esac
done

exit 0
