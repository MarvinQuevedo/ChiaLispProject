# Chapter 7: CAT Staking System -- Capstone Project

This is the **final project** of the ChiaLisp learning guide. We build a complete **CAT staking system** that combines everything from the previous chapters: currying, inner puzzles, conditions, announcements, singletons, CATs, and Python drivers.

---

## Table of Contents

1. [Project Overview](#project-overview)
2. [Architecture](#architecture)
3. [Staking Tiers and APY](#staking-tiers-and-apy)
4. [Puzzle Design: Staking Pool](#puzzle-design-staking-pool)
5. [Puzzle Design: Stake Lock](#puzzle-design-stake-lock)
6. [How Staking Works (Step by Step)](#how-staking-works-step-by-step)
7. [How Claiming Works](#how-claiming-works)
8. [Driver Design](#driver-design)
9. [Security Considerations](#security-considerations)
10. [Testing Strategy](#testing-strategy)
11. [Block Height Time Reference](#block-height-time-reference)
12. [Extending the Project](#extending-the-project)

---

## Project Overview

A CAT staking pool where:

1. A **pool operator** creates a staking pool loaded with reward tokens.
2. Users **stake** their CAT tokens by interacting with the pool coin.
3. When staking, **two coins** are created:
   - **Pool Coin** (recreated): Contains the remaining reward tokens after deducting the user's reward allocation.
   - **Stake Lock Coin** (new): Contains the user's staked amount plus their earned rewards, locked until a configured block height.
4. After the lock period expires, the user **claims** their staked tokens plus rewards by spending the Stake Lock Coin.

This is a real-world DeFi primitive. Staking pools incentivize users to lock tokens, reducing circulating supply and rewarding long-term holders.

### Key Concepts Reinforced

| Concept | Where It Appears |
|---------|-----------------|
| Currying | Both puzzles curry in their parameters |
| Conditions | CREATE_COIN, ASSERT_HEIGHT, AGG_SIG_ME, etc. |
| Announcements | Atomic binding between pool and user spends |
| Height locks | ASSERT_HEIGHT_ABSOLUTE in stake_lock.clsp |
| Signatures | AGG_SIG_ME for staker claims and operator actions |
| Integer arithmetic | Reward calculation without floating point |
| Spend bundles | Combining multiple spends into one atomic operation |
| Python drivers | Full lifecycle management via chia-blockchain API |
| Testing | Simulator-based tests for every scenario |

---

## Architecture

```
                    STAKING POOL COIN
                    ==================
                    Curried with:
                    - POOL_AUTH_KEY
                    - CAT_TAIL_HASH
                    - STAKING_TIERS
                    - TOTAL_REWARDS
                           |
                           | User calls "stake" with chosen tier
                           | and stake_amount
                           v
              +------------+-------------+
              |                          |
              v                          v
     NEW POOL COIN               STAKE LOCK COIN
     ==============               ================
     Same puzzle, but             Curried with:
     TOTAL_REWARDS is             - STAKER_PUBKEY
     reduced by the               - STAKE_AMOUNT
     reward allocated             - REWARD_AMOUNT
     to this stake                - UNLOCK_HEIGHT
                                  - CAT_TAIL_HASH
                                         |
                                         | After UNLOCK_HEIGHT
                                         v
                                  STAKER CLAIMS
                                  stake + reward
```

### Detailed Transaction Flow

```
STEP 1: STAKE TRANSACTION (single SpendBundle with two coordinated spends)
==========================================================================

  Spend A: User's CAT coin (1000 tokens)
  +-----------------------------------------+
  | Conditions:                             |
  |   CREATE_COIN: change (if any)          |
  |   ASSERT_COIN_ANNOUNCEMENT:            |
  |     sha256(pool_coin_id + msg)         |
  |   CREATE_COIN_ANNOUNCEMENT: msg2       |
  +-----------------------------------------+
              |                    ^
              | assert each other  |
              v                    |
  Spend B: Pool coin (10000 reward tokens)
  +-----------------------------------------+
  | Solution: (stake staker_pk 1000 tier_0) |
  | Conditions:                             |
  |   CREATE_COIN: new pool @ 9950 tokens  |
  |   CREATE_COIN: lock @ 1050 tokens      |
  |   ASSERT_MY_COIN_ID                    |
  |   ASSERT_MY_AMOUNT                     |
  |   CREATE_COIN_ANNOUNCEMENT: msg        |
  |   ASSERT_COIN_ANNOUNCEMENT:            |
  |     sha256(user_coin_id + msg2)        |
  |   AGG_SIG_ME: pool_auth_key            |
  +-----------------------------------------+

STEP 2: CLAIM TRANSACTION (after unlock height reached)
=======================================================

  Spend: Stake Lock coin (1050 tokens)
  +-----------------------------------------+
  | Solution: (claim_puzzle_hash)           |
  | Conditions:                             |
  |   ASSERT_HEIGHT_ABSOLUTE: unlock_h     |
  |   AGG_SIG_ME: staker_pubkey            |
  |   CREATE_COIN: user_ph @ 1050 tokens   |
  +-----------------------------------------+
```

### Why Two Coins?

The pool coin and the stake lock coin serve fundamentally different purposes:

- The **pool coin** is a singleton-like coin that tracks available rewards. It must be updated atomically when someone stakes. It forms a chain: each stake operation consumes the old pool coin and creates a new one with reduced rewards.

- The **stake lock coin** is a personal coin for the staker. After creation, it is completely independent of the pool. The staker can claim it without any interaction with the pool coin. This independence means the pool operator cannot interfere with existing stakes.

### Why Announcements?

When a user stakes, both the pool coin spend and the stake lock coin creation must happen atomically in the same spend bundle. Announcements provide the binding:

- The pool coin creates a `CREATE_COIN_ANNOUNCEMENT` containing a hash of the stake parameters.
- The user's coin spend asserts this announcement via `ASSERT_COIN_ANNOUNCEMENT`.
- The user's coin creates its own announcement.
- The pool coin asserts the user's announcement.

This two-way binding means neither spend can be included on-chain without the other. If either fails, the entire bundle is rejected.

### Why CATs?

The system uses CAT (Chia Asset Token) tokens. The `CAT_TAIL_HASH` (asset ID) is curried into both puzzles, ensuring that only the correct token type is accepted. The CAT standard's token conservation rules guarantee that no tokens are created from nothing or destroyed.

---

## Staking Tiers and APY

The pool supports multiple staking tiers. Longer locks earn higher rewards.

| Tier | Lock Period | APY | Lock Blocks | Reward per 1000 tokens |
|------|------------|-----|-------------|----------------------|
| 1 | 30 days | 5% | 138,240 | ~4 tokens |
| 2 | 90 days | 12% | 414,720 | ~29 tokens |
| 3 | 180 days | 20% | 829,440 | ~98 tokens |
| 4 | 365 days | 35% | 1,681,920 | 350 tokens |

### APY Calculation

Chia uses block heights, not calendar time. Conversions:

```
1 block  ~ 18.75 seconds
1 hour   ~ 192 blocks
1 day    ~ 4,608 blocks
1 month  ~ 138,240 blocks (30 days)
1 year   ~ 1,681,920 blocks (365 days)
```

The reward for a given stake is:

```
reward = stake_amount * apy_rate * lock_days / 365 / 100
```

For example, staking 1000 tokens for 90 days at 12% APY:

```
reward = 1000 * 12 * 90 / 365 / 100 = 29.59 tokens
```

On-chain, we use integer arithmetic with basis points (1/10000) to avoid floating point:

```
reward = (stake_amount * apy_bps * lock_blocks) / (BLOCKS_PER_YEAR * 10000)
```

Where `apy_bps` = APY in basis points (12% = 1200 bps) and `BLOCKS_PER_YEAR` = 1,681,920.

### Reward Examples

| Stake | Tier | Lock Blocks | Rate (bps) | Calculation | Reward |
|-------|------|-------------|-----------|-------------|--------|
| 1000 | 1 | 138,240 | 500 | 1000 * 500 * 138240 / (1681920 * 10000) | 4 |
| 1000 | 2 | 414,720 | 1200 | 1000 * 1200 * 414720 / (1681920 * 10000) | 29 |
| 1000 | 3 | 829,440 | 2000 | 1000 * 2000 * 829440 / (1681920 * 10000) | 98 |
| 1000 | 4 | 1,681,920 | 3500 | 1000 * 3500 * 1681920 / (1681920 * 10000) | 350 |
| 5000 | 2 | 414,720 | 1200 | 5000 * 1200 * 414720 / (1681920 * 10000) | 148 |
| 10000 | 4 | 1,681,920 | 3500 | 10000 * 3500 * 1681920 / (1681920 * 10000) | 3500 |

All values in CAT mojos. Integer division truncates remainders, so the pool never over-commits.

---

## Puzzle Design: Staking Pool

**File**: `puzzles/staking_pool.clsp`

### Curried Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `POOL_AUTH_KEY` | G1 pubkey (48 bytes) | Pool operator's BLS public key |
| `CAT_TAIL_HASH` | bytes32 | Asset ID of the CAT token |
| `STAKING_TIERS` | list | List of `(lock_blocks apy_bps)` pairs |
| `TOTAL_REWARDS` | int | Total reward tokens remaining in the pool |

### Mode 0 -- Stake

A user stakes tokens. The solution provides:
- `tier_index`: Which staking tier (0-indexed)
- `stake_amount`: How many tokens to stake
- `staker_pubkey`: The staker's public key
- `my_amount`: Current pool coin amount
- `my_coin_id`: This coin's ID (verified by ASSERT_MY_COIN_ID)
- `user_coin_id`: The user's CAT coin ID (for announcement assertion)
- `new_pool_puzzle_hash`: Puzzle hash for the recreated pool coin
- `stake_lock_puzzle_hash`: Puzzle hash for the new stake lock coin

The puzzle logic:
1. Looks up the tier by index from `STAKING_TIERS`
2. Calculates the reward: `(stake_amount * apy_bps * lock_blocks) / (BLOCKS_PER_YEAR * 10000)`
3. Verifies the pool has enough rewards: `reward <= TOTAL_REWARDS`
4. Creates a new pool coin with `TOTAL_REWARDS - reward`
5. Creates a coin announcement binding this spend to the stake lock coin
6. Asserts the user's coin announcement
7. Requires the pool operator's signature (`AGG_SIG_ME`)

### Mode 1 -- Add Rewards

The pool operator adds more reward tokens. The solution provides:
- `add_amount`: How many tokens to add

The puzzle:
1. Requires `AGG_SIG_ME` from `POOL_AUTH_KEY`
2. Recreates the pool coin with `TOTAL_REWARDS + add_amount`
3. The additional tokens come from another coin in the same spend bundle

### Mode 2 -- Withdraw Rewards

The pool operator withdraws unclaimed rewards. The solution provides:
- `withdraw_amount`: How many tokens to withdraw

The puzzle:
1. Requires `AGG_SIG_ME` from `POOL_AUTH_KEY`
2. Enforces `ASSERT_HEIGHT_RELATIVE` with a 30-day timelock (138,240 blocks) to prevent instant rug pulls
3. Recreates the pool coin with `TOTAL_REWARDS - withdraw_amount`

---

## Puzzle Design: Stake Lock

**File**: `puzzles/stake_lock.clsp`

### Curried Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `STAKER_PUBKEY` | G1 pubkey (48 bytes) | The staker's BLS public key |
| `STAKE_AMOUNT` | int | Amount originally staked |
| `REWARD_AMOUNT` | int | Calculated reward amount |
| `UNLOCK_HEIGHT` | int | Absolute block height when unlock is allowed |
| `CAT_TAIL_HASH` | bytes32 | Asset ID (for verification and tracking) |

### Mode 0 -- Claim (after unlock)

The staker claims their tokens. The solution provides:
- `destination_puzzle_hash`: Where to send the tokens
- `my_amount`: This coin's amount (should equal STAKE_AMOUNT + REWARD_AMOUNT)

The puzzle:
1. Asserts `ASSERT_HEIGHT_ABSOLUTE >= UNLOCK_HEIGHT`
2. Requires `AGG_SIG_ME` from `STAKER_PUBKEY`
3. Creates a coin at `destination_puzzle_hash` with `STAKE_AMOUNT + REWARD_AMOUNT`
4. The coin is consumed (not recreated)

### Why ASSERT_HEIGHT_ABSOLUTE?

We use absolute height (not relative) because the unlock time is computed at stake creation:

```
UNLOCK_HEIGHT = current_height + lock_blocks
```

This value is curried into the coin at creation time. Using absolute height means:
- The unlock time is deterministic and visible to anyone inspecting the coin
- It cannot be manipulated by re-spending or restructuring the coin
- The check is enforced by the consensus layer, not by puzzle logic

---

## How Staking Works (Step by Step)

### Step 1: User Chooses a Tier

The user selects Tier 2 (90 days, 12% APY) and wants to stake 1000 tokens.

### Step 2: Driver Calculates the Reward

```python
lock_blocks = 414_720         # 90 days
apy_bps = 1200                # 12% = 1200 basis points
stake_amount = 1_000_000      # 1000 tokens (in mojos)
blocks_per_year = 1_681_920

reward = (stake_amount * apy_bps * lock_blocks) // (blocks_per_year * 10000)
# reward = 29_589  (~29.59 tokens)
```

### Step 3: Driver Builds the Stake Lock Puzzle

The driver curries the stake lock puzzle with the computed parameters:

```python
stake_lock_puzzle = STAKE_LOCK_MOD.curry(
    staker_pubkey,       # User's public key
    stake_amount,        # 1,000,000 mojos
    reward,              # 29,589 mojos
    current_height + lock_blocks,  # Unlock height
    cat_tail_hash,       # Asset ID
)
```

### Step 4: Driver Builds the Spend Bundle

The spend bundle contains two coordinated coin spends:

**Spend 1 -- Pool coin** (mode 0: stake):
- Consumes the current pool coin
- Creates a new pool coin with reduced TOTAL_REWARDS
- Creates a coin announcement with the stake parameters
- Asserts the user's coin announcement

**Spend 2 -- User's CAT coin**:
- Spends the user's tokens
- Creates a coin announcement that the pool coin expects
- Asserts the pool coin's announcement

The stake lock coin is created as an output of the pool coin spend. Its amount = `stake_amount + reward`.

### Step 5: Push to Network

Both spends are bundled together and submitted. If all conditions pass (valid signatures, valid announcements, sufficient rewards), the transaction is confirmed atomically.

### Result

After confirmation:
- The old pool coin is destroyed
- A new pool coin exists with `TOTAL_REWARDS - reward`
- A stake lock coin exists with `stake_amount + reward`, locked until `UNLOCK_HEIGHT`

---

## How Claiming Works

### Step 1: Check Unlock Status

```python
driver = StakeLockDriver(stake_lock_coin, config)
if driver.is_unlocked(current_block_height):
    print("Stake is ready to claim!")
```

### Step 2: Build the Claim Spend

The driver builds a spend that:
1. Spends the stake lock coin in mode 0 (claim)
2. Asserts height >= UNLOCK_HEIGHT
3. Requires the staker's BLS signature
4. Creates a coin at the staker's wallet puzzle hash with `STAKE_AMOUNT + REWARD_AMOUNT`

### Step 3: Sign and Push

The staker signs with their private key. The signature is bound to this specific coin via `AGG_SIG_ME`.

### Result

The staker receives their original tokens plus the reward. The stake lock coin is consumed and ceases to exist.

---

## Driver Design

**File**: `drivers/staking_driver.py`

### StakingPoolDriver

Manages the pool coin lifecycle:

| Method | Description |
|--------|-------------|
| `deploy_pool(total_rewards, tiers, auth_key, cat_tail_hash)` | Create the initial pool coin |
| `stake(pool_coin, staker_pk, tier_index, stake_amount, current_height)` | Process a staking request |
| `add_rewards(pool_coin, add_amount, operator_sk)` | Operator adds more reward tokens |
| `withdraw_rewards(pool_coin, withdraw_amount, operator_sk)` | Operator withdraws unclaimed rewards |
| `get_pool_info(pool_coin)` | Read current pool state (rewards, tiers, etc.) |
| `calculate_reward(tier_index, stake_amount)` | Compute the reward for a given stake |

### StakeLockDriver

Manages individual stake lock coins:

| Method | Description |
|--------|-------------|
| `claim(stake_lock_coin, destination_ph, staker_sk)` | Claim stake + reward after unlock |
| `get_stake_info(stake_lock_coin)` | Read stake details (amount, reward, unlock height) |
| `is_unlocked(current_height)` | Check if the stake can be claimed now |
| `time_until_unlock(current_height)` | Estimate remaining time until unlock |

---

## Security Considerations

### 1. Pool Solvency

The pool puzzle enforces `reward <= TOTAL_REWARDS` on-chain. If a stake request would require more rewards than the pool has, the transaction fails at the consensus level. There is no off-chain trust required.

### 2. Lock Enforcement

`ASSERT_HEIGHT_ABSOLUTE` is a consensus-layer condition. Every full node on the network rejects blocks that include a spend violating this condition. It is not a "soft" check -- the coin literally cannot be included in a block before the specified height.

### 3. Atomic Operations

The spend bundle mechanism guarantees atomicity. The pool coin spend and user coin spend are either both included or both rejected. Announcements provide the binding. This eliminates race conditions and partial execution.

### 4. Authorization

- **Pool operations**: Only `POOL_AUTH_KEY` holder can authorize stakes, add rewards, or withdraw.
- **Claim operations**: Only `STAKER_PUBKEY` holder can claim their specific stake.
- **AGG_SIG_ME**: All signatures include the coin ID, preventing signature replay across different coins.

### 5. CAT Conservation

The CAT standard enforces token conservation at the consensus level. The total amount of CAT tokens entering a transaction must equal the total amount leaving. This means:
- The pool cannot create reward tokens from nothing
- Tokens cannot be destroyed during staking or claiming
- The system is a closed loop: rewards deposited by the operator flow to stakers

### 6. Operator Withdrawal Timelock

The operator cannot instantly drain the reward pool. Mode 2 (withdraw) requires `ASSERT_HEIGHT_RELATIVE` with a 30-day timelock (138,240 blocks). This gives stakers advance warning and time to react.

### 7. Integer Arithmetic Safety

CLVM uses arbitrary-precision integers (no overflow). Reward calculations use integer division, which truncates (rounds down). This means rewards are slightly less than the theoretical APY, but the pool never over-commits. The dust from rounding accumulates as a tiny surplus in the pool.

---

## Testing Strategy

**File**: `tests/test_staking.py`

### Test Cases

| Test | Description | Expected Result |
|------|-------------|-----------------|
| `test_pool_creation` | Create a pool with valid tiers and rewards | Pool coin exists with correct puzzle hash and amount |
| `test_stake_tier_1` | Stake 1000 tokens for 30 days at 5% | Lock coin created with ~1004 tokens, pool reduced by ~4 |
| `test_stake_tier_4` | Stake 500 tokens for 365 days at 35% | Lock coin created with ~675 tokens, pool reduced by ~175 |
| `test_claim_after_unlock` | Claim stake after lock period elapses | Staker receives full stake + reward |
| `test_claim_before_unlock` | Attempt claim before lock period | Transaction FAILS (ASSERT_HEIGHT_ABSOLUTE) |
| `test_pool_exhaustion` | Stake more than pool can reward | Transaction FAILS (insufficient rewards) |
| `test_add_rewards` | Operator adds 5000 tokens to pool | Pool TOTAL_REWARDS increases by 5000 |
| `test_multiple_stakes` | Three users stake in sequence | Pool correctly tracks remaining rewards |
| `test_operator_withdraw` | Operator withdraws after 30-day timelock | Rewards returned to operator |
| `test_reward_calculation` | Verify reward math for each tier | Rewards match expected values |

### Running Tests

With `chia-blockchain` and `pytest` installed:

```bash
cd 07-staking-project
python -m pytest tests/test_staking.py -v
```

---

## Block Height Time Reference

| Duration | Blocks | Formula |
|----------|--------|---------|
| 1 minute | ~3.2 | 60 / 18.75 |
| 1 hour | ~192 | 3600 / 18.75 |
| 1 day | ~4,608 | 86400 / 18.75 |
| 1 week | ~32,256 | 4608 * 7 |
| 30 days | ~138,240 | 4608 * 30 |
| 90 days | ~414,720 | 4608 * 90 |
| 180 days | ~829,440 | 4608 * 180 |
| 365 days | ~1,681,920 | 4608 * 365 |

---

## Extending the Project

Ideas for building on top of this staking system:

1. **Early withdrawal with penalty**: Allow stakers to exit early but forfeit a percentage of their reward. The forfeited reward returns to the pool, benefiting remaining stakers.

2. **Auto-compounding**: When a stake unlocks, the staker can immediately re-stake the principal plus reward into a new lock, compounding returns.

3. **NFT staking receipts**: Issue an NFT when someone stakes, representing their position. This NFT could be traded on secondary markets, allowing stake position transfers.

4. **Multi-token pools**: Stake Token A, earn Token B. For example, stake a governance token and earn a utility token.

5. **Dynamic APY**: Adjust the APY based on total staked amount. More stakers means lower APY per person, creating natural demand equilibrium.

6. **Governance integration**: Combine with the voting system from Chapter 6. Staked tokens could double as governance votes, rewarding active participants.

7. **Proper singleton wrapping**: Wrap the pool coin in a Chia singleton for guaranteed uniqueness and easier tracking.

---

## How to Study This Chapter

1. **Start with the README** (this file). Understand the architecture and flow.

2. **Read `stake_lock.clsp` first**. It is the simpler puzzle. Make sure you understand every condition it produces.

3. **Read `staking_pool.clsp`**. Follow the mode-based branching and the reward calculation logic.

4. **Trace the reward math by hand**. Pick a tier and amount. Follow the integer arithmetic step by step. Verify against the examples table above.

5. **Read `staking_driver.py`**. See how the Python code constructs curried puzzles, builds solutions, and assembles spend bundles. Pay attention to how announcements are created and matched between the pool spend and user spend.

6. **Read `test_staking.py`**. Each test documents what it verifies. Try modifying values (e.g., change the unlock height or stake amount) to see how failures manifest.

7. **Build your own extension**. Pick one of the ideas above and implement it. This is the best way to solidify your understanding.

---

## File Structure

```
07-staking-project/
  README.md                      # This file (project overview and guide)
  puzzles/
    staking_pool.clsp            # Pool puzzle (manages rewards, handles stake requests)
    stake_lock.clsp              # Lock puzzle (locks staker's tokens until unlock height)
  drivers/
    staking_driver.py            # Python driver for both puzzles
  tests/
    test_staking.py              # Comprehensive test scenarios
```
