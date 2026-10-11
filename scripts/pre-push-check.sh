#!/usr/bin/env bash
# pre-push-check.sh - refuse to push a repo that leaks local or secret data.
#
# Usage:
#   scripts/pre-push-check.sh [repo-path]     run by hand (default: current repo)
#   scripts/install-pre-push.sh <repo-path>   install as .git/hooks/pre-push
#
# What is scanned
#   1. the git-tracked files at HEAD, always;
#   2. the pushed commit range, when git feeds ref lines on stdin (hook mode):
#        <local ref> <local sha> <remote ref> <remote sha>
#      - remote sha all zeros (new branch): commits reachable from <local sha>
#        but not from any remote-tracking ref (git rev-list <local> --not --remotes)
#      - otherwise <remote sha>..<local sha>
#        (a remote sha unknown locally is treated like a new branch)
#      - local sha all zeros (ref deletion): skipped
#      For every commit in the range the ADDED lines are scanned with the same
#      patterns, and a commit that ADDED a forbidden file is a finding even when
#      a later commit deleted it again (the file stays in the pushed history).
#   Manual run (stdin is a terminal, empty, or has no ref lines): HEAD only.
#   Commit author e-mails are NOT scanned.
#
# Exit 0 = clean, exit 1 = findings. Bypass once with `git push --no-verify`.
#
# Checks
#   local-path   absolute user paths: /Users/<name>/, /home/<name>/, C:\Users\<name>\
#   email        e-mail addresses, except *@users.noreply.github.com and
#                *@example.com / *@example.org / *@example.net
#   secret       private key headers, ghp_/gho_/ghu_/ghs_/ghr_ and github_pat_ tokens,
#                AKIA... AWS key ids, sk-... style keys, password=... literals
#   forbidden    tracked .env*, anything under data/ except data/README.md, *.pdf
#   range-error  the commits of a pushed ref could not be listed (push is blocked)
#
# Exceptions: optional file `.prepush-allow` in the repo root (working tree).
# One extended regex per line (grep -E); blank lines and lines starting with #
# are ignored. A finding is skipped when any regex matches its record:
#   content findings:  <path>:<line>:<matched text>
#   forbidden files:   <path>
# For range findings <line> is the line number in the commit that added it.
# Examples:
#   ^docs/examples/                                # everything under a sample dir
#   ^\.env\.example$                               # a committed env template
# The .prepush-allow file itself is not scanned. Secret values are never printed.
#
# POSIX-ish: bash + git + grep/sed/awk only; works with the macOS defaults.

repo_arg="${1:-.}"
if [ -d "$repo_arg" ]; then
  root="$(git -C "$repo_arg" rev-parse --show-toplevel 2>/dev/null)" || root=""
else
  # invoked by git as a hook: args are <remote-name> <remote-url>
  root="$(git rev-parse --show-toplevel 2>/dev/null)" || root=""
fi
if [ -z "$root" ]; then
  echo "pre-push-check: not inside a git repository" >&2
  exit 1
fi

tmp="$(mktemp -d "${TMPDIR:-/tmp}/prepush.XXXXXX")" || exit 1
trap 'rm -rf "$tmp"' EXIT

# Pushed refs arrive on stdin. Read them before anything else; never wait on a
# terminal, and give a silent inherited pipe only a few seconds.
refs="$tmp/refs"
: > "$refs"
if [ ! -t 0 ]; then
  while IFS= read -r -t 3 line || [ -n "$line" ]; do
    printf '%s\n' "$line" >> "$refs"
    line=""
  done
fi

cd "$root" || exit 1

if ! git rev-parse --verify -q HEAD >/dev/null; then
  echo "pre-push-check: no commits yet, nothing to scan"
  exit 0
fi

findings="$tmp/findings"   # kind <TAB> record [<TAB> commit]
: > "$findings"

# allow-list: strip comments/blank lines
allow="$tmp/allow"
: > "$allow"
if [ -f .prepush-allow ]; then
  grep -v -e '^[[:space:]]*#' -e '^[[:space:]]*$' .prepush-allow > "$allow" || true
fi

# filter_allowed: stdin records -> stdout records not matched by .prepush-allow
filter_allowed() {
  if [ -s "$allow" ]; then
    grep -E -v -f "$allow" || true
  else
    cat
  fi
}

# is_allowed <record>: success when .prepush-allow matches the record
is_allowed() {
  [ -s "$allow" ] && printf '%s\n' "$1" | grep -E -q -f "$allow"
}

# scan <category> <show-match:1|0> [extra git-grep flags] <pattern>
# Records are "<path>:<line>:<match>"; secrets print only path:line.
scan() {
  local category="$1" show="$2"; shift 2
  local pattern="${!#}"
  local flags=("${@:1:$#-1}")
  git grep -I -n -o ${flags[@]+"${flags[@]}"} -E -e "$pattern" HEAD -- . ':(exclude).prepush-allow' 2>/dev/null \
    | sed 's/^HEAD://' \
    | filter_allowed \
    | while IFS= read -r rec; do
        if [ "$show" = 1 ]; then
          printf '%s\t%s\n' "$category" "$rec"
        else
          path_line="$(printf '%s' "$rec" | cut -d: -f1,2)"
          printf '%s\t%s:<value hidden>\n' "$category" "$path_line"
        fi
      done >> "$findings"
}

# ----------------------------------------------------------------- HEAD

# local absolute paths
scan local-path 1 '/(Users|home)/[A-Za-z0-9._-]+/'
scan local-path 1 '[A-Za-z]:\\Users\\[A-Za-z0-9._ -]+\\'

# e-mail addresses (allow-list applied on the matched address)
git grep -I -n -o -E -e '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}' HEAD -- . ':(exclude).prepush-allow' 2>/dev/null \
  | sed 's/^HEAD://' \
  | grep -E -v -i '@(users\.noreply\.github\.com|example\.(com|org|net))$' \
  | filter_allowed \
  | while IFS= read -r rec; do printf 'email\t%s\n' "$rec"; done >> "$findings"

# secrets (values are never echoed)
scan secret 0 '-----BEGIN ([A-Z0-9]+ )*PRIVATE KEY-----'
scan secret 0 'gh[pousr]_[A-Za-z0-9]{30,}'
scan secret 0 'github_pat_[A-Za-z0-9_]{20,}'
scan secret 0 'AKIA[0-9A-Z]{16}'
scan secret 0 '(^|[^A-Za-z0-9_-])sk-[A-Za-z0-9_-]{20,}'
scan secret 0 -i "(password|passwd)[[:space:]]*[:=][[:space:]]*[\"'][^\"']{3,}[\"']"
scan secret 0 -i '(password|passwd)=[^[:space:]"'"'"'$<{(%]{4,}'

# forbidden tracked files
git ls-tree -r --name-only HEAD \
  | grep -E -e '(^|/)\.env($|\.)' -e '^data/' -e '\.[Pp][Dd][Ff]$' \
  | grep -v -x 'data/README.md' \
  | filter_allowed \
  | while IFS= read -r f; do printf 'forbidden\t%s\n' "$f"; done >> "$findings"

# ----------------------------------------------------------------- pushed range

is_zero() { case "$1" in ""|*[!0]*) return 1 ;; *) return 0 ;; esac; }
is_sha()  { case "$1" in *[!0-9a-f]*|"") return 1 ;; *) [ "${#1}" -ge 40 ] ;; esac; }

commits="$tmp/commits"
: > "$commits"
range_refs=0
while read -r lref lsha rref rsha _; do
  is_sha "$lsha" || continue          # blank or garbage line
  is_zero "$lsha" && continue         # deleting a remote ref: nothing is pushed
  range_refs=$((range_refs + 1))
  if is_sha "$rsha" && ! is_zero "$rsha" && git cat-file -e "$rsha^{commit}" 2>/dev/null; then
    spec=("$rsha..$lsha")
  else
    # new branch, or the remote tip is unknown locally: everything no remote has
    spec=("$lsha" --not --remotes)
  fi
  if ! git rev-list "${spec[@]}" >> "$commits" 2>/dev/null; then
    printf 'range-error\t%s: cannot list the commits to push\n' "${lref:-$lsha}" >> "$findings"
  fi
done < "$refs"
sort -u "$commits" -o "$commits"
commit_count="$(wc -l < "$commits" | tr -d ' ')"

if [ "$commit_count" -gt 0 ]; then
  added="$tmp/added.txt"      # one added line of text per row
  meta="$tmp/added.meta"      # parallel rows: <sha> <TAB> <path> <TAB> <line>
  : > "$added"; : > "$meta"

  # Added lines of every commit. Combined diffs (--cc) keep merge commits honest:
  # only lines that are in none of the parents count as added by the merge.
  git -c core.quotepath=off diff-tree --stdin -r --root -p -U0 --cc --no-color \
      --no-ext-diff --no-textconv --no-renames < "$commits" 2>/dev/null \
    | LC_ALL=C awk -v added="$added" -v meta="$meta" '
        function is_commit_id(s) { return (length(s) == 40 || length(s) == 64) && s ~ /^[0-9a-f]+$/ }
        {
          if (is_commit_id($0)) { sha = $0; inhunk = 0; path = ""; next }
          if ($0 ~ /^diff /)    { inhunk = 0; path = ""; next }
          if (!inhunk && substr($0, 1, 4) == "+++ ") {
            p = substr($0, 5); sub(/\t.*$/, "", p)
            if (p == "/dev/null") path = ""; else { sub(/^b\//, "", p); path = p }
            next
          }
          if (substr($0, 1, 2) == "@@") {
            inhunk = 1
            match($0, /^@+/); ncol = RLENGTH - 1
            match($0, / \+[0-9]+/); ln = substr($0, RSTART + 2, RLENGTH - 2) + 0
            next
          }
          if (!inhunk || $0 == "" || substr($0, 1, 1) == "\\") next
          prefix = substr($0, 1, ncol)
          if (length(prefix) == ncol && prefix ~ /^\++$/) {
            if (path != "" && path != ".prepush-allow") {
              print substr($0, ncol + 1) >> added
              print sha "\t" path "\t" ln >> meta
            }
            ln++
          } else if (index(prefix, "-") == 0) {
            ln++
          }
        }'

  # range_scan <category> <show-match:1|0> [extra grep flags] <pattern>
  range_scan() {
    local category="$1" show="$2"; shift 2
    local pattern="${!#}"
    local flags=("${@:1:$#-1}")
    LC_ALL=C grep -a -n -o ${flags[@]+"${flags[@]}"} -E -e "$pattern" "$added" 2>/dev/null \
      | range_join \
      | sort -u \
      | while IFS=$'\t' read -r sha rec; do
          is_allowed "$rec" && continue
          if [ "$show" = 1 ]; then
            printf '%s\t%s\t%s\n' "$category" "$rec" "${sha:0:12}"
          else
            printf '%s\t%s:<value hidden>\t%s\n' "$category" "$(printf '%s' "$rec" | cut -d: -f1,2)" "${sha:0:12}"
          fi
        done >> "$findings"
  }

  # stdin "<row>:<match>" -> "<sha> <TAB> <path>:<line>:<match>"
  range_join() {
    awk -v meta="$meta" '
      BEGIN { while ((getline l < meta) > 0) m[++c] = l }
      { i = index($0, ":"); n = substr($0, 1, i - 1) + 0
        split(m[n], a, "\t")
        print a[1] "\t" a[2] ":" a[3] ":" substr($0, i + 1) }'
  }

  range_scan local-path 1 '/(Users|home)/[A-Za-z0-9._-]+/'
  range_scan local-path 1 '[A-Za-z]:\\Users\\[A-Za-z0-9._ -]+\\'

  LC_ALL=C grep -a -n -o -E -e '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}' "$added" 2>/dev/null \
    | grep -E -v -i '@(users\.noreply\.github\.com|example\.(com|org|net))$' \
    | range_join | sort -u \
    | while IFS=$'\t' read -r sha rec; do
        is_allowed "$rec" && continue
        printf 'email\t%s\t%s\n' "$rec" "${sha:0:12}"
      done >> "$findings"

  range_scan secret 0 '-----BEGIN ([A-Z0-9]+ )*PRIVATE KEY-----'
  range_scan secret 0 'gh[pousr]_[A-Za-z0-9]{30,}'
  range_scan secret 0 'github_pat_[A-Za-z0-9_]{20,}'
  range_scan secret 0 'AKIA[0-9A-Z]{16}'
  range_scan secret 0 '(^|[^A-Za-z0-9_-])sk-[A-Za-z0-9_-]{20,}'
  range_scan secret 0 -i "(password|passwd)[[:space:]]*[:=][[:space:]]*[\"'][^\"']{3,}[\"']"
  range_scan secret 0 -i '(password|passwd)=[^[:space:]"'"'"'$<{(%]{4,}'

  # forbidden files ADDED anywhere in the range (still in history even if deleted later).
  # --name-status -c: a merge reports "AA path" only when no parent had the file.
  git -c core.quotepath=off diff-tree --stdin -r --root --name-status -c --no-renames \
      < "$commits" 2>/dev/null \
    | awk '
        function is_commit_id(s) { return (length(s) == 40 || length(s) == 64) && s ~ /^[0-9a-f]+$/ }
        is_commit_id($0) { sha = $0; next }
        { t = index($0, "\t"); if (t == 0) next
          st = substr($0, 1, t - 1)
          if (st ~ /^A+$/) print sha "\t" substr($0, t + 1) }' \
    | grep -E -e $'\t''(.*/)?\.env($|\.)' -e $'\t''data/' -e '\.[Pp][Dd][Ff]$' \
    | grep -v -E -e $'\t''data/README\.md$' \
    | sort -u \
    | while IFS=$'\t' read -r sha f; do
        is_allowed "$f" && continue
        printf 'forbidden\t%s\t%s\n' "$f" "${sha:0:12}"
      done >> "$findings"
fi

# ----------------------------------------------------------------- verdict

if [ -s "$findings" ]; then
  scope="HEAD"
  [ "$commit_count" -gt 0 ] && scope="HEAD and $commit_count pushed commit(s)"
  echo "pre-push-check: blocked, $(sort -u "$findings" | wc -l | tr -d ' ') finding(s) in $scope" >&2
  sort -u "$findings" | while IFS=$'\t' read -r kind rec commit; do
    if [ -n "$commit" ]; then
      printf '  [%s] %s (in pushed commit %s)\n' "$kind" "$rec" "$commit" >&2
    else
      printf '  [%s] %s\n' "$kind" "$rec" >&2
    fi
  done
  echo "Fix them (a secret already committed must be removed from history, not just from HEAD)," >&2
  echo "add an intentional exception to .prepush-allow, or push with --no-verify." >&2
  exit 1
fi

if [ "$commit_count" -gt 0 ]; then
  echo "pre-push-check: clean (HEAD + $commit_count pushed commit(s))"
else
  echo "pre-push-check: clean"
fi
exit 0
