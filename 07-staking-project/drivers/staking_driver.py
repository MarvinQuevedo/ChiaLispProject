"""
CAT Staking Pool Driver
========================

Provides classes to interact with the staking pool and stake lock puzzles.

This module contains two main classes:

    StakingPoolDriver
        Handles pool-level operations: deploying the pool, processing stakes,
        adding rewards, and withdrawing unclaimed rewards.

    StakeLockDriver
        Handles lock-level operations: claiming unlocked tokens and querying
        lock status.

Both classes follow the standard chia-blockchain driver patterns, using
real types from the chia library (Program, CoinSpend, SpendBundle, etc.)
and constructing proper spend bundles with BLS signatures.

Usage Example
-------------
    from chia.rpc.full_node_rpc_client import FullNodeRpcClient
    from blspy import G1Element, G2Element, AugSchemeMPL, PrivateKey

    # Connect to a full node
    client = await FullNodeRpcClient.create(...)

    # Define staking tiers: (lock_blocks, rate_numerator, rate_denominator)
    tiers = [
        (138240,  5,  100),   # 30 days,  5% APY
        (414720,  12, 100),   # 90 days,  12% APY
        (829440,  20, 100),   # 180 days, 20% APY
        (1681920, 35, 100),   # 365 days, 35% APY
    ]

    # Create driver instances
    pool_driver = StakingPoolDriver(client, cat_tail_hash, pool_auth_pk, tiers)
    lock_driver = StakeLockDriver(client, cat_tail_hash)

    # Deploy the pool with 10000 CAT mojos as rewards
    deploy_bundle = await pool_driver.deploy_pool(10000)

    # A user stakes 1000 tokens for 30 days (tier 0)
    stake_bundle = await pool_driver.stake(pool_coin, staker_pk, 1000, 0)

    # After lock period, user claims
    claim_bundle = await lock_driver.claim(lock_coin, staker_sk, user_puzzle_hash)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

# =============================================================================
# Chia blockchain imports
# =============================================================================
# These imports come from the chia-blockchain library. Install with:
#   pip install chia-blockchain
#
# The key types used:
#   - Program: Represents a CLVM program (compiled puzzle or solution)
#   - CoinSpend: A spend of a single coin (coin + puzzle + solution)
#   - SpendBundle: A collection of CoinSpends + aggregated signature
#   - Coin: Represents an unspent coin on the blockchain
#   - G1Element / G2Element: BLS public key / signature types
#   - AugSchemeMPL: BLS signature scheme used by Chia
# =============================================================================

from chia.types.blockchain_format.program import Program
from chia.types.blockchain_format.coin import Coin
from chia.types.blockchain_format.sized_bytes import bytes32
from chia.types.coin_spend import CoinSpend
from chia.types.spend_bundle import SpendBundle
from chia.types.condition_opcodes import ConditionOpcode
from chia.util.hash import std_hash
from chia.wallet.puzzles.load_clvm import load_clvm

from blspy import G1Element, G2Element, AugSchemeMPL, PrivateKey

logger = logging.getLogger(__name__)

# =============================================================================
# Constants
# =============================================================================

# Number of blocks in one year (4608 blocks/day * 365 days)
BLOCKS_PER_YEAR = 1_681_920

# Path to compiled puzzle files (relative to this driver)
PUZZLE_DIR = Path(__file__).parent.parent / "puzzles"

# =============================================================================
# Staking Tier Data Class
# =============================================================================

@dataclass
class StakingTier:
    """
    Represents a single staking tier configuration.

    Attributes:
        lock_blocks: Number of blocks the stake is locked.
        rate_numerator: APY numerator (e.g., 5 for 5%).
        rate_denominator: APY denominator (e.g., 100).
    """
    lock_blocks: int
    rate_numerator: int
    rate_denominator: int

    def calculate_reward(self, stake_amount: int) -> int:
        """
        Calculate the reward for a given stake amount using integer arithmetic.

        Formula:
            reward = stake_amount * rate_numerator * lock_blocks
                     / (BLOCKS_PER_YEAR * rate_denominator)

        This matches the on-chain calculation in staking_pool.clsp exactly.

        Args:
            stake_amount: Number of CAT mojos being staked.

        Returns:
            Reward amount in CAT mojos (integer, truncated).

        Example:
            >>> tier = StakingTier(138240, 5, 100)
            >>> tier.calculate_reward(1000)
            4
        """
        return (
            stake_amount * self.rate_numerator * self.lock_blocks
            // (BLOCKS_PER_YEAR * self.rate_denominator)
        )

    def to_clvm(self) -> Program:
        """
        Convert this tier to a CLVM list for currying into the pool puzzle.

        Returns:
            Program representing (lock_blocks rate_numerator rate_denominator).
        """
        return Program.to([
            self.lock_blocks,
            self.rate_numerator,
            self.rate_denominator,
        ])


# =============================================================================
# Helper Functions
# =============================================================================

def load_pool_puzzle() -> Program:
    """
    Load and return the compiled staking pool puzzle.

    Returns:
        The compiled CLVM program for staking_pool.clsp.

    Raises:
        FileNotFoundError: If the compiled puzzle file is not found.
    """
    return load_clvm("staking_pool.clsp", search_paths=[str(PUZZLE_DIR)])


def load_lock_puzzle() -> Program:
    """
    Load and return the compiled stake lock puzzle.

    Returns:
        The compiled CLVM program for stake_lock.clsp.

    Raises:
        FileNotFoundError: If the compiled puzzle file is not found.
    """
    return load_clvm("stake_lock.clsp", search_paths=[str(PUZZLE_DIR)])


def tiers_to_clvm(tiers: List[StakingTier]) -> Program:
    """
    Convert a list of StakingTier objects to a CLVM list for currying.

    Args:
        tiers: List of StakingTier objects.

    Returns:
        Program representing the list of tier triplets.

    Example:
        >>> tiers = [StakingTier(138240, 5, 100), StakingTier(414720, 12, 100)]
        >>> tiers_to_clvm(tiers)
        ((138240 5 100) (414720 12 100))
    """
    return Program.to([t.to_clvm() for t in tiers])


# =============================================================================
# StakingPoolDriver
# =============================================================================

class StakingPoolDriver:
    """
    Driver for the CAT Staking Pool puzzle.

    This class handles all pool-level operations:
    - Deploying a new staking pool
    - Processing user stakes
    - Adding rewards (operator)
    - Withdrawing unclaimed rewards (operator)

    The driver constructs CLVM puzzles, builds solutions, creates spend bundles,
    and handles BLS signatures. It communicates with a Chia full node via RPC
    to look up coins and blockchain state.

    Attributes:
        full_node_client: RPC client connected to a Chia full node.
        cat_tail_hash: The asset ID (TAIL hash) of the CAT token.
        pool_auth_key: The operator's BLS public key.
        staking_tiers: List of StakingTier configurations.
    """

    def __init__(
        self,
        full_node_client,
        cat_tail_hash: bytes32,
        pool_auth_key: G1Element,
        staking_tiers: List[StakingTier],
    ):
        """
        Initialize the StakingPoolDriver.

        Args:
            full_node_client: An RPC client connected to a Chia full node.
                Expected to have methods like get_coin_record_by_name(),
                get_blockchain_state(), push_tx(), etc.
            cat_tail_hash: The 32-byte asset ID of the CAT token that this
                pool will manage.
            pool_auth_key: The BLS public key (G1Element) of the pool
                operator. This key is curried into the puzzle and required
                for administrative operations.
            staking_tiers: A list of StakingTier objects defining the
                available lock durations and APY rates. These are curried
                into the puzzle at deployment time and cannot be changed.
        """
        self.full_node_client = full_node_client
        self.cat_tail_hash = cat_tail_hash
        self.pool_auth_key = pool_auth_key
        self.staking_tiers = staking_tiers

        # Load and cache the compiled base puzzles
        self._base_pool_puzzle: Program = load_pool_puzzle()
        self._base_lock_puzzle: Program = load_lock_puzzle()

        # Cache the curried pool puzzle (parameters are fixed at init)
        self._curried_pool_puzzle: Optional[Program] = None

    # -----------------------------------------------------------------
    # Puzzle Construction
    # -----------------------------------------------------------------

    def compile_pool_puzzle(self) -> Program:
        """
        Compile and curry the staking pool puzzle with this driver's parameters.

        The pool puzzle is curried with three parameters:
        1. POOL_AUTH_KEY: The operator's public key
        2. CAT_TAIL_HASH: The asset ID of the CAT token
        3. STAKING_TIERS: The list of tier configurations

        Returns:
            The fully curried pool puzzle as a Program.

        Note:
            The result is cached after the first call since the curried
            parameters never change for this driver instance.
        """
        if self._curried_pool_puzzle is None:
            logger.debug("Currying pool puzzle with auth_key=%s, tail=%s, tiers=%d",
                         self.pool_auth_key, self.cat_tail_hash.hex(), len(self.staking_tiers))

            self._curried_pool_puzzle = self._base_pool_puzzle.curry(
                # Parameter 1: POOL_AUTH_KEY
                # Passed as a G1Element (48 bytes). In CLVM, this is an atom.
                Program.to(bytes(self.pool_auth_key)),

                # Parameter 2: CAT_TAIL_HASH
                # Passed as bytes32. In CLVM, this is a 32-byte atom.
                Program.to(self.cat_tail_hash),

                # Parameter 3: STAKING_TIERS
                # Passed as a list of lists: ((lock rate_num rate_denom) ...)
                tiers_to_clvm(self.staking_tiers),
            )

        return self._curried_pool_puzzle

    def get_pool_puzzle_hash(self) -> bytes32:
        """
        Get the puzzle hash of the curried pool puzzle.

        This is the hash that identifies pool coins on-chain. All pool coins
        share the same puzzle hash because the curried parameters are fixed.

        Returns:
            The 32-byte puzzle hash of the curried pool puzzle.
        """
        return self.compile_pool_puzzle().get_tree_hash()

    def compile_lock_puzzle(
        self,
        staker_pubkey: G1Element,
        total_amount: int,
        unlock_height: int,
        pool_coin_id: bytes32,
    ) -> Program:
        """
        Compile and curry the stake lock puzzle for a specific stake.

        Each stake lock puzzle is unique because it is curried with parameters
        specific to that individual stake (staker's key, amount, unlock height,
        and the pool coin that created it).

        Args:
            staker_pubkey: The staker's BLS public key (G1Element).
            total_amount: The total locked amount (stake + reward) in CAT mojos.
            unlock_height: The absolute block height when tokens unlock.
            pool_coin_id: The coin ID of the pool coin creating this lock.

        Returns:
            The fully curried lock puzzle as a Program.
        """
        logger.debug(
            "Currying lock puzzle: staker=%s, amount=%d, unlock=%d, pool_id=%s",
            staker_pubkey, total_amount, unlock_height, pool_coin_id.hex()
        )

        return self._base_lock_puzzle.curry(
            # Parameter 1: STAKER_PUBKEY
            Program.to(bytes(staker_pubkey)),

            # Parameter 2: TOTAL_AMOUNT
            Program.to(total_amount),

            # Parameter 3: UNLOCK_HEIGHT
            Program.to(unlock_height),

            # Parameter 4: POOL_COIN_ID
            Program.to(pool_coin_id),
        )

    # -----------------------------------------------------------------
    # Pool Deployment
    # -----------------------------------------------------------------

    async def deploy_pool(self, initial_rewards_amount: int) -> CoinSpend:
        """
        Deploy a new staking pool by creating the initial pool coin.

        This method creates the first pool coin with the specified amount of
        CAT tokens as staking rewards. The operator must fund this coin from
        their wallet.

        Args:
            initial_rewards_amount: The number of CAT mojos to deposit as
                initial staking rewards.

        Returns:
            A CoinSpend that, when included in a SpendBundle and pushed to
            the network, creates the initial pool coin.

        Raises:
            ValueError: If initial_rewards_amount is not positive.

        Note:
            The returned CoinSpend is for the pool creation only. The caller
            must also include a spend for the funding coin (from the operator's
            wallet) in the same SpendBundle.
        """
        if initial_rewards_amount <= 0:
            raise ValueError(
                f"Initial rewards must be positive, got {initial_rewards_amount}"
            )

        pool_puzzle = self.compile_pool_puzzle()
        pool_puzzle_hash = pool_puzzle.get_tree_hash()

        logger.info(
            "Deploying staking pool: rewards=%d, puzzle_hash=%s",
            initial_rewards_amount, pool_puzzle_hash.hex()
        )

        # The deploy operation is handled by the wallet layer, which creates
        # a coin with the pool puzzle hash and the specified amount.
        # Here we return the puzzle and metadata needed for deployment.
        #
        # In a real deployment, the operator would:
        #   1. Create a CAT coin with puzzle_hash = pool_puzzle_hash
        #   2. Set amount = initial_rewards_amount
        #   3. Sign and push the transaction
        #
        # We provide the puzzle information for this step.

        return {
            "puzzle": pool_puzzle,
            "puzzle_hash": pool_puzzle_hash,
            "amount": initial_rewards_amount,
            "tiers": [
                {
                    "lock_blocks": t.lock_blocks,
                    "rate_numerator": t.rate_numerator,
                    "rate_denominator": t.rate_denominator,
                }
                for t in self.staking_tiers
            ],
        }

    # -----------------------------------------------------------------
    # Staking
    # -----------------------------------------------------------------

    async def stake(
        self,
        pool_coin: Coin,
        staker_pubkey: G1Element,
        stake_amount: int,
        tier_index: int,
    ) -> SpendBundle:
        """
        Create a spend bundle for staking CAT tokens.

        This is the most complex operation. It creates a SpendBundle containing:
        1. The pool coin spend (creates new pool coin + stake lock coin)
        2. The staker's CAT coin spend (provides the staked tokens)

        Both spends are bound together atomically via announcements.

        Args:
            pool_coin: The current pool coin (Coin object with parent_id,
                puzzle_hash, and amount).
            staker_pubkey: The staker's BLS public key.
            stake_amount: Number of CAT mojos to stake.
            tier_index: Index of the chosen staking tier (0-3).

        Returns:
            A SpendBundle ready to be pushed to the network.

        Raises:
            ValueError: If tier_index is out of range.
            ValueError: If stake_amount is not positive.
            InsufficientRewardsError: If the pool cannot cover the reward.

        Example:
            >>> pool_coin = Coin(parent_id, pool_ph, 10000)
            >>> bundle = await driver.stake(pool_coin, staker_pk, 1000, 0)
            >>> await client.push_tx(bundle)
        """
        # ---- Input Validation ----

        if tier_index < 0 or tier_index >= len(self.staking_tiers):
            raise ValueError(
                f"tier_index must be 0-{len(self.staking_tiers)-1}, got {tier_index}"
            )

        if stake_amount <= 0:
            raise ValueError(f"stake_amount must be positive, got {stake_amount}")

        # ---- Tier and Reward Calculation ----

        tier = self.staking_tiers[tier_index]
        reward = tier.calculate_reward(stake_amount)
        total_lock_amount = stake_amount + reward

        logger.info(
            "Staking: amount=%d, tier=%d (lock=%d blocks, rate=%d/%d), reward=%d",
            stake_amount, tier_index, tier.lock_blocks,
            tier.rate_numerator, tier.rate_denominator, reward
        )

        # Verify the pool has enough rewards
        if reward > pool_coin.amount:
            raise ValueError(
                f"Pool has insufficient rewards: needs {reward}, "
                f"has {pool_coin.amount}"
            )

        # ---- Get Current Blockchain Height ----
        # We need the current height to calculate the unlock height.

        blockchain_state = await self.full_node_client.get_blockchain_state()
        current_height = blockchain_state["peak"].height
        unlock_height = current_height + tier.lock_blocks

        logger.debug(
            "Current height: %d, unlock height: %d", current_height, unlock_height
        )

        # ---- Build the Lock Puzzle ----
        # Curry the stake_lock puzzle with this stake's specific parameters.

        pool_coin_id = pool_coin.name()  # Coin.name() returns the coin ID (bytes32)

        lock_puzzle = self.compile_lock_puzzle(
            staker_pubkey=staker_pubkey,
            total_amount=total_lock_amount,
            unlock_height=unlock_height,
            pool_coin_id=pool_coin_id,
        )
        lock_puzzle_hash = lock_puzzle.get_tree_hash()

        logger.debug("Lock puzzle hash: %s", lock_puzzle_hash.hex())

        # ---- Build the Pool Coin Solution ----
        # The solution tells the pool puzzle what action to take (action=1 for STAKE)
        # and provides all the parameters needed for the stake operation.

        pool_puzzle = self.compile_pool_puzzle()
        pool_puzzle_hash = pool_puzzle.get_tree_hash()
        new_pool_amount = pool_coin.amount - reward

        pool_solution = Program.to([
            1,                          # action_type: STAKE
            bytes(staker_pubkey),       # arg1: staker's public key
            stake_amount,               # arg2: amount being staked
            tier_index,                 # arg3: tier selection
            pool_coin_id,               # my_coin_id: for ASSERT_MY_COIN_ID
            pool_coin.amount,           # my_amount: for ASSERT_MY_AMOUNT
            pool_puzzle_hash,           # my_puzzle_hash: to recreate the pool coin
            lock_puzzle_hash,           # extra_arg: precomputed lock puzzle hash
        ])

        # ---- Create the Pool CoinSpend ----
        # A CoinSpend is the combination of: the coin being spent, its puzzle
        # (reveal), and the solution.

        pool_coin_spend = CoinSpend(
            pool_coin,          # The coin being spent
            pool_puzzle,        # The puzzle reveal (full curried puzzle)
            pool_solution,      # The solution
        )

        # ---- Build the Announcement Message ----
        # The pool coin creates an announcement that the staker's coin will assert.
        # This is the message: sha256(staker_pubkey + stake_amount + tier_index)
        # It must match what the pool puzzle computes internally.

        announcement_msg = std_hash(
            bytes(staker_pubkey)
            + stake_amount.to_bytes(32, "big")
            + tier_index.to_bytes(32, "big")
        )

        # The full coin announcement hash that the staker will assert is:
        #   sha256(pool_coin_id + announcement_msg)
        coin_announcement_hash = std_hash(pool_coin_id + announcement_msg)

        logger.debug("Announcement hash: %s", coin_announcement_hash.hex())

        # ---- Assemble the SpendBundle ----
        # In a complete implementation, we would also include:
        #   - The staker's CAT coin spend (which asserts the pool's announcement
        #     and creates its own announcement for the pool to assert)
        #   - The staker's BLS signature for their CAT spend
        #
        # For this educational driver, we construct the pool-side spend and
        # return the bundle. The caller is responsible for adding the staker's
        # coin spend via the standard CAT wallet.

        # Create an unsigned spend bundle with just the pool spend.
        # The empty G2Element is a placeholder for the aggregated signature.
        spend_bundle = SpendBundle(
            [pool_coin_spend],
            G2Element(),  # Signature placeholder (pool spend needs no sig for STAKE)
        )

        # Attach metadata that the caller needs to complete the transaction:
        spend_bundle.staking_metadata = {
            "pool_coin_id": pool_coin_id,
            "lock_puzzle_hash": lock_puzzle_hash,
            "lock_amount": total_lock_amount,
            "reward": reward,
            "unlock_height": unlock_height,
            "new_pool_amount": new_pool_amount,
            "coin_announcement_hash": coin_announcement_hash,
            "announcement_msg": announcement_msg,
        }

        logger.info(
            "Stake bundle created: pool %d -> %d, lock amount: %d, unlock: %d",
            pool_coin.amount, new_pool_amount, total_lock_amount, unlock_height
        )

        return spend_bundle

    # -----------------------------------------------------------------
    # Add Rewards
    # -----------------------------------------------------------------

    async def add_rewards(
        self,
        pool_coin: Coin,
        amount: int,
        pool_auth_sk: PrivateKey,
    ) -> SpendBundle:
        """
        Create a spend bundle for the operator to add rewards to the pool.

        The operator deposits additional CAT tokens into the pool to fund
        future staking rewards. This requires the operator's private key
        for signing.

        Args:
            pool_coin: The current pool coin.
            amount: Number of CAT mojos to add as rewards.
            pool_auth_sk: The operator's BLS private key (PrivateKey).

        Returns:
            A SpendBundle that adds rewards to the pool.

        Raises:
            ValueError: If amount is not positive.
            ValueError: If the private key does not match POOL_AUTH_KEY.
        """
        if amount <= 0:
            raise ValueError(f"Amount must be positive, got {amount}")

        # Verify the private key matches the curried public key
        derived_pk = pool_auth_sk.get_g1()
        if derived_pk != self.pool_auth_key:
            raise ValueError(
                "Private key does not match the pool's POOL_AUTH_KEY. "
                "Only the pool operator can add rewards."
            )

        pool_puzzle = self.compile_pool_puzzle()
        pool_puzzle_hash = pool_puzzle.get_tree_hash()
        pool_coin_id = pool_coin.name()
        new_pool_amount = pool_coin.amount + amount

        # ---- Build the Solution ----
        # Action type 2: ADD_REWARDS
        # The operator specifies the amount to add.

        pool_solution = Program.to([
            2,                  # action_type: ADD_REWARDS
            amount,             # arg1: additional_amount
            0,                  # arg2: unused
            0,                  # arg3: unused
            pool_coin_id,       # my_coin_id
            pool_coin.amount,   # my_amount
            pool_puzzle_hash,   # my_puzzle_hash
            0,                  # extra_arg: unused
        ])

        # ---- Create the CoinSpend ----

        pool_coin_spend = CoinSpend(
            pool_coin,
            pool_puzzle,
            pool_solution,
        )

        # ---- Create the Signature ----
        # The puzzle requires AGG_SIG_ME with the message sha256(amount).
        # AGG_SIG_ME signs: message + coin_id + GENESIS_CHALLENGE
        #
        # For AGG_SIG_ME, the full signed message is:
        #   sha256(amount) + coin_id + genesis_challenge
        #
        # We use AugSchemeMPL.sign() which handles the augmented scheme.

        # Get the genesis challenge for the current blockchain
        blockchain_state = await self.full_node_client.get_blockchain_state()
        genesis_challenge = blockchain_state["genesis_challenge"]

        # The message the puzzle expects: sha256(amount)
        msg = std_hash(amount.to_bytes(32, "big"))

        # Full message for AGG_SIG_ME: msg + coin_id + genesis_challenge
        full_msg = msg + pool_coin_id + genesis_challenge

        # Create the BLS signature
        signature = AugSchemeMPL.sign(pool_auth_sk, full_msg)

        # ---- Assemble the SpendBundle ----

        spend_bundle = SpendBundle(
            [pool_coin_spend],
            signature,
        )

        logger.info(
            "Add rewards bundle created: pool %d -> %d (+%d)",
            pool_coin.amount, new_pool_amount, amount
        )

        return spend_bundle

    # -----------------------------------------------------------------
    # Withdraw Rewards
    # -----------------------------------------------------------------

    async def withdraw_rewards(
        self,
        pool_coin: Coin,
        amount: int,
        pool_auth_sk: PrivateKey,
        withdrawal_puzzle_hash: Optional[bytes32] = None,
    ) -> SpendBundle:
        """
        Create a spend bundle for the operator to withdraw rewards from the pool.

        The operator can remove unclaimed reward tokens, for example when
        shutting down the pool or rebalancing across multiple pools.

        Args:
            pool_coin: The current pool coin.
            amount: Number of CAT mojos to withdraw.
            pool_auth_sk: The operator's BLS private key.
            withdrawal_puzzle_hash: Where to send the withdrawn tokens.
                If None, defaults to a puzzle hash derived from the operator's key.

        Returns:
            A SpendBundle that withdraws rewards from the pool.

        Raises:
            ValueError: If amount exceeds the pool's current balance.
            ValueError: If amount is not positive.
            ValueError: If the private key does not match POOL_AUTH_KEY.
        """
        if amount <= 0:
            raise ValueError(f"Amount must be positive, got {amount}")

        if amount > pool_coin.amount:
            raise ValueError(
                f"Cannot withdraw {amount} from pool with {pool_coin.amount} rewards"
            )

        # Verify the private key matches
        derived_pk = pool_auth_sk.get_g1()
        if derived_pk != self.pool_auth_key:
            raise ValueError(
                "Private key does not match the pool's POOL_AUTH_KEY."
            )

        # Default withdrawal destination: derive from operator's public key
        if withdrawal_puzzle_hash is None:
            # In practice, you would use a standard puzzle hash for the operator.
            # Here we use a simple hash of the public key as a placeholder.
            withdrawal_puzzle_hash = bytes32(std_hash(bytes(self.pool_auth_key)))

        pool_puzzle = self.compile_pool_puzzle()
        pool_puzzle_hash = pool_puzzle.get_tree_hash()
        pool_coin_id = pool_coin.name()
        new_pool_amount = pool_coin.amount - amount

        # ---- Build the Solution ----
        # Action type 3: WITHDRAW

        pool_solution = Program.to([
            3,                          # action_type: WITHDRAW
            amount,                     # arg1: withdraw_amount
            withdrawal_puzzle_hash,     # arg2: withdrawal_puzzle_hash
            0,                          # arg3: unused
            pool_coin_id,               # my_coin_id
            pool_coin.amount,           # my_amount
            pool_puzzle_hash,           # my_puzzle_hash
            0,                          # extra_arg: unused
        ])

        # ---- Create the CoinSpend ----

        pool_coin_spend = CoinSpend(
            pool_coin,
            pool_puzzle,
            pool_solution,
        )

        # ---- Create the Signature ----
        # Same pattern as add_rewards: AGG_SIG_ME with sha256(amount)

        blockchain_state = await self.full_node_client.get_blockchain_state()
        genesis_challenge = blockchain_state["genesis_challenge"]

        msg = std_hash(amount.to_bytes(32, "big"))
        full_msg = msg + pool_coin_id + genesis_challenge

        signature = AugSchemeMPL.sign(pool_auth_sk, full_msg)

        # ---- Assemble the SpendBundle ----

        spend_bundle = SpendBundle(
            [pool_coin_spend],
            signature,
        )

        logger.info(
            "Withdraw bundle created: pool %d -> %d (-%d), dest=%s",
            pool_coin.amount, new_pool_amount, amount,
            withdrawal_puzzle_hash.hex()
        )

        return spend_bundle

    # -----------------------------------------------------------------
    # Pool Info
    # -----------------------------------------------------------------

    async def get_pool_info(self, pool_coin: Coin) -> dict:
        """
        Get information about the current state of the staking pool.

        Queries the pool coin and returns a human-readable summary of the
        pool's state, including available rewards and tier configurations.

        Args:
            pool_coin: The current pool coin.

        Returns:
            A dictionary with pool information:
            {
                "pool_coin_id": bytes32,
                "puzzle_hash": bytes32,
                "available_rewards": int,
                "tiers": [
                    {
                        "index": int,
                        "lock_blocks": int,
                        "lock_days": int,
                        "apy_percent": float,
                        "max_reward_for_1000": int,
                    },
                    ...
                ],
            }
        """
        pool_coin_id = pool_coin.name()

        tiers_info = []
        for i, tier in enumerate(self.staking_tiers):
            lock_days = tier.lock_blocks / 4608  # Approximate days
            apy_percent = tier.rate_numerator / tier.rate_denominator * 100
            example_reward = tier.calculate_reward(1000)  # Reward for 1000 mojos

            tiers_info.append({
                "index": i,
                "lock_blocks": tier.lock_blocks,
                "lock_days": round(lock_days, 1),
                "apy_percent": round(apy_percent, 2),
                "max_reward_for_1000": example_reward,
            })

        return {
            "pool_coin_id": pool_coin_id,
            "puzzle_hash": pool_coin.puzzle_hash,
            "available_rewards": pool_coin.amount,
            "tiers": tiers_info,
        }


# =============================================================================
# StakeLockDriver
# =============================================================================

class StakeLockDriver:
    """
    Driver for the Stake Lock puzzle.

    This class handles lock-level operations:
    - Claiming unlocked tokens after the lock period
    - Querying lock status and details
    - Checking if a lock has reached its unlock height

    Attributes:
        full_node_client: RPC client connected to a Chia full node.
        cat_tail_hash: The asset ID (TAIL hash) of the CAT token.
    """

    def __init__(self, full_node_client, cat_tail_hash: bytes32):
        """
        Initialize the StakeLockDriver.

        Args:
            full_node_client: An RPC client connected to a Chia full node.
            cat_tail_hash: The 32-byte asset ID of the CAT token.
        """
        self.full_node_client = full_node_client
        self.cat_tail_hash = cat_tail_hash

        # Load and cache the compiled base lock puzzle
        self._base_lock_puzzle: Program = load_lock_puzzle()

    # -----------------------------------------------------------------
    # Puzzle Construction
    # -----------------------------------------------------------------

    def compile_lock_puzzle(
        self,
        staker_pubkey: G1Element,
        total_amount: int,
        unlock_height: int,
        pool_coin_id: bytes32,
    ) -> Program:
        """
        Compile and curry the stake lock puzzle for a specific stake.

        This produces the same puzzle as StakingPoolDriver.compile_lock_puzzle().
        It is provided here for convenience when the caller only has the lock
        driver (e.g., when claiming).

        Args:
            staker_pubkey: The staker's BLS public key.
            total_amount: The total locked amount (stake + reward).
            unlock_height: The block height when tokens unlock.
            pool_coin_id: The coin ID of the pool coin that created this lock.

        Returns:
            The fully curried lock puzzle as a Program.
        """
        return self._base_lock_puzzle.curry(
            Program.to(bytes(staker_pubkey)),
            Program.to(total_amount),
            Program.to(unlock_height),
            Program.to(pool_coin_id),
        )

    # -----------------------------------------------------------------
    # Claim
    # -----------------------------------------------------------------

    async def claim(
        self,
        lock_coin: Coin,
        staker_sk: PrivateKey,
        new_puzzle_hash: bytes32,
        staker_pubkey: Optional[G1Element] = None,
        total_amount: Optional[int] = None,
        unlock_height: Optional[int] = None,
        pool_coin_id: Optional[bytes32] = None,
    ) -> SpendBundle:
        """
        Create a spend bundle to claim tokens from a stake lock coin.

        This is called after the lock period has expired. The staker provides
        their private key to sign the claim, and specifies where the tokens
        should be sent.

        Args:
            lock_coin: The stake lock coin to claim.
            staker_sk: The staker's BLS private key.
            new_puzzle_hash: Where to send the unlocked tokens (typically
                the staker's standard wallet puzzle hash).
            staker_pubkey: The staker's public key. If None, derived from
                staker_sk.
            total_amount: The total locked amount. If None, uses lock_coin.amount.
            unlock_height: The unlock height. Required to rebuild the puzzle.
            pool_coin_id: The pool coin ID. Required to rebuild the puzzle.

        Returns:
            A SpendBundle that claims the locked tokens.

        Raises:
            ValueError: If the lock has not yet reached its unlock height.
            ValueError: If unlock_height or pool_coin_id are not provided.

        Example:
            >>> bundle = await lock_driver.claim(
            ...     lock_coin, staker_sk, my_wallet_ph,
            ...     unlock_height=1138240, pool_coin_id=pool_id
            ... )
            >>> await client.push_tx(bundle)
        """
        # ---- Derive Public Key if Not Provided ----

        if staker_pubkey is None:
            staker_pubkey = staker_sk.get_g1()

        if total_amount is None:
            total_amount = lock_coin.amount

        if unlock_height is None:
            raise ValueError(
                "unlock_height is required to reconstruct the lock puzzle. "
                "Use get_lock_info() to retrieve it from the coin's puzzle."
            )

        if pool_coin_id is None:
            raise ValueError(
                "pool_coin_id is required to reconstruct the lock puzzle. "
                "Use get_lock_info() to retrieve it from the coin's puzzle."
            )

        # ---- Check if Unlocked ----
        # Query the current blockchain height to verify the lock has expired.

        is_ready = await self.is_unlocked(lock_coin, unlock_height=unlock_height)
        if not is_ready:
            blockchain_state = await self.full_node_client.get_blockchain_state()
            current_height = blockchain_state["peak"].height
            blocks_remaining = unlock_height - current_height
            raise ValueError(
                f"Lock has not expired yet. Current height: {current_height}, "
                f"unlock height: {unlock_height}, "
                f"blocks remaining: {blocks_remaining} "
                f"(~{blocks_remaining / 4608:.1f} days)"
            )

        # ---- Rebuild the Lock Puzzle ----
        # We need the full puzzle reveal to create the CoinSpend.

        lock_puzzle = self.compile_lock_puzzle(
            staker_pubkey=staker_pubkey,
            total_amount=total_amount,
            unlock_height=unlock_height,
            pool_coin_id=pool_coin_id,
        )

        # Verify the puzzle hash matches the coin's puzzle hash
        expected_ph = lock_puzzle.get_tree_hash()
        if expected_ph != lock_coin.puzzle_hash:
            raise ValueError(
                f"Reconstructed puzzle hash {expected_ph.hex()} does not match "
                f"lock coin's puzzle hash {lock_coin.puzzle_hash.hex()}. "
                f"Check the curried parameters."
            )

        # ---- Build the Solution ----
        # The lock puzzle takes three arguments:
        #   new_puzzle_hash: destination for the tokens
        #   my_coin_id: for ASSERT_MY_COIN_ID
        #   my_amount: for ASSERT_MY_AMOUNT

        lock_coin_id = lock_coin.name()

        lock_solution = Program.to([
            new_puzzle_hash,    # Where to send the unlocked tokens
            lock_coin_id,       # For ASSERT_MY_COIN_ID
            total_amount,       # For ASSERT_MY_AMOUNT (must equal TOTAL_AMOUNT)
        ])

        # ---- Create the CoinSpend ----

        lock_coin_spend = CoinSpend(
            lock_coin,
            lock_puzzle,
            lock_solution,
        )

        # ---- Create the Signature ----
        # The puzzle requires AGG_SIG_ME with the message being new_puzzle_hash.
        # Full signed message for AGG_SIG_ME:
        #   new_puzzle_hash + coin_id + genesis_challenge

        blockchain_state = await self.full_node_client.get_blockchain_state()
        genesis_challenge = blockchain_state["genesis_challenge"]

        # The message is the raw new_puzzle_hash bytes (32 bytes)
        full_msg = bytes(new_puzzle_hash) + lock_coin_id + genesis_challenge

        signature = AugSchemeMPL.sign(staker_sk, full_msg)

        # ---- Assemble the SpendBundle ----

        spend_bundle = SpendBundle(
            [lock_coin_spend],
            signature,
        )

        logger.info(
            "Claim bundle created: lock_coin=%s, amount=%d, dest=%s",
            lock_coin_id.hex(), total_amount, new_puzzle_hash.hex()
        )

        return spend_bundle

    # -----------------------------------------------------------------
    # Lock Info
    # -----------------------------------------------------------------

    async def get_lock_info(
        self,
        lock_coin: Coin,
        staker_pubkey: Optional[G1Element] = None,
        total_amount: Optional[int] = None,
        unlock_height: Optional[int] = None,
        pool_coin_id: Optional[bytes32] = None,
    ) -> dict:
        """
        Get information about a stake lock coin.

        Returns a dictionary with the lock's parameters and current status.
        If the curried parameters are provided, the method verifies them against
        the coin's puzzle hash.

        Args:
            lock_coin: The stake lock coin.
            staker_pubkey: The staker's public key (optional, for verification).
            total_amount: Expected total amount (optional, for verification).
            unlock_height: Expected unlock height (optional, for verification).
            pool_coin_id: Expected pool coin ID (optional, for verification).

        Returns:
            A dictionary with lock information:
            {
                "lock_coin_id": bytes32,
                "puzzle_hash": bytes32,
                "amount": int,
                "staker_pubkey": G1Element or None,
                "total_amount": int or None,
                "unlock_height": int or None,
                "pool_coin_id": bytes32 or None,
                "is_unlocked": bool or None,
                "blocks_remaining": int or None,
                "days_remaining": float or None,
                "puzzle_verified": bool,
            }
        """
        lock_coin_id = lock_coin.name()

        # Try to get current height for status calculation
        current_height = None
        try:
            blockchain_state = await self.full_node_client.get_blockchain_state()
            current_height = blockchain_state["peak"].height
        except Exception:
            logger.warning("Could not get current blockchain height")

        # Calculate status fields if we have the necessary information
        is_unlocked = None
        blocks_remaining = None
        days_remaining = None

        if unlock_height is not None and current_height is not None:
            is_unlocked = current_height >= unlock_height
            if not is_unlocked:
                blocks_remaining = unlock_height - current_height
                days_remaining = round(blocks_remaining / 4608, 1)

        # Verify puzzle hash if all parameters are provided
        puzzle_verified = False
        if all(p is not None for p in [staker_pubkey, total_amount, unlock_height, pool_coin_id]):
            rebuilt_puzzle = self.compile_lock_puzzle(
                staker_pubkey, total_amount, unlock_height, pool_coin_id
            )
            puzzle_verified = rebuilt_puzzle.get_tree_hash() == lock_coin.puzzle_hash

        return {
            "lock_coin_id": lock_coin_id,
            "puzzle_hash": lock_coin.puzzle_hash,
            "amount": lock_coin.amount,
            "staker_pubkey": staker_pubkey,
            "total_amount": total_amount,
            "unlock_height": unlock_height,
            "pool_coin_id": pool_coin_id,
            "is_unlocked": is_unlocked,
            "blocks_remaining": blocks_remaining,
            "days_remaining": days_remaining,
            "puzzle_verified": puzzle_verified,
        }

    # -----------------------------------------------------------------
    # Unlock Check
    # -----------------------------------------------------------------

    async def is_unlocked(
        self,
        lock_coin: Coin,
        unlock_height: Optional[int] = None,
        current_height: Optional[int] = None,
    ) -> bool:
        """
        Check if a stake lock coin has reached its unlock height.

        Args:
            lock_coin: The stake lock coin.
            unlock_height: The unlock height. If None, this method cannot
                determine the lock status and returns False.
            current_height: The current blockchain height. If None, it is
                fetched from the full node.

        Returns:
            True if the current height >= unlock_height, False otherwise.
        """
        if unlock_height is None:
            logger.warning(
                "Cannot check unlock status without unlock_height. "
                "Returning False."
            )
            return False

        if current_height is None:
            blockchain_state = await self.full_node_client.get_blockchain_state()
            current_height = blockchain_state["peak"].height

        is_ready = current_height >= unlock_height

        logger.debug(
            "Unlock check: current=%d, unlock=%d, is_unlocked=%s",
            current_height, unlock_height, is_ready
        )

        return is_ready
