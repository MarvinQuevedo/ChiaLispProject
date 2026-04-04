# Chapter 6: Advanced Real-World Examples

In this chapter we move beyond toy examples and build **five practical systems** that demonstrate how ChiaLisp puzzles solve real problems. Each example includes:

- A fully commented `.clsp` puzzle
- A Python driver that shows how an application would interact with the puzzle
- An explanation of the design decisions and security considerations

> **Prerequisites**: You should be comfortable with currying, inner puzzles, conditions, announcements, and Python drivers (Chapters 1-5).

---

## Table of Contents

1. [Escrow System](#1-escrow-system)
2. [Savings Account](#2-savings-account)
3. [Lottery / Raffle](#3-lottery--raffle)
4. [Token Vesting](#4-token-vesting)
5. [Simple Voting System](#5-simple-voting-system)

---

## 1. Escrow System

**Files**: [`examples/escrow/escrow.clsp`](https://github.com/MarvinQuevedo/ChiaLispProject/blob/main/06-advanced-examples/examples/escrow/escrow.clsp) | [`examples/escrow/escrow_driver.py`](https://github.com/MarvinQuevedo/ChiaLispProject/blob/main/06-advanced-examples/examples/escrow/escrow_driver.py)

### Concept and Real-World Use Case

An escrow is a financial arrangement where a third party holds funds on behalf of two transacting parties. The funds are released only when predefined conditions are met. This is the backbone of marketplace transactions, freelance payments, and real-estate closings.

On Chia, we implement this as a coin whose puzzle enforces three possible resolution paths:

| Path | Condition | Result |
|------|-----------|--------|
| **Mutual agreement** | Buyer AND Seller both sign | Funds go to seller (or refund to buyer) |
| **Arbiter decides** | Arbiter signs + one party signs | Arbiter directs funds |
| **Timeout** | Block height exceeds deadline | Buyer gets automatic refund |

### How the Puzzle Logic Works

The escrow puzzle is curried with five parameters:

- `BUYER_PUBKEY` -- the buyer who deposits funds
- `SELLER_PUBKEY` -- the seller who will receive funds on completion
- `ARBITER_PUBKEY` -- a trusted third party for disputes
- `SELLER_PUZZLE_HASH` -- where funds go on successful completion
- `BUYER_PUZZLE_HASH` -- where funds go on refund
- `TIMEOUT_HEIGHT` -- block height after which the buyer can reclaim funds

The solution provides a `mode` argument:

1. **Mode 0 -- Mutual Release**: Both buyer and seller agree. The puzzle requires `AGG_SIG_ME` from both `BUYER_PUBKEY` and `SELLER_PUBKEY`. Funds are sent to `SELLER_PUZZLE_HASH`.

2. **Mode 1 -- Arbiter Decision**: The arbiter steps in. The puzzle requires `AGG_SIG_ME` from `ARBITER_PUBKEY` and the solution specifies the destination puzzle hash. This allows the arbiter to send funds to either party.

3. **Mode 2 -- Timeout Refund**: No signatures required beyond the buyer's. The puzzle checks `ASSERT_HEIGHT_RELATIVE` against `TIMEOUT_HEIGHT` and sends funds back to `BUYER_PUZZLE_HASH`.

Announcements are used so that when the escrow is resolved, the buyer's or seller's wallet coin can `ASSERT_COIN_ANNOUNCEMENT` to confirm the escrow coin was spent in the same bundle.

### Security Considerations

- **No single point of failure**: No single key can steal funds. Mutual agreement needs two keys; arbiter path needs the arbiter key; timeout only returns to the buyer.
- **Timeout prevents deadlock**: If the seller disappears, the buyer is not stuck forever.
- **Arbiter flexibility**: The arbiter can split funds or direct them to either party, useful for partial disputes.
- **Replay protection**: `AGG_SIG_ME` ties signatures to this specific coin, preventing replay on other escrow coins.

### Driver Interaction

The Python driver (`escrow_driver.py`) handles:

1. **Creating the escrow**: Currying parameters into the puzzle, creating the coin.
2. **Releasing funds**: Building the spend bundle for mutual agreement or arbiter decision.
3. **Timeout refund**: Building the spend bundle that asserts height and refunds to buyer.

The driver assembles the correct `CoinSpend` and pairs it with the appropriate aggregated signature.

---

## 2. Savings Account

**Files**: [`examples/savings/savings_account.clsp`](https://github.com/MarvinQuevedo/ChiaLispProject/blob/main/06-advanced-examples/examples/savings/savings_account.clsp) | [`examples/savings/savings_driver.py`](https://github.com/MarvinQuevedo/ChiaLispProject/blob/main/06-advanced-examples/examples/savings/savings_driver.py)

### Concept and Real-World Use Case

A savings account that enforces withdrawal discipline:

- **Deposit anytime**: Anyone can add funds by recreating the coin with a higher amount.
- **Limited withdrawals**: Only X% of the balance can be withdrawn per time period.
- **Emergency withdrawal**: The full balance can be withdrawn, but only after a much longer time lock (e.g., 30 days).

This is useful for personal savings discipline, treasury management for DAOs, or any scenario where you want to prevent impulsive large withdrawals.

### How the Puzzle Logic Works

The puzzle uses a **singleton-like pattern** to maintain state. The coin always recreates itself with updated parameters.

Curried parameters:
- `OWNER_PUBKEY` -- the account owner
- `WITHDRAWAL_LIMIT_PERCENT` -- max percentage per withdrawal (e.g., 10 = 10%)
- `WITHDRAWAL_COOLDOWN` -- minimum blocks between withdrawals (e.g., 4608 blocks ~ 1 day)
- `EMERGENCY_TIMELOCK` -- blocks to wait for full emergency withdrawal (e.g., 138240 ~ 30 days)
- `LAST_WITHDRAWAL_HEIGHT` -- the block height of the last withdrawal (state)

Solution modes:

1. **Mode 0 -- Deposit**: The coin is spent and recreated with the same puzzle hash but a higher amount. The difference is the deposit. Owner signature required.

2. **Mode 1 -- Partial Withdrawal**: Owner can withdraw up to `WITHDRAWAL_LIMIT_PERCENT` of the current balance. The puzzle enforces:
   - `ASSERT_HEIGHT_RELATIVE` for the cooldown period
   - The withdrawal amount is at most the allowed percentage
   - A new coin is created with the remaining balance and an updated `LAST_WITHDRAWAL_HEIGHT`

3. **Mode 2 -- Emergency Withdrawal**: Owner can withdraw the full balance after the emergency timelock. The puzzle enforces `ASSERT_HEIGHT_RELATIVE` with the longer timelock.

### Security Considerations

- **State continuity**: The puzzle recreates itself, carrying forward the updated last-withdrawal height. This prevents rapid repeated withdrawals.
- **Percentage enforcement**: The puzzle calculates the maximum allowed amount on-chain using integer division. Since CLVM only has integers, percentages are computed as `(amount * WITHDRAWAL_LIMIT_PERCENT) / 100`.
- **Cooldown reset**: Each withdrawal resets the cooldown clock by currying the new height into the recreated coin.
- **Emergency escape**: The long timelock for full withdrawal balances security with the need to access funds in genuine emergencies.

### Driver Interaction

The driver tracks the singleton-like coin, computes allowed withdrawal amounts, and builds spend bundles for each operation mode.

---

## 3. Lottery / Raffle

**Files**: [`examples/lottery/lottery.clsp`](https://github.com/MarvinQuevedo/ChiaLispProject/blob/main/06-advanced-examples/examples/lottery/lottery.clsp) | [`examples/lottery/lottery_driver.py`](https://github.com/MarvinQuevedo/ChiaLispProject/blob/main/06-advanced-examples/examples/lottery/lottery_driver.py)

### Concept and Real-World Use Case

A decentralized lottery where:

1. Players buy tickets by sending XCH to the lottery coin.
2. After a deadline, anyone can trigger the draw.
3. The winner is selected using on-chain randomness (parent coin ID + block height).
4. The prize pool is sent to the winner.

This can be used for raffles, giveaways, or any fair random selection process.

### How the Puzzle Logic Works

The lottery uses two phases, each with its own puzzle:

**Phase 1 -- Ticket Sales (the lottery coin)**

Curried parameters:
- `OPERATOR_PUBKEY` -- the lottery operator
- `TICKET_PRICE` -- price per ticket in mojos
- `DEADLINE_HEIGHT` -- block height when ticket sales close
- `MAX_TICKETS` -- maximum number of tickets
- `TICKET_LIST` -- list of (puzzle_hash) for each ticket holder

When a player buys a ticket, the coin is spent and recreated with the player's puzzle hash appended to `TICKET_LIST` and the amount increased by `TICKET_PRICE`.

**Phase 2 -- Draw**

After `DEADLINE_HEIGHT`, anyone can trigger the draw. The puzzle:
1. Asserts `ASSERT_HEIGHT_RELATIVE` to confirm the deadline has passed.
2. Computes a pseudo-random index: `(sha256 MY_COIN_ID) mod (length TICKET_LIST)`.
3. Sends the prize pool to the winner's puzzle hash.
4. A small operator fee (e.g., 2%) is sent to the operator.

### Security Considerations

- **Randomness**: On-chain randomness is not perfectly secure -- a miner could theoretically manipulate which block includes the draw transaction. For low-stakes lotteries this is acceptable. For high-stakes applications, consider commit-reveal schemes or VRF oracles.
- **Deadline enforcement**: Ticket purchases are blocked after the deadline via `ASSERT_BEFORE_HEIGHT_RELATIVE`.
- **Operator fee**: Kept small and transparent; the operator cannot take more than the curried percentage.
- **Minimum tickets**: The draw can enforce a minimum number of tickets; if not met, refunds are issued.

### Driver Interaction

The driver handles ticket purchases (spending and recreating the lottery coin), triggering the draw, and distributing prizes. It tracks the evolving lottery coin as tickets are purchased.

---

## 4. Token Vesting

**Files**: [`examples/vesting/vesting.clsp`](https://github.com/MarvinQuevedo/ChiaLispProject/blob/main/06-advanced-examples/examples/vesting/vesting.clsp) | [`examples/vesting/vesting_driver.py`](https://github.com/MarvinQuevedo/ChiaLispProject/blob/main/06-advanced-examples/examples/vesting/vesting_driver.py)

### Concept and Real-World Use Case

Token vesting is standard in crypto projects: team members, investors, or advisors receive tokens that unlock gradually over time. This prevents dumping and aligns long-term incentives.

Our vesting schedule:
- **Cliff period**: No tokens can be claimed for the first N blocks (e.g., 6 months).
- **Linear vesting**: After the cliff, tokens unlock proportionally over the remaining vesting period.
- **Full vest**: After the total vesting period, all remaining tokens are available.

### How the Puzzle Logic Works

Curried parameters:
- `BENEFICIARY_PUBKEY` -- who receives the vesting tokens
- `TOTAL_AMOUNT` -- total tokens to vest
- `CLIFF_HEIGHT` -- block height when the cliff ends
- `VESTING_START_HEIGHT` -- the block height when vesting began (usually same as coin creation)
- `TOTAL_VESTING_PERIOD` -- total blocks for full vesting (cliff + linear period)
- `CLAIMED_AMOUNT` -- how many tokens have already been claimed (state)

Solution provides the `claim_amount` and the current `block_height`.

The puzzle logic:

1. **Before cliff**: Spending is completely blocked (`ASSERT_HEIGHT_RELATIVE` would fail since puzzle requires height >= `CLIFF_HEIGHT`).

2. **After cliff, during vesting**:
   - Calculate elapsed blocks since vesting start: `elapsed = current_height - VESTING_START_HEIGHT`
   - Calculate vested amount: `vested = (TOTAL_AMOUNT * elapsed) / TOTAL_VESTING_PERIOD`
   - Available to claim: `available = vested - CLAIMED_AMOUNT`
   - The puzzle verifies `claim_amount <= available`
   - Creates a new coin with updated `CLAIMED_AMOUNT` and reduced balance

3. **After full vesting period**: All remaining tokens can be claimed.

### Security Considerations

- **Height-based time**: Chia blocks average 18.75 seconds. 1 day ~ 4608 blocks, 1 month ~ 138240 blocks. This is approximate but sufficient for vesting.
- **Integer arithmetic**: All vesting math uses integer division, which means tiny rounding dust may remain. The "after full vesting" path handles this by allowing full withdrawal.
- **State tracking**: `CLAIMED_AMOUNT` is curried into each new coin, preventing double-claiming.
- **Beneficiary-only**: Only the beneficiary can claim, enforced by `AGG_SIG_ME`.

### Driver Interaction

The driver calculates the current vested amount based on block height, determines how much is claimable, and builds the spend bundle. It also provides a `vesting_schedule()` method that shows a human-readable timeline.

---

## 5. Simple Voting System

**Files**: [`examples/voting/voting.clsp`](https://github.com/MarvinQuevedo/ChiaLispProject/blob/main/06-advanced-examples/examples/voting/voting.clsp) | [`examples/voting/voting_driver.py`](https://github.com/MarvinQuevedo/ChiaLispProject/blob/main/06-advanced-examples/examples/voting/voting_driver.py)

### Concept and Real-World Use Case

A simple on-chain voting system where:

1. A proposal is created with a set of options.
2. Token holders cast votes by spending coins that reference the proposal.
3. Votes are tallied via announcements.
4. After the voting period, the result is determined.

This is useful for DAO governance, community decisions, or any scenario requiring transparent, verifiable voting.

### How the Puzzle Logic Works

The system has two puzzle types:

**Proposal Puzzle (the ballot box)**

Curried parameters:
- `PROPOSAL_HASH` -- hash of the proposal text (for verification)
- `OPTIONS` -- list of option hashes (e.g., "yes", "no", "abstain")
- `DEADLINE_HEIGHT` -- when voting ends
- `TALLY` -- list of vote counts per option (state, starts at all zeros)
- `CREATOR_PUBKEY` -- who created the proposal

The proposal coin accepts votes and recreates itself with updated tallies. After the deadline, it can be finalized to announce the result.

**Vote Puzzle (individual vote)**

When a voter wants to cast a vote, they create a coin announcement containing:
- The proposal coin ID
- Their chosen option index
- The weight of their vote (based on coin amount)

The proposal coin uses `ASSERT_COIN_ANNOUNCEMENT` to verify each vote in the same spend bundle, then recreates itself with updated tallies.

### Security Considerations

- **One coin, one vote**: Each coin can only vote once because spending it destroys it.
- **Vote weight**: Vote weight is proportional to coin amount, giving a token-weighted voting system.
- **Deadline enforcement**: No votes accepted after the deadline.
- **Transparent tallying**: The tally is part of the puzzle state, so anyone can verify counts by examining the coin.
- **Sybil resistance**: Since votes are weighted by token amount, splitting coins does not give extra voting power.

### Driver Interaction

The driver creates proposals, submits votes (coordinating the vote coin spend with the proposal coin spend in the same bundle), and reads the final tally from the last proposal coin state.

---

## Running the Examples

Each example is self-contained. To study them:

1. Read the `.clsp` file and its comments to understand the on-chain logic.
2. Read the `_driver.py` file to understand how a wallet or application interacts with the puzzle.
3. Try modifying parameters (timeouts, percentages, etc.) to see how the behavior changes.

In the next chapter, we will combine many of these patterns into a full **CAT Staking System** as the capstone project.
