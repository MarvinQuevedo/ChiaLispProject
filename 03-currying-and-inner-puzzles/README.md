# Chapter 3: Currying and Inner Puzzles

## Table of Contents

1. [What is Currying?](#1-what-is-currying)
2. [How Currying Works Under the Hood](#2-how-currying-works-under-the-hood)
3. [Inner Puzzles -- The Composition Pattern](#3-inner-puzzles----the-composition-pattern)
4. [How Inner Puzzles Work in Practice](#4-how-inner-puzzles-work-in-practice)
5. [The Standard Transaction (p2_delegated_or_hidden)](#5-the-standard-transaction-p2_delegated_or_hidden)
6. [Puzzle Hashing](#6-puzzle-hashing)

---

## 1. What is Currying?

### Partial Application: Baking Arguments Into a Puzzle

In functional programming, **currying** is the process of transforming a function
that takes multiple arguments into a series of functions that each take a single
argument. In ChiaLisp, currying means something more specific and practical:
**pre-filling some arguments into a puzzle at compile time**, so the resulting
puzzle only needs the remaining arguments at spend time.

Think of it like a form that has some fields already filled in. The blank form
is the original puzzle. The curried version has certain fields permanently
written in ink -- they cannot be changed later.

```
  UNCURRIED PUZZLE                      CURRIED PUZZLE
  ================                      ==============

  (mod (NAME GREETING)                  (mod (GREETING)
    (list NAME GREETING))                 (list "Alice" GREETING))

  Needs: NAME + GREETING                Needs: only GREETING
  at spend time                          NAME is baked in as "Alice"
```

### Same Puzzle Code, Different Parameters, Different Hashes

This is one of the most important concepts in Chia. When you curry different
values into the same puzzle template, you get **different compiled puzzles**
with **different puzzle hashes**. Since the puzzle hash is one of the three
properties that define a coin, this means currying directly affects coin identity.

```
  Template: greeting_puzzle.clsp

  Curry with "Alice"  ---->  puzzle_hash_A = 0x1a2b3c...
  Curry with "Bob"    ---->  puzzle_hash_B = 0x4d5e6f...
  Curry with "Carol"  ---->  puzzle_hash_C = 0x7a8b9c...

  Same code, different parameters = different puzzle hashes = different coins
```

This is how the Chia ecosystem creates millions of unique coins from just a
handful of puzzle templates. The standard transaction puzzle is one single
template, but every wallet has a different puzzle hash because each one is
curried with a different public key.

### Example: A Greeting Puzzle

Consider this simple puzzle that takes a name and a message:

```lisp
; greeting.clsp
(mod (NAME message)
    (list NAME message)
)
```

Uppercase `NAME` indicates it is meant to be curried in (this is a convention,
not a language rule). Lowercase `message` is provided at spend time.

If we curry this with the name "Alice":

```bash
$ cdv clsp curry greeting.clsp -a "Alice"
```

The result is a new puzzle that only needs `message` in its solution. The name
"Alice" is permanently embedded in the compiled output.

### The Manual CLVM Pattern: `(c (q . VALUE) 1)`

Under the hood, currying uses a specific CLVM trick to prepend values to the
environment (the argument tree). The pattern is:

```
(c (q . VALUE) 1)
```

Let us break this down:

- `(q . VALUE)` -- Quote the value so it is treated as data, not code.
- `1` -- This refers to the entire current environment (all arguments).
- `(c ... ...)` -- Cons (prepend) the quoted value onto the environment.

So `(c (q . 42) 1)` means: "Take the number 42 and stick it at the front
of whatever arguments were passed in." This effectively adds a new first
argument with a fixed value.

When you curry multiple values, the wrapping is nested:

```
; Curry A=5, B=10 into a puzzle:
(c (q . 5) (c (q . 10) 1))

; This transforms the environment from:
;   (remaining_args...)
; to:
;   (5 10 remaining_args...)
```

### Why Currying Changes the Puzzle Hash

This point deserves extra emphasis because it is fundamental to how Chia works:

```
  ORIGINAL PUZZLE                  CURRIED PUZZLE
  ===============                  ==============

  (mod (A B)                       (a (q . <original_code>)
    (+ A B))                          (c (q . 5) 1))

  puzzle_hash = 0xabc123...        puzzle_hash = 0xdef456...
```

The curried puzzle is literally different CLVM code. It wraps the original
puzzle in an `(a ...)` (apply) call that first modifies the environment to
inject the curried value. Because the code is different, the sha256 hash is
different. Because the hash is different, any coin locked with this puzzle
has a different identity.

This means:
- A wallet curried with YOUR public key produces coins only YOU can spend.
- A CAT curried with a specific TAIL hash only works for THAT token type.
- A singleton curried with a specific launcher ID is unique on the blockchain.

---

## 2. How Currying Works Under the Hood

### Step-by-Step Transformation

Let us trace exactly what happens when we curry a value into a puzzle.

**Step 1: Start with the original puzzle**

```lisp
; add.clsp
(mod (A B)
    (+ A B)
)
```

When compiled with `run`, this becomes the CLVM bytecode:

```
(+ 2 5)
```

Here `2` means "first argument" (A) and `5` means "second argument" (B).
These are tree paths in the environment -- remember from Chapter 1 that
arguments are stored in a binary tree, and odd numbers are paths into that tree.

**Step 2: Curry A = 5**

When we curry A=5, the system wraps the original compiled code like this:

```
(a (q . (+ 2 5)) (c (q . 5) 1))
```

Breaking this down piece by piece:

```
(a                          ; "apply" -- run the following code with
                            ;   the following environment
    (q . (+ 2 5))           ; the original compiled code, quoted
                            ;   so it is not evaluated yet
    (c                      ; "cons" -- construct a new pair
        (q . 5)             ;   the curried value 5, quoted
        1                   ;   the rest of the arguments (the solution)
    )
)
```

What this does at runtime:

1. Take whatever arguments come in from the solution (let us say `(10)`)
2. Prepend 5 to the front: `(5 10)`
3. Run the original code `(+ 2 5)` with this new environment `(5 10)`
4. `2` resolves to the first element = 5, `5` resolves to the second = 10
5. Result: 15

**Step 3: Verify with brun**

```bash
# Run the curried puzzle with solution (10)
$ brun '(a (q . (+ 2 5)) (c (q . 5) 1))' '(10)'
15

# Compare with running the original with both arguments
$ brun '(+ 2 5)' '(5 10)'
15
```

Both produce the same result. The curried version just has the 5 already baked in.

### Currying Multiple Values

When you curry more than one value, each value gets its own wrapping layer:

```
; Original: (mod (A B C) (+ A (* B C)))
; Compiled: (+ 2 (* 5 11))

; Curry A=3, B=7:
(a
    (q . (+ 2 (* 5 11)))       ; original code
    (c (q . 3)                  ; A = 3
        (c (q . 7)              ; B = 7
            1                   ; remaining args (just C now)
        )
    )
)
```

Now the solution only needs to provide C:

```bash
$ brun '(a (q . (+ 2 (* 5 11))) (c (q . 3) (c (q . 7) 1)))' '(4)'
31
; 3 + (7 * 4) = 3 + 28 = 31
```

### The Chialisp Convention: UPPERCASE = Curried

In Chialisp (the higher-level language), the convention is clear:

```lisp
(mod (CURRIED_PARAM_1 CURRIED_PARAM_2 runtime_param_1 runtime_param_2)
    ; UPPERCASE parameters are curried in at compile time
    ; lowercase parameters are provided in the solution at spend time
    ...
)
```

The `mod` form lists ALL parameters -- both curried and runtime -- in order.
The curried parameters come first (UPPERCASE), and the runtime parameters
come after (lowercase). The `cdv clsp curry` command knows to wrap the puzzle
so that the curried values are injected as the first N arguments.

### Using cdv to Curry

The standard way to curry in practice:

```bash
# Compile the puzzle first
$ cdv clsp build my_puzzle.clsp

# Curry values into the compiled puzzle
$ cdv clsp curry my_puzzle.clsp.hex -a 0x1234 -a 500

# Or curry from source directly
$ cdv clsp curry my_puzzle.clsp -a "hello" -a 42
```

Each `-a` flag provides one curried argument, in the order they appear in the
`mod` parameter list.

---

## 3. Inner Puzzles -- The Composition Pattern

### THE Most Important Pattern in Chia Programming

If you take one thing away from this chapter, make it this: **inner puzzles
are the composition pattern that makes the entire Chia ecosystem work.**

Almost every advanced puzzle in Chia follows this pattern:

```
  OUTER PUZZLE (adds rules/features)
      wraps
  INNER PUZZLE (handles ownership/authorization)
```

The outer puzzle does not know or care about the specific inner puzzle. It
just knows that:

1. There IS an inner puzzle (curried in).
2. It will run that inner puzzle with a provided solution.
3. It will take the conditions the inner puzzle outputs.
4. It will add its own conditions on top.
5. It will return the combined set of conditions.

This is like decorating a function in Python, or middleware in a web framework.
Each layer adds functionality without needing to understand the layers below it.

### Why This Pattern Exists

Consider the problem: you want to create a token (a CAT) that can be owned by
anyone. The token has rules (preserve supply, identify token type), but the
ownership rules (who can spend it, what signatures are needed) should be flexible.

Without inner puzzles, you would need a separate token puzzle for every type
of ownership:
- CAT + single owner
- CAT + multisig
- CAT + time-locked
- CAT + delegated spending
- ...an infinite list

With inner puzzles, you write ONE CAT outer puzzle and let it wrap ANY inner
puzzle for ownership. The CAT puzzle does not care whether the inner puzzle
requires a signature, a multisig vote, or a timelock. It just runs the inner
puzzle and adds its token rules to whatever conditions come back.

### The Architecture

```
┌──────────────────────────────────────────────┐
│              OUTER PUZZLE                     │
│                                              │
│  Curried values:                             │
│    - INNER_PUZZLE (the ownership puzzle)      │
│    - OUTER_PARAMS (e.g., token type, rules)  │
│                                              │
│  At spend time:                              │
│    1. Receive inner_solution from spender     │
│    2. Run INNER_PUZZLE with inner_solution    │
│    3. Get back inner_conditions               │
│    4. Generate own outer_conditions            │
│    5. Return inner_conditions + outer_conds   │
│                                              │
│  ┌────────────────────────────────────────┐  │
│  │          INNER PUZZLE                  │  │
│  │                                        │  │
│  │  e.g., standard_transaction:           │  │
│  │    - Verifies owner signature          │  │
│  │    - Returns CREATE_COIN, etc.         │  │
│  │                                        │  │
│  └────────────────────────────────────────┘  │
│                                              │
│  + OUTER CONDITIONS                          │
│    e.g., ASSERT supply is preserved           │
│    e.g., ASSERT correct token type            │
│                                              │
└──────────────────────────────────────────────┘
```

### Real Examples in the Chia Ecosystem

**CATs (Chia Asset Tokens):**
```
outer = CAT puzzle (enforces token supply rules)
inner = standard_transaction (handles wallet ownership)
```
The CAT puzzle ensures that the total supply of the token is preserved across
spends. The standard transaction inside handles who is allowed to spend it.

**Singletons:**
```
outer = singleton_top_layer (enforces uniqueness)
inner = anything (NFT state, DAO vote, etc.)
```
The singleton outer puzzle ensures there is always exactly one coin with this
identity. The inner puzzle can be whatever application logic is needed.

**NFTs:**
```
outer = singleton_top_layer
  inner = NFT state layer (tracks ownership, royalties)
    inner-inner = standard_transaction (wallet ownership)
```
NFTs actually have THREE layers of nesting. Each layer adds its own rules.

**DataLayer:**
```
outer = singleton_top_layer
  inner = data store puzzle (manages merkle root updates)
```

The pattern composes arbitrarily deep. Each layer is independent and reusable.

---

## 4. How Inner Puzzles Work in Practice

### The Basic Code Pattern

Here is the fundamental template for an outer puzzle with an inner puzzle:

```lisp
(mod (INNER_PUZZLE        ; curried: the inner puzzle (ownership logic)
      MY_CUSTOM_PARAM     ; curried: some parameter for outer logic
      inner_solution      ; runtime: solution for the inner puzzle
      extra_data          ; runtime: any extra data the outer needs
     )

    ; Condition codes
    (defconstant CREATE_COIN 51)
    (defconstant ASSERT_MY_AMOUNT 73)

    ; Step 1: Run the inner puzzle with its solution
    ; The "a" operator applies (runs) a program with given arguments
    (assign
        inner_conditions (a INNER_PUZZLE inner_solution)

        ; Step 2: Build outer conditions
        my_conditions (list
            (list ASSERT_MY_AMOUNT extra_data)
        )

        ; Step 3: Combine inner and outer conditions
        ; "c" prepends our conditions to the inner condition list
        (c (f my_conditions) inner_conditions)
    )
)
```

Let us walk through each step:

**Step 1: `(a INNER_PUZZLE inner_solution)`**

The `a` operator means "apply" -- it runs a program. Here we run the
`INNER_PUZZLE` (which was curried in) using `inner_solution` (which was
provided at spend time by the person spending the coin).

The inner puzzle returns a list of conditions, just like any puzzle. For
example, if the inner puzzle is a standard transaction, it might return:

```
((51 0xabc123... 1000)     ; CREATE_COIN: send 1000 mojos somewhere
 (50 0xdef456... ...)      ; AGG_SIG_ME: require owner's signature
)
```

**Step 2: Build outer conditions**

The outer puzzle generates its own conditions based on its rules. These
might enforce supply limits, time locks, puzzle announcements, or any
other restriction the outer puzzle is designed to apply.

**Step 3: Combine and return**

The outer puzzle merges both sets of conditions into a single list. The
blockchain validates ALL conditions in the combined list. This means:

- The inner puzzle's signature requirement is enforced.
- The inner puzzle's spending instructions are followed.
- The outer puzzle's restrictions are ALSO enforced.
- If ANY condition fails, the entire spend is invalid.

### A More Complete Example

Here is a more realistic outer puzzle that enforces a maximum spend amount:

```lisp
(mod (MAX_AMOUNT           ; curried: the spending limit
      INNER_PUZZLE         ; curried: the ownership puzzle
      inner_solution       ; runtime: solution for inner puzzle
     )

    (include condition_codes.clib)

    ; Helper: scan conditions and sum all CREATE_COIN amounts
    (defun sum_create_coins (conditions)
        (if conditions
            (if (= (f (f conditions)) CREATE_COIN)
                ; This is a CREATE_COIN condition -- add its amount
                ; CREATE_COIN format: (51 puzzle_hash amount)
                (+ (f (r (r (f conditions))))   ; amount of this one
                   (sum_create_coins (r conditions))  ; plus the rest
                )
                ; Not a CREATE_COIN, skip it
                (sum_create_coins (r conditions))
            )
            0  ; base case: no more conditions
        )
    )

    ; Run the inner puzzle
    (assign
        inner_conditions (a INNER_PUZZLE inner_solution)
        total_output (sum_create_coins inner_conditions)

        ; Verify the restriction
        (if (> total_output MAX_AMOUNT)
            (x "Output exceeds maximum allowed amount")
            ; All good -- return inner conditions
            ; (the outer puzzle does not need to add extra conditions here
            ;  because the check itself is the restriction)
            inner_conditions
        )
    )
)
```

### Understanding the Data Flow

```
  SPEND TRANSACTION
  =================

  Puzzle Reveal: outer_puzzle(curried with MAX_AMOUNT=1000, INNER_PUZZLE=p2_puzzle)
  Solution:      (inner_solution)

  Execution:
  ┌─────────────────────────────────────────────────────┐
  │ outer_puzzle receives:                               │
  │   MAX_AMOUNT = 1000       (from currying)            │
  │   INNER_PUZZLE = p2_puzzle (from currying)           │
  │   inner_solution = (...)  (from solution)            │
  │                                                      │
  │   1. inner_conditions = run(p2_puzzle, inner_sol)    │
  │      => ((51 0xabc 500) (51 0xdef 300) (50 ...))    │
  │                                                      │
  │   2. total = 500 + 300 = 800                         │
  │                                                      │
  │   3. 800 <= 1000? YES => return inner_conditions     │
  │                                                      │
  │   Output: ((51 0xabc 500) (51 0xdef 300) (50 ...))  │
  └─────────────────────────────────────────────────────┘
```

### Nesting Multiple Layers

Inner puzzles can themselves contain inner puzzles, creating layers:

```lisp
; Layer 1: Time-lock wrapper
(mod (LOCK_TIME INNER_PUZZLE_1 inner_solution_1)
    (c (list ASSERT_HEIGHT_RELATIVE LOCK_TIME)
       (a INNER_PUZZLE_1 inner_solution_1)
    )
)

; Layer 2 (used as INNER_PUZZLE_1): Amount-limit wrapper
(mod (MAX_AMOUNT INNER_PUZZLE_2 inner_solution_2)
    ; ... amount checking logic ...
    (a INNER_PUZZLE_2 inner_solution_2)
)

; Layer 3 (used as INNER_PUZZLE_2): Standard transaction
; ... signature verification ...
```

The solution for this nested structure would also be nested:

```
solution = (inner_solution_1)
where inner_solution_1 = (inner_solution_2)
where inner_solution_2 = (delegated_puzzle delegated_solution)
```

---

## 5. The Standard Transaction (p2_delegated_or_hidden)

### The Most Important Puzzle in Chia

The **standard transaction puzzle**, officially called `p2_delegated_puzzle_or_hidden_puzzle`,
is the puzzle that locks virtually every XCH coin in existence. Understanding it
is essential for working with Chia.

Its full name reveals its two modes of operation:

1. **Delegated puzzle** -- The owner provides a puzzle and solution at spend time.
2. **Hidden puzzle** -- A pre-committed puzzle baked in via a taproot-style construction.

### How the Delegated Path Works

In delegated mode, the owner does not commit to any specific spending logic at
the time the coin is created. Instead, at spend time, the owner provides:

1. A **delegated puzzle** -- any arbitrary puzzle they want to run.
2. A **delegated solution** -- the solution for that delegated puzzle.
3. A **signature** -- proving they authorized this specific delegated puzzle.

```
  COIN CREATION                          COIN SPENDING
  =============                          =============

  puzzle = standard_transaction           solution = (delegated_puzzle
  curried with SYNTHETIC_KEY                         delegated_solution)

  The coin just sits there,              The owner provides any puzzle
  waiting to be spent by whoever         they want. The standard tx
  knows the private key.                 puzzle runs it and returns
                                         those conditions, plus a
                                         signature requirement.
```

The standard transaction puzzle essentially does this:

```lisp
; Simplified version (the real one is more complex)
(mod (SYNTHETIC_KEY delegated_puzzle delegated_solution)
    (c
        ; Require a signature from the owner on the delegated puzzle hash
        (list AGG_SIG_ME SYNTHETIC_KEY (sha256tree delegated_puzzle))
        ; Run the delegated puzzle and return its conditions
        (a delegated_puzzle delegated_solution)
    )
)
```

This is incredibly powerful. The owner can create ANY conditions they want at
spend time, as long as they sign the delegated puzzle. Want to send coins
somewhere? Provide a delegated puzzle that outputs CREATE_COIN. Want to do
something complex? Provide a complex delegated puzzle. The standard transaction
does not care -- it just requires a valid signature.

### The Hidden Puzzle Path

The hidden puzzle path is a taproot-style construction. At coin creation time,
a puzzle hash can be "hidden" inside the synthetic key using a cryptographic
commitment:

```
synthetic_key = original_key + hash(original_key, hidden_puzzle_hash) * G
```

Where `G` is the generator point of the BLS curve. This means:

- If you only know `original_key`, you can still use delegated mode.
- If you know `original_key` AND `hidden_puzzle_hash`, you can spend via
  the hidden puzzle without revealing `original_key`.
- Nobody can tell from the `synthetic_key` whether a hidden puzzle exists.

```
  SYNTHETIC KEY CONSTRUCTION
  ==========================

  original_key ─────────┐
                        ├──── synthetic_key
  hidden_puzzle_hash ───┘
       (optional)

  The synthetic_key looks like a normal public key.
  Nobody can tell if there is a hidden puzzle inside.
```

This is used for things like:
- **Clawback puzzles** -- coins that can be recovered after a timeout.
- **Atomic swaps** -- hidden conditions that enable trustless exchanges.
- **Default hidden puzzle** -- In normal wallets, the hidden puzzle is
  `(=)` which always fails, meaning the hidden path is never used and
  only delegated mode works.

### Why This Design is Powerful

The standard transaction is the **universal inner puzzle**. Because it can
run any delegated puzzle, it provides infinite flexibility. When you wrap it
inside an outer puzzle (like a CAT), the owner can still do anything they
want -- the only additional constraints come from the outer puzzle.

```
  CAT PUZZLE (outer)
  ├── Enforces: token supply preservation
  └── STANDARD TRANSACTION (inner)
      ├── Owner provides delegated puzzle at spend time
      ├── Can create any conditions
      └── Must sign with their key

  Result: The owner has full flexibility WITHIN the CAT rules
```

This is why Chia's architecture is so composable. You do not need to write
custom inner puzzles for every use case. The standard transaction handles
ownership, and outer puzzles add restrictions.

---

## 6. Puzzle Hashing

### Treehash: The SHA-256 of CLVM Structure

Every CLVM program is a binary tree of atoms and pairs. The **treehash** is
computed recursively over this tree structure:

```
treehash(atom)    = sha256(0x01 + atom_bytes)
treehash(pair)    = sha256(0x02 + treehash(left) + treehash(right))
```

The prefix bytes (`0x01` for atoms, `0x02` for pairs) ensure that an atom
can never have the same hash as a pair, preventing collision attacks.

Example:

```
  Program: (+ 2 5)

  Tree structure:
         (+ . (2 . (5 . ())))

  As a binary tree:
           cons
          /    \
        +       cons
               /    \
              2      cons
                    /    \
                   5     ()

  treehash:
    h(+)    = sha256(0x01 + bytes(+))    = 0x...
    h(2)    = sha256(0x01 + bytes(2))    = 0x...
    h(5)    = sha256(0x01 + bytes(5))    = 0x...
    h(())   = sha256(0x01 + bytes(nil))  = 0x...
    h(5,()) = sha256(0x02 + h(5) + h(())) = 0x...
    ... and so on up the tree
```

### Why Puzzle Hash Matters

The puzzle hash is one of the three defining properties of a coin:

```
coin_id = sha256(parent_coin_id + puzzle_hash + amount)
```

This means:

1. **Different puzzle = different coin identity.** If you change even one
   byte of the puzzle, the hash changes, and it is a fundamentally different
   coin type.

2. **Puzzle hash is a commitment.** When a coin is created, only the puzzle
   HASH is recorded on chain, not the full puzzle. The actual puzzle code is
   only revealed when the coin is spent. This provides privacy -- nobody
   knows what kind of coin it is until it is spent.

3. **You can compute puzzle hashes offline.** Since the hash is deterministic,
   you can compute the puzzle hash for any combination of curried parameters
   without interacting with the blockchain. This is essential for building
   wallets and drivers.

### Using cdv to Compute Puzzle Hashes

```bash
# Compute the puzzle hash of a compiled puzzle
$ cdv clsp treehash my_puzzle.clsp.hex
0x1a2b3c4d5e6f...

# Compile and hash in one step (from source)
$ run my_puzzle.clsp | cdv clsp treehash --bytes -
0x1a2b3c4d5e6f...

# Hash a curried puzzle
$ cdv clsp curry my_puzzle.clsp -a 42 --treehash
0xaabbccddee...
```

### Puzzle Hash vs. Puzzle Reveal

There is an important distinction between these two concepts:

```
  PUZZLE HASH                           PUZZLE REVEAL
  ===========                           =============

  - 32 bytes (sha256 output)            - Variable size (full CLVM code)
  - Stored on chain at coin creation    - Provided at spend time
  - Part of the coin ID calculation     - Must hash to the stored puzzle_hash
  - Public from the moment the coin     - Private until the coin is spent
    is created                          - Verified by all full nodes
  - Used to identify coin types         - Contains the actual logic
  - Enables address generation          - Enables spending
```

When someone sends you XCH, the CREATE_COIN condition specifies a puzzle_hash.
The network creates a coin with that hash. Nobody except you (the one who knows
the puzzle) can spend it, because spending requires revealing the full puzzle,
and nobody else knows what puzzle hashes to your puzzle_hash.

### Puzzle Hash and Addresses

Chia addresses (like `xch1...`) are simply bech32m-encoded puzzle hashes:

```
  xch1qyq0g30mp3t34su0es6750kzwpredhkqe0v6y2nzfv3n2gy4m3hqyqa7d6

  Decode:
  ┌────────┬──────────────────────────────────────┐
  │ Prefix │ xch1                                  │
  │ Data   │ puzzle_hash (32 bytes, bech32m)       │
  │ Check  │ checksum (error detection)            │
  └────────┴──────────────────────────────────────┘
```

So when you "send XCH to an address," you are really creating a coin whose
puzzle_hash is the decoded address. The recipient can spend it because they
know the full puzzle (the standard transaction curried with their key) that
hashes to that puzzle_hash.

### Computing Puzzle Hashes for Curried Puzzles

When you curry values into a puzzle, the puzzle hash changes. You can compute
the hash of a curried puzzle without actually constructing the full curried
puzzle, using a technique called **curry-treehash**:

```
curried_puzzle_hash = sha256(
    0x02,
    sha256(0x02, APPLY_HASH, sha256(0x02, QUOTE_HASH, original_puzzle_hash)),
    sha256(0x02, CURRY_WRAPPER_HASH, curried_args_hash)
)
```

This is useful in drivers (Chapter 4) where you need to compute puzzle hashes
for coins that do not exist yet. For example, when creating a CAT, you need
to know the puzzle hash of the resulting coin before you create it.

In Python, the `chia-blockchain` library provides utilities:

```python
from chia.wallet.puzzles.load_clvm import load_clvm
from chia.types.blockchain_format.program import Program

# Load and curry
MOD = load_clvm("my_puzzle.clsp")
curried = MOD.curry(42, 100)

# Get the puzzle hash
puzzle_hash = curried.get_tree_hash()
```

---

## Summary

| Concept | Description |
|---------|-------------|
| **Currying** | Baking arguments into a puzzle at compile time, changing its hash |
| **`(c (q . V) 1)`** | The CLVM pattern that prepends a value to the environment |
| **Inner puzzle** | A puzzle embedded inside an outer puzzle for composition |
| **Outer puzzle** | Adds restrictions/features on top of an inner puzzle |
| **Standard transaction** | The universal inner puzzle; runs any delegated puzzle with signature |
| **Puzzle hash** | SHA-256 treehash of CLVM tree; defines coin identity |
| **Puzzle reveal** | Full CLVM code shown at spend time; must match puzzle hash |

---

## Exercises

1. **Curry practice:** Take the `simple_curry.clsp` example and curry it with
   different values. Verify that each produces a different puzzle hash.

2. **Read the inner puzzle pattern:** Look at `rate_limited_wallet.clsp` and
   trace the execution flow. What happens if someone tries to create coins
   totaling more than MAX_AMOUNT?

3. **Compose layers:** Using `timelocked_wrapper.clsp` as the outer puzzle
   and `multi_sig.clsp` as the inner puzzle, describe how you would construct
   a time-locked multisig coin. What would the solution look like?

4. **Hash prediction:** Given a puzzle template and specific curry arguments,
   use `cdv clsp curry --treehash` to predict the puzzle hash. Verify by
   actually currying and hashing the result.

---

## Example Files

| File | Description |
|------|-------------|
| [`examples/simple_curry.clsp`](examples/simple_curry.clsp) | Basic greeting puzzle demonstrating currying |
| [`examples/rate_limited_wallet.clsp`](examples/rate_limited_wallet.clsp) | Outer puzzle enforcing a spending limit |
| [`examples/multi_sig.clsp`](examples/multi_sig.clsp) | 2-of-3 multisig puzzle |
| [`examples/delegated_puzzle.clsp`](examples/delegated_puzzle.clsp) | Simplified delegated spending (like standard tx) |
| [`examples/timelocked_wrapper.clsp`](examples/timelocked_wrapper.clsp) | Generic timelock wrapper for any inner puzzle |

---

**Next:** [Chapter 4 -- Python Drivers](../04-python-drivers/README.md)
