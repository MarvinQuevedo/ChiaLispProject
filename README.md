# ChiaLisp Complete Learning Guide

> **⚠️ Disclaimer:** This project is provided for educational purposes only. We do not guarantee that any programs or code examples will function correctly in all environments or scenarios. Use at your own risk. We are not responsible for any loss of funds, data, or other damages that may result from using this code.

A comprehensive, hands-on guide to learning ChiaLisp — the smart coin programming language of the Chia blockchain. This guide takes you from zero to building a fully functional CAT Staking system.

## Who Is This For?

- Developers who want to build on Chia blockchain
- Anyone who has struggled with ChiaLisp's unique paradigm
- Programmers coming from imperative languages (Python, JavaScript, etc.)

## Prerequisites

- **Python 3.9+** installed
- Basic programming knowledge (any language)
- Command line familiarity
- No blockchain experience required (we'll cover everything)

## Environment Setup

### 1. Install Chia Dev Tools

```bash
pip install chia-dev-tools
```

This gives you:
- `run` — Compile ChiaLisp (.clsp) to CLVM bytecode
- `brun` — Execute compiled CLVM with a solution
- `cdv` — Chia Dev tools CLI (hashing, encoding, key utilities)

### 2. Verify Installation

```bash
# Compile a simple program
run "(mod (X) (+ X 1))"
# Output: (+ 2 (q . 1))

# Run compiled CLVM with a solution
brun "(+ 2 (q . 1))" "(42)"
# Output: 43

# Check cdv
cdv --help
```

### 3. Install Chia Blockchain (for drivers and RPC)

```bash
pip install chia-blockchain
```

### 4. (Optional) CLVM Tools RS for faster compilation

```bash
pip install clvm_tools_rs
```

---

## Learning Path

| Chapter | Topic | What You'll Learn |
|---------|-------|-------------------|
| [01](01-fundamentals/README.md) | **Fundamentals** | CLVM basics, data types, operators, ChiaLisp syntax |
| [02](02-puzzles-and-conditions/README.md) | **Puzzles & Conditions** | The coin model, puzzles, conditions, security |
| [03](03-currying-and-inner-puzzles/README.md) | **Currying & Inner Puzzles** | Partial application, composition, standard transactions |
| [04](04-python-drivers/README.md) | **Python Drivers** | Connecting puzzles to the blockchain with Python |
| [05](05-cats/README.md) | **CATs (Chia Asset Tokens)** | Fungible tokens, TAILs, CAT mechanics |
| [06](06-advanced-examples/README.md) | **Advanced Examples** | Escrow, Savings, Lottery, Vesting, Voting |
| [07](07-staking-project/README.md) | **Final Project: CAT Staking** | Complete staking system with pool + lock mechanics |

---

## How to Use This Guide

1. **Read each chapter in order** — concepts build on each other
2. **Run every example** — type the code yourself, don't just read it
3. **Experiment** — modify examples, break them, fix them
4. **Build the final project** — this ties everything together

### Running Examples

```bash
# Compile a .clsp file
run -i include_files/ my_puzzle.clsp

# Run compiled output with a solution
brun "compiled_output" "(solution args here)"

# Or compile and run in one step
brun "$(run my_puzzle.clsp)" "(solution)"
```

---

## Key Mental Model Shifts

If you come from imperative programming, ChiaLisp will feel alien. Here are the key shifts:

### 1. No Variables, No State
There are no variables. Everything is computed from inputs (the solution). Think of it as a pure mathematical function.

### 2. No Loops
There are no for/while loops. Use recursion instead. Every repetition is a function calling itself.

### 3. Coins Are Programs
Every coin on Chia IS a program (puzzle). Spending a coin means running that program with arguments (solution). The output is a list of conditions (instructions to the blockchain).

### 4. Immutable Coins (UTXO)
Coins can't be modified. To "change" a coin, you spend it and create a new one. Like breaking a $20 bill into two $10s.

### 5. Everything is a Tree
Data in CLVM is a binary tree of atoms. Lists are just a convenient way to represent trees. Understanding this is crucial.

---

## Quick Reference

### Common Commands
```bash
run "program"              # Compile ChiaLisp to CLVM
brun "clvm" "solution"     # Run CLVM with solution
cdv hash treehash "clvm"   # Get puzzle hash
cdv encode "hash"          # Convert hash to address (xch1...)
cdv decode "address"       # Convert address to hash
```

### Common Condition Opcodes
| Code | Name | Purpose |
|------|------|---------|
| 51 | CREATE_COIN | Create a new coin |
| 50 | AGG_SIG_ME | Require signature |
| 60 | CREATE_COIN_ANNOUNCEMENT | Send message to other coins |
| 61 | ASSERT_COIN_ANNOUNCEMENT | Receive message from other coins |
| 73 | ASSERT_MY_AMOUNT | Verify this coin's amount |
| 80 | ASSERT_SECONDS_RELATIVE | Time lock (seconds) |
| 82 | ASSERT_HEIGHT_RELATIVE | Time lock (blocks) |

---

**Let's begin → [Chapter 1: Fundamentals](01-fundamentals/README.md)**
