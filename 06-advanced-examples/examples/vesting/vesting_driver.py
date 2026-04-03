"""
=============================================================================
TOKEN VESTING DRIVER
=============================================================================

Manages a token vesting schedule on the Chia blockchain.

The driver handles:
  - Creating the initial vesting coin with the full token amount
  - Computing how many tokens have vested at any block height
  - Claiming vested tokens (partial or full)
  - Providing a human-readable vesting schedule

The vesting coin recreates itself after each partial claim with an
updated CLAIMED_AMOUNT. The driver tracks this state.

This is pseudocode-style but complete in logic.
"""

from dataclasses import dataclass
from typing import Optional, List, Tuple

# Placeholder types
class Program:
    @staticmethod
    def to(value): return Program()
    def curry(self, *args): return Program()
    def get_tree_hash(self): return b'\x00' * 32

class Coin:
    def __init__(self, parent_coin_id, puzzle_hash, amount):
        self.parent_coin_id = parent_coin_id
        self.puzzle_hash = puzzle_hash
        self.amount = amount
    def name(self): return b'\x00' * 32

class CoinSpend:
    def __init__(self, coin, puzzle_reveal, solution):
        self.coin = coin
        self.puzzle_reveal = puzzle_reveal
        self.solution = solution

class SpendBundle:
    def __init__(self, coin_spends, aggregated_signature):
        self.coin_spends = coin_spends
        self.aggregated_signature = aggregated_signature


def load_vesting_puzzle() -> Program:
    """Load and compile the vesting.clsp puzzle."""
    return Program()


# ---------------------------------------------------------------------------
# Constants: Chia block timing
# ---------------------------------------------------------------------------

SECONDS_PER_BLOCK = 18.75
BLOCKS_PER_HOUR = 3600 / SECONDS_PER_BLOCK       # ~192
BLOCKS_PER_DAY = 86400 / SECONDS_PER_BLOCK        # ~4608
BLOCKS_PER_MONTH = BLOCKS_PER_DAY * 30            # ~138240
BLOCKS_PER_YEAR = BLOCKS_PER_DAY * 365            # ~1681920


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class VestingConfig:
    """Configuration for a token vesting schedule."""
    beneficiary_pubkey: bytes   # 48-byte G1 public key
    total_amount: int           # Total tokens to vest (in mojos)
    cliff_months: int           # Cliff period in months
    total_vesting_months: int   # Total vesting period in months (includes cliff)
    start_height: int           # Block height when vesting starts


# ===========================================================================
# VESTING DRIVER
# ===========================================================================

class VestingDriver:
    """
    Manages a token vesting coin through its lifecycle.

    Usage:
        config = VestingConfig(
            beneficiary_pubkey=beneficiary_pk,
            total_amount=1_000_000_000_000,  # 1000 tokens
            cliff_months=6,
            total_vesting_months=24,
            start_height=100_000,
        )
        driver = VestingDriver(config)

        # Create the vesting coin
        ph = driver.get_puzzle_hash()
        # Send total_amount to ph

        # Check vesting status at height 500_000
        info = driver.get_vesting_info(500_000)

        # Claim vested tokens
        bundle = driver.claim(vesting_coin, claim_amount, current_height, sk)
    """

    def __init__(self, config: VestingConfig):
        self.config = config
        self.base_puzzle = load_vesting_puzzle()

        # Convert months to blocks
        self.cliff_blocks = int(config.cliff_months * BLOCKS_PER_MONTH)
        self.total_vesting_blocks = int(config.total_vesting_months * BLOCKS_PER_MONTH)
        self.cliff_height = config.start_height + self.cliff_blocks

        # Track claimed amount (state)
        self.claimed_amount = 0
        self.current_coin: Optional[Coin] = None

    # -----------------------------------------------------------------------
    # Puzzle construction
    # -----------------------------------------------------------------------

    def _build_puzzle(self, claimed_amount: int) -> Program:
        """
        Build the curried vesting puzzle for a given claimed amount.

        Each time tokens are claimed, the puzzle is re-curried with
        the new CLAIMED_AMOUNT. This updates the puzzle hash, which
        the driver must track.
        """
        puzzle = self.base_puzzle.curry(
            Program.to(self.config.beneficiary_pubkey),
            Program.to(self.config.total_amount),
            Program.to(self.cliff_height),
            Program.to(self.config.start_height),
            Program.to(self.total_vesting_blocks),
            Program.to(claimed_amount),
            # MY_PUZZLE_HASH will be computed after currying
        )
        # In production, we would also curry the puzzle hash of the
        # NEXT state. This is a simplification.
        return puzzle

    def get_puzzle_hash(self, claimed_amount: int = 0) -> bytes:
        """Get the puzzle hash for a given claimed amount state."""
        return self._build_puzzle(claimed_amount).get_tree_hash()

    # -----------------------------------------------------------------------
    # Vesting calculations (off-chain, mirrors on-chain logic)
    # -----------------------------------------------------------------------

    def calc_vested_amount(self, current_height: int) -> int:
        """
        Calculate how many tokens have vested at the given block height.

        This mirrors the on-chain calc-vested function exactly, using
        the same integer arithmetic to ensure consistency.
        """
        if current_height < self.cliff_height:
            # Still in cliff period -- nothing vested
            return 0

        end_height = self.config.start_height + self.total_vesting_blocks
        if current_height >= end_height:
            # Fully vested
            return self.config.total_amount

        # Linear vesting: proportional to elapsed time
        elapsed = current_height - self.config.start_height
        vested = (self.config.total_amount * elapsed) // self.total_vesting_blocks
        return vested

    def calc_claimable_amount(self, current_height: int) -> int:
        """
        Calculate how many tokens can be claimed right now.

        claimable = vested - already_claimed
        """
        vested = self.calc_vested_amount(current_height)
        return max(0, vested - self.claimed_amount)

    # -----------------------------------------------------------------------
    # CLAIM TOKENS
    # -----------------------------------------------------------------------

    def claim(
        self,
        vesting_coin: Coin,
        claim_amount: int,
        current_height: int,
        beneficiary_private_key,
    ) -> SpendBundle:
        """
        Claim vested tokens.

        Args:
            vesting_coin: The current vesting coin on-chain
            claim_amount: How many tokens to claim
            current_height: Current block height
            beneficiary_private_key: Beneficiary's key for signing

        Returns:
            SpendBundle to claim the tokens

        Raises:
            ValueError: If claim_amount exceeds claimable amount
        """
        claimable = self.calc_claimable_amount(current_height)
        if claim_amount > claimable:
            raise ValueError(
                f"Cannot claim {claim_amount}: only {claimable} tokens are "
                f"claimable at height {current_height}. "
                f"Total vested: {self.calc_vested_amount(current_height)}, "
                f"already claimed: {self.claimed_amount}."
            )

        # Determine the new state after claiming
        new_claimed = self.claimed_amount + claim_amount
        remaining = self.config.total_amount - new_claimed
        is_final_claim = (remaining == 0)

        # Build the current puzzle
        current_puzzle = self._build_puzzle(self.claimed_amount)

        # If not final, compute the new puzzle hash for the recreated coin
        if not is_final_claim:
            new_puzzle_hash = self.get_puzzle_hash(new_claimed)
        else:
            new_puzzle_hash = b'\x00' * 32  # Not used

        solution = Program.to([
            0,                  # mode = claim
            claim_amount,       # how much to claim
            current_height,     # current block height
        ])

        coin_spend = CoinSpend(
            coin=vesting_coin,
            puzzle_reveal=current_puzzle,
            solution=solution,
        )

        sig = self._sign(beneficiary_private_key, claim_amount, vesting_coin)

        # Update state
        self.claimed_amount = new_claimed
        if is_final_claim:
            self.current_coin = None
            print(f"Final claim of {claim_amount} tokens. Vesting complete!")
        else:
            self.current_coin = Coin(
                parent_coin_id=vesting_coin.name(),
                puzzle_hash=new_puzzle_hash,
                amount=remaining,
            )
            print(f"Claimed {claim_amount} tokens. Remaining: {remaining}")

        return SpendBundle(
            coin_spends=[coin_spend],
            aggregated_signature=sig,
        )

    # -----------------------------------------------------------------------
    # VESTING SCHEDULE (human-readable)
    # -----------------------------------------------------------------------

    def vesting_schedule(self) -> List[Tuple[str, int, int]]:
        """
        Generate a human-readable vesting schedule.

        Returns a list of (label, block_height, vested_amount) tuples
        showing the vesting milestones.
        """
        schedule = []
        start = self.config.start_height

        # Add cliff milestone
        schedule.append((
            f"Cliff ends (month {self.config.cliff_months})",
            self.cliff_height,
            self.calc_vested_amount(self.cliff_height),
        ))

        # Add monthly milestones after cliff
        for month in range(self.config.cliff_months + 1, self.config.total_vesting_months + 1):
            height = start + int(month * BLOCKS_PER_MONTH)
            vested = self.calc_vested_amount(height)
            schedule.append((
                f"Month {month}",
                height,
                vested,
            ))

        return schedule

    def get_vesting_info(self, current_height: int) -> dict:
        """Return a summary of the vesting state at the given height."""
        vested = self.calc_vested_amount(current_height)
        claimable = self.calc_claimable_amount(current_height)
        total = self.config.total_amount
        percent_vested = (vested * 100) // total if total > 0 else 0

        end_height = self.config.start_height + self.total_vesting_blocks
        if current_height < self.cliff_height:
            status = "CLIFF (locked)"
            blocks_until_next = self.cliff_height - current_height
        elif current_height < end_height:
            status = "VESTING (linear unlock)"
            blocks_until_next = end_height - current_height
        else:
            status = "FULLY VESTED"
            blocks_until_next = 0

        return {
            "status": status,
            "total_amount": total,
            "vested_amount": vested,
            "claimed_amount": self.claimed_amount,
            "claimable_amount": claimable,
            "percent_vested": percent_vested,
            "blocks_until_next_milestone": blocks_until_next,
            "approx_days_until_next": (blocks_until_next * SECONDS_PER_BLOCK) / 86400,
        }

    def _sign(self, private_key, message, coin):
        """Placeholder for BLS signing."""
        return b'\xc0' + b'\x00' * 95


# ===========================================================================
# USAGE EXAMPLE
# ===========================================================================

def example_usage():
    """Demonstrate the vesting lifecycle."""

    config = VestingConfig(
        beneficiary_pubkey=b'\x01' * 48,
        total_amount=1_000_000_000_000_000,  # 1,000,000 tokens (in mojos)
        cliff_months=6,
        total_vesting_months=24,
        start_height=100_000,
    )

    driver = VestingDriver(config)

    # Show the vesting schedule
    print("=== VESTING SCHEDULE ===\n")
    schedule = driver.vesting_schedule()
    for label, height, amount in schedule:
        print(f"  {label:30s}  Height: {height:>10,}  Vested: {amount:>20,}")

    # Check vesting at different heights
    print("\n=== VESTING STATUS AT DIFFERENT HEIGHTS ===\n")

    # Before cliff (month 3)
    height_month_3 = 100_000 + int(3 * BLOCKS_PER_MONTH)
    info = driver.get_vesting_info(height_month_3)
    print(f"Month 3 (height {height_month_3}):")
    print(f"  Status: {info['status']}")
    print(f"  Vested: {info['vested_amount']:,} ({info['percent_vested']}%)")
    print(f"  Claimable: {info['claimable_amount']:,}")

    # After cliff (month 12)
    height_month_12 = 100_000 + int(12 * BLOCKS_PER_MONTH)
    info = driver.get_vesting_info(height_month_12)
    print(f"\nMonth 12 (height {height_month_12}):")
    print(f"  Status: {info['status']}")
    print(f"  Vested: {info['vested_amount']:,} ({info['percent_vested']}%)")
    print(f"  Claimable: {info['claimable_amount']:,}")

    # Fully vested (month 24)
    height_month_24 = 100_000 + int(24 * BLOCKS_PER_MONTH)
    info = driver.get_vesting_info(height_month_24)
    print(f"\nMonth 24 (height {height_month_24}):")
    print(f"  Status: {info['status']}")
    print(f"  Vested: {info['vested_amount']:,} ({info['percent_vested']}%)")
    print(f"  Claimable: {info['claimable_amount']:,}")

    # Simulate claiming at month 12
    print("\n=== CLAIMING AT MONTH 12 ===")
    puzzle_hash = driver.get_puzzle_hash()
    vesting_coin = Coin(b'\x00' * 32, puzzle_hash, config.total_amount)
    beneficiary_sk = b'\x10' * 32

    claim_amount = driver.calc_claimable_amount(height_month_12)
    print(f"Claiming {claim_amount:,} tokens...")
    bundle = driver.claim(vesting_coin, claim_amount, height_month_12, beneficiary_sk)

    # Check status after claiming
    info = driver.get_vesting_info(height_month_12)
    print(f"After claim -- claimable: {info['claimable_amount']:,}")


if __name__ == "__main__":
    example_usage()
