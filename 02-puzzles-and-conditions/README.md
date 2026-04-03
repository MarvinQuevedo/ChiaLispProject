# Chapter 2: Puzzles and Conditions

## Table of Contents

1. [The Coin Model](#1-the-coin-model)
2. [What is a Puzzle?](#2-what-is-a-puzzle)
3. [Conditions - The Output Language](#3-conditions---the-output-language)
4. [Announcements - Cross-Coin Communication](#4-announcements---cross-coin-communication)
5. [Signatures](#5-signatures)
6. [Security Patterns](#6-security-patterns)

---

## 1. The Coin Model

### Everything is a Coin

In Chia, there are no "accounts" or "balances" like in Ethereum. Instead, the entire
blockchain state is represented as a set of **unspent coins**. This is called the
**UTXO model** (Unspent Transaction Output), similar to Bitcoin but with a critical
difference: in Chia, every coin carries a **program** (a puzzle) that determines how
it can be spent.

```
  Traditional Account Model          Chia Coin Model
  ========================          ================

  Account: 0xABC...                 Coin #1: 0.5 XCH (puzzle A)
  Balance: 3.5 XCH                  Coin #2: 1.0 XCH (puzzle A)
                                    Coin #3: 2.0 XCH (puzzle B)
                                    -------------------------
                                    Total:   3.5 XCH
```

### The Three Properties of a Coin

Every coin in Chia has exactly **three properties**:

```
+---------------------------------------------+
|                   COIN                       |
+---------------------------------------------+
| parent_coin_id  : bytes32 (32 bytes)         |
| puzzle_hash     : bytes32 (32 bytes)         |
| amount          : uint64  (in mojos)         |
+---------------------------------------------+
```

1. **parent_coin_id** - The coin ID of the coin that created this one. Every coin
   (except the genesis coin) was created by spending another coin.

2. **puzzle_hash** - The SHA-256 hash of the compiled ChiaLisp puzzle that locks
   this coin. This determines WHO can spend it and UNDER WHAT CONDITIONS.

3. **amount** - How many mojos this coin is worth (1 XCH = 1,000,000,000,000 mojos).

### How coin_id is Computed

The coin's unique identifier is computed deterministically:

```
coin_id = sha256(parent_coin_id + puzzle_hash + amount)
```

This means:
- No two coins can have the same ID (unless they share all three properties,
  which the network prevents).
- You can compute a coin's ID without seeing it on chain, as long as you know
  its three properties.
- The coin ID is NOT stored anywhere on chain -- it is derived.

```
  parent_coin_id (32 bytes)
         |
         v
  +-------------+
  |             |
  |   SHA-256   | <--- puzzle_hash (32 bytes)
  |             |
  +-------------+ <--- amount (8 bytes, big-endian)
         |
         v
    coin_id (32 bytes)
```

### Coins are Immutable (UTXO Model)

You **never modify** a coin. Instead:

1. You **spend** an existing coin (it is destroyed).
2. The puzzle outputs **conditions** that can **create new coins**.
3. The new coins become part of the blockchain state.

```
  BEFORE SPEND                    AFTER SPEND
  ============                    ===========

  [Coin A: 5 XCH] --spend-->     [Coin B: 3 XCH]  (payment)
                                  [Coin C: 2 XCH]  (change)

  Coin A is DESTROYED.
  Coins B and C are CREATED.
```

This is exactly like physical cash: you cannot modify a $5 bill to become a $3 bill.
You break it into a $3 and a $2. The original is gone.

### A Coin's Puzzle Determines Everything

The puzzle_hash in a coin references a ChiaLisp program. This program is the
**sole authority** over what happens when the coin is spent. There is no external
authority, no admin key, no override -- only the puzzle's logic matters.

```
  "Who can spend this coin?"       --> Determined by the puzzle
  "Where can the funds go?"        --> Determined by the puzzle
  "Are there time restrictions?"   --> Determined by the puzzle
  "Does it need a signature?"      --> Determined by the puzzle
```

---

## 2. What is a Puzzle?

### Puzzles are Programs

A **puzzle** is a ChiaLisp program that is referenced by coins (via its hash).
When someone wants to spend a coin, they must:

1. **Reveal the puzzle** -- provide the full source of the compiled program
   (so nodes can verify its hash matches the coin's puzzle_hash).
2. **Provide a solution** -- arguments to the puzzle (like function parameters).
3. The puzzle **runs** with the solution and produces **conditions**.
4. If the conditions are valid and satisfiable, the spend succeeds.

```
                +------------------+
  solution ---->|                  |
                |     PUZZLE       |----> CONDITIONS
                |  (ChiaLisp pgm) |      (list of opcodes)
                |                  |
                +------------------+

  The puzzle is the JUDGE.
  The solution is the EVIDENCE.
  The conditions are the VERDICT.
```

### The Puzzle Lifecycle

```
  Step 1: WRITE the puzzle in ChiaLisp (.clsp file)
          (mod (password) ...)

  Step 2: COMPILE the puzzle to CLVM (serialized bytecode)
          $ run my_puzzle.clsp
          (a (q 2 ... ) (c (q . ...) 1))

  Step 3: HASH the compiled puzzle
          $ opc -H '(a (q 2 ...) ...)'
          puzzle_hash = 0x4bf5122f...

  Step 4: CREATE a coin that references this puzzle_hash
          The coin now exists on chain, locked by this puzzle.

  Step 5: SPEND the coin by revealing the full puzzle + solution
          Nodes verify: sha256(revealed_puzzle) == coin.puzzle_hash
          Then run: (revealed_puzzle solution) --> conditions
```

### The Puzzle Hash Matters

On-chain, coins only store the **puzzle_hash** (32 bytes), not the full puzzle.
This is important:

- It is space-efficient (puzzles can be large).
- The puzzle code is only revealed at spend time (some privacy).
- The same puzzle hash can lock millions of coins.
- You cannot tell what a coin's puzzle does until it is spent.

### Puzzle vs. Solution

| Aspect      | Puzzle                        | Solution                     |
|-------------|-------------------------------|------------------------------|
| When set    | When coin is created          | When coin is spent           |
| Who decides | The coin creator              | The coin spender             |
| On chain    | Only the hash (until spent)   | Revealed at spend time       |
| Purpose     | Define the rules              | Provide the inputs           |
| Analogy     | A lock                        | A key                        |

### A Simple Example

```chialisp
; Puzzle: require the solution to be the number 42
(mod (answer)
  (if (= answer 42)
    ()        ; success - return empty conditions
    (x)       ; fail - raise exception
  )
)
```

If you provide solution `(42)`, the puzzle succeeds.
If you provide solution `(99)`, the puzzle raises an exception and the spend fails.

---

## 3. Conditions - The Output Language

When a puzzle runs successfully, it returns a **list of conditions**. Each condition
is a list where the first element is an **opcode** (a number) and the remaining
elements are arguments to that condition.

```
conditions = (
  (OPCODE_1  arg1  arg2  ...)
  (OPCODE_2  arg1  arg2  ...)
  ...
)
```

The Chia network reads these conditions and enforces them. If any condition cannot
be satisfied, the entire spend (and possibly the entire transaction) fails.

Here is every important condition opcode:

---

### CREATE_COIN (51)

**Creates a new coin on the blockchain.**

```
(51 puzzle_hash amount ...memos)
```

| Argument    | Type    | Description                              |
|-------------|---------|------------------------------------------|
| puzzle_hash | bytes32 | The puzzle hash for the new coin         |
| amount      | uint64  | Amount in mojos                          |
| memos       | list    | Optional. Used for hints/recipient info  |

**What it does:**
This is how value moves in Chia. When you spend a coin, you create new coins with
CREATE_COIN. The sum of created coin amounts (plus fees) must equal the sum of
spent coin amounts in the transaction.

**When to use it:**
- Every time you want to send funds somewhere
- Creating change coins (sending leftover back to yourself)
- Creating new smart coins with different puzzles

**Common patterns:**

```chialisp
; Send 1 XCH to someone (puzzle_hash is their address)
(51 0xTHEIR_PUZZLE_HASH 1000000000000)

; Create a change coin back to yourself
(51 0xMY_PUZZLE_HASH remaining_amount)

; Create a coin with memos (for wallet discovery)
(51 0xTHEIR_PUZZLE_HASH 1000000000000 (0xTHEIR_PUZZLE_HASH))
```

**Security implications:**
- If your puzzle does NOT include a CREATE_COIN condition, anyone who spends
  your coin can direct the funds ANYWHERE. This is the #1 beginner mistake.
- Always control where funds go by having the puzzle dictate CREATE_COIN.

---

### AGG_SIG_UNSAFE (49)

**Requires a BLS aggregate signature from a specific public key over a specific message.**

```
(49 public_key message)
```

| Argument   | Type    | Description                                 |
|------------|---------|---------------------------------------------|
| public_key | G1Point | 48-byte BLS public key                      |
| message    | bytes   | The message that must be signed              |

**What it does:**
Requires that the transaction's aggregated BLS signature includes a signature from
the given public key over the given message. "Unsafe" because the message is used
as-is, without binding to any specific coin.

**When to use it:**
- Almost never! Prefer AGG_SIG_ME in nearly all cases.
- Only use when you specifically need a signature not bound to a coin.
- Useful for signing messages that are shared across multiple spends.

**Security implications:**
- DANGEROUS: Since the message is not bound to a coin, an attacker who sees your
  signature can replay it in a different context. If you sign message "hello" on
  one coin, that same signature satisfies AGG_SIG_UNSAFE on ANY coin requiring
  "hello" signed by your key.
- This is a **replay attack vector**.

---

### AGG_SIG_ME (50)

**Requires a BLS aggregate signature bound to THIS specific coin.**

```
(50 public_key message)
```

| Argument   | Type    | Description                                 |
|------------|---------|---------------------------------------------|
| public_key | G1Point | 48-byte BLS public key                      |
| message    | bytes   | The message that must be signed              |

**What it does:**
Like AGG_SIG_UNSAFE, but the actual message that must be signed is:

```
signed_data = message + coin_id + GENESIS_CHALLENGE
```

This binds the signature to this specific coin on this specific blockchain (mainnet
vs testnet).

**When to use it:**
- Whenever you need to prove ownership / authorization.
- This is what the standard wallet transaction uses.
- Any time a human needs to approve a spend.

**Common patterns:**

```chialisp
; Require the owner's signature (standard pattern)
; PUBLIC_KEY is curried in, message is empty or specific
(50 PUBLIC_KEY ())
```

**Security implications:**
- SAFE against replay attacks: the signature is bound to this exact coin_id, so
  it cannot be reused on another coin.
- This is the correct choice 99% of the time.

---

### ASSERT_MY_COIN_ID (70)

**Verifies that this coin's ID matches the given value.**

```
(70 coin_id)
```

**What it does:**
Asserts that the coin being spent has the given coin_id. If it does not match,
the spend fails.

**When to use it:**
- Preventing a puzzle from being used on a different coin than intended.
- The solution can pass in the coin ID, and the puzzle verifies it.
- Critical for security when the solution contains CREATE_COIN conditions.

**Common patterns:**

```chialisp
; Verify we are the coin we think we are
(70 my_coin_id)  ; my_coin_id comes from solution
```

**Security implications:**
- Without this, an attacker could take your puzzle and put it on a different coin
  with a different amount, potentially stealing value.
- If your puzzle lets the solution dictate CREATE_COIN outputs, you MUST assert
  the coin ID to prevent the solution from being replayed on a richer coin.

---

### ASSERT_MY_PARENT_ID (71)

**Verifies this coin's parent coin ID.**

```
(71 parent_coin_id)
```

**What it does:**
Asserts that the coin being spent was created by the coin with the given ID.

**When to use it:**
- Verifying lineage (important for singletons and CATs).
- Ensuring a coin was created by a specific parent.
- Building chains of coins that track their history.

**Security implications:**
- Important for protocols that rely on provenance (e.g., NFTs, singletons).

---

### ASSERT_MY_PUZZLEHASH (72)

**Verifies this coin's puzzle hash.**

```
(72 puzzle_hash)
```

**What it does:**
Asserts that the coin being spent has the given puzzle hash.

**When to use it:**
- When the puzzle needs to know its own hash (for creating child coins with
  the same puzzle).
- Self-referencing puzzles that perpetuate themselves.
- Useful in combination with ASSERT_MY_AMOUNT for full self-identification.

**Common patterns:**

```chialisp
; Create a child coin with the same puzzle
(72 MY_PUZZLE_HASH)        ; assert our own puzzle hash
(51 MY_PUZZLE_HASH amount) ; create child with same puzzle
```

---

### ASSERT_MY_AMOUNT (73)

**Verifies this coin's amount.**

```
(73 amount)
```

**What it does:**
Asserts that the coin being spent holds the given amount in mojos.

**When to use it:**
- When the puzzle needs to know how much value it holds.
- Critical for preventing a "value drain" attack.
- Often used with ASSERT_MY_COIN_ID or ASSERT_MY_PUZZLEHASH.

**Security implications:**
- Without asserting the amount, an attacker could create a copy of your puzzle
  on a coin with more value, spend it with your conditions, and steal the
  difference as fees. Always assert amount if your puzzle creates output coins.

**Example of the attack prevented:**

```
  Your coin: 100 mojos, puzzle creates coin of 100 mojos to recipient.
  Attacker creates: 1000 mojos coin with SAME puzzle.
  Attacker spends it: puzzle creates coin of 100 mojos to recipient.
  900 mojos become fees --> stolen by attacker (as a farmer).

  Fix: puzzle asserts (73 100), so it cannot run on the 1000-mojo coin.
```

---

### ASSERT_SECONDS_RELATIVE (80)

**The spend is only valid if at least N seconds have passed since this coin was created.**

```
(80 seconds)
```

**When to use it:**
- Time-locked coins: "cannot spend for 1 hour after creation."
- Cooling-off periods.
- Rate limiting.

---

### ASSERT_SECONDS_ABSOLUTE (81)

**The spend is only valid after a specific UNIX timestamp.**

```
(81 timestamp)
```

**When to use it:**
- "Cannot spend before January 1, 2027."
- Vesting schedules.
- Scheduled releases.

---

### ASSERT_HEIGHT_RELATIVE (82)

**The spend is only valid if at least N blocks have been created since this coin was created.**

```
(82 block_count)
```

**When to use it:**
- Block-based time locks (more predictable than seconds).
- "Must wait 32 blocks before spending."
- In Chia, blocks average about 18.75 seconds apart (~46 seconds per transaction block).

---

### ASSERT_HEIGHT_ABSOLUTE (83)

**The spend is only valid after a specific block height.**

```
(83 block_height)
```

**When to use it:**
- "Cannot spend before block 5,000,000."
- Coordinating events at specific block heights.

---

### RESERVE_FEE (52)

**Reserves a minimum transaction fee.**

```
(52 fee_amount)
```

**What it does:**
Ensures that at least `fee_amount` mojos are left over as fees in this transaction.
The fee is the difference between total input and total output amounts.

**When to use it:**
- When a puzzle wants to guarantee a minimum fee is paid.
- Preventing spam or ensuring timely inclusion in a block.

**Common patterns:**

```chialisp
; Require at least 1 million mojos in fees
(52 1000000)
```

---

### CREATE_COIN_ANNOUNCEMENT (60)

**Creates an announcement that other coins can assert.**

```
(60 message)
```

**What it does:**
Broadcasts a message from this coin. The announcement ID is:

```
announcement_id = sha256(coin_id + message)
```

Other coins in the same transaction can assert this announcement exists.

**When to use it:**
- Coordinating multiple coin spends in the same transaction.
- Ensuring two coins are spent together atomically.
- The foundation of cross-coin communication.

---

### ASSERT_COIN_ANNOUNCEMENT (61)

**Asserts that a specific coin announcement exists in this transaction.**

```
(61 announcement_id)
```

Where `announcement_id = sha256(announcing_coin_id + message)`.

**What it does:**
Fails the spend unless another coin in the same transaction created a matching
announcement. This creates a dependency between coins.

**When to use it:**
- Requiring that a specific other coin is being spent alongside this one.
- Atomic swaps and paired spends.

---

### CREATE_PUZZLE_ANNOUNCEMENT (62)

**Creates a puzzle announcement (identified by puzzle hash instead of coin ID).**

```
(62 message)
```

The announcement ID is:

```
announcement_id = sha256(puzzle_hash + message)
```

**When to use it:**
- When you need announcements tied to a puzzle type rather than a specific coin.
- Useful when you do not know the exact coin ID in advance.

---

### ASSERT_PUZZLE_ANNOUNCEMENT (63)

**Asserts that a specific puzzle announcement exists.**

```
(63 announcement_id)
```

Where `announcement_id = sha256(announcing_puzzle_hash + message)`.

---

### REMARK (1)

**A no-op condition. Does nothing but can carry data.**

```
(1 ...data)
```

**What it does:**
Nothing. The blockchain ignores it. But the data is visible on chain, so it can
be used for metadata, comments, or tagging.

**When to use it:**
- Storing metadata with a spend.
- Tagging transactions for off-chain indexing.

---

### Quick Reference Table

```
+--------+------------------------------+---------------------------------------+
| Opcode | Name                         | Arguments                             |
+--------+------------------------------+---------------------------------------+
|   1    | REMARK                       | ...data                               |
|  49    | AGG_SIG_UNSAFE               | pubkey, message                       |
|  50    | AGG_SIG_ME                   | pubkey, message                       |
|  51    | CREATE_COIN                  | puzzle_hash, amount, ...memos         |
|  52    | RESERVE_FEE                  | amount                                |
|  60    | CREATE_COIN_ANNOUNCEMENT     | message                               |
|  61    | ASSERT_COIN_ANNOUNCEMENT     | announcement_id                       |
|  62    | CREATE_PUZZLE_ANNOUNCEMENT   | message                               |
|  63    | ASSERT_PUZZLE_ANNOUNCEMENT   | announcement_id                       |
|  70    | ASSERT_MY_COIN_ID            | coin_id                               |
|  71    | ASSERT_MY_PARENT_ID          | parent_id                             |
|  72    | ASSERT_MY_PUZZLEHASH         | puzzle_hash                           |
|  73    | ASSERT_MY_AMOUNT             | amount                                |
|  80    | ASSERT_SECONDS_RELATIVE      | seconds                               |
|  81    | ASSERT_SECONDS_ABSOLUTE      | timestamp                             |
|  82    | ASSERT_HEIGHT_RELATIVE       | block_count                           |
|  83    | ASSERT_HEIGHT_ABSOLUTE       | block_height                          |
+--------+------------------------------+---------------------------------------+
```

---

## 4. Announcements - Cross-Coin Communication

### The Problem

In Chia, each coin is spent independently. Its puzzle runs, produces conditions,
and those conditions are validated. But what if you need **two coins to be spent
together**? What if coin A should only be spent if coin B is also spent in the
same transaction?

This is where **announcements** come in.

### How Announcements Work

Announcements are ephemeral messages that exist only within a single transaction.
They are created by one coin and asserted by another.

```
  Transaction
  ================================================

  Coin A spends:
    CONDITIONS:
      (60 "hello")                  ; CREATE_COIN_ANNOUNCEMENT "hello"

  Coin B spends:
    CONDITIONS:
      (61 <sha256(coinA_id + "hello")>)  ; ASSERT_COIN_ANNOUNCEMENT

  ================================================

  Result: Both spends succeed ONLY if they are in the same transaction.
  If coin A is not present, coin B's assertion fails.
  If coin B is not present, coin A still succeeds (it only creates, does not assert).
```

### Announcement ID Computation

For **coin announcements**:
```
announcement_id = sha256(coin_id_of_announcer + message)
```

For **puzzle announcements**:
```
announcement_id = sha256(puzzle_hash_of_announcer + message)
```

The coin_id binding means that a coin announcement is unique to that specific coin.
No other coin can fake it.

### Bidirectional Announcements (Atomic Pair)

For truly atomic operations, both coins should announce AND assert:

```
  Coin A:                           Coin B:
  ------                            ------
  CREATE_COIN_ANNOUNCEMENT "a2b"    CREATE_COIN_ANNOUNCEMENT "b2a"
  ASSERT_COIN_ANNOUNCEMENT          ASSERT_COIN_ANNOUNCEMENT
    sha256(B_id + "b2a")              sha256(A_id + "a2b")

  Neither can be spent without the other!
```

This is the foundation of:
- **Atomic swaps**: "I will give you my XCH only if you give me your CAT."
- **Offer files**: Chia's decentralized exchange uses this exact pattern.
- **Singleton state updates**: The singleton launcher and child use announcements.

### Detailed Example: Paired Vault Coins

Imagine a vault with two coins: a "key coin" and a "vault coin." The vault can
only be opened if the key coin is spent in the same transaction.

```
  +-------------------+          +-------------------+
  |    KEY COIN       |          |   VAULT COIN      |
  |                   |          |                   |
  | Creates announce: |--------->| Asserts announce: |
  | "open_sesame"     |          | sha256(key_id +   |
  |                   |          |   "open_sesame")  |
  +-------------------+          +-------------------+

  The vault coin CANNOT be spent unless the key coin
  is also spent in the same transaction.
```

### Coin Announcements vs. Puzzle Announcements

| Aspect              | Coin Announcement (60/61)        | Puzzle Announcement (62/63)     |
|---------------------|----------------------------------|---------------------------------|
| ID includes         | coin_id of announcer             | puzzle_hash of announcer        |
| Uniqueness          | Unique to specific coin          | Any coin with that puzzle       |
| Use case            | Specific coin must participate   | Any coin of a type can satisfy  |
| Security            | Stronger (tied to exact coin)    | Weaker (any matching puzzle)    |

**Rule of thumb:** Use coin announcements unless you have a specific reason to use
puzzle announcements.

---

## 5. Signatures

### BLS Signatures in Chia

Chia uses **BLS (Boneh-Lynn-Shacham) signatures** on the **BLS12-381 curve**. The
key property of BLS signatures is **aggregation**: multiple signatures from multiple
keys can be combined into a single signature that can be verified in one step.

```
  Traditional:                    BLS Aggregation:
  sig1 = sign(sk1, msg1)         sig1 = sign(sk1, msg1)
  sig2 = sign(sk2, msg2)         sig2 = sign(sk2, msg2)
  sig3 = sign(sk3, msg3)         agg_sig = aggregate(sig1, sig2, sig3)

  Verify: check each one         Verify: check agg_sig against
  individually (3 checks)        all (pk, msg) pairs (1 check)
```

A Chia transaction carries exactly ONE aggregated signature. All the AGG_SIG
conditions from all coin spends in the transaction are verified against this
single signature.

### Public Key / Private Key

```
  Private Key (sk)         Public Key (pk)
  32 bytes                 48 bytes (G1 point)
  KEEP SECRET              Share freely

  sk ---derive---> pk      (one-way, cannot reverse)

  sign(sk, message) --> signature (96 bytes, G2 point)
  verify(pk, message, signature) --> true/false
```

### How AGG_SIG_ME Works in Detail

When a puzzle outputs `(50 pk msg)`:

```
  1. The network computes the ACTUAL message to verify:
     actual_msg = msg + coin_id + GENESIS_CHALLENGE

     - coin_id: the ID of the coin being spent (binds to this coin)
     - GENESIS_CHALLENGE: a constant per-network value
       (different for mainnet and testnet, binds to this chain)

  2. The aggregated signature in the transaction must include
     a valid signature: sign(sk, actual_msg) where sk corresponds to pk.

  3. All AGG_SIG conditions across all spends in the transaction are
     collected, and the single aggregated signature is verified
     against all of them at once.
```

### Why AGG_SIG_ME is Safer than AGG_SIG_UNSAFE

```
  AGG_SIG_UNSAFE (49):
  --------------------
  actual_msg = msg              (just the raw message)

  Problem: If you sign "approve" for coin A, that same signature
  works for ANY coin that requires "approve" signed by your key.
  An attacker can REPLAY your signature.


  AGG_SIG_ME (50):
  -----------------
  actual_msg = msg + coin_id + GENESIS_CHALLENGE

  Safe: The signature is bound to THIS coin on THIS network.
  It cannot be replayed on any other coin, or on testnet vs mainnet.
```

### The Standard Signature Pattern

The most common pattern (used by the standard wallet):

```chialisp
(mod (PUBLIC_KEY conditions)
  ; PUBLIC_KEY is curried in (baked into the puzzle)
  ; conditions come from the solution (delegated spending)

  (list
    (50 PUBLIC_KEY (sha256tree conditions))  ; sign the conditions
    ; ...then include the conditions
  )
)
```

The owner signs the exact conditions they want, preventing anyone from
modifying the transaction.

---

## 6. Security Patterns

### Why You Must Assert Your Own Coin ID

This is the **most important security lesson** in ChiaLisp. Consider this puzzle:

```chialisp
; INSECURE: password coin that lets solution specify outputs
(mod (password conditions)
  (if (= (sha256 password) 0xABC123...)
    conditions           ; return whatever the spender wants
    (x)
  )
)
```

The problem: once someone sees the password in a spend on-chain, they can:
1. Create a NEW coin with the same puzzle but a LARGER amount.
2. Spend it with the same password but different conditions.
3. Steal the extra funds.

**Fix: assert the coin ID in the solution.**

```chialisp
(mod (password my_coin_id conditions)
  (if (= (sha256 password) 0xABC123...)
    (c (list 70 my_coin_id) conditions)  ; ASSERT_MY_COIN_ID
    (x)
  )
)
```

Now the spend is bound to ONE specific coin and cannot be replayed.

### Preventing Replay Attacks

A **replay attack** is when someone takes a valid spend and reuses it on a
different coin. Defenses:

1. **ASSERT_MY_COIN_ID (70):** Binds the spend to a specific coin.
2. **AGG_SIG_ME (50):** Binds the signature to a specific coin.
3. **ASSERT_MY_AMOUNT (73):** Prevents the puzzle from running on coins
   with different amounts.

```
  Defense Layers:
  +-------------------------------------------------+
  | AGG_SIG_ME: signature bound to this coin        |
  | +---------------------------------------------+ |
  | | ASSERT_MY_COIN_ID: conditions bound to coin | |
  | | +-----------------------------------------+ | |
  | | | ASSERT_MY_AMOUNT: amount verified       | | |
  | | | +-------------------------------------+ | | |
  | | | | CREATE_COIN: funds go where intended | | | |
  | | | +-------------------------------------+ | | |
  | | +-----------------------------------------+ | |
  | +---------------------------------------------+ |
  +-------------------------------------------------+
```

### Change Coins

When you spend a coin of 10 XCH but only want to send 3 XCH, you must create
a **change coin** for the remaining 7 XCH:

```
  Spend: Coin (10 XCH)
  Create: Payment Coin (3 XCH) --> to recipient
  Create: Change Coin  (7 XCH) --> back to yourself

  If you forget the change coin:
  10 XCH - 3 XCH = 7 XCH becomes FEES (lost!)
```

**Always account for all value.** The sum of CREATE_COIN amounts plus fees must
equal the sum of spent coin amounts.

```
  SUM(spent coins) = SUM(created coins) + fees

  If your puzzle does not create coins for ALL the value,
  the remainder becomes fees and is gone forever.
```

### The Secure Coin Pattern (Summary)

Every well-written coin should follow this pattern:

```chialisp
(mod (PUBLIC_KEY my_coin_id delegated_conditions)
  ; 1. Require a signature from the owner
  ; 2. Assert our own identity (prevent replay)
  ; 3. Return the signed conditions

  (c (list 50 PUBLIC_KEY (sha256tree delegated_conditions))  ; AGG_SIG_ME
  (c (list 70 my_coin_id)                                    ; ASSERT_MY_COIN_ID
     delegated_conditions                                    ; user's conditions
  ))
)
```

---

## Example Files

See the `examples/` directory for working ChiaLisp code:

| File                      | Description                                          |
|---------------------------|------------------------------------------------------|
| `anyone_can_spend.clsp`   | Simplest puzzle -- anyone can spend it (insecure)    |
| `password_coin.clsp`      | Password-locked coin (still insecure -- learn why)   |
| `secure_password_coin.clsp` | Password coin done right with all protections      |
| `signature_locked.clsp`   | BLS signature locked coin (standard pattern)         |
| `time_locked_coin.clsp`   | Time-locked coin with relative and absolute variants |
| `announcement_pair.clsp`  | Two coins that must be spent together                |

---

## Key Takeaways

1. **Coins are immutable.** You spend old coins and create new ones.
2. **Puzzles are the law.** The puzzle is the sole authority over a coin.
3. **Conditions are the output.** Every puzzle produces a list of conditions.
4. **Always assert your identity.** Use ASSERT_MY_COIN_ID to prevent replay.
5. **Use AGG_SIG_ME, not AGG_SIG_UNSAFE.** Bind signatures to specific coins.
6. **Account for all value.** Create change coins or lose funds to fees.
7. **Announcements enable coordination.** Use them for atomic multi-coin operations.

---

Next chapter: [Currying and Inner Puzzles](../03-currying-and-inner-puzzles/README.md)
