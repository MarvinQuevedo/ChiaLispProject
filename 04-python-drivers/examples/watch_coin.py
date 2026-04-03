"""
watch_coin.py
=============
A utility that watches the Chia blockchain for coin lifecycle events:
  - Waiting for a coin to be created (confirmed on chain)
  - Waiting for a coin to be spent
  - Finding child coins after a spend
  - Following a chain of spends (singleton-like tracking)

This is essential for any driver that needs to react to on-chain events.

Requirements:
    pip install chia-dev-tools
    A running Chia full node

Usage:
    python watch_coin.py
"""

import asyncio
import time
from typing import Optional, List

from chia.types.blockchain_format.program import Program
from chia.types.blockchain_format.coin import Coin
from chia.types.blockchain_format.sized_bytes import bytes32
from chia.types.coin_spend import CoinSpend
from chia.rpc.full_node_rpc_client import FullNodeRpcClient
from chia.util.config import load_config
from chia.util.default_root_path import DEFAULT_ROOT_PATH
from chia.util.ints import uint16
from chia.util.bech32m import encode_puzzle_hash


# =============================================================================
# RPC CLIENT HELPER
# =============================================================================

async def create_rpc_client() -> FullNodeRpcClient:
    """
    Create and return an RPC client connected to the local Chia full node.

    The client reads connection details from the Chia config file
    (~/.chia/mainnet/config/config.yaml).

    Make sure your full node is running:
        chia start node
    """
    config = load_config(DEFAULT_ROOT_PATH, "config.yaml")
    rpc_port = config["full_node"]["rpc_port"]

    client = await FullNodeRpcClient.create(
        "localhost",
        uint16(rpc_port),
        DEFAULT_ROOT_PATH,
        config
    )
    return client


# =============================================================================
# COIN WATCHER CLASS
# =============================================================================

class CoinWatcher:
    """
    Watches the blockchain for coin lifecycle events.

    This class provides methods to:
    1. Wait for a coin to appear at a puzzle hash (coin creation)
    2. Wait for a specific coin to be spent
    3. Get the puzzle and solution used to spend a coin
    4. Find child coins created by a spend
    5. Follow a chain of spends
    """

    def __init__(self, client: FullNodeRpcClient):
        self.client = client

    # -------------------------------------------------------------------
    # WATCHING FOR COIN CREATION
    # -------------------------------------------------------------------

    async def wait_for_coin_at_puzzle_hash(
        self,
        puzzle_hash: bytes32,
        poll_interval: float = 5.0,
        timeout: float = 300.0,
    ) -> Optional[Coin]:
        """
        Poll the blockchain until a coin appears at the given puzzle hash.

        This is what you call after sending XCH to an address -- you want
        to wait until the coin is confirmed on chain.

        Parameters:
            puzzle_hash:    The puzzle hash to watch
            poll_interval:  Seconds between polls (default 5)
            timeout:        Maximum seconds to wait (default 300 = 5 minutes)

        Returns:
            The Coin object if found, None if timeout
        """
        address = encode_puzzle_hash(puzzle_hash, "txch")
        print(f"[Watch] Waiting for coin at {address[:20]}...")
        print(f"[Watch] Puzzle hash: {puzzle_hash.hex()[:16]}...")
        print(f"[Watch] Poll interval: {poll_interval}s, Timeout: {timeout}s")

        start_time = time.time()

        while True:
            elapsed = time.time() - start_time
            if elapsed > timeout:
                print(f"[Watch] Timeout after {timeout}s. No coin found.")
                return None

            # Query the blockchain for unspent coins at this puzzle hash
            try:
                records = await self.client.get_coin_records_by_puzzle_hash(
                    puzzle_hash,
                    include_spent_coins=False
                )
            except Exception as e:
                print(f"[Watch] RPC error: {e}. Retrying...")
                await asyncio.sleep(poll_interval)
                continue

            if records:
                coin = records[0].coin
                height = records[0].confirmed_block_index
                print(f"[Watch] Coin found!")
                print(f"  Coin ID: {coin.name().hex()}")
                print(f"  Amount:  {coin.amount} mojos")
                print(f"  Height:  {height}")
                print(f"  Waited:  {elapsed:.1f}s")
                return coin

            # Not found yet, wait and try again
            remaining = timeout - elapsed
            print(f"[Watch] No coin yet. Checking again in {poll_interval}s... "
                  f"({remaining:.0f}s remaining)")
            await asyncio.sleep(poll_interval)

    # -------------------------------------------------------------------
    # WATCHING FOR COIN SPEND
    # -------------------------------------------------------------------

    async def wait_for_coin_spend(
        self,
        coin_id: bytes32,
        poll_interval: float = 5.0,
        timeout: float = 300.0,
    ) -> Optional[dict]:
        """
        Poll until a specific coin is spent.

        Parameters:
            coin_id:        The coin ID (name) to watch
            poll_interval:  Seconds between polls
            timeout:        Maximum seconds to wait

        Returns:
            A dict with:
                - 'coin': the original Coin
                - 'spent_height': the block height where it was spent
                - 'puzzle_reveal': the Program used as puzzle reveal
                - 'solution': the Program used as solution
                - 'conditions': the output conditions
            Or None if timeout
        """
        print(f"[Watch] Waiting for coin {coin_id.hex()[:16]}... to be spent")

        start_time = time.time()

        while True:
            elapsed = time.time() - start_time
            if elapsed > timeout:
                print(f"[Watch] Timeout after {timeout}s. Coin not spent.")
                return None

            try:
                record = await self.client.get_coin_record_by_name(coin_id)
            except Exception as e:
                print(f"[Watch] RPC error: {e}. Retrying...")
                await asyncio.sleep(poll_interval)
                continue

            if record is None:
                print(f"[Watch] Coin not found on chain. It may not be confirmed yet.")
                await asyncio.sleep(poll_interval)
                continue

            if record.spent:
                spent_height = record.spent_block_index
                print(f"[Watch] Coin spent at height {spent_height}!")

                # Retrieve the puzzle and solution that were used
                try:
                    ps = await self.client.get_puzzle_and_solution(
                        coin_id,
                        spent_height
                    )

                    puzzle_reveal = ps.puzzle_reveal
                    solution = ps.solution

                    # Run the puzzle with the solution to see the conditions
                    conditions = puzzle_reveal.run(solution)

                    result = {
                        'coin': record.coin,
                        'spent_height': spent_height,
                        'puzzle_reveal': puzzle_reveal,
                        'solution': solution,
                        'conditions': conditions,
                    }

                    self._print_spend_details(result)
                    return result

                except Exception as e:
                    print(f"[Watch] Could not retrieve puzzle/solution: {e}")
                    return {
                        'coin': record.coin,
                        'spent_height': spent_height,
                        'puzzle_reveal': None,
                        'solution': None,
                        'conditions': None,
                    }

            # Not spent yet
            remaining = timeout - elapsed
            print(f"[Watch] Coin exists but not spent. "
                  f"Checking in {poll_interval}s... ({remaining:.0f}s remaining)")
            await asyncio.sleep(poll_interval)

    def _print_spend_details(self, details: dict):
        """Pretty-print the details of a coin spend."""
        print(f"\n  --- Spend Details ---")
        print(f"  Coin ID:       {details['coin'].name().hex()}")
        print(f"  Amount:        {details['coin'].amount} mojos")
        print(f"  Spent height:  {details['spent_height']}")

        if details['puzzle_reveal']:
            ph = details['puzzle_reveal'].get_tree_hash()
            print(f"  Puzzle hash:   {ph.hex()[:16]}...")

        if details['conditions']:
            print(f"  Output conditions:")
            try:
                for cond in details['conditions'].as_iter():
                    parts = list(cond.as_iter())
                    code = parts[0].as_int()
                    args = []
                    for p in parts[1:]:
                        try:
                            args.append(p.as_python())
                        except Exception:
                            args.append(str(p))
                    print(f"    Code {code}: {args}")
            except Exception:
                print(f"    (could not parse conditions)")

    # -------------------------------------------------------------------
    # FINDING CHILD COINS
    # -------------------------------------------------------------------

    async def get_children(self, parent_coin_id: bytes32) -> List[Coin]:
        """
        Find all coins created when a parent coin was spent.

        When a coin is spent, its conditions may include CREATE_COIN (51).
        Each CREATE_COIN creates a new child coin whose parent_coin_info
        is the ID of the spent coin.

        Parameters:
            parent_coin_id: The ID of the spent parent coin

        Returns:
            A list of child Coin objects
        """
        print(f"\n[Children] Looking for children of {parent_coin_id.hex()[:16]}...")

        records = await self.client.get_coin_records_by_parent_ids(
            [parent_coin_id],
            include_spent_coins=True  # Include even if already spent
        )

        children = []
        for record in records:
            coin = record.coin
            children.append(coin)
            status = "SPENT" if record.spent else "UNSPENT"
            print(f"  Child: {coin.name().hex()[:16]}... "
                  f"amount={coin.amount} [{status}]")

        if not children:
            print(f"  No children found.")

        return children

    # -------------------------------------------------------------------
    # FOLLOWING A CHAIN OF SPENDS (SINGLETON TRACKING)
    # -------------------------------------------------------------------

    async def follow_spend_chain(
        self,
        starting_coin_id: bytes32,
        max_depth: int = 100,
    ) -> List[dict]:
        """
        Follow a chain of coin spends from a starting coin.

        This is useful for tracking singletons or any puzzle that
        recreates itself when spent. The chain is:
            coin_0 -> spend -> coin_1 -> spend -> coin_2 -> ...

        At each step, we look at the children of the spent coin and
        follow the first unspent child (or the first child if all are spent).

        Parameters:
            starting_coin_id: The coin to start tracking from
            max_depth:        Maximum number of spends to follow

        Returns:
            A list of dicts, each containing spend details
        """
        print(f"\n[Chain] Following spend chain from {starting_coin_id.hex()[:16]}...")

        chain = []
        current_coin_id = starting_coin_id

        for depth in range(max_depth):
            # Get the coin record
            record = await self.client.get_coin_record_by_name(current_coin_id)
            if record is None:
                print(f"  [Depth {depth}] Coin not found. End of chain.")
                break

            if not record.spent:
                print(f"  [Depth {depth}] Coin {current_coin_id.hex()[:16]}... "
                      f"is UNSPENT (current state)")
                chain.append({
                    'coin': record.coin,
                    'spent': False,
                    'depth': depth,
                })
                break

            # Coin is spent -- get the details
            print(f"  [Depth {depth}] Coin {current_coin_id.hex()[:16]}... "
                  f"spent at height {record.spent_block_index}")

            try:
                ps = await self.client.get_puzzle_and_solution(
                    current_coin_id,
                    record.spent_block_index
                )
                conditions = ps.puzzle_reveal.run(ps.solution)
            except Exception:
                ps = None
                conditions = None

            chain.append({
                'coin': record.coin,
                'spent': True,
                'spent_height': record.spent_block_index,
                'puzzle_reveal': ps.puzzle_reveal if ps else None,
                'solution': ps.solution if ps else None,
                'conditions': conditions,
                'depth': depth,
            })

            # Find children and follow the chain
            children = await self.get_children(current_coin_id)
            if not children:
                print(f"  [Depth {depth}] No children. End of chain.")
                break

            # Heuristic: follow the first child
            # For singletons, you would filter by the singleton puzzle hash
            current_coin_id = children[0].name()

        print(f"\n[Chain] Total depth: {len(chain)} spends")
        return chain

    # -------------------------------------------------------------------
    # FULL LIFECYCLE WATCHER
    # -------------------------------------------------------------------

    async def watch_full_lifecycle(
        self,
        puzzle_hash: bytes32,
        poll_interval: float = 5.0,
    ):
        """
        Watch the complete lifecycle of a coin:
        1. Wait for it to be created
        2. Print its details
        3. Wait for it to be spent
        4. Print the spend details
        5. Show the child coins

        This is a convenience method that chains all the above together.
        """
        print("\n" + "=" * 60)
        print("Full Coin Lifecycle Watcher")
        print("=" * 60)

        address = encode_puzzle_hash(puzzle_hash, "txch")
        print(f"Watching address: {address}")
        print(f"Send coins to this address to begin.\n")

        # Phase 1: Wait for creation
        print("--- Phase 1: Waiting for coin creation ---")
        coin = await self.wait_for_coin_at_puzzle_hash(
            puzzle_hash,
            poll_interval=poll_interval,
            timeout=600  # 10 minutes
        )

        if coin is None:
            print("No coin created within timeout. Exiting.")
            return

        # Phase 2: Wait for spend
        print("\n--- Phase 2: Waiting for coin to be spent ---")
        coin_id = coin.name()
        spend_details = await self.wait_for_coin_spend(
            coin_id,
            poll_interval=poll_interval,
            timeout=600
        )

        if spend_details is None:
            print("Coin not spent within timeout. Exiting.")
            return

        # Phase 3: Find children
        print("\n--- Phase 3: Finding child coins ---")
        children = await self.get_children(coin_id)

        print(f"\n--- Lifecycle Complete ---")
        print(f"  Created: coin {coin_id.hex()[:16]}... with {coin.amount} mojos")
        print(f"  Spent:   at height {spend_details['spent_height']}")
        print(f"  Children: {len(children)} coins created")
        for child in children:
            print(f"    -> {child.name().hex()[:16]}... ({child.amount} mojos)")


# =============================================================================
# MAIN / DEMO
# =============================================================================

async def demo_with_node():
    """
    Run the watcher against a real Chia node.

    Uncomment and customize this to use with your node.
    """
    client = await create_rpc_client()

    try:
        watcher = CoinWatcher(client)

        # Example 1: Check blockchain state
        state = await client.get_blockchain_state()
        print(f"Blockchain synced: {state['sync']['synced']}")
        print(f"Peak height: {state['peak'].height}")

        # Example 2: Watch for coins at a specific puzzle hash
        # Replace with your actual puzzle hash:
        # puzzle_hash = bytes32.from_hexstr("your_puzzle_hash_here")
        # await watcher.watch_full_lifecycle(puzzle_hash)

        # Example 3: Check a specific coin
        # coin_id = bytes32.from_hexstr("your_coin_id_here")
        # details = await watcher.wait_for_coin_spend(coin_id, timeout=10)

        # Example 4: Follow a spend chain
        # coin_id = bytes32.from_hexstr("starting_coin_id")
        # chain = await watcher.follow_spend_chain(coin_id)

    finally:
        client.close()
        await client.await_closed()


def demo_offline():
    """
    Demonstrate the watcher concepts without a running node.
    """
    print("=" * 60)
    print("Coin Watcher - Offline Demo")
    print("=" * 60)
    print()
    print("The CoinWatcher class provides these methods:")
    print()
    print("1. wait_for_coin_at_puzzle_hash(puzzle_hash)")
    print("   - Polls the blockchain until a coin appears at the given puzzle hash")
    print("   - Useful after sending XCH to a custom puzzle address")
    print("   - Returns the Coin object when found")
    print()
    print("2. wait_for_coin_spend(coin_id)")
    print("   - Polls until a specific coin is spent")
    print("   - Returns the puzzle reveal, solution, and output conditions")
    print("   - Useful for reacting to on-chain events")
    print()
    print("3. get_children(parent_coin_id)")
    print("   - Finds all coins created when a parent coin was spent")
    print("   - Each CREATE_COIN condition creates one child")
    print()
    print("4. follow_spend_chain(starting_coin_id)")
    print("   - Follows a sequence of spends (parent -> child -> grandchild)")
    print("   - Useful for tracking singletons")
    print()
    print("5. watch_full_lifecycle(puzzle_hash)")
    print("   - Combines all of the above: creation -> spend -> children")
    print()
    print("To use with a real node, uncomment the demo_with_node() call below.")
    print()
    print("Example usage:")
    print()
    print("  client = await create_rpc_client()")
    print("  watcher = CoinWatcher(client)")
    print()
    print("  # Watch for a coin to appear")
    print("  coin = await watcher.wait_for_coin_at_puzzle_hash(my_puzzle_hash)")
    print()
    print("  # Wait for it to be spent")
    print("  details = await watcher.wait_for_coin_spend(coin.name())")
    print()
    print("  # See what it created")
    print("  children = await watcher.get_children(coin.name())")


if __name__ == "__main__":
    # Offline demo (no node required)
    demo_offline()

    # Uncomment below to run with a real Chia node:
    # asyncio.run(demo_with_node())
