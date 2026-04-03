# Chapter 1: Fundamentals

> **⚠️ Educational Purpose Only:** This content is for learning ChiaLisp. Code examples may not work in all scenarios. Use at your own risk.

Welcome to ChiaLisp! This chapter covers the foundation you need before writing any puzzles. Take your time here — everything else builds on these concepts.

---

## Table of Contents

1. [What is CLVM?](#1-what-is-clvm)
2. [Atoms and Pairs — The Only Two Data Types](#2-atoms-and-pairs)
3. [How Lists Work (They're Really Trees)](#3-how-lists-work)
4. [Referencing Arguments — The Solution Tree](#4-referencing-arguments)
5. [Operators — Your Toolkit](#5-operators)
6. [ChiaLisp vs CLVM — The High-Level Language](#6-chialisp-vs-clvm)
7. [Practical Examples](#7-practical-examples)

---

## 1. What is CLVM?

**CLVM** (Chia Lisp Virtual Machine) is the low-level language that runs on the Chia blockchain. Every coin on Chia contains a CLVM program (called a "puzzle").

**ChiaLisp** is the higher-level language that compiles down to CLVM. Think of it like:
- ChiaLisp = Python (what you write)
- CLVM = bytecode (what the machine runs)

When you write `(mod (X) (+ X 1))` in ChiaLisp, it compiles to `(+ 2 (q . 1))` in CLVM.

### The Two Tools

```bash
# 'run' compiles ChiaLisp → CLVM
run "(mod (X) (+ X 1))"
# Output: (+ 2 (q . 1))

# 'brun' executes CLVM with a solution
brun "(+ 2 (q . 1))" "(42)"
# Output: 43
```

**Key insight**: `run` is for compile-time. `brun` is for run-time. The puzzle is compiled once and stored on-chain. Each time someone spends the coin, `brun` executes with their solution.

---

## 2. Atoms and Pairs

In CLVM, there are **only two types of data**:

### Atoms
An atom is a sequence of bytes. It can represent:
- **Numbers**: `42`, `-7`, `0`
- **Bytes/Strings**: `0xDEADBEEF`, `"hello"`
- **Nil (empty)**: `()` — the empty atom, also represents false and zero

```bash
# Atoms evaluate to themselves when quoted
brun "(q . 42)" "()"
# Output: 42

brun "(q . 0xCAFE)" "()"
# Output: 0xcafe
```

### Cons Pairs
A **cons pair** (also called cons cell) is a pair of two things. Written as `(A . B)`:

```
(A . B)
   /\
  A  B
```

Each side can be another atom or another cons pair. This creates a **binary tree**:

```
    (A . (B . C))
        /\
       A  /\
         B  C
```

### That's It. Everything is Built From These Two Types.

Numbers? Atoms. Strings? Atoms. Lists? Chains of cons pairs. Programs? Trees of cons pairs and atoms.

---

## 3. How Lists Work

A "list" in CLVM is just syntactic sugar for nested cons pairs ending in nil `()`:

```
The list (A B C) is actually: (A . (B . (C . ())))

As a tree:
        .
       / \
      A   .
         / \
        B   .
           / \
          C  ()
```

### Examples

```bash
# These are equivalent:
brun "(q . (1 2 3))" "()"
brun "(q . (1 . (2 . (3 . ()))))" "()"
# Both output: (1 2 3)

# A pair is NOT a list (no nil terminator):
brun "(q . (1 . 2))" "()"
# Output: (1 . 2)

# Nested lists:
brun "(q . ((1 2) (3 4)))" "()"
# Output: ((1 2) (3 4))
```

### Why This Matters

Understanding that lists are trees is **critical** for:
1. Accessing arguments (path-based referencing)
2. Building data structures
3. Understanding how puzzles work

---

## 4. Referencing Arguments — The Solution Tree

When a puzzle runs, it receives a **solution** (a tree of data). You access parts of this tree using **numeric paths**.

### The Rules

- `1` = the entire solution tree
- `2` = the **first** element (left branch) — same as `f` (first)
- `3` = the **rest** (right branch) — same as `r` (rest)
- From any node, multiply by 2 to go left, multiply by 2 and add 1 to go right

### Path Reference Diagram

For solution `(A B C D)`:

```
The tree structure:
              1
             / \
            2   3
           /   / \
          A   5   7
             /   / \
            B  11  15
                  / \
                 C  ()          (Note: D would need 5 elements)
```

Wait — let's be precise. For `(A B C)`:

```
         1              ← The whole tree: (A B C)
        / \
       2   3            ← 2 = A,  3 = (B C)
          / \
         5   7          ← 5 = B,  7 = (C)
            / \
          11  15        ← 11 = C, 15 = ()
```

### Practical Examples

```bash
# Solution: (100 200 300)
# Access first element (path 2):
brun "2" "(100 200 300)"
# Output: 100

# Access second element (path 5):
brun "5" "(100 200 300)"
# Output: 200

# Access third element (path 11):
brun "11" "(100 200 300)"
# Output: 300

# Access "the rest" after first (path 3):
brun "3" "(100 200 300)"
# Output: (200 300)
```

### The Pattern for Flat Lists

| Position | Path | How to Remember |
|----------|------|-----------------|
| 1st element | 2 | |
| 2nd element | 5 | |
| 3rd element | 11 | |
| 4th element | 23 | |
| 5th element | 47 | |
| nth element | (2^n + 2^(n-1) - 2) | Formula: go right (n-1) times, then left |

**Shortcut**: In ChiaLisp (high-level), you just name your arguments:

```lisp
(mod (first_arg second_arg third_arg)
  ; first_arg = path 2
  ; second_arg = path 5
  ; third_arg = path 11
  ; ChiaLisp handles the paths for you!
  (+ first_arg second_arg)
)
```

---

## 5. Operators

### Quoting: `q`

The most important operator. `(q . VALUE)` returns VALUE without evaluating it.

```bash
# Without quote — 1 is interpreted as "the whole solution"
brun "1" "(hello)"
# Output: (hello)

# With quote — 42 is returned literally
brun "(q . 42)" "(anything)"
# Output: 42
```

### Arithmetic Operators

```bash
# Addition
brun "(+ (q . 10) (q . 20))" "()"
# Output: 30

# Subtraction
brun "(- (q . 100) (q . 30))" "()"
# Output: 70

# Multiplication
brun "(* (q . 6) (q . 7))" "()"
# Output: 42

# Division (integer)
brun "(/ (q . 100) (q . 3))" "()"
# Output: 33

# Divmod (quotient and remainder)
brun "(divmod (q . 100) (q . 3))" "()"
# Output: (33 . 1)

# Using solution arguments
brun "(+ 2 5)" "(10 20)"
# Output: 30  (because 2=10, 5=20)
```

### Comparison Operators

```bash
# Greater than (returns () for false, 1 for true)
brun "(> (q . 10) (q . 5))" "()"
# Output: 1

brun "(> (q . 3) (q . 10))" "()"
# Output: ()

# Equality: use (= a b)
brun "(= (q . 42) (q . 42))" "()"
# Output: 1

# Not
brun "(not (q . 0))" "()"
# Output: 1

brun "(not (q . 42))" "()"
# Output: ()
```

### Logic Operators

```bash
# any (logical OR)
brun "(any (q . 0) (q . 0) (q . 1))" "()"
# Output: 1

# all (logical AND)
brun "(all (q . 1) (q . 1) (q . 0))" "()"
# Output: ()

brun "(all (q . 1) (q . 1) (q . 1))" "()"
# Output: 1
```

### If / Conditional

```bash
# (i condition then else)
# IMPORTANT: 'i' evaluates BOTH branches! Use 'if' in ChiaLisp instead.

brun "(i (= 2 (q . 1)) (q . yes) (q . no))" "(1)"
# Output: yes

brun "(i (= 2 (q . 1)) (q . yes) (q . no))" "(0)"
# Output: no
```

### List Operators

```bash
# f (first) — get the first element
brun "(f (q . (10 20 30)))" "()"
# Output: 10

# r (rest) — get everything except the first
brun "(r (q . (10 20 30)))" "()"
# Output: (20 30)

# c (cons) — construct a pair
brun "(c (q . 1) (q . (2 3)))" "()"
# Output: (1 2 3)

# l (listp) — check if something is a list/pair (not an atom)
brun "(l (q . (1 2)))" "()"
# Output: 1

brun "(l (q . 42))" "()"
# Output: ()
```

### Crypto Operators

```bash
# sha256 — hash data
brun "(sha256 (q . hello))" "()"
# Output: 0x2cf24dba5fb0a30e... (32-byte hash)

# concat — join bytes together
brun "(concat (q . hello) (q . world))" "()"
# Output: helloworld (as bytes)

# strlen — byte length
brun "(strlen (q . hello))" "()"
# Output: 5
```

### Apply and Raise

```bash
# a (apply) — run a program with an environment
brun "(a (q . (+ 2 5)) (q . (10 20)))" "()"
# Output: 30
# This runs the program (+ 2 5) with solution (10 20)

# x (raise) — abort execution with an error
brun "(x (q . \"something went wrong\"))" "()"
# FAIL: clvm raise (something went wrong)
```

---

## 6. ChiaLisp vs CLVM

ChiaLisp adds high-level features that compile to CLVM:

### `mod` — Define a Module

```lisp
; A ChiaLisp program starts with mod
; Arguments are named (no more numeric paths!)
(mod (name greeting)
  (concat greeting name)
)
```

Compile: `run "(mod (name greeting) (concat greeting name))"`

### `defun` — Define Functions

```lisp
(mod (N)
  (defun square (x)
    (* x x)
  )

  (defun sum-of-squares (a b)
    (+ (square a) (square b))
  )

  (sum-of-squares N 5)
)
```

### `defun-inline` — Inline Functions (No Overhead)

```lisp
(mod (amount)
  ; Inline functions are expanded at compile time
  ; More efficient but can't be recursive
  (defun-inline double (x) (* x 2))
  (defun-inline fee (x) (/ x 100))

  (- (double amount) (fee amount))
)
```

### `defconstant` — Named Constants

```lisp
(mod (amount)
  (defconstant CREATE_COIN 51)
  (defconstant AGG_SIG_ME 50)
  (defconstant FEE_RATE 5)

  (list
    (list CREATE_COIN 0xPUZZLEHASH (- amount FEE_RATE))
  )
)
```

### `let` and `let*` — Local Bindings

```lisp
(mod (price quantity tax_rate)
  ; let* allows sequential bindings (each can reference previous)
  (let* (
    (subtotal (* price quantity))
    (tax (/ (* subtotal tax_rate) 100))
    (total (+ subtotal tax))
  )
    total
  )
)
```

### `assign` — Named Bindings (Modern Style)

```lisp
(mod (price quantity tax_rate)
  (assign
    subtotal (* price quantity)
    tax (/ (* subtotal tax_rate) 100)
    total (+ subtotal tax)
    ; The last expression is the return value
    total
  )
)
```

### `include` — Import Libraries

```lisp
(mod (public_key conditions)
  ; Include standard Chia libraries
  (include condition_codes.clib)
  (include sha256tree.clib)
  (include curry-and-treehash.clib)

  ; Now you can use CREATE_COIN, sha256tree, etc.
  (list
    (list AGG_SIG_ME public_key (sha256tree conditions))
  )
)
```

### `if` vs `i`

```lisp
; In ChiaLisp, use 'if' (lazy evaluation — only evaluates chosen branch)
(mod (x)
  (if (> x 10)
    "big"      ; only evaluated if x > 10
    "small"    ; only evaluated if x <= 10
  )
)

; In CLVM, 'i' evaluates BOTH branches (can cause errors!)
; ChiaLisp's 'if' compiles to a safe pattern using 'a' (apply)
```

---

## 7. Practical Examples

See the [examples/](examples/) directory:

| File | Concept |
|------|---------|
| [hello_world.clsp](examples/hello_world.clsp) | Basic program structure |
| [basic_math.clsp](examples/basic_math.clsp) | Arithmetic operations |
| [list_operations.clsp](examples/list_operations.clsp) | List manipulation and recursion |
| [fibonacci.clsp](examples/fibonacci.clsp) | Recursive computation |
| [factorial.clsp](examples/factorial.clsp) | Tail-call optimization |

### Running the Examples

```bash
cd 01-fundamentals/examples

# Compile and see the CLVM output
run hello_world.clsp

# Run with a solution
brun "$(run hello_world.clsp)" "(\"World\")"
```

---

## Exercises

1. **Warm-up**: Write a program that takes two numbers and returns their average (integer division is fine).

2. **List Builder**: Write a program that takes 3 arguments and returns them as a single list in reverse order.

3. **Max Function**: Write a program that takes two numbers and returns the larger one.

4. **Absolute Value**: Write a program that takes a number and returns its absolute value.

5. **Challenge**: Write a program that takes a list of numbers and returns their sum. (Hint: you'll need recursion.)

---

**Next → [Chapter 2: Puzzles & Conditions](../02-puzzles-and-conditions/README.md)**
