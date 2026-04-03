# Chapter 5: Chia Asset Tokens (CATs)

CATs are Chia's fungible token standard. If you have ever used ERC-20 tokens on
Ethereum, CATs are the Chia equivalent -- but designed very differently. Instead
of a single smart contract tracking balances, each CAT is an individual coin on
the blockchain with its own puzzle.

This chapter builds on everything you learned about puzzles, currying, inner
puzzles, and drivers. CATs are a real-world application of puzzle composition.

---

## Table of Contents

1. [What is a CAT?](#1-what-is-a-cat)
2. [How the CAT Puzzle Works](#2-how-the-cat-puzzle-works)
3. [TAIL Programs](#3-tail-programs)
4. [CAT Spend Mechanics](#4-cat-spend-mechanics)
5. [Creating Your Own CAT](#5-creating-your-own-cat)
6. [Interacting with CATs Programmatically](#6-interacting-with-cats-programmatically)

---

## 1. What is a CAT?

### Definition

A **Chia Asset Token (CAT)** is a fungible token that lives on the Chia blockchain.
Each CAT type is identified by its **asset ID** (also called the **TAIL hash**),
which is derived from a special program called the TAIL.

Key properties:

- **Fungible**: All tokens of the same CAT type are interchangeable (like currency)
- **Decentralized**: No single smart contract -- each CAT is its own coin
- **Composable**: CATs wrap an inner puzzle, so you can put any logic inside them
- **Conserved**: You cannot create or destroy CATs unless the TAIL allows it

### The Anatomy of a CAT

Every CAT coin has these layers:

```
+------------------------------------------+
|            CAT Outer Puzzle              |
|  (enforces token rules, conservation)    |
|                                          |
|  +------------------------------------+  |
|  |        Inner Puzzle                 |  |
|  |  (defines spending conditions)      |  |
|  |  (usually: standard transaction)    |  |
|  +------------------------------------+  |
|                                          |
|  Asset ID: hash of the TAIL program      |
+------------------------------------------+
```

- The **outer puzzle** (the CAT puzzle itself) enforces the token rules:
  token conservation, proper lineage, and TAIL validation.
- The **inner puzzle** defines who can spend the coin and how. For most CATs
  in wallets, this is the standard transaction puzzle (signature-locked).
- The **TAIL** (Token and Asset Issuance Limiter) is a separate program that
  controls minting and melting rules. Its hash is the asset ID.

### Why Not Just Use Regular Coins?

You might ask: "Why not just track tokens as regular XCH coins with special puzzles?"

The answer is **conservation guarantees**. The CAT outer puzzle mathematically
ensures that:

1. You cannot create CAT coins out of thin air (unless the TAIL authorizes it)
2. You cannot destroy CAT coins (unless the TAIL authorizes it)
3. The asset ID stays consistent across all spends
4. Every CAT coin has a verifiable lineage back to an authorized minting event

Without the CAT puzzle, someone could write a puzzle that claims to be a "token"
but actually creates tokens from nothing. The CAT standard prevents this.

### The CAT2 Standard

The current standard is **CAT2**. The original CAT1 standard had a vulnerability
that was discovered and responsibly disclosed. All CAT1 tokens were migrated to
CAT2 in a coordinated effort. When we say "CAT" today, we always mean CAT2.

### Common CATs on Chia

| Token | Description | Type |
|-------|-------------|------|
| USDS  | Stably USD stablecoin | Stablecoin |
| SBX   | Spacebucks community token | Community |
| DBX   | dexie bucks | DEX utility token |
| HOA   | Chia Holiday 2021 token | Commemorative |

### Asset ID

The asset ID is the tree hash of the TAIL program:

```
Asset ID = sha256tree(TAIL_program)
```

Two tokens with the same TAIL have the same asset ID (same token type). Changing
anything about the TAIL -- even one curried parameter -- produces a different asset ID,
and therefore a completely different token.

---

## 2. How the CAT Puzzle Works

### The Inner Puzzle Pattern

This is the most important concept. A CAT coin is not a new type of coin -- it is
a **regular Chia coin** whose puzzle has been wrapped in a special outer layer.

```
+--------------------------------------------------+
|  CAT Outer Puzzle                                 |
|  - Enforces token rules (conservation, lineage)   |
|  - Transforms CREATE_COIN to wrap in CAT puzzle   |
|                                                   |
|  +--------------------------------------------+  |
|  |  Inner Puzzle (any valid puzzle)            |  |
|  |  - Controls spending (e.g., standard tx)    |  |
|  |  - Returns conditions as normal             |  |
|  +--------------------------------------------+  |
|                                                   |
+--------------------------------------------------+
```

The inner puzzle can be **anything**:
- A standard transaction puzzle (p2_delegated_puzzle) -- the most common
- A custom puzzle that requires a password
- A multisig puzzle
- A time-locked puzzle
- Any other valid ChiaLisp program

The CAT outer puzzle does not care what the inner puzzle is. It only cares about
enforcing token-level rules.

### Curried Parameters

The CAT outer puzzle has three curried parameters:

```
(MOD_HASH  TAIL_HASH  INNER_PUZZLE)
```

| Parameter | Description |
|-----------|-------------|
| `MOD_HASH` | The tree hash of the CAT outer puzzle itself (for self-reference) |
| `TAIL_HASH` | The hash of the TAIL program (this is the asset_id) |
| `INNER_PUZZLE` | The actual inner puzzle (e.g., standard transaction puzzle) |

`MOD_HASH` is needed so the CAT puzzle can recreate itself when wrapping new coins.
When the inner puzzle says "create a coin with puzzle hash X", the CAT outer puzzle
wraps X inside a new CAT puzzle, and it needs its own hash to compute that wrapping.

### Step-by-Step Execution

When a CAT coin is spent:

**Step 1: Run the Inner Puzzle**

```chialisp
(a INNER_PUZZLE inner_puzzle_solution)
```

This returns conditions like any normal coin spend:
```
((CREATE_COIN 0xabc123... 1000)
 (AGG_SIG_ME 0xpubkey... 0xmessage...))
```

**Step 2: Transform CREATE_COIN Conditions**

The CAT puzzle intercepts every `CREATE_COIN` condition and wraps the target puzzle
hash inside a new CAT puzzle:

```
Original:  (CREATE_COIN inner_puzzle_hash amount)
Becomes:   (CREATE_COIN (cat_puzzle_hash MOD_HASH TAIL_HASH inner_puzzle_hash) amount)
```

This is automatic and transparent. The inner puzzle does not need to know it is
inside a CAT.

**Step 3: Verify Conservation**

The CAT puzzle checks that total tokens in = total tokens out:

```
sum(all input amounts) - sum(all output amounts) + sum(all extra_deltas) = 0
```

If `extra_delta` is non-zero for any coin, the TAIL must authorize it.

**Step 4: Verify Lineage**

Each CAT must prove it descended from a valid CAT of the same type through a
**lineage proof** containing:

```
(parent_parent_coin_id  parent_inner_puzzle_hash  parent_amount)
```

Without lineage proofs, anyone could create fake CATs by wrapping a normal coin
in the CAT puzzle. The lineage proof ensures an unbroken chain back to the
original minting event.

### The Full Picture

```
CAT Coin Spend
    |
    +-- 1. Run inner puzzle --> get conditions
    |
    +-- 2. For each CREATE_COIN:
    |       wrap inner_puzzle_hash in CAT puzzle
    |
    +-- 3. Check conservation:
    |       total_in = total_out (or TAIL approves delta)
    |
    +-- 4. Verify lineage:
            parent was a valid CAT of same type
```

---

## 3. TAIL Programs

### What is a TAIL?

TAIL stands for **Token and Asset Issuance Limiter**. It is a ChiaLisp program
that answers one question:

> "Is this minting or melting event authorized?"

The TAIL is only consulted when `extra_delta != 0` -- when tokens are being created
or destroyed. For normal transfers, the TAIL never runs. This is an elegant
optimization since most CAT spends are simple transfers.

### How the TAIL is Called

When a CAT spend has a non-zero `extra_delta`, the CAT outer puzzle calls the TAIL:

```chialisp
; The TAIL receives:
(mod (
    Truths          ; Information about the CAT coin (amount, puzzle hash, etc.)
    parent_is_cat   ; Whether the parent was a CAT (for lineage)
    lineage_proof   ; Proof of parent
    extra_delta     ; The amount being minted (+) or melted (-)
    inner_conditions ; Conditions from the inner puzzle
    ... )
  ; Return conditions to authorize, or (x) to reject
)
```

If the TAIL returns successfully (does not raise), the minting/melting is authorized.

### Common TAIL Types

#### 1. Genesis by Coin ID (Single Issuance)

The simplest and most common TAIL. Allows minting exactly once, tied to a specific coin.

**How it works:**
1. Curry in a `GENESIS_COIN_ID` -- the coin ID of a coin you control
2. The TAIL checks: "Is the genesis coin being spent in this transaction?"
3. If yes, minting is approved. Since a coin can only be spent once, tokens can
   only ever be minted once.

**Use case:** Fixed-supply tokens. You decide the total supply at creation time.

See: [examples/simple_tail.clsp](examples/simple_tail.clsp)

#### 2. Everything with Signature (Authorized Minter)

Allows ongoing minting, controlled by a public key.

**How it works:**
1. Curry in an `AUTHORIZED_PUBKEY`
2. The TAIL requires a valid signature from this public key
3. If the signature is valid, minting is approved

**Use case:** Tokens where you want to mint more over time (stablecoins, reward tokens).

See: [examples/authorized_minter_tail.clsp](examples/authorized_minter_tail.clsp)

#### 3. Delegated TAIL

The most flexible option. Accepts a delegated puzzle in the solution.

**How it works:**
1. Curry in a `PUBKEY`
2. The TAIL receives a delegated puzzle, runs it, and requires a signature
3. The delegated puzzle can enforce any minting rules you want

**Use case:** Different minting rules at different times without redeploying.

### TAIL Hash = Asset ID

```python
from chia.types.blockchain_format.program import Program

tail_program = Program.fromhex("...")
# If currying parameters:
curried_tail = tail_program.curry(Program.to(genesis_coin_id))
asset_id = curried_tail.get_tree_hash()
print(f"Asset ID: {asset_id.hex()}")
```

---

## 4. CAT Spend Mechanics

### The Ring of CATs

This is the most complex part. When you spend one or more CAT coins in a transaction,
they form a **ring** (a circular linked list):

```
         +--------+       +--------+       +--------+
         | CAT #1 | ----> | CAT #2 | ----> | CAT #3 |
         +--------+       +--------+       +--------+
              ^                                  |
              |                                  |
              +----------------------------------+
                       (ring closes)
```

Even a single CAT spend forms a ring of size 1 (it points to itself).

Each CAT in the ring knows about:
- The **previous** CAT coin in the ring
- The **next** CAT coin in the ring
- A **running subtotal** that tracks the conservation balance

### Why a Ring and Not a List?

In Chia's coin set model, all coins in a spend bundle are spent simultaneously.
There is no "first" or "last." A ring has no beginning or end, which matches the
parallel nature of coin spends. Each coin can independently verify its part of the
conservation check without needing a position.

### Conservation Check

The ring enables the conservation check through a running subtotal:

```
For each CAT in the ring:
    subtotal = prev_subtotal + this_coin.amount - sum(output_amounts)

After going around the full ring:
    final_subtotal must equal 0 (or equal sum of extra_deltas)
```

### The Full Solution Structure

When spending a CAT, the solution contains:

```chialisp
(
  inner_puzzle_solution    ; Solution for the inner puzzle
  lineage_proof            ; Proof that parent was a valid CAT
  prev_coin_id             ; Previous CAT in the ring
  this_coin_info           ; (parent_id, inner_puzzle_hash, amount)
  next_coin_proof          ; Info to verify next CAT in ring
  prev_subtotal            ; Running subtotal from previous CAT
  extra_delta              ; Minting(+) or melting(-), usually 0
)
```

### Example: Simple CAT Transfer

Alice has a CAT coin worth 100 tokens. She sends 30 to Bob, keeps 70.

```
INPUT:                           OUTPUTS:
Alice's CAT (100 tokens)  --->   Bob's CAT (30 tokens)
                           --->   Alice's CAT (70 tokens) [change]

Conservation: 100 = 30 + 70  [balanced, extra_delta = 0]
```

Ring: size 1 (Alice's coin points to itself).

### Example: Multi-Coin CAT Spend

Alice has two CAT coins (60 and 40) and wants to send 90 to Bob.

```
INPUTS:                          OUTPUTS:
Alice's CAT #1 (60 tokens) --->  Bob's CAT (90 tokens)
Alice's CAT #2 (40 tokens) --->  Alice's CAT (10 tokens) [change]

Conservation: 60 + 40 = 90 + 10  [balanced]
```

Ring: `CAT #1 ---> CAT #2 ---> (back to CAT #1)`

### The Extra Delta (Minting/Melting)

When `extra_delta` is not zero:

- **Positive**: Tokens are being minted (created from nothing)
- **Negative**: Tokens are being melted (destroyed)

The TAIL must authorize any non-zero extra_delta.

---

## 5. Creating Your Own CAT

### Step-by-Step Process

#### Step 1: Choose or Write a TAIL

| Need | TAIL Type |
|------|-----------|
| Fixed supply, mint once | Genesis by Coin ID |
| Ongoing minting by authority | Everything with Signature |
| Complex/changing rules | Delegated TAIL |
| Custom rules | Write your own |

#### Step 2: Compile the TAIL

```python
from clvm_tools_rs import compile_clvm_text
from chia.types.blockchain_format.program import Program

tail_source = """
(mod (Truths parent_is_cat lineage_proof extra_delta inner_conditions)
  (if parent_is_cat
    (x)    ; Reject: no minting after genesis
    ()     ; Allow: this is the genesis mint
  )
)
"""

compiled = compile_clvm_text(tail_source, [])
tail_program = Program.fromhex(compiled)
```

#### Step 3: Curry Parameters (if needed)

```python
# For an authorized minter TAIL, curry in the minter's public key
curried_tail = tail_program.curry(Program.to(minter_public_key))
```

#### Step 4: Get the Asset ID

```python
asset_id = curried_tail.get_tree_hash()
print(f"Asset ID: {asset_id.hex()}")
```

Save this -- it is how your token is identified everywhere.

#### Step 5: Construct the CAT Puzzle

```python
from chia.wallet.cat_wallet.cat_utils import construct_cat_puzzle, CAT_MOD

cat_puzzle = construct_cat_puzzle(
    CAT_MOD,
    asset_id,        # Your TAIL hash
    inner_puzzle     # The inner puzzle for the first recipient
)
```

#### Step 6: Mint Initial Tokens

Minting is the most complex part. You need to:

1. Choose a coin you control (the genesis coin)
2. Build a spend that invokes the CAT puzzle with the TAIL
3. Set `extra_delta` to the desired token supply
4. The TAIL authorizes the genesis mint
5. The spend creates CAT coins as output

In practice, most people use the CAT admin tool:

```bash
# Install chia-dev-tools
pip install chia-dev-tools

# Issue a new CAT
cats --tail ./my_tail.clsp \
     --send-to <your_address> \
     --amount 1000000 \
     --fee 100000000
```

#### Step 7: Verify and Distribute

```bash
# Add to your wallet
chia wallet add_token -id <asset_id> -n "My Token"

# Check balance
chia wallet show

# Send tokens
chia wallet send -i <wallet_id> -a <amount> -t <target_address>
```

### Complete Flow Diagram

```
1. Write TAIL          2. Compile & Curry      3. Get Asset ID
   (.clsp file)    -->    (CLVM bytecode)   -->   (tree hash)
                                                      |
6. Distribute      5. Mint Tokens           4. Build CAT Puzzle
   (send to     <--   (spend genesis    <--   (curry CAT mod with
    users)             coin + create          TAIL hash + inner)
                       CAT coins)
```

---

## 6. Interacting with CATs Programmatically

### Loading the CAT Module

```python
from chia.wallet.puzzles.cat_loader import CAT_MOD, CAT_MOD_HASH

# CAT_MOD is the compiled CAT outer puzzle (Program)
# CAT_MOD_HASH is its tree hash (bytes32)

full_cat_puzzle = CAT_MOD.curry(
    CAT_MOD_HASH,      # The CAT module hash (always the same)
    asset_id,           # Your TAIL hash (bytes32)
    inner_puzzle        # The inner puzzle (Program)
)
```

### Parsing CAT Coins from the Blockchain

```python
from chia.wallet.cat_wallet.cat_utils import match_cat_puzzle

# Given a puzzle_reveal from a coin spend:
matched = match_cat_puzzle(puzzle_reveal)

if matched is not None:
    cat_mod_hash, tail_hash_program, inner_puzzle = matched
    asset_id = tail_hash_program.as_atom()
    print(f"This is a CAT with asset_id: {asset_id.hex()}")
    print(f"Inner puzzle hash: {inner_puzzle.get_tree_hash().hex()}")
else:
    print("This is not a CAT coin")
```

### Building CAT Spends with SpendableCAT

```python
from chia.wallet.cat_wallet.cat_utils import (
    construct_cat_puzzle,
    unsigned_spend_bundle_for_spendable_cats,
    SpendableCAT,
    CAT_MOD,
)
from chia.wallet.lineage_proof import LineageProof

# Create a SpendableCAT for each input coin
spendable = SpendableCAT(
    coin=cat_coin,
    limitations_program_hash=asset_id,  # TAIL hash
    inner_puzzle=inner_puzzle,
    inner_solution=inner_solution,
    lineage_proof=LineageProof(
        parent_name=parent_parent_id,
        inner_puzzle_hash=parent_inner_ph,
        amount=parent_amount,
    ),
    extra_delta=0,                      # 0 for normal transfer
    limitations_program_reveal=None,    # Only for minting/melting
    limitations_solution=None,          # Only for minting/melting
)

# Build the spend bundle (ring construction is automatic)
spend_bundle = unsigned_spend_bundle_for_spendable_cats(
    CAT_MOD,
    [spendable],
)
```

### Complete CAT Spend Walkthrough

```python
from chia.types.blockchain_format.program import Program
from chia.types.blockchain_format.coin import Coin
from chia.wallet.cat_wallet.cat_utils import (
    construct_cat_puzzle,
    unsigned_spend_bundle_for_spendable_cats,
    SpendableCAT,
    CAT_MOD,
    CAT_MOD_HASH,
)
from chia.wallet.lineage_proof import LineageProof
from chia.wallet.puzzles.p2_delegated_puzzle_or_hidden_puzzle import (
    puzzle_for_pk,
    solution_for_delegated_puzzle,
)

# 1. Define your token
asset_id = bytes.fromhex("abcd1234...")

# 2. Build inner puzzle (standard transaction)
inner_puzzle = puzzle_for_pk(my_public_key)

# 3. Build CAT puzzle
cat_puzzle = construct_cat_puzzle(CAT_MOD, asset_id, inner_puzzle)

# 4. Identify the coin to spend
cat_coin = Coin(parent_id, cat_puzzle.get_tree_hash(), 1000)

# 5. Build inner solution (send 600 to Bob, keep 400)
delegated_puzzle = Program.to((1, [
    [51, bob_inner_puzzle_hash, 600],
    [51, my_inner_puzzle_hash, 400],
]))
inner_solution = solution_for_delegated_puzzle(delegated_puzzle, Program.to(0))

# 6. Get lineage proof
lineage = LineageProof(parent_parent_id, parent_inner_ph, parent_amount)

# 7. Create SpendableCAT
spendable = SpendableCAT(
    coin=cat_coin,
    limitations_program_hash=asset_id,
    inner_puzzle=inner_puzzle,
    inner_solution=inner_solution,
    lineage_proof=lineage,
)

# 8. Build spend bundle
spend_bundle = unsigned_spend_bundle_for_spendable_cats(CAT_MOD, [spendable])

# 9. Sign and submit (depends on your key management)
```

### Using the Wallet RPC for CAT Operations

For standard operations, the wallet RPC is simplest:

```python
from chia.rpc.wallet_rpc_client import WalletRpcClient

async def send_cat(wallet_id, recipient, amount, fee=0):
    client = await get_wallet_client()
    try:
        result = await client.cat_spend(
            wallet_id=wallet_id,
            amount=amount,
            inner_address=recipient,
            fee=fee,
        )
        print(f"Transaction ID: {result['transaction_id']}")
    finally:
        client.close()
        await client.await_closed()
```

### Trading CATs with Offers

One of the most powerful features -- trustless trading:

```python
# Offer 100 of my CAT (wallet 2) for 0.1 XCH (wallet 1)
offer_dict = {
    2: -100,            # I give 100 CAT tokens
    1: 100000000000     # I want 0.1 XCH
}

result = await wallet_client.create_offer_for_ids(offer_dict)
offer_str = result['offer']  # Shareable offer string
```

### Chia Blockchain CAT Module Structure

For reference, the implementation lives in:

```
chia/wallet/cat_wallet/
    cat_utils.py         - Core utilities for CAT puzzle construction
    cat_wallet.py        - CAT wallet implementation
    cat_info.py          - Data structures for CAT info
    lineage_store.py     - Storage for lineage proofs

chia/wallet/puzzles/
    cat_v2.clvm          - The CAT2 outer puzzle
    genesis_by_coin_id.clvm     - Genesis TAIL
    everything_with_signature.clvm  - Signature-based TAIL
    delegated_tail.clvm  - Delegated TAIL
```

---

## Examples

See the `examples/` directory:

- [`simple_tail.clsp`](examples/simple_tail.clsp) - A single-issuance TAIL program
- [`authorized_minter_tail.clsp`](examples/authorized_minter_tail.clsp) - A TAIL for authorized minting
- [`cat_with_custom_inner.clsp`](examples/cat_with_custom_inner.clsp) - A CAT with a custom inner puzzle
- [`cat_driver.py`](examples/cat_driver.py) - Python driver for CAT operations

---

## Key Takeaways

1. **CATs are coins wrapped in a special outer puzzle** that enforces fungible token rules.
2. **The TAIL controls minting and melting** -- its hash is the asset_id.
3. **Conservation is verified through the ring** -- inputs must equal outputs.
4. **Lineage proofs prevent counterfeiting** -- every CAT traces back to a valid mint.
5. **The inner puzzle pattern makes CATs composable** -- any puzzle works inside a CAT.
6. **For standard operations**, use the wallet RPC or CAT admin tools.
7. **For custom CATs**, you need to understand the ring, lineage proofs, and TAIL.

---

**Previous chapter**: [Chapter 4 - Python Drivers](../04-python-drivers/README.md)

**Next chapter**: [Chapter 6 - Advanced Examples](../06-advanced-examples/README.md)
