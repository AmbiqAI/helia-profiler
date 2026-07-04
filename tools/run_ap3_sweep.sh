#!/usr/bin/env bash
set -u -o pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
board="apollo3p_evb"
jlink_serial="1160000174"
output_dir="$repo_root/results/validation/ap3-full-sweep-pr23"
timeout_s="900"
repeat="1"
resume=0
stop_on_fail=0
dry_run=0
limit=""

models=(kws vww ic ad)
engines=(helia-rt helia-aot)
toolchains=(arm-none-eabi-gcc armclang atfe)
transports=(rtt swo uart)
memories=(auto sram mram)

usage() {
    cat <<'EOF'
Usage: bash tools/run_ap3_sweep.sh [options]

Runs the AP3 hardware validation matrix one hpx validate case at a time.
Progress is printed to stdout. Per-case stdout/stderr logs are written under:
  <output-dir>/logs/<case-id>.log

Options:
  --output-dir DIR       Results directory (default: results/validation/ap3-full-sweep-pr23)
  --jlink-serial SERIAL  AP3 J-Link serial (default: 1160000174)
  --models CSV           Models (default: kws,vww,ic,ad)
  --engines CSV          Engines (default: helia-rt,helia-aot)
  --toolchains CSV       Toolchains (default: arm-none-eabi-gcc,armclang,atfe)
  --interfaces CSV       Interfaces/transports (default: rtt,swo,uart)
    --memories CSV         Memory presets (default: auto,sram,mram)
  --timeout SECONDS      Per-case timeout passed to hpx validate (default: 900)
  --repeat N             Repeat each selected case N times (default: 1)
  --limit N              Run only the first N expanded cases
  --resume               Skip cases already marked PASS in status.tsv
  --stop-on-fail         Stop after the first failing case
  --dry-run              Print selected case IDs without running hardware
EOF
}

split_csv() {
    local raw="$1"
    local -n out_ref="$2"
    IFS=',' read -r -a out_ref <<< "$raw"
}

while (($#)); do
    case "$1" in
        --output-dir)
            output_dir="$2"
            shift 2
            ;;
        --jlink-serial)
            jlink_serial="$2"
            shift 2
            ;;
        --models)
            split_csv "$2" models
            shift 2
            ;;
        --engines)
            split_csv "$2" engines
            shift 2
            ;;
        --toolchains)
            split_csv "$2" toolchains
            shift 2
            ;;
        --interfaces|--transports)
            split_csv "$2" transports
            shift 2
            ;;
        --memories)
            split_csv "$2" memories
            shift 2
            ;;
        --timeout)
            timeout_s="$2"
            shift 2
            ;;
        --repeat)
            repeat="$2"
            shift 2
            ;;
        --limit)
            limit="$2"
            shift 2
            ;;
        --resume)
            resume=1
            shift
            ;;
        --stop-on-fail)
            stop_on_fail=1
            shift
            ;;
        --dry-run)
            dry_run=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

mkdir -p "$output_dir/logs" "$output_dir/cases"
status_file="$output_dir/status.tsv"
summary_file="$output_dir/summary.txt"

engine_slug() {
    case "$1" in
        helia-rt) echo "rt" ;;
        helia-aot) echo "aot" ;;
        *) echo "$1" ;;
    esac
}

case_id_for() {
    local model="$1"
    local engine="$2"
    local toolchain="$3"
    local transport="$4"
    local memory="$5"
    local attempt="$6"
    local slug
    slug="$(engine_slug "$engine")"
    local id="$board-$model-$slug-$toolchain-$transport-$memory"
    if ((repeat > 1)); then
        printf '%s-run%02d\n' "$id" "$attempt"
    else
        printf '%s\n' "$id"
    fi
}

already_passed() {
    local case_id="$1"
    [[ -f "$status_file" ]] && grep -Fq $'\tPASS\t' <(grep -F "$case_id" "$status_file" || true)
}

total_cases=0
for _model in "${models[@]}"; do
    for _engine in "${engines[@]}"; do
        for _toolchain in "${toolchains[@]}"; do
            for _transport in "${transports[@]}"; do
                for _memory in "${memories[@]}"; do
                    for ((_attempt = 1; _attempt <= repeat; _attempt++)); do
                        ((total_cases += 1))
                    done
                done
            done
        done
    done
done
if [[ -n "$limit" && "$limit" -lt "$total_cases" ]]; then
    selected_cases="$limit"
else
    selected_cases="$total_cases"
fi

echo "HPX AP3 sweep"
echo "  repo:    $repo_root"
echo "  output:  $output_dir"
echo "  logs:    $output_dir/logs"
echo "  board:   $board"
echo "  serial:  $jlink_serial"
echo "  cases:   $selected_cases selected / $total_cases expanded"
echo "  status:  $status_file"

pass_count=0
fail_count=0
skip_count=0
run_index=0
start_epoch="$(date +%s)"

for model in "${models[@]}"; do
    for engine in "${engines[@]}"; do
        for toolchain in "${toolchains[@]}"; do
            for transport in "${transports[@]}"; do
                for memory in "${memories[@]}"; do
                    for ((attempt = 1; attempt <= repeat; attempt++)); do
                        ((run_index += 1))
                        if [[ -n "$limit" && "$run_index" -gt "$limit" ]]; then
                            break 6
                        fi

                        case_id="$(case_id_for "$model" "$engine" "$toolchain" "$transport" "$memory" "$attempt")"
                        log_file="$output_dir/logs/$case_id.log"
                        case_output="$output_dir/cases/$case_id"

                        if ((dry_run)); then
                            printf '[%03d/%03d] DRY  %s\n' "$run_index" "$selected_cases" "$case_id"
                            continue
                        fi

                        if ((resume)) && already_passed "$case_id"; then
                            ((skip_count += 1))
                            printf '[%03d/%03d] SKIP %s (prior PASS)\n' "$run_index" "$selected_cases" "$case_id"
                            continue
                        fi

                        printf '[%03d/%03d] RUN  %s\n' "$run_index" "$selected_cases" "$case_id"
                        echo "          log: $log_file"
                        cmd=(
                            uv --directory "$repo_root" run hpx validate
                            --boards "$board"
                            --models "$model"
                            --engines "$engine"
                            --power off
                            --toolchains "$toolchain"
                            --interfaces "$transport"
                            --memories "$memory"
                            --jlink-serials "$board=$jlink_serial"
                            --repeat 1
                            --timeout "$timeout_s"
                            --output-dir "$case_output"
                            -v
                        )
                        case_start="$(date +%s)"
                        {
                            printf '$'
                            printf ' %q' "${cmd[@]}"
                            printf '\n\n'
                            "${cmd[@]}"
                        } >"$log_file" 2>&1
                        rc=$?
                        case_end="$(date +%s)"
                        duration=$((case_end - case_start))

                        if ((rc == 0)); then
                            ((pass_count += 1))
                            status="PASS"
                        else
                            ((fail_count += 1))
                            status="FAIL"
                        fi
                        printf '%s\t%s\t%s\t%d\t%s\t%s\n' \
                            "$(date -Is)" "$status" "$case_id" "$duration" "$log_file" "$case_output" \
                            >>"$status_file"
                        printf '[%03d/%03d] %-4s %s (%ss)\n' "$run_index" "$selected_cases" "$status" "$case_id" "$duration"

                        if ((rc != 0)); then
                            echo "          log:    $log_file"
                            echo "          report: $case_output/validation_report.md"
                            if ((stop_on_fail)); then
                                break 6
                            fi
                        fi
                    done
                done
            done
        done
    done
done

end_epoch="$(date +%s)"
elapsed=$((end_epoch - start_epoch))
{
    echo "HPX AP3 sweep summary"
    echo "  output: $output_dir"
    echo "  status: $status_file"
    echo "  pass:   $pass_count"
    echo "  fail:   $fail_count"
    echo "  skip:   $skip_count"
    echo "  elapsed_s: $elapsed"
} | tee "$summary_file"

if ((fail_count > 0)); then
    exit 1
fi
exit 0