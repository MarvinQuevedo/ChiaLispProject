"""
=============================================================================
SIMPLE VOTING SYSTEM DRIVER
=============================================================================

Manages the lifecycle of an on-chain proposal and voting system:
  1. Create a proposal with options
  2. Accept votes from token holders
  3. Finalize the proposal and determine the result

The proposal coin evolves with each vote (the TALLY state changes),
so the driver must track the evolving coin.

Votes are coordinated: the voter's coin and the proposal coin must be
spent in the SAME spend bundle. The driver assembles this bundle.

This is pseudocode-style but complete in logic.
"""

import hashlib
from dataclasses import dataclass, field
from typing import List, Optional, Dict

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


def load_voting_puzzle() -> Program:
    """Load and compile the voting.clsp puzzle."""
    return Program()


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class ProposalConfig:
    """Configuration for a voting proposal."""
    creator_pubkey: bytes       # 48-byte G1 public key
    proposal_text: str          # Human-readable proposal text
    options: List[str]          # List of option labels (e.g., ["yes", "no"])
    deadline_height: int        # Absolute block height when voting ends


# ===========================================================================
# VOTING DRIVER
# ===========================================================================

class VotingDriver:
    """
    Manages a voting proposal from creation through finalization.

    The proposal coin tracks the running tally. Each vote updates the tally
    by spending and recreating the proposal coin. The driver coordinates
    the voter's coin spend with the proposal coin spend in a single bundle.

    Usage:
        config = ProposalConfig(
            creator_pubkey=creator_pk,
            proposal_text="Should we fund project X?",
            options=["Yes", "No", "Abstain"],
            deadline_height=1_500_000,
        )
        driver = VotingDriver(config)

        # Create the proposal
        proposal_ph = driver.get_proposal_puzzle_hash()
        # Send a small amount to proposal_ph to create the proposal coin

        # Cast a vote
        bundle = driver.cast_vote(
            proposal_coin, voter_coin, voter_puzzle, option_index=0,
            creator_sk=creator_sk
        )

        # Finalize after deadline
        bundle = driver.finalize(proposal_coin, creator_sk)
    """

    def __init__(self, config: ProposalConfig):
        self.config = config
        self.base_puzzle = load_voting_puzzle()

        # Compute proposal hash from text
        self.proposal_hash = hashlib.sha256(
            config.proposal_text.encode()
        ).digest()

        # Initialize tally: all zeros, one per option
        self.tally: List[int] = [0] * len(config.options)
        self.total_votes: int = 0

        # Track the current proposal coin
        self.current_coin: Optional[Coin] = None

        # Track individual votes for auditing
        self.vote_log: List[Dict] = []

    # -----------------------------------------------------------------------
    # Puzzle construction
    # -----------------------------------------------------------------------

    def _build_puzzle(self, tally: List[int], total_votes: int) -> Program:
        """
        Build the curried proposal puzzle for the current state.

        The tally and total_votes change with each vote, producing a
        new puzzle hash each time.
        """
        return self.base_puzzle.curry(
            Program.to(self.config.creator_pubkey),
            Program.to(self.proposal_hash),
            Program.to(len(self.config.options)),
            Program.to(self.config.deadline_height),
            Program.to(tally),
            Program.to(total_votes),
        )

    def get_proposal_puzzle_hash(self) -> bytes:
        """Get the initial proposal puzzle hash (zero tally)."""
        return self._build_puzzle(self.tally, self.total_votes).get_tree_hash()

    # -----------------------------------------------------------------------
    # CREATE PROPOSAL
    # -----------------------------------------------------------------------

    def create_proposal(self) -> dict:
        """
        Return the information needed to create the proposal on-chain.

        The caller must send a small amount of XCH to the puzzle hash
        to create the proposal coin. This amount serves as the "ballot box"
        and is returned to the creator after finalization.
        """
        puzzle_hash = self.get_proposal_puzzle_hash()
        return {
            "puzzle_hash": puzzle_hash,
            "proposal_hash": self.proposal_hash,
            "options": self.config.options,
            "deadline_height": self.config.deadline_height,
            "instructions": (
                f"Send a small amount (e.g., 1 mojo) to puzzle hash "
                f"{puzzle_hash.hex()} to create the proposal coin."
            ),
        }

    # -----------------------------------------------------------------------
    # CAST VOTE (Mode 0)
    # -----------------------------------------------------------------------

    def cast_vote(
        self,
        proposal_coin: Coin,
        voter_coin: Coin,
        voter_puzzle_reveal: Program,
        option_index: int,
        creator_private_key,
    ) -> SpendBundle:
        """
        Cast a vote on the proposal.

        This builds a spend bundle containing TWO coin spends:
          1. The voter's coin -- creates a coin announcement with the vote
          2. The proposal coin -- asserts the announcement and updates tally

        The vote weight equals the voter's coin amount. Larger coins
        get more voting power (token-weighted voting).

        Args:
            proposal_coin: The current proposal coin
            voter_coin: The voter's coin to spend (vote weight = amount)
            voter_puzzle_reveal: The voter's coin puzzle (for spending it)
            option_index: Which option to vote for (0-indexed)
            creator_private_key: Creator's key to approve the vote

        Returns:
            SpendBundle with both coin spends

        Raises:
            ValueError: If option_index is out of range
        """
        # Validate option index
        if option_index < 0 or option_index >= len(self.config.options):
            raise ValueError(
                f"Option index {option_index} is out of range. "
                f"Valid options: {list(range(len(self.config.options)))}"
            )

        vote_weight = voter_coin.amount
        option_label = self.config.options[option_index]

        # --- Build the voter's coin spend ---
        # The voter's coin creates an announcement that the proposal can verify.
        # The announcement message: sha256("vote" + option_index)
        # This is verified by the proposal puzzle via ASSERT_COIN_ANNOUNCEMENT.
        voter_solution = Program.to([
            # The voter's puzzle solution would normally recreate their coin
            # minus the vote weight. For simplicity, we assume the voter's
            # entire coin is consumed as the vote.
            # The voter's puzzle must include CREATE_COIN_ANNOUNCEMENT.
        ])

        voter_spend = CoinSpend(
            coin=voter_coin,
            puzzle_reveal=voter_puzzle_reveal,
            solution=voter_solution,
        )

        # --- Build the proposal coin spend ---
        # Compute the new tally after this vote
        new_tally = self.tally.copy()
        new_tally[option_index] += vote_weight
        new_total = self.total_votes + vote_weight

        # Compute the new puzzle hash for the recreated proposal coin
        new_puzzle = self._build_puzzle(new_tally, new_total)
        new_puzzle_hash = new_puzzle.get_tree_hash()

        # Build current puzzle
        current_puzzle = self._build_puzzle(self.tally, self.total_votes)

        proposal_solution = Program.to([
            0,                       # mode = accept vote
            option_index,            # which option
            vote_weight,             # weight of this vote
            voter_coin.name(),       # voter's coin ID (for announcement)
            proposal_coin.amount,    # proposal coin amount
            new_puzzle_hash,         # new puzzle hash (updated tally)
        ])

        proposal_spend = CoinSpend(
            coin=proposal_coin,
            puzzle_reveal=current_puzzle,
            solution=proposal_solution,
        )

        # Aggregate signatures (creator approves the vote)
        sig = self._sign(creator_private_key, b"accept_vote", proposal_coin)

        # Update driver state
        self.tally = new_tally
        self.total_votes = new_total
        self.current_coin = Coin(
            parent_coin_id=proposal_coin.name(),
            puzzle_hash=new_puzzle_hash,
            amount=proposal_coin.amount,
        )

        # Log the vote
        self.vote_log.append({
            "voter_coin_id": voter_coin.name().hex(),
            "option_index": option_index,
            "option_label": option_label,
            "weight": vote_weight,
        })

        print(f"Vote cast: '{option_label}' with weight {vote_weight}")

        return SpendBundle(
            coin_spends=[voter_spend, proposal_spend],
            aggregated_signature=sig,
        )

    # -----------------------------------------------------------------------
    # FINALIZE (Mode 1)
    # -----------------------------------------------------------------------

    def finalize(
        self,
        proposal_coin: Coin,
        creator_private_key,
    ) -> SpendBundle:
        """
        Finalize the proposal after the voting period.

        Determines the winning option and announces the result.
        The proposal coin is consumed (not recreated).

        Args:
            proposal_coin: The final proposal coin (after all votes)
            creator_private_key: Creator's key for signing

        Returns:
            SpendBundle to finalize the proposal
        """
        # Determine the winner
        winner_index = self._find_winner()
        winner_label = self.config.options[winner_index]

        current_puzzle = self._build_puzzle(self.tally, self.total_votes)

        solution = Program.to([
            1,                       # mode = finalize
            0,                       # option_index (unused)
            0,                       # vote_weight (unused)
            0,                       # voter_coin_id (unused)
            proposal_coin.amount,    # my_amount
            0,                       # my_puzzle_hash (unused, coin consumed)
        ])

        coin_spend = CoinSpend(
            coin=proposal_coin,
            puzzle_reveal=current_puzzle,
            solution=solution,
        )

        sig = self._sign(creator_private_key, b"finalize", proposal_coin)

        self.current_coin = None

        return SpendBundle(
            coin_spends=[coin_spend],
            aggregated_signature=sig,
        )

    # -----------------------------------------------------------------------
    # Result and status methods
    # -----------------------------------------------------------------------

    def _find_winner(self) -> int:
        """Find the option index with the most votes."""
        if not self.tally or all(v == 0 for v in self.tally):
            return 0
        return self.tally.index(max(self.tally))

    def get_results(self) -> dict:
        """
        Get the current voting results.

        Can be called at any time to see the running tally.
        """
        results = {}
        for i, option in enumerate(self.config.options):
            count = self.tally[i] if i < len(self.tally) else 0
            percent = (count * 100 / self.total_votes) if self.total_votes > 0 else 0
            results[option] = {
                "votes": count,
                "percent": round(percent, 2),
            }

        winner_index = self._find_winner()
        return {
            "proposal": self.config.proposal_text,
            "total_votes": self.total_votes,
            "options": results,
            "current_leader": self.config.options[winner_index],
            "deadline_height": self.config.deadline_height,
            "vote_count": len(self.vote_log),
        }

    def _sign(self, private_key, message, coin):
        """Placeholder for BLS signing."""
        return b'\xc0' + b'\x00' * 95


# ===========================================================================
# USAGE EXAMPLE
# ===========================================================================

def example_usage():
    """Demonstrate the voting lifecycle."""

    # Create a proposal
    config = ProposalConfig(
        creator_pubkey=b'\x01' * 48,
        proposal_text="Should the community fund the development of Project Alpha?",
        options=["Yes", "No", "Abstain"],
        deadline_height=1_500_000,
    )

    driver = VotingDriver(config)

    # Deploy the proposal
    info = driver.create_proposal()
    print("=== PROPOSAL CREATED ===")
    print(f"  Text: {config.proposal_text}")
    print(f"  Options: {config.options}")
    print(f"  Deadline height: {config.deadline_height}")
    print(f"  Puzzle hash: {info['puzzle_hash'].hex()}")

    # Simulate the proposal coin on-chain
    proposal_coin = Coin(
        parent_coin_id=b'\x00' * 32,
        puzzle_hash=info['puzzle_hash'],
        amount=1,  # 1 mojo (just to create the coin)
    )

    creator_sk = b'\x10' * 32

    # Simulate votes from different holders
    print("\n=== CASTING VOTES ===")

    voters = [
        {"ph": b'\xaa' * 32, "amount": 500_000_000_000, "choice": 0},   # Yes, 500B mojos
        {"ph": b'\xbb' * 32, "amount": 300_000_000_000, "choice": 0},   # Yes, 300B mojos
        {"ph": b'\xcc' * 32, "amount": 200_000_000_000, "choice": 1},   # No, 200B mojos
        {"ph": b'\xdd' * 32, "amount": 100_000_000_000, "choice": 2},   # Abstain, 100B mojos
        {"ph": b'\xee' * 32, "amount": 400_000_000_000, "choice": 0},   # Yes, 400B mojos
    ]

    for voter in voters:
        voter_coin = Coin(b'\x00' * 32, voter["ph"], voter["amount"])
        voter_puzzle = Program()  # Placeholder for the voter's standard puzzle
        bundle = driver.cast_vote(
            proposal_coin, voter_coin, voter_puzzle,
            option_index=voter["choice"],
            creator_private_key=creator_sk,
        )
        proposal_coin = driver.current_coin  # Track the evolving proposal coin

    # Show results
    print("\n=== VOTING RESULTS ===")
    results = driver.get_results()
    print(f"  Proposal: {results['proposal']}")
    print(f"  Total votes (weighted): {results['total_votes']:,}")
    for option, data in results['options'].items():
        bar = '#' * int(data['percent'] / 2)
        print(f"  {option:10s}: {data['votes']:>15,} ({data['percent']:5.1f}%) {bar}")
    print(f"  Leader: {results['current_leader']}")

    # Finalize
    print("\n=== FINALIZING ===")
    bundle = driver.finalize(proposal_coin, creator_sk)
    print(f"Proposal finalized. Winner: {results['current_leader']}")


if __name__ == "__main__":
    example_usage()
