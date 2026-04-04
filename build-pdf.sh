#!/bin/sh
# =============================================================================
# Build PDF - mirrors the GitHub Actions workflow
# POSIX sh compatible (works in Alpine/Docker and Linux/macOS)
#
# Usage:
#   sh build-pdf.sh           (inside container or with pandoc+xelatex installed)
#   build-pdf.bat             (Windows - runs this via Docker automatically)
#
# Output:
#   ChiaLisp-Learning-Guide.pdf in the project root
# =============================================================================

set -eu

cd "$(dirname "$0")"

# --- Check dependencies ---
for cmd in pandoc xelatex; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "ERROR: '$cmd' not found."
    echo ""
    echo "On Windows, run build-pdf.bat instead (uses Docker)."
    echo ""
    echo "Or install manually:"
    echo "  Ubuntu/Debian : sudo apt-get install pandoc texlive-xetex texlive-fonts-recommended texlive-fonts-extra lmodern"
    echo "  macOS         : brew install pandoc mactex-no-gui"
    exit 1
  fi
done

DATE=$(date +%Y-%m-%d)
OUTPUT="ChiaLisp-Learning-Guide.pdf"

echo "=== Building combined markdown ==="

# --- Helper: append all code files from a directory ---
append_code_files() {
  search_dir="$1"
  chapter_label="$2"
  [ -d "$search_dir" ] || return 0
  printf '\n\n## Source Code: %s\n' "$chapter_label" >> combined.md
  find "$search_dir" -type f \( -name '*.clsp' -o -name '*.py' \) | sort | while read -r file; do
    lang=""
    case "$file" in
      *.clsp) lang="lisp" ;;
      *.py)   lang="python" ;;
    esac
    printf '\n### `%s`\n\n```%s\n' "$file" "$lang" >> combined.md
    cat "$file" >> combined.md
    printf '\n```\n' >> combined.md
  done
}

# --- Frontmatter ---
cat > combined.md <<FRONTMATTER
---
title: "ChiaLisp Complete Learning Guide"
subtitle: "From Zero to Advanced Projects"
author: "Marvin Quevedo"
date: "${DATE}"
titlepage: true
toc: true
toc-depth: 3
geometry: margin=2.5cm
fontsize: 11pt
documentclass: report
header-includes:
  - \\usepackage{hyperref}
  - \\hypersetup{colorlinks=true,linkcolor=blue,urlcolor=blue}
  - \\usepackage{fvextra}
  - \\DefineVerbatimEnvironment{Highlighting}{Verbatim}{breaklines,breakanywhere,commandchars=\\\\\\{\\}}
  - \\renewenvironment{Shaded}{\\begin{snugshade}\\footnotesize}{\\end{snugshade}}
---

FRONTMATTER

# --- License and author page ---
cat >> combined.md <<'LICPAGE'
\thispagestyle{empty}
\vspace*{\fill}

\begin{center}
{\Large\textbf{ChiaLisp Complete Learning Guide}}\\[0.5cm]
{\large From Zero to Advanced Projects}\\[2cm]

{\large Author: \textbf{Marvin Quevedo}}\\[0.3cm]
{\normalsize \url{https://github.com/MarvinQuevedo}}\\[2cm]

{\small This work is licensed under a}\\[0.2cm]
{\normalsize \textbf{Creative Commons Attribution-ShareAlike 4.0 International License}}\\[0.2cm]
{\small (CC BY-SA 4.0)}\\[0.5cm]
{\small \url{https://creativecommons.org/licenses/by-sa/4.0/}}\\[2cm]

{\footnotesize You are free to share and adapt this material for any purpose, even commercially,}\\
{\footnotesize as long as you give appropriate credit and distribute your contributions}\\
{\footnotesize under the same license.}\\[1cm]

{\footnotesize Source code: \url{https://github.com/MarvinQuevedo/ChiaLispProject}}

\end{center}

\vspace*{\fill}
\newpage

LICPAGE

# --- README (introduction) - skip H1 title ---
echo "  Adding: README.md (introduction)"
tail -n +2 README.md >> combined.md
printf '\n\n\\newpage\n\n' >> combined.md

# --- Chapters 1-6 ---
for entry in \
  "01-fundamentals|examples|Chapter 1 - Fundamentals" \
  "02-puzzles-and-conditions|examples|Chapter 2 - Puzzles & Conditions" \
  "03-currying-and-inner-puzzles|examples|Chapter 3 - Currying & Inner Puzzles" \
  "04-python-drivers|examples|Chapter 4 - Python Drivers" \
  "05-cats|examples|Chapter 5 - CATs" \
  "06-advanced-examples|examples|Chapter 6 - Advanced Examples"
do
  dir=$(echo "$entry" | cut -d'|' -f1)
  codedir=$(echo "$entry" | cut -d'|' -f2)
  label=$(echo "$entry" | cut -d'|' -f3)
  echo "  Adding: $dir"
  cat "$dir/README.md" >> combined.md
  append_code_files "$dir/$codedir" "$label"
  printf '\n\n\\newpage\n\n' >> combined.md
done

# --- Chapter 7 (multiple code directories) ---
echo "  Adding: 07-staking-project"
cat 07-staking-project/README.md >> combined.md
append_code_files "07-staking-project/puzzles" "Chapter 7 - Staking Puzzles"
append_code_files "07-staking-project/drivers" "Chapter 7 - Staking Drivers"
append_code_files "07-staking-project/tests" "Chapter 7 - Staking Tests"
printf '\n\n\\newpage\n\n' >> combined.md

LINES=$(wc -l < combined.md)
echo "  combined.md ready ($LINES lines)"

# --- Build PDF ---
echo ""
echo "=== Building PDF with pandoc + xelatex ==="

pandoc combined.md \
  -o "$OUTPUT" \
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

# --- Cleanup ---
rm -f combined.md

echo ""
echo "=== Done! ==="
echo "Output: $(pwd)/$OUTPUT"
