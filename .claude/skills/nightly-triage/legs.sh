#!/usr/bin/env bash
# Per-board legs of recent Hardware Validation runs: board, conclusion, runner,
# failed steps. Read a red leg together with its runner name: jobs land on
# whichever runner carries the board label, so the same board can fail on one
# bench and pass on its sibling.
#   legs.sh            # last 5 scheduled runs on main
#   legs.sh <run_id>   # one run
#   legs.sh -n 10      # last 10 scheduled runs
set -euo pipefail
repo=AmbiqAI/helia-profiler
n=5
runs=()
while [[ $# -gt 0 ]]; do
  case $1 in
    -n) n=$2; shift 2 ;;
    *) runs+=("$1"); shift ;;
  esac
done
if [[ ${#runs[@]} -eq 0 ]]; then
  mapfile -t runs < <(gh run list -R "$repo" --workflow hardware-validation.yml \
    --event schedule --limit "$n" --json databaseId -q '.[].databaseId')
fi
for r in "${runs[@]}"; do
  gh api "repos/$repo/actions/runs/$r" -q '"run \(.id)  \(.created_at)  \(.head_branch)@\(.head_sha[0:7])  \(.conclusion)"'
  gh api "repos/$repo/actions/runs/$r/jobs" -q \
    '.jobs[] | select(.name != "plan") | "  \(.name)\t\(.conclusion)\t\(.runner_name)\t" + ([.steps[] | select(.conclusion == "failure") | .name] | join("; "))' \
    | column -t -s $'\t'
done
