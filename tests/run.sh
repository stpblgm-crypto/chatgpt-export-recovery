#!/usr/bin/env bash

set -euo pipefail

TEST_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"

tests=(
    test_shell_syntax.sh
    test_range_validation.sh
    test_resume_logic.sh
    test_no_secret_output.sh
)

for test_script in "${tests[@]}"; do
    printf '\n==> %s\n' "$test_script"
    bash "$TEST_DIR/$test_script"
done

printf '\nPASS: all %s offline test scripts completed successfully.\n' "${#tests[@]}"
