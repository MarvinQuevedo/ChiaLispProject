# Chapter 4: Python Drivers for ChiaLisp

This is arguably the most important chapter in this entire guide. You can write the
most elegant ChiaLisp puzzle in the world, but if you cannot create coins from it,
spend them, and track them on the blockchain, your puzzle is useless. That is what
**drivers** do.

If you have been struggling with this part, you are not alone. The gap between
"I wrote a .clsp file" and "I spent a coin on-chain" is wide, and the official
documentation scatters the pieces across many repos. This chapter puts everything
in one place.

---

## Table of Contents

1. [What is a Driver?](#1-what-is-a-driver)
2. [Key Chia Python Libraries](#2-key-chia-python-libraries)
3. [The Program Class - Your Best Friend](#3-the-program-class---your-best-friend)
4. [Building a Spend Bundle Step by Step](#4-building-a-spend-bundle-step-by-step)
5. [RPC Client Usage](#5-rpc-client-usage)
6. [Signing Transactions](#6-signing-transactions)
7. [Common Driver Patterns](#7-common-driver-patterns)
8. [Troubleshooting](#8-troubleshooting)

---

## 1. What is a Driver?

### The Problem

Imagine you have a ChiaLisp puzzle that locks coins so only a specific password can
unlock them. The puzzle itself is just CLVM bytecode -- a tree of atoms. The Chia
blockchain does not know or care what language you wrote it in. It only sees:

- A **coin** identified by (parent_id, puzzle_hash, amount)
- A **puzzle reveal** (the full CLVM program)
- A **solution** (the arguments you pass to that program)

Someone needs to:

1. **Compile** your `.clsp` source into CLVM bytecode
2. **Curry** any parameters into the puzzle
3. **Hash** the final puzzle to get a puzzle hash
4. **Derive** a bech32m address from that puzzle hash
5. **Fund** that address (send XCH to it, creating a coin)
6. **Find** the coin on the blockchain
7. **Build** a solution that satisfies the puzzle
8. **Sign** anything that needs signing
9. **Bundle** the coin spend into a SpendBundle
10. **Push** the SpendBundle to the mempool

That "someone" is your **driver**. It is Python code that does all ten steps above.

### The Mental Model

Think of it like this:

```
+------------------+       +------------------+       +------------------+
|   ChiaLisp       |       |   Python Driver  |       |   Chia           |
|   Puzzle (.clsp) | ----> |   (your code)    | ----> |   Blockchain     |
|                  |       |                  |       |                  |
|   "the rules"    |       |   "the glue"     |       |   "the ledger"   |
+------------------+       +------------------+       +------------------+
```

- The **puzzle** defines the rules (conditions under which a coin can be spent).
- The **driver** compiles the puzzle, constructs coins and spends, and talks to the node.
- The **blockchain** validates everything and records the result.

### What a Driver Typically Contains

A complete driver file usually has these sections:

```python
# 1. Imports
from chia.types.blockchain_format.program import Program
from chia.types.blockchain_format.coin import Coin
from chia.types.spend_bundle import SpendBundle
from chia.types.coin_spend import CoinSpend

# 2. Puzzle loading / compilation
#    Load the .clsp file, compile it, get a Program object

# 3. Puzzle customization (currying)
#    Curry in any parameters (public keys, amounts, hashes, etc.)

# 4. Address derivation
#    puzzle_hash -> bech32m address

# 5. Coin discovery
#    Use RPC to find coins locked by this puzzle

# 6. Spend construction
#    Build the solution, create CoinSpend, optionally sign, create SpendBundle

# 7. Submission
#    Push the SpendBundle to the mempool via RPC
```

---

## 2. Key Chia Python Libraries

Before writing any driver code, you need to understand the ecosystem of Python
packages you will be using. Here is the complete map:

### 2.1 chia-blockchain

This is the main Chia node software. When you `pip install chia-blockchain`, you get
access to a massive library of types and utilities. The key modules are:

| Module | Purpose |
|--------|---------|
| `chia.types.blockchain_format.coin` | The `Coin` dataclass |
| `chia.types.blockchain_format.program` | The `Program` class (CLVM programs) |
| `chia.types.blockchain_format.sized_bytes` | `bytes32` and similar types |
| `chia.types.spend_bundle` | `SpendBundle` |
| `chia.types.coin_spend` | `CoinSpend` |
| `chia.rpc.full_node_rpc_client` | `FullNodeRpcClient` for talking to the node |
| `chia.rpc.wallet_rpc_client` | `WalletRpcClient` for talking to the wallet |
| `chia.util.bech32m` | Encoding/decoding bech32m addresses (xch1...) |
| `chia.util.hash` | `std_hash()` for SHA256 |
| `chia.wallet.puzzles.load_clvm` | Loading pre-compiled puzzles |
| `chia.consensus.default_constants` | Network constants |

### 2.2 clvm_tools_rs

This is the Rust-based compiler for ChiaLisp. It is much faster than the pure Python
`clvm_tools` package.

```python
from clvm_tools_rs import compile_clvm_text

# Compile ChiaLisp source code to CLVM bytecode
bytecode = compile_clvm_text(
    source_text,           # Your .clsp source as a string
    search_paths=["./include"]  # Where to find include files
)
```

### 2.3 clvm

The low-level CLVM runtime. You rarely use it directly, but it powers `Program.run()`.

```python
from clvm.SExp import SExp
```

### 2.4 blspy (BLS Signatures)

Chia uses BLS12-381 signatures. The `blspy` library provides:

```python
from blspy import (
    AugSchemeMPL,      # The signing scheme Chia uses
    PrivateKey,         # BLS private key
    G1Element,          # BLS public key (48 bytes)
    G2Element,          # BLS signature (96 bytes)
)
```

### 2.5 Installation

```bash
# The recommended way: install chia-dev-tools which pulls in everything
pip install chia-dev-tools

# Or install individually
pip install chia-blockchain
pip install clvm_tools_rs
pip install blspy
```

---

## 3. The Program Class - Your Best Friend

The `Program` class from `chia.types.blockchain_format.program` is the single most
important class you will use. It represents a CLVM program (which is both code and
data in CLVM, since everything is an S-expression).

### 3.1 Creating Program Objects

There are several ways to create a `Program`:

```python
from chia.types.blockchain_format.program import Program

# ---------- Method 1: From a Python value ----------
# Program.to() converts Python objects to CLVM S-expressions
p = Program.to(1)              # The atom 1
p = Program.to("hello")       # The atom "hello" (as bytes)
p = Program.to([1, 2, 3])     # The list (1 2 3)
p = Program.to((1, (2, (3, None))))  # Same list, cons-pair notation
p = Program.to([1, [2, 3]])   # Nested: (1 (2 3))

# ---------- Method 2: From compiled CLVM hex ----------
p = Program.fromhex("ff02ffff01ff02ff02ffff04ff02ffff04ff05ff80808080ff0580")

# ---------- Method 3: From bytes ----------
p = Program.from_bytes(some_bytes)

# ---------- Method 4: Compile from source ----------
from clvm_tools_rs import compile_clvm_text

source = "(mod (X) (+ X 1))"
compiled_hex = compile_clvm_text(source, [])
puzzle = Program.fromhex(compiled_hex)
```

### 3.2 Running Programs Locally

This is incredibly useful for testing. You can run any puzzle with a solution
**without touching the blockchain**:

```python
from chia.types.blockchain_format.program import Program
from clvm_tools_rs import compile_clvm_text

# Compile a simple puzzle
source = "(mod (PASSWORD) (if (= PASSWORD 0xcafef00d) (q . 1) (x)))"
compiled = compile_clvm_text(source, [])
puzzle = Program.fromhex(compiled)

# Run it with a correct password
solution = Program.to([0xcafef00d])
result = puzzle.run(solution)
print(f"Result: {result}")  # Result: 1

# Run it with a wrong password
try:
    bad_solution = Program.to([0xdeadbeef])
    result = puzzle.run(bad_solution)
except Exception as e:
    print(f"Failed as expected: {e}")  # The (x) raises an exception
```

**Why this matters**: You can test your entire puzzle logic locally before ever
sending XCH to it. This saves you from losing real money to bugs.

### 3.3 Currying

Currying is how you bake parameters into a puzzle. If your puzzle is:

```chialisp
(mod (PUBLIC_KEY amount message)
  ; PUBLIC_KEY is curried in, amount and message come from the solution
  ...)
```

You curry `PUBLIC_KEY` at puzzle-creation time, and `amount` and `message` are
provided later in the solution.

```python
from chia.types.blockchain_format.program import Program

# Original puzzle (compiled)
puzzle = Program.fromhex("...")

# Curry in a public key
public_key_bytes = bytes.fromhex("a4b2c3...")
curried_puzzle = puzzle.curry(
    Program.to(public_key_bytes)
)

# You can curry multiple arguments
curried_puzzle = puzzle.curry(
    Program.to(public_key_bytes),
    Program.to(1000),           # second curried arg
    Program.to("some_value"),   # third curried arg
)
```

**Important**: The order of curried arguments must match the order they appear in
your `mod` declaration.

When you curry, a new program is created that wraps the original. The curried
program, when called, automatically prepends the curried values before your solution
arguments.

### 3.4 Uncurrying

You can also reverse the process to extract curried arguments from an existing
curried puzzle:

```python
# If you have a curried puzzle and want to see what was curried in
mod, curried_args = curried_puzzle.uncurry()
# mod = the original puzzle
# curried_args = Program containing the list of curried values
```

### 3.5 Getting the Puzzle Hash (Tree Hash)

Every puzzle has a unique hash. This hash is fundamental -- it determines the address
where coins locked by this puzzle live.

```python
puzzle_hash = puzzle.get_tree_hash()
print(f"Puzzle hash: {puzzle_hash.hex()}")
# Output: something like "4bf5122f344554c53bde2ebb8cd2b7e3d1600ad631c385a5d7cce23c7785459a"
```

The tree hash is computed by hashing the tree structure of the CLVM program. Two
programs produce the same hash if and only if they are identical.

### 3.6 Converting to/from Bytes

```python
# To bytes (for serialization, sending over the network, etc.)
raw_bytes = bytes(puzzle)

# From bytes
puzzle_again = Program.from_bytes(raw_bytes)

# To hex string
hex_str = puzzle.as_bin().hex()
# or
hex_str = bytes(puzzle).hex()

# From hex string
puzzle_again = Program.fromhex(hex_str)
```

### 3.7 Inspecting Programs

```python
# Check if it's an atom or a pair
puzzle.atom      # Returns bytes if it's an atom, None if it's a pair
puzzle.pair      # Returns (left, right) if it's a pair, None if it's an atom

# Iterate over a list
for item in puzzle.as_iter():
    print(item)

# Get as integer (if it's an atom)
value = puzzle.as_int()

# Get as Python object
value = puzzle.as_python()

# Pretty print
print(puzzle)
```

---

## 4. Building a Spend Bundle Step by Step

This is the core workflow. Every time you want to spend a coin, you go through
these steps. I will walk through each one in painful detail.

### Step 1: Load and Compile the Puzzle

```python
from clvm_tools_rs import compile_clvm_text
from chia.types.blockchain_format.program import Program

# Option A: Compile from a source string
source = """
(mod (PASSWORD CREATE_COIN_PUZZLE_HASH amount)
  (if (= PASSWORD 0xcafef00d)
    (list
      (list 51 CREATE_COIN_PUZZLE_HASH amount)  ; CREATE_COIN
    )
    (x)
  )
)
"""
compiled_hex = compile_clvm_text(source, ["./include"])
puzzle = Program.fromhex(compiled_hex)

# Option B: Compile from a .clsp file
with open("my_puzzle.clsp", "r") as f:
    source = f.read()
compiled_hex = compile_clvm_text(source, ["./include"])
puzzle = Program.fromhex(compiled_hex)

# Option C: Load a pre-compiled .clvm.hex file
with open("my_puzzle.clvm.hex", "r") as f:
    hex_str = f.read().strip()
puzzle = Program.fromhex(hex_str)
```

### Step 2: Curry Parameters (if needed)

```python
# If your puzzle takes curried arguments, apply them now
# For example, currying a public key into a signature-locked puzzle
curried_puzzle = puzzle.curry(
    Program.to(my_public_key)
)
```

### Step 3: Get the Puzzle Hash and Derive the Address

```python
from chia.util.bech32m import encode_puzzle_hash

# Get the puzzle hash
puzzle_hash = curried_puzzle.get_tree_hash()
print(f"Puzzle hash: {puzzle_hash.hex()}")

# Convert to a bech32m address
# "xch" for mainnet, "txch" for testnet
address = encode_puzzle_hash(puzzle_hash, "txch")
print(f"Address: {address}")
# Output: something like "txch1abc123..."
```

**This is the address you send XCH to.** Any coins sent to this address are locked
by your puzzle. To spend them, someone must provide a valid solution.

### Step 4: Fund the Coin

This happens outside your driver code. You (or someone) sends XCH to the address
from Step 3. You can do this from:

- The Chia GUI wallet
- The Chia CLI: `chia wallet send -t <address> -a <amount>`
- Another driver using the wallet RPC

Once the transaction confirms, a coin exists on the blockchain with:
- `parent_coin_info`: the coin ID of the coin that created it
- `puzzle_hash`: the hash from Step 3
- `amount`: the amount in mojos (1 XCH = 1,000,000,000,000 mojos)

### Step 5: Find the Coin on Chain

```python
import asyncio
from chia.rpc.full_node_rpc_client import FullNodeRpcClient
from chia.util.config import load_config
from chia.util.default_root_path import DEFAULT_ROOT_PATH
from chia.util.ints import uint16

async def find_coin(puzzle_hash):
    config = load_config(DEFAULT_ROOT_PATH, "config.yaml")
    rpc_port = config["full_node"]["rpc_port"]

    client = await FullNodeRpcClient.create(
        "localhost",
        uint16(rpc_port),
        DEFAULT_ROOT_PATH,
        config
    )

    try:
        # Find all coins with this puzzle hash
        coin_records = await client.get_coin_records_by_puzzle_hash(
            puzzle_hash,
            include_spent_coins=False  # Only unspent coins
        )

        if not coin_records:
            print("No coins found at this puzzle hash!")
            return None

        # Each record has: coin, spent, confirmed_block_index, etc.
        for record in coin_records:
            coin = record.coin
            print(f"Found coin:")
            print(f"  Parent:  {coin.parent_coin_info.hex()}")
            print(f"  Puzzle:  {coin.puzzle_hash.hex()}")
            print(f"  Amount:  {coin.amount} mojos")
            print(f"  Coin ID: {coin.name().hex()}")

        return coin_records[0].coin
    finally:
        client.close()
        await client.await_closed()

# Run it
coin = asyncio.run(find_coin(puzzle_hash))
```

**Understanding the Coin object:**

```python
from chia.types.blockchain_format.coin import Coin
from chia.types.blockchain_format.sized_bytes import bytes32

# A Coin is defined by three fields:
coin = Coin(
    parent_coin_info=bytes32(...),  # 32-byte hash of the parent coin
    puzzle_hash=bytes32(...),       # 32-byte hash of the puzzle that locks it
    amount=uint64(1000)             # Amount in mojos
)

# The coin's unique ID is the hash of these three fields
coin_id = coin.name()  # bytes32
```

### Step 6: Build the Solution

The solution is just another CLVM program (an S-expression). It contains whatever
arguments your puzzle expects.

```python
# For our password puzzle: (PASSWORD CREATE_COIN_PUZZLE_HASH amount)
solution = Program.to([
    0xcafef00d,                   # PASSWORD
    destination_puzzle_hash,       # Where to send the coins
    coin.amount                    # Amount to send
])
```

**Critical tip**: Test the solution locally first!

```python
# Run the puzzle with this solution BEFORE sending it to the blockchain
result = curried_puzzle.run(solution)
print(f"Output conditions: {result}")
# Should print the list of conditions your puzzle returns
```

If `puzzle.run(solution)` raises an exception, the spend will also fail on-chain.
Test locally, save yourself grief.

### Step 7: Create the CoinSpend

```python
from chia.types.coin_spend import CoinSpend

coin_spend = CoinSpend(
    coin,                # The actual Coin object from Step 5
    curried_puzzle,      # The full puzzle reveal (Program)
    solution             # The solution (Program)
)
```

The `CoinSpend` is a single coin being spent. A `SpendBundle` can contain multiple
`CoinSpend` objects (for spending multiple coins in one transaction).

### Step 8: Sign if Needed

If your puzzle uses `AGG_SIG_ME` or `AGG_SIG_UNSAFE`, you need a BLS signature.
We will cover this in detail in Section 6, but here is the quick version:

```python
from blspy import AugSchemeMPL, PrivateKey, G2Element

# If your puzzle requires AGG_SIG_ME:
# You sign: message + coin_id + genesis_challenge
message = b"..."  # whatever your puzzle expects
coin_id = coin.name()
genesis_challenge = bytes.fromhex("ccd5bb71183532bff220ba46c268991a3ff07eb358e8255a65c30a2dce0e5fbb")  # mainnet

data_to_sign = message + coin_id + genesis_challenge
signature = AugSchemeMPL.sign(private_key, data_to_sign)
```

If your puzzle does NOT use any `AGG_SIG` conditions, you can use an empty signature:

```python
from chia.types.blockchain_format.sized_bytes import bytes96

# The "empty" G2 element (identity point)
empty_sig = G2Element()
```

### Step 9: Create the SpendBundle

```python
from chia.types.spend_bundle import SpendBundle

spend_bundle = SpendBundle(
    coin_spends=[coin_spend],   # List of CoinSpend objects
    aggregated_signature=signature  # G2Element (or empty if no signing needed)
)
```

If you have multiple signatures (from multiple `AGG_SIG` conditions), aggregate them:

```python
from blspy import AugSchemeMPL

aggregated = AugSchemeMPL.aggregate([sig1, sig2, sig3])
spend_bundle = SpendBundle([coin_spend1, coin_spend2], aggregated)
```

### Step 10: Push to the Mempool

```python
async def push_spend(spend_bundle):
    config = load_config(DEFAULT_ROOT_PATH, "config.yaml")
    rpc_port = config["full_node"]["rpc_port"]

    client = await FullNodeRpcClient.create(
        "localhost",
        uint16(rpc_port),
        DEFAULT_ROOT_PATH,
        config
    )

    try:
        # push_tx sends the spend bundle to the mempool
        result = await client.push_tx(spend_bundle)
        print(f"Push result: {result}")
        # If successful, result will have status "SUCCESS"
        # The transaction will be included in a future block
    finally:
        client.close()
        await client.await_closed()

asyncio.run(push_spend(spend_bundle))
```

### The Complete Picture

Here is a diagram of the full flow:

```
  .clsp file
      |
      v
  [compile]  -->  CLVM bytecode (Program)
      |
      v
  [curry params]  -->  Curried Program
      |
      v
  [get_tree_hash]  -->  puzzle_hash (bytes32)
      |
      v
  [encode_puzzle_hash]  -->  bech32m address (xch1...)
      |
      v
  [send XCH to address]  -->  Coin exists on chain
      |
      v
  [find coin via RPC]  -->  Coin object
      |
      v
  [build solution]  -->  Program (solution)
      |
      v
  [CoinSpend(coin, puzzle, solution)]
      |
      v
  [sign if needed]  -->  G2Element (signature)
      |
      v
  [SpendBundle(coin_spends, signature)]
      |
      v
  [push_tx via RPC]  -->  Transaction in mempool --> Confirmed in block
```

---

## 5. RPC Client Usage

The Chia full node exposes an RPC API that your driver uses to interact with the
blockchain. Here is everything you need to know.

### 5.1 Connecting to the Full Node

```python
import asyncio
from chia.rpc.full_node_rpc_client import FullNodeRpcClient
from chia.util.config import load_config
from chia.util.default_root_path import DEFAULT_ROOT_PATH
from chia.util.ints import uint16

async def get_client():
    """Create and return an RPC client connected to the local full node."""
    config = load_config(DEFAULT_ROOT_PATH, "config.yaml")
    rpc_port = config["full_node"]["rpc_port"]

    client = await FullNodeRpcClient.create(
        "localhost",
        uint16(rpc_port),
        DEFAULT_ROOT_PATH,
        config
    )
    return client

# Usage pattern (always close the client!)
async def main():
    client = await get_client()
    try:
        # ... do stuff with client ...
        pass
    finally:
        client.close()
        await client.await_closed()

asyncio.run(main())
```

### 5.2 Important RPC Methods

#### get_coin_records_by_puzzle_hash

Find all coins locked by a specific puzzle hash.

```python
records = await client.get_coin_records_by_puzzle_hash(
    puzzle_hash,                    # bytes32
    include_spent_coins=False,      # True to include already-spent coins
    start_height=None,              # Optional: only look from this block height
    end_height=None                 # Optional: only look up to this block height
)

for record in records:
    print(f"Coin: {record.coin.name().hex()}")
    print(f"  Amount: {record.coin.amount}")
    print(f"  Spent: {record.spent}")
    print(f"  Confirmed at height: {record.confirmed_block_index}")
    if record.spent:
        print(f"  Spent at height: {record.spent_block_index}")
```

#### get_coin_record_by_name

Look up a specific coin by its ID (coin name).

```python
coin_id = bytes32.from_hexstr("abc123...")
record = await client.get_coin_record_by_name(coin_id)

if record is None:
    print("Coin not found!")
else:
    print(f"Coin amount: {record.coin.amount}")
    print(f"Spent: {record.spent}")
```

#### get_blockchain_state

Check the current state of the blockchain.

```python
state = await client.get_blockchain_state()
print(f"Synced: {state['sync']['synced']}")
print(f"Peak height: {state['peak'].height}")
```

#### push_tx

Submit a spend bundle to the mempool.

```python
result = await client.push_tx(spend_bundle)
# result is a dict with "status" and "success" keys
# status can be "SUCCESS", "PENDING", or an error
```

#### get_puzzle_and_solution

After a coin is spent, retrieve the puzzle and solution that were used.

```python
coin_id = bytes32.from_hexstr("...")
height = 1234567  # The block height where it was spent

response = await client.get_puzzle_and_solution(coin_id, height)
puzzle_reveal = response.puzzle_reveal  # Program
solution = response.solution            # Program
```

This is extremely useful for debugging and for tracking coin state.

### 5.3 Using the Wallet RPC (Alternative)

You can also use the wallet RPC for simpler operations:

```python
from chia.rpc.wallet_rpc_client import WalletRpcClient

async def get_wallet_client():
    config = load_config(DEFAULT_ROOT_PATH, "config.yaml")
    wallet_port = config["wallet"]["rpc_port"]

    client = await WalletRpcClient.create(
        "localhost",
        uint16(wallet_port),
        DEFAULT_ROOT_PATH,
        config
    )
    return client
```

The wallet RPC is useful for:
- Sending standard transactions
- Getting your balance
- Managing CATs and NFTs
- But for custom puzzles, you usually need the full node RPC

---

## 6. Signing Transactions

Chia uses **BLS12-381** signatures, specifically the **AugSchemeMPL** (Augmented
Scheme, Minimal Public Key Length) variant. This section explains how signing works
in the context of spending coins.

### 6.1 When Do You Need to Sign?

You need a signature when your puzzle outputs one of these conditions:

| Condition | Code | What it means |
|-----------|------|---------------|
| `AGG_SIG_UNSAFE` | 49 | Signature required on arbitrary message |
| `AGG_SIG_ME` | 50 | Signature required on message + coin_id + genesis_challenge |

If your puzzle does not output any `AGG_SIG` conditions, you do not need to sign.
Use the empty `G2Element()` as the signature.

**AGG_SIG_ME vs AGG_SIG_UNSAFE:**

- `AGG_SIG_ME` (50) is the safe one. It appends the coin ID and genesis challenge
  to the message before verification. This prevents replay attacks -- a signature
  for one coin cannot be reused for another.
- `AGG_SIG_UNSAFE` (49) signs just the raw message. Use it only when you need a
  coin-independent signature (rare).

### 6.2 Key Generation

```python
from blspy import AugSchemeMPL, PrivateKey, G1Element, G2Element

# Generate a new random private key
import secrets
seed = secrets.token_bytes(32)
private_key = AugSchemeMPL.key_gen(seed)

# Get the corresponding public key
public_key = private_key.get_g1()  # G1Element (48 bytes)

print(f"Private key: {bytes(private_key).hex()}")
print(f"Public key:  {bytes(public_key).hex()}")

# Derive a child key (how HD wallets work)
child_sk = AugSchemeMPL.derive_child_sk(private_key, 0)
child_pk = child_sk.get_g1()
```

### 6.3 Signing for AGG_SIG_ME

When your puzzle outputs `(AGG_SIG_ME public_key message)`, the blockchain verifies:

```
AugSchemeMPL.verify(public_key, message + coin_id + genesis_challenge, signature)
```

So your driver must sign `message + coin_id + genesis_challenge`:

```python
from blspy import AugSchemeMPL

# The message your puzzle expects to be signed
# (often a hash of conditions, or a delegated puzzle hash)
message = bytes.fromhex("...")

# The coin ID of the coin being spent
coin_id = coin.name()  # bytes32

# The genesis challenge for the network
# Mainnet:
MAINNET_GENESIS = bytes.fromhex(
    "ccd5bb71183532bff220ba46c268991a3ff07eb358e8255a65c30a2dce0e5fbb"
)
# Testnet11:
TESTNET_GENESIS = bytes.fromhex(
    "37a90eb5185a9c4439a91ddc98bbadce7b4feba060d50116a067de66bf236615"
)

# Build the data to sign
data_to_sign = message + coin_id + TESTNET_GENESIS

# Sign it
signature = AugSchemeMPL.sign(private_key, data_to_sign)
```

### 6.4 Signing Delegated Puzzles (Standard Transaction Pattern)

In the standard transaction puzzle, the most common signing pattern is:

1. The inner puzzle is a "delegated puzzle" -- you provide conditions in the solution
2. The puzzle hashes those conditions
3. It outputs `(AGG_SIG_ME public_key conditions_hash)`
4. You sign the conditions hash

```python
from chia.wallet.puzzles.p2_delegated_puzzle_or_hidden_puzzle import (
    puzzle_for_pk,
    solution_for_delegated_puzzle,
    calculate_synthetic_secret_key,
)
from chia.consensus.default_constants import DEFAULT_CONSTANTS

# Create the standard puzzle for a public key
puzzle = puzzle_for_pk(public_key)

# The conditions you want to output
conditions = Program.to([
    [51, destination_puzzle_hash, amount],  # CREATE_COIN
    [51, change_puzzle_hash, change_amount] # Change coin
])

# Build the solution
delegated_puzzle = Program.to((1, conditions))  # (q . conditions) = always return these
solution = solution_for_delegated_puzzle(delegated_puzzle, Program.to(0))

# Sign
synthetic_sk = calculate_synthetic_secret_key(
    private_key,
    puzzle_for_pk(public_key).get_tree_hash()
)

conditions_hash = delegated_puzzle.get_tree_hash()
coin_id = coin.name()
genesis = DEFAULT_CONSTANTS.AGG_SIG_ME_ADDITIONAL_DATA

signature = AugSchemeMPL.sign(
    synthetic_sk,
    conditions_hash + coin_id + genesis
)
```

### 6.5 Aggregating Signatures

One of the elegant properties of BLS signatures is that multiple signatures can be
aggregated into a single signature. The SpendBundle takes ONE aggregated signature
for ALL coin spends:

```python
from blspy import AugSchemeMPL

# If you have multiple signatures from different spends
sig1 = AugSchemeMPL.sign(sk1, msg1)
sig2 = AugSchemeMPL.sign(sk2, msg2)
sig3 = AugSchemeMPL.sign(sk3, msg3)

# Aggregate them into one
aggregated_sig = AugSchemeMPL.aggregate([sig1, sig2, sig3])

# The blockchain will verify all of them at once
spend_bundle = SpendBundle(
    [coin_spend1, coin_spend2, coin_spend3],
    aggregated_sig
)
```

### 6.6 Verifying Signatures Locally

Before pushing, you can verify the signature locally:

```python
# Verify a single signature
is_valid = AugSchemeMPL.verify(
    public_key,       # G1Element
    data_to_sign,     # bytes
    signature          # G2Element
)
print(f"Signature valid: {is_valid}")

# Verify an aggregate signature
is_valid = AugSchemeMPL.aggregate_verify(
    [pk1, pk2, pk3],           # List of public keys
    [msg1, msg2, msg3],        # List of messages
    aggregated_sig              # The aggregated signature
)
```

---

## 7. Common Driver Patterns

### 7.1 Coin Tracking

After you create a coin, you often need to track it -- know when it is spent and
what new coins it created.

```python
async def wait_for_coin_spent(client, coin_id, poll_interval=5):
    """Poll until a coin is spent, then return the spend details."""
    import time

    while True:
        record = await client.get_coin_record_by_name(coin_id)

        if record is None:
            print("Coin not yet created, waiting...")
        elif record.spent:
            print(f"Coin spent at height {record.spent_block_index}!")
            # Get the puzzle and solution that were used
            ps = await client.get_puzzle_and_solution(
                coin_id, record.spent_block_index
            )
            return ps
        else:
            print(f"Coin exists but not yet spent. Waiting...")

        time.sleep(poll_interval)
```

### 7.2 Finding Child Coins

When a coin is spent, the conditions it outputs may create new coins. To find them:

```python
async def get_children(client, parent_coin_id):
    """Find all coins created by spending this coin."""
    children = await client.get_coin_records_by_parent_ids(
        [parent_coin_id],
        include_spent_coins=False
    )
    return children
```

### 7.3 State Management with Singletons

Many advanced puzzles use the singleton pattern -- a single coin that "updates" by
spending itself and creating a new coin with a modified inner puzzle. The driver
needs to track the singleton through its lineage:

```python
async def follow_singleton(client, launcher_id):
    """Follow a singleton from its launcher to its current state."""
    current_coin_id = launcher_id

    while True:
        record = await client.get_coin_record_by_name(current_coin_id)
        if not record.spent:
            print(f"Current singleton coin: {current_coin_id.hex()}")
            return record

        # Get what conditions it produced
        ps = await client.get_puzzle_and_solution(
            current_coin_id, record.spent_block_index
        )

        # Find the child coin with the same puzzle hash pattern
        children = await client.get_coin_records_by_parent_ids(
            [current_coin_id]
        )

        # The singleton child is the one that follows the singleton rules
        # (This is simplified -- real singleton tracking is more complex)
        for child in children:
            if child.coin.amount % 2 == 1:  # Singletons are always odd amount
                current_coin_id = child.coin.name()
                break
```

### 7.4 Change Coins

When you spend a coin and want to send only part of its value, you need a "change"
coin -- a new coin that returns the remainder to you.

```python
# Coin has 1 XCH (1_000_000_000_000 mojos)
# You want to send 0.1 XCH to someone
# The rest goes back to your puzzle

send_amount = 100_000_000_000    # 0.1 XCH
fee = 50_000_000                  # 0.00005 XCH fee
change_amount = coin.amount - send_amount - fee

conditions = [
    [51, recipient_puzzle_hash, send_amount],     # Send to recipient
    [51, my_puzzle_hash, change_amount],           # Change back to myself
    [52, fee],                                      # Reserve fee
]
```

**Important**: The sum of all `CREATE_COIN` amounts plus the fee MUST equal the
spent coin's amount. If it does not, the transaction will be rejected.

```
spent_coin.amount = sum(CREATE_COIN amounts) + fee
```

### 7.5 Fee Handling

Fees are declared with condition code 52 (`RESERVE_FEE`). The fee is the difference
between the total input amount and the total output amount:

```python
total_input = sum(coin.amount for coin in coins_being_spent)
total_output = sum(amount for (_, _, amount) in create_coin_conditions)
fee = total_input - total_output

# You must include a RESERVE_FEE condition to declare the fee
# condition code 52, minimum fee amount
conditions.append([52, fee])
```

### 7.6 Putting It All Together: A Reusable Driver Template

```python
"""
Template for a complete coin driver.
Customize the puzzle, currying, and solution for your specific use case.
"""
import asyncio
from chia.types.blockchain_format.program import Program
from chia.types.blockchain_format.coin import Coin
from chia.types.blockchain_format.sized_bytes import bytes32
from chia.types.spend_bundle import SpendBundle
from chia.types.coin_spend import CoinSpend
from chia.rpc.full_node_rpc_client import FullNodeRpcClient
from chia.util.config import load_config
from chia.util.default_root_path import DEFAULT_ROOT_PATH
from chia.util.ints import uint16
from chia.util.bech32m import encode_puzzle_hash
from blspy import G2Element
from clvm_tools_rs import compile_clvm_text


class MyPuzzleDriver:
    def __init__(self, puzzle_source: str):
        # Compile the puzzle
        compiled = compile_clvm_text(puzzle_source, ["./include"])
        self.base_puzzle = Program.fromhex(compiled)
        self.client = None

    async def connect(self):
        config = load_config(DEFAULT_ROOT_PATH, "config.yaml")
        rpc_port = config["full_node"]["rpc_port"]
        self.client = await FullNodeRpcClient.create(
            "localhost", uint16(rpc_port), DEFAULT_ROOT_PATH, config
        )

    async def disconnect(self):
        if self.client:
            self.client.close()
            await self.client.await_closed()

    def get_puzzle(self, *curry_args) -> Program:
        if curry_args:
            return self.base_puzzle.curry(*[Program.to(a) for a in curry_args])
        return self.base_puzzle

    def get_address(self, puzzle: Program, prefix="txch") -> str:
        return encode_puzzle_hash(puzzle.get_tree_hash(), prefix)

    async def find_coins(self, puzzle: Program):
        ph = puzzle.get_tree_hash()
        records = await self.client.get_coin_records_by_puzzle_hash(
            ph, include_spent_coins=False
        )
        return [r.coin for r in records]

    async def spend(self, coin: Coin, puzzle: Program, solution: Program,
                    signature=G2Element()):
        coin_spend = CoinSpend(coin, puzzle, solution)
        spend_bundle = SpendBundle([coin_spend], signature)

        # Validate locally first
        # (In production, add more validation here)

        result = await self.client.push_tx(spend_bundle)
        return result
```

---

## 8. Troubleshooting

### "Coin not found"
- The coin may not be confirmed yet. Wait a few blocks.
- Double-check the puzzle hash. Even a tiny change to the puzzle changes the hash.
- Make sure you are looking at the right network (mainnet vs testnet).

### "ASSERT_MY_COIN_ID failed"
- The coin you are trying to spend does not match the coin ID in the condition.
- Check that parent_coin_info, puzzle_hash, and amount are all correct.

### "AGG_SIG verification failed"
- You are signing the wrong data.
- For `AGG_SIG_ME`: make sure you sign `message + coin_id + genesis_challenge`.
- Make sure the genesis challenge matches the network you are on.
- Make sure the public key in the puzzle matches the private key you signed with.

### "ASSERT_HEIGHT / ASSERT_SECONDS failed"
- Timelock conditions are not yet satisfied. Wait until the required height/time.

### "Spend bundle not valid"
- The sum of inputs does not equal the sum of outputs plus fees.
- A condition raised by one coin conflicts with a condition from another coin.
- Run `puzzle.run(solution)` locally to see what conditions are produced.

### General debugging workflow
1. Run `puzzle.run(solution)` locally and inspect the output conditions
2. Verify all amounts balance (inputs = outputs + fee)
3. Verify all signatures
4. Check that the coin exists and is unspent
5. Check that you are on the right network

---

## Examples

See the `examples/` directory for complete, runnable driver scripts:

- [`compile_and_run.py`](https://github.com/MarvinQuevedo/ChiaLispProject/blob/main/04-python-drivers/examples/compile_and_run.py) - Compile a puzzle and run it locally
- [`create_coin_driver.py`](https://github.com/MarvinQuevedo/ChiaLispProject/blob/main/04-python-drivers/examples/create_coin_driver.py) - Full driver for creating and spending a coin
- [`signature_driver.py`](https://github.com/MarvinQuevedo/ChiaLispProject/blob/main/04-python-drivers/examples/signature_driver.py) - Driver with BLS signature handling
- [`watch_coin.py`](https://github.com/MarvinQuevedo/ChiaLispProject/blob/main/04-python-drivers/examples/watch_coin.py) - Utility to watch a coin's lifecycle

---

**Next chapter**: [Chapter 5 - Chia Asset Tokens (CATs)](../05-cats/README.md)
