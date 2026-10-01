#!/usr/bin/env bash

set -euo pipefail

TEST_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"

tests=(
    test_shell_syntax.sh
    test_range_validation.sh
    test_resume_logic.sh
    test_no_secret_output.sh
    test_browser_detection.sh
    test_download_engine.sh
    test_zip_integrity.sh
)

for test_script in "${tests[@]}"; do
    printf '\n==> %s\n' "$test_script"
    bash "$TEST_DIR/$test_script"
done

python3 -B "$TEST_DIR/test_chromium_provider.py"
python3 -B "$TEST_DIR/test_browser_bridge_static.py"
python3 -B "$TEST_DIR/test_browser_bridge_assembler.py"

printf '\nPASS: all %s offline shell suites plus Python provider/bridge tests completed successfully.\n' "${#tests[@]}"
