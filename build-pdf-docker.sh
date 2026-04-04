#!/bin/bash
# =============================================================================
# Build PDF using Docker - no local dependencies needed
#
# Prerequisites: Docker running
#
# Usage (from project root):
#   bash build-pdf-docker.sh
#
# Output:
#   ChiaLisp-Learning-Guide.pdf in the project root
# =============================================================================

set -euo pipefail

cd "$(dirname "$0")"

if ! docker info &>/dev/null; then
  echo "ERROR: Docker is not running. Start Docker Desktop and try again."
  exit 1
fi

IMAGE="pandoc/extra:latest"

echo "=== Pulling pandoc Docker image (first time may take a minute) ==="
docker pull "$IMAGE"

echo ""
echo "=== Building PDF inside container ==="

export MSYS_NO_PATHCONV=1
docker run --rm \
  -v "$(pwd)://workspace" \
  -w //workspace \
  "$IMAGE" \
  bash -c '
    set -euo pipefail

    DATE=$(date +%Y-%m-%d)

    append_code_files() {
      local search_dir="$1"
      local chapter_label="$2"
      [ -d "$search_dir" ] || return
      printf "\n\n## Source Code: %s\n" "$chapter_label" >> combined.md
      find "$search_dir" -type f \( -name "*.clsp" -o -name "*.py" \) | sort | while read -r file; do
        case "$file" in
          *.clsp) lang="lisp" ;;
          *.py)   lang="python" ;;
          *)      lang="" ;;
        esac
        printf "\n### \`%s\`\n\n\`\`\`%s\n" "$file" "$lang" >> combined.md
        cat "$file" >> combined.md
        printf "\n\`\`\`\n" >> combined.md
      done
    }

    # --- Frontmatter ---
    printf "%s\n" \
      "---" \
      "title: \"ChiaLisp Complete Learning Guide\"" \
      "subtitle: \"From Zero to CAT Staking\"" \
      "author: \"Marvin Quevedo\"" \
      "date: \"${DATE}\"" \
      "titlepage: true" \
      "toc: true" \
      "toc-depth: 3" \
      "geometry: margin=2.5cm" \
      "fontsize: 11pt" \
      "documentclass: report" \
      "header-includes:" \
      "  - \usepackage{hyperref}" \
      "  - \hypersetup{colorlinks=true,linkcolor=blue,urlcolor=blue}" \
      "  - \usepackage{fvextra}" \
      "  - \DefineVerbatimEnvironment{Highlighting}{Verbatim}{breaklines,breakanywhere,commandchars=\\\\\\\{\\\}}" \
      "  - \renewenvironment{Shaded}{\begin{snugshade}\footnotesize}{\end{snugshade}}" \
      "---" \
      "" > combined.md

    # --- License and author page ---
    printf "%s\n" \
      "\thispagestyle{empty}" \
      "\vspace*{\fill}" \
      "" \
      "\begin{center}" \
      "{\Large\textbf{ChiaLisp Complete Learning Guide}}\\\\[0.5cm]" \
      "{\large From Zero to CAT Staking}\\\\[2cm]" \
      "" \
      "{\large Author: \textbf{Marvin Quevedo}}\\\\[0.3cm]" \
      "{\normalsize \url{https://github.com/MarvinQuevedo}}\\\\[2cm]" \
      "" \
      "{\small This work is licensed under a}\\\\[0.2cm]" \
      "{\normalsize \textbf{Creative Commons Attribution-ShareAlike 4.0 International License}}\\\\[0.2cm]" \
      "{\small (CC BY-SA 4.0)}\\\\[0.5cm]" \
      "{\small \url{https://creativecommons.org/licenses/by-sa/4.0/}}\\\\[2cm]" \
      "" \
      "{\footnotesize You are free to share and adapt this material for any purpose, even commercially,}\\\\" \
      "{\footnotesize as long as you give appropriate credit and distribute your contributions}\\\\" \
      "{\footnotesize under the same license.}\\\\[1cm]" \
      "" \
      "{\footnotesize Source code: \url{https://github.com/MarvinQuevedo/ChiaLispProject}}" \
      "" \
      "\end{center}" \
      "" \
      "\vspace*{\fill}" \
      "\newpage" \
      "" >> combined.md

    # --- README ---
    echo "  Adding: README.md"
    tail -n +2 README.md >> combined.md
    printf "\n\n\\\\newpage\n\n" >> combined.md

    # --- Chapters 1-6 ---
    for entry in \
      "01-fundamentals|examples|Chapter 1 - Fundamentals" \
      "02-puzzles-and-conditions|examples|Chapter 2 - Puzzles & Conditions" \
      "03-currying-and-inner-puzzles|examples|Chapter 3 - Currying & Inner Puzzles" \
      "04-python-drivers|examples|Chapter 4 - Python Drivers" \
      "05-cats|examples|Chapter 5 - CATs" \
      "06-advanced-examples|examples|Chapter 6 - Advanced Examples"
    do
      IFS="|" read -r dir codedir label <<< "$entry"
      echo "  Adding: $dir"
      cat "$dir/README.md" >> combined.md
      append_code_files "$dir/$codedir" "$label"
      printf "\n\n\\\\newpage\n\n" >> combined.md
    done

    # --- Chapter 7 ---
    echo "  Adding: 07-staking-project"
    cat 07-staking-project/README.md >> combined.md
    append_code_files "07-staking-project/puzzles" "Chapter 7 - Staking Puzzles"
    append_code_files "07-staking-project/drivers" "Chapter 7 - Staking Drivers"
    append_code_files "07-staking-project/tests" "Chapter 7 - Staking Tests"
    printf "\n\n\\\\newpage\n\n" >> combined.md

    echo "  combined.md: $(wc -l < combined.md) lines"
    echo ""
    echo "=== Running pandoc ==="

    pandoc combined.md \
      -o ChiaLisp-Learning-Guide.pdf \
      --pdf-engine=xelatex \
      --toc \
      --toc-depth=3 \
      --highlight-style=tango \
      -V geometry:margin=2.5cm \
      -V fontsize=11pt \
      -V documentclass=report \
      -V colorlinks=true \
      -V linkcolor=blue \
      -V urlcolor=blue \
      -V toccolor=blue

    rm -f combined.md
    echo "=== PDF built ==="
  '

echo ""
echo "=== Done! ==="
echo "Output: $(pwd)/ChiaLisp-Learning-Guide.pdf"
