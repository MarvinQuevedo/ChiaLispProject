"""
=============================================================================
SAVINGS ACCOUNT DRIVER
=============================================================================

Manages the lifecycle of a savings account coin:
  - Depositing funds (mode 0)
  - Partial withdrawals with cooldown enforcement (mode 1)
  - Emergency full withdrawal (mode 2)

The driver tracks the "current" savings coin as it gets recreated with each
operation. Each spend destroys the old coin and creates a new one with the
updated balance, so the driver must track the latest coin.

This is pseudocode-style but complete in logic.
"""

from dataclasses import dataclass
from typing import Optional

# Placeholder types (see escrow_driver.py for explanation)
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


def load_savings_puzzle() -> Program:
    """Load and compile the savings_account.clsp puzzle."""
    return Program()


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class SavingsConfig:
    """Configuration for a savings account."""
    owner_pubkey: bytes           # 48-byte G1 public key
    withdrawal_limit_percent: int # e.g., 10 for 10%
    withdrawal_cooldown: int      # blocks between withdrawals (4608 ~ 1 day)
    emergency_timelock: int       # blocks for emergency withdrawal (138240 ~ 30 days)


# ===========================================================================
# SAVINGS ACCOUNT DRIVER
# ===========================================================================

class SavingsAccountDriver:
    """
    Manages a savings account coin through deposits and withdrawals.

    The savings coin is a "self-recreating" coin -- each operation spends
    the current coin and creates a new one. The driver tracks the latest
    coin to enable sequential operations.

    Usage:
        config = SavingsConfig(
            owner_pubkey=my_pk,
            withdrawal_limit_percent=10,
            withdrawal_cooldown=4608,      # ~1 day
            emergency_timelock=138240,     # ~30 days
        )
        driver = SavingsAccountDriver(config)
        puzzle_hash = driver.get_puzzle_hash()

        # Send initial funds to puzzle_hash to create the account
        # Then track the coin and perform operations
    """

    def __init__(self, config: SavingsConfig):
        self.config = config
        self.base_puzzle = load_savings_puzzle()
        self.curried_puzzle = self._curry_puzzle()

        # Track the current savings coin (updated after each operation)
        self.current_coin: Optional[Coin] = None

    def _curry_puzzle(self) -> Program:
        """
        Curry the savings parameters into the puzzle.

        The curried puzzle is the same for all operations -- the "state"
        is tracked via the coin amount, and the cooldown is enforced by
        the relative height check on the new coin.
        """
        return self.base_puzzle.curry(
            Program.to(self.config.owner_pubkey),
            Program.to(self.config.withdrawal_limit_percent),
            Program.to(self.config.withdrawal_cooldown),
            Program.to(self.config.emergency_timelock),
        )

    def get_puzzle_hash(self) -> bytes:
        """
        Get the puzzle hash for this savings account.

        Funds sent to this puzzle hash become part of the savings account.
        """
        return self.curried_puzzle.get_tree_hash()

    # -----------------------------------------------------------------------
    # DEPOSIT (Mode 0)
    # -----------------------------------------------------------------------

    def deposit(
        self,
        current_coin: Coin,
        deposit_amount: int,
        owner_private_key,
    ) -> SpendBundle:
        """
        Deposit additional funds into the savings account.

        The current coin is spent and recreated with:
            new_amount = current_amount + deposit_amount

        The deposit_amount XCH must come from another coin in the same
        spend bundle. The driver is responsible for assembling the full
        bundle that includes both the savings coin spend and the funding
        coin spend.

        Args:
            current_coin: The current savings coin on-chain
            deposit_amount: How many mojos to add
            owner_private_key: Owner's key for signing

        Returns:
            SpendBundle for the savings coin spend (the caller must add
            the funding coin spend separately)
        """
        puzzle_hash = self.get_puzzle_hash()

        # Solution: mode=0, my_amount, deposit_delta, my_puzzle_hash
        solution = Program.to([
            0,                       # mode = deposit
            current_coin.amount,     # current amount
            deposit_amount,          # amount to add (reuses withdraw_amount slot)
            puzzle_hash,             # puzzle hash for recreation
        ])

        coin_spend = CoinSpend(
            coin=current_coin,
            puzzle_reveal=self.curried_puzzle,
            solution=solution,
        )

        # Sign with owner key: sha256("deposit") + coin_id + genesis_challenge
        sig = self._sign(owner_private_key, b"deposit", current_coin)

        # After this spend, the new coin will have:
        #   parent = current_coin.name()
        #   puzzle_hash = same puzzle_hash
        #   amount = current_coin.amount + deposit_amount
        new_coin = Coin(
            parent_coin_id=current_coin.name(),
            puzzle_hash=puzzle_hash,
            amount=current_coin.amount + deposit_amount,
        )
        self.current_coin = new_coin

        return SpendBundle(
            coin_spends=[coin_spend],
            aggregated_signature=sig,
        )

    # -----------------------------------------------------------------------
    # PARTIAL WITHDRAWAL (Mode 1)
    # -----------------------------------------------------------------------

    def partial_withdrawal(
        self,
        current_coin: Coin,
        withdraw_amount: int,
        owner_private_key,
    ) -> SpendBundle:
        """
        Withdraw up to WITHDRAWAL_LIMIT_PERCENT of the current balance.

        This will fail on-chain if:
          - withdraw_amount > max allowed (percentage limit)
          - Not enough blocks have passed since the last operation (cooldown)

        The driver pre-checks the percentage limit but cannot verify the
        cooldown -- that is enforced by the blockchain at inclusion time.

        Args:
            current_coin: The current savings coin
            withdraw_amount: How many mojos to withdraw
            owner_private_key: Owner's key for signing

        Returns:
            SpendBundle for the withdrawal
        """
        # Pre-check: verify the withdrawal amount is within limits
        max_allowed = self.get_max_withdrawal(current_coin.amount)
        if withdraw_amount > max_allowed:
            raise ValueError(
                f"Withdrawal of {withdraw_amount} exceeds limit of {max_allowed} "
                f"({self.config.withdrawal_limit_percent}% of {current_coin.amount})"
            )

        if withdraw_amount >= current_coin.amount:
            raise ValueError(
                "Cannot withdraw entire balance via partial withdrawal. "
                "Use emergency_withdrawal() instead."
            )

        puzzle_hash = self.get_puzzle_hash()

        solution = Program.to([
            1,                       # mode = partial withdrawal
            current_coin.amount,     # current amount
            withdraw_amount,         # amount to withdraw
            puzzle_hash,             # puzzle hash for recreation
        ])

        coin_spend = CoinSpend(
            coin=current_coin,
            puzzle_reveal=self.curried_puzzle,
            solution=solution,
        )

        # Sign with owner key: sha256(withdraw_amount) + coin_id + genesis
        sig = self._sign(owner_private_key, str(withdraw_amount).encode(), current_coin)

        # Track the new coin with reduced balance
        new_coin = Coin(
            parent_coin_id=current_coin.name(),
            puzzle_hash=puzzle_hash,
            amount=current_coin.amount - withdraw_amount,
        )
        self.current_coin = new_coin

        return SpendBundle(
            coin_spends=[coin_spend],
            aggregated_signature=sig,
        )

    # -----------------------------------------------------------------------
    # EMERGENCY WITHDRAWAL (Mode 2)
    # -----------------------------------------------------------------------

    def emergency_withdrawal(
        self,
        current_coin: Coin,
        owner_private_key,
    ) -> SpendBundle:
        """
        Withdraw the entire balance after the emergency timelock.

        This is the "break glass" option. The long timelock (e.g., 30 days)
        discourages casual use but ensures funds are never permanently locked.

        WARNING: This spend will be rejected by the mempool if the emergency
        timelock has not yet elapsed since the coin was created.

        Args:
            current_coin: The current savings coin
            owner_private_key: Owner's key for signing

        Returns:
            SpendBundle for the emergency withdrawal
        """
        solution = Program.to([
            2,                       # mode = emergency withdrawal
            current_coin.amount,     # current amount
            current_coin.amount,     # withdraw full amount
            0,                       # puzzle hash unused (coin not recreated)
        ])

        coin_spend = CoinSpend(
            coin=current_coin,
            puzzle_reveal=self.curried_puzzle,
            solution=solution,
        )

        sig = self._sign(owner_private_key, b"emergency", current_coin)

        # After emergency withdrawal, there is no savings coin anymore
        self.current_coin = None

        return SpendBundle(
            coin_spends=[coin_spend],
            aggregated_signature=sig,
        )

    # -----------------------------------------------------------------------
    # Utility Methods
    # -----------------------------------------------------------------------

    def get_max_withdrawal(self, balance: int) -> int:
        """
        Calculate the maximum allowed partial withdrawal for a given balance.

        Uses integer division to match the on-chain CLVM calculation.
        """
        return (balance * self.config.withdrawal_limit_percent) // 100

    def get_account_info(self, coin: Coin) -> dict:
        """
        Return a human-readable summary of the savings account state.
        """
        max_withdraw = self.get_max_withdrawal(coin.amount)
        return {
            "balance_mojos": coin.amount,
            "balance_xch": coin.amount / 1_000_000_000_000,
            "max_withdrawal_mojos": max_withdraw,
            "max_withdrawal_xch": max_withdraw / 1_000_000_000_000,
            "withdrawal_limit_percent": self.config.withdrawal_limit_percent,
            "cooldown_blocks": self.config.withdrawal_cooldown,
            "cooldown_approx_hours": (self.config.withdrawal_cooldown * 18.75) / 3600,
            "emergency_timelock_blocks": self.config.emergency_timelock,
            "emergency_timelock_approx_days": (self.config.emergency_timelock * 18.75) / 86400,
        }

    def _sign(self, private_key, message: bytes, coin: Coin):
        """Placeholder for BLS signing."""
        return b'\xc0' + b'\x00' * 95


# ===========================================================================
# USAGE EXAMPLE
# ===========================================================================

def example_usage():
    """Demonstrate the savings account lifecycle."""

    config = SavingsConfig(
        owner_pubkey=b'\x01' * 48,
        withdrawal_limit_percent=10,      # 10% max per withdrawal
        withdrawal_cooldown=4608,          # ~1 day between withdrawals
        emergency_timelock=138240,         # ~30 days for emergency
    )

    driver = SavingsAccountDriver(config)
    puzzle_hash = driver.get_puzzle_hash()
    print(f"Savings account puzzle hash: {puzzle_hash.hex()}")

    # Simulate an initial coin (created by sending funds to the puzzle hash)
    savings_coin = Coin(
        parent_coin_id=b'\x00' * 32,
        puzzle_hash=puzzle_hash,
        amount=10_000_000_000_000,  # 10 XCH
    )

    owner_sk = b'\x10' * 32  # Placeholder private key

    # Show account info
    info = driver.get_account_info(savings_coin)
    print(f"\nAccount balance: {info['balance_xch']} XCH")
    print(f"Max withdrawal: {info['max_withdrawal_xch']} XCH ({info['withdrawal_limit_percent']}%)")
    print(f"Cooldown: ~{info['cooldown_approx_hours']:.1f} hours")
    print(f"Emergency timelock: ~{info['emergency_timelock_approx_days']:.1f} days")

    # Partial withdrawal of 0.5 XCH (within 10% limit of 1 XCH)
    print("\n--- Partial Withdrawal: 0.5 XCH ---")
    bundle = driver.partial_withdrawal(savings_coin, 500_000_000_000, owner_sk)
    print(f"New balance after withdrawal: {driver.current_coin.amount / 1e12} XCH")

    # Try to withdraw too much (should fail)
    print("\n--- Attempt: Withdraw 2 XCH (exceeds 10% limit) ---")
    try:
        driver.partial_withdrawal(driver.current_coin, 2_000_000_000_000, owner_sk)
    except ValueError as e:
        print(f"Rejected: {e}")


if __name__ == "__main__":
    example_usage()
