"""
=============================================================================
LOTTERY / RAFFLE DRIVER
=============================================================================

Manages the full lottery lifecycle:
  1. Create the initial lottery coin with zero tickets
  2. Process ticket purchases (curry updated ticket list, recreate coin)
  3. Trigger the draw after the deadline
  4. Distribute prizes

The driver must track the evolving lottery coin, since each ticket
purchase changes the puzzle hash (because TICKET_LIST is curried).

This is pseudocode-style but complete in logic.
"""

import hashlib
from dataclasses import dataclass, field
from typing import List, Optional

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


def load_lottery_puzzle() -> Program:
    """Load and compile the lottery.clsp puzzle."""
    return Program()


@dataclass
class LotteryConfig:
    """Configuration for a lottery."""
    operator_pubkey: bytes       # 48-byte G1 public key
    operator_puzzle_hash: bytes  # 32-byte puzzle hash for fee
    ticket_price: int            # Price per ticket in mojos
    deadline_height: int         # Absolute block height when sales close
    fee_percent: int             # Operator fee percentage (e.g., 2)


# ===========================================================================
# LOTTERY DRIVER
# ===========================================================================

class LotteryDriver:
    """
    Manages a lottery from creation through ticket sales to the draw.

    The lottery coin evolves with each ticket purchase:
      - The TICKET_LIST grows (one puzzle hash per ticket)
      - The amount increases (by TICKET_PRICE per ticket)
      - The puzzle hash changes (because TICKET_LIST is curried)

    The driver tracks this evolution and can reconstruct the current
    puzzle at any point.

    Usage:
        config = LotteryConfig(
            operator_pubkey=op_pk,
            operator_puzzle_hash=op_ph,
            ticket_price=100_000_000_000,  # 0.1 XCH
            deadline_height=1_000_000,
            fee_percent=2,
        )
        driver = LotteryDriver(config)

        # Create the initial lottery coin (send initial_amount to puzzle_hash)
        ph = driver.get_current_puzzle_hash()

        # Process ticket purchases
        bundle = driver.buy_ticket(lottery_coin, player_ph, operator_sk)

        # After deadline, draw
        bundle = driver.draw(lottery_coin, operator_sk)
    """

    def __init__(self, config: LotteryConfig):
        self.config = config
        self.base_puzzle = load_lottery_puzzle()

        # State: tracks ticket list as it evolves
        self.ticket_list: List[bytes] = []
        self.current_coin: Optional[Coin] = None

    # -----------------------------------------------------------------------
    # Puzzle construction with current state
    # -----------------------------------------------------------------------

    def _build_curried_puzzle(self, ticket_list: List[bytes]) -> Program:
        """
        Build the fully curried lottery puzzle for the given ticket list.

        Each ticket purchase changes the ticket list, which changes the
        puzzle hash. This is how the lottery "state" evolves on-chain.
        """
        return self.base_puzzle.curry(
            Program.to(self.config.operator_pubkey),
            Program.to(self.config.operator_puzzle_hash),
            Program.to(self.config.ticket_price),
            Program.to(self.config.deadline_height),
            Program.to(self.config.fee_percent),
            Program.to(ticket_list),         # Current ticket list
            Program.to(len(ticket_list)),    # Ticket count
        )

    def get_current_puzzle_hash(self) -> bytes:
        """Get the puzzle hash for the current state of the lottery."""
        puzzle = self._build_curried_puzzle(self.ticket_list)
        return puzzle.get_tree_hash()

    # -----------------------------------------------------------------------
    # CREATE INITIAL LOTTERY (deploy)
    # -----------------------------------------------------------------------

    def get_initial_puzzle_hash(self) -> bytes:
        """
        Get the puzzle hash for a fresh lottery with zero tickets.

        The operator sends a small initial amount to this puzzle hash
        to create the lottery coin. This initial amount covers blockchain
        fees and is included in the final prize pool.
        """
        return self._build_curried_puzzle([]).get_tree_hash()

    # -----------------------------------------------------------------------
    # BUY TICKET (Mode 0)
    # -----------------------------------------------------------------------

    def buy_ticket(
        self,
        lottery_coin: Coin,
        player_puzzle_hash: bytes,
        operator_private_key,
    ) -> SpendBundle:
        """
        Process a ticket purchase.

        The current lottery coin is spent and recreated with:
          - player_puzzle_hash appended to the ticket list
          - amount increased by ticket_price

        The operator must sign each ticket purchase. In a real application,
        the player would submit their puzzle hash to the operator's service,
        which then builds and signs the spend bundle.

        Args:
            lottery_coin: The current lottery coin
            player_puzzle_hash: The buyer's puzzle hash (where prize goes if they win)
            operator_private_key: Operator's key for signing

        Returns:
            SpendBundle to process the ticket purchase
        """
        # Build the current puzzle (before ticket purchase)
        current_puzzle = self._build_curried_puzzle(self.ticket_list)

        # Compute the new puzzle hash (after adding this ticket)
        new_ticket_list = self.ticket_list + [player_puzzle_hash]
        new_puzzle = self._build_curried_puzzle(new_ticket_list)
        new_puzzle_hash = new_puzzle.get_tree_hash()

        # Build the solution
        solution = Program.to([
            0,                           # mode = buy ticket
            player_puzzle_hash,          # the buyer's puzzle hash
            lottery_coin.amount,         # current amount
            new_puzzle_hash,             # new puzzle hash (with updated ticket list)
        ])

        coin_spend = CoinSpend(
            coin=lottery_coin,
            puzzle_reveal=current_puzzle,
            solution=solution,
        )

        # Operator signs to approve the ticket
        sig = self._sign(operator_private_key, player_puzzle_hash, lottery_coin)

        # Update driver state
        self.ticket_list = new_ticket_list
        new_amount = lottery_coin.amount + self.config.ticket_price
        self.current_coin = Coin(
            parent_coin_id=lottery_coin.name(),
            puzzle_hash=new_puzzle_hash,
            amount=new_amount,
        )

        print(f"Ticket #{len(self.ticket_list)} sold to {player_puzzle_hash.hex()[:16]}...")
        print(f"Prize pool: {new_amount / 1e12:.4f} XCH")

        return SpendBundle(
            coin_spends=[coin_spend],
            aggregated_signature=sig,
        )

    # -----------------------------------------------------------------------
    # DRAW (Mode 1)
    # -----------------------------------------------------------------------

    def draw(
        self,
        lottery_coin: Coin,
        operator_private_key,
    ) -> SpendBundle:
        """
        Trigger the lottery draw after the deadline.

        Selects a winner pseudo-randomly and distributes the prize pool:
          - Winner receives: prize_pool * (100 - fee_percent) / 100
          - Operator receives: prize_pool * fee_percent / 100

        The winner is selected using:
          winner_index = hash(coin_id) % ticket_count

        Args:
            lottery_coin: The final lottery coin (after all ticket sales)
            operator_private_key: Operator's key for signing

        Returns:
            SpendBundle to execute the draw
        """
        if len(self.ticket_list) == 0:
            raise ValueError("No tickets sold -- cannot draw")

        # Select winner (pseudo-random based on coin ID)
        coin_id = lottery_coin.name()
        winner_index = int.from_bytes(
            hashlib.sha256(coin_id).digest(), 'big'
        ) % len(self.ticket_list)
        winner_ph = self.ticket_list[winner_index]

        # Calculate prize distribution
        total = lottery_coin.amount
        operator_fee = (total * self.config.fee_percent) // 100
        winner_prize = total - operator_fee

        print(f"\n=== LOTTERY DRAW ===")
        print(f"Total tickets: {len(self.ticket_list)}")
        print(f"Prize pool: {total / 1e12:.4f} XCH")
        print(f"Winner index: {winner_index}")
        print(f"Winner: {winner_ph.hex()[:16]}...")
        print(f"Winner prize: {winner_prize / 1e12:.4f} XCH")
        print(f"Operator fee: {operator_fee / 1e12:.4f} XCH")

        # Build the current puzzle
        current_puzzle = self._build_curried_puzzle(self.ticket_list)

        solution = Program.to([
            1,                       # mode = draw
            0,                       # player_puzzle_hash (unused)
            lottery_coin.amount,     # current amount
            0,                       # my_puzzle_hash (unused, coin not recreated)
        ])

        coin_spend = CoinSpend(
            coin=lottery_coin,
            puzzle_reveal=current_puzzle,
            solution=solution,
        )

        sig = self._sign(operator_private_key, b"draw", lottery_coin)

        # Lottery is complete -- no more coins to track
        self.current_coin = None

        return SpendBundle(
            coin_spends=[coin_spend],
            aggregated_signature=sig,
        )

    # -----------------------------------------------------------------------
    # Utility Methods
    # -----------------------------------------------------------------------

    def get_lottery_info(self) -> dict:
        """Return a summary of the current lottery state."""
        total = (
            self.current_coin.amount if self.current_coin
            else self.config.ticket_price * len(self.ticket_list)
        )
        return {
            "tickets_sold": len(self.ticket_list),
            "ticket_price_xch": self.config.ticket_price / 1e12,
            "prize_pool_xch": total / 1e12,
            "operator_fee_percent": self.config.fee_percent,
            "deadline_height": self.config.deadline_height,
        }

    def _sign(self, private_key, message, coin):
        """Placeholder for BLS signing."""
        return b'\xc0' + b'\x00' * 95


# ===========================================================================
# USAGE EXAMPLE
# ===========================================================================

def example_usage():
    """Demonstrate the lottery lifecycle."""

    config = LotteryConfig(
        operator_pubkey=b'\x01' * 48,
        operator_puzzle_hash=b'\xaa' * 32,
        ticket_price=100_000_000_000,    # 0.1 XCH per ticket
        deadline_height=1_000_000,
        fee_percent=2,                    # 2% operator fee
    )

    driver = LotteryDriver(config)

    # Create initial lottery coin
    initial_ph = driver.get_initial_puzzle_hash()
    print(f"Initial lottery puzzle hash: {initial_ph.hex()}")
    print("Send a small amount to this puzzle hash to start the lottery.\n")

    # Simulate the lottery coin existing on-chain
    lottery_coin = Coin(
        parent_coin_id=b'\x00' * 32,
        puzzle_hash=initial_ph,
        amount=1_000_000,  # Small initial amount (dust)
    )

    operator_sk = b'\x10' * 32

    # Simulate 5 ticket purchases
    players = [bytes([i]) * 32 for i in range(1, 6)]
    for player_ph in players:
        bundle = driver.buy_ticket(lottery_coin, player_ph, operator_sk)
        lottery_coin = driver.current_coin  # Track the evolving coin

    # Draw
    print()
    bundle = driver.draw(lottery_coin, operator_sk)


if __name__ == "__main__":
    example_usage()
