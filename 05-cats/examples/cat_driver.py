"""
cat_driver.py - Python Driver for CAT (Chia Asset Token) Operations

This driver demonstrates how to interact with CATs programmatically:
  1. Compile and curry TAIL programs to compute asset IDs
  2. Construct CAT puzzles with inner puzzles
  3. Build CAT spend bundles (with ring construction)
  4. Find and parse CAT coins on the blockchain
  5. Mint new CAT tokens
  6. Transfer CATs between owners

IMPORTANT: This uses real chia-blockchain class names and patterns.
Some imports may vary depending on your chia-blockchain version.

Prerequisites:
  pip install chia-blockchain
  pip install chia-dev-tools
  pip install blspy
"""

import asyncio
from dataclasses import dataclass
from typing import List, Optional, Tuple

# ============================================================
# Chia blockchain imports
# ============================================================
# These are the real imports from chia-blockchain.
# If you get import errors, make sure chia-blockchain is installed.

from chia.types.blockchain_format.coin import Coin
from chia.types.blockchain_format.program import Program
from chia.types.blockchain_format.sized_bytes import bytes32
from chia.types.coin_spend import CoinSpend
from chia.types.spend_bundle import SpendBundle
from chia.util.bech32m import encode_puzzle_hash, decode_puzzle_hash
from chia.util.ints import uint64
from chia.wallet.cat_wallet.cat_utils import (
    construct_cat_puzzle,
    CAT_MOD,
    CAT_MOD_HASH,
    SpendableCAT,
    unsigned_spend_bundle_for_spendable_cats,
)
from chia.wallet.lineage_proof import LineageProof
from chia.rpc.full_node_rpc_client import FullNodeRpcClient
from chia.util.config import load_config
from chia.util.default_root_path import DEFAULT_ROOT_PATH

# BLS signature library
from blspy import AugSchemeMPL, G1Element, G2Element, PrivateKey

# Compiler (for building TAIL programs from source)
from clvm_tools_rs import compile_clvm_text


# ============================================================
# TAIL Compilation Helpers
# ============================================================

def compile_tail(source: str, search_paths: list = None) -> Program:
    """
    Compile a TAIL program from ChiaLisp source code.

    Parameters:
        source:       The ChiaLisp source code as a string
        search_paths: Directories to search for include files

    Returns:
        A compiled Program object representing the TAIL

    Example:
        tail_source = open("simple_tail.clsp").read()
        tail = compile_tail(tail_source, ["./include"])
    """
    if search_paths is None:
        search_paths = []

    compiled_hex = compile_clvm_text(source, search_paths)
    return Program.fromhex(compiled_hex)


def compute_asset_id(tail_program: Program, curry_args: list = None) -> Tuple[Program, bytes32]:
    """
    Compute the asset ID for a TAIL program.

    The asset ID is the tree hash of the fully curried TAIL program.
    Two tokens with the same curried TAIL have the same asset ID.

    Parameters:
        tail_program: The compiled (uncurried) TAIL program
        curry_args:   Arguments to curry into the TAIL

    Returns:
        (curried_tail, asset_id) tuple

    Example:
        tail = compile_tail(source)
        curried_tail, asset_id = compute_asset_id(tail, [genesis_coin_id])
        print(f"Asset ID: {asset_id.hex()}")
    """
    if curry_args:
        curried = tail_program.curry(*[Program.to(arg) for arg in curry_args])
    else:
        curried = tail_program

    asset_id = curried.get_tree_hash()
    return curried, asset_id


# ============================================================
# CATDriver - Main Driver Class
# ============================================================

@dataclass
class CATDriver:
    """
    Driver for interacting with Chia Asset Tokens (CATs).

    A CAT is a fungible token on Chia. Each CAT type is identified
    by its TAIL hash (also called asset_id). The TAIL (Token and
    Asset Issuance Limiter) controls minting and burning rules.

    Usage:
        driver = CATDriver(tail_hash=my_asset_id)
        puzzle = driver.get_cat_puzzle(inner_puzzle)
        address = driver.get_cat_address(inner_puzzle)
    """

    tail_hash: bytes32  # The asset_id -- identifies the token type

    # ----------------------------------------------------------
    # Puzzle Construction
    # ----------------------------------------------------------

    def get_cat_puzzle(self, inner_puzzle: Program) -> Program:
        """
        Construct the full CAT puzzle by wrapping an inner puzzle.

        The CAT puzzle structure is:
            CAT_MOD(CAT_MOD_HASH, TAIL_HASH, INNER_PUZZLE)

        Where:
        - CAT_MOD is the standard CAT v2 puzzle template
        - CAT_MOD_HASH is the hash of CAT_MOD (needed for self-reference
          when the puzzle wraps CREATE_COIN conditions)
        - TAIL_HASH identifies which token type this is
        - INNER_PUZZLE handles ownership (usually standard transaction)

        Parameters:
            inner_puzzle: The inner puzzle that controls spending

        Returns:
            The full CAT puzzle (Program)
        """
        return construct_cat_puzzle(
            CAT_MOD,           # The CAT outer puzzle template
            self.tail_hash,    # Which token this is (asset_id)
            inner_puzzle       # Who controls spending (ownership)
        )

    def get_cat_puzzle_hash(self, inner_puzzle: Program) -> bytes32:
        """Get the puzzle hash for a CAT coin with the given inner puzzle."""
        cat_puzzle = self.get_cat_puzzle(inner_puzzle)
        return cat_puzzle.get_tree_hash()

    def get_cat_address(self, inner_puzzle: Program, prefix: str = "xch") -> str:
        """
        Get the bech32m address for a CAT coin.

        Note: CAT addresses use the same prefix as XCH addresses, but the
        puzzle hash is different because it includes the CAT wrapper. Sending
        XCH to a CAT address will NOT create a CAT -- it creates a regular
        coin that happens to be locked by the CAT puzzle (which would be
        unspendable without a valid lineage proof).

        For creating CATs, use the minting process instead.
        """
        puzzle_hash = self.get_cat_puzzle_hash(inner_puzzle)
        return encode_puzzle_hash(puzzle_hash, prefix)

    # ----------------------------------------------------------
    # Building CAT Spends
    # ----------------------------------------------------------

    def create_spendable_cat(
        self,
        coin: Coin,
        inner_puzzle: Program,
        inner_solution: Program,
        lineage_proof: LineageProof,
        extra_delta: int = 0,
        limitations_program_reveal: Program = Program.to(0),
        limitations_solution: Program = Program.to(0),
    ) -> SpendableCAT:
        """
        Create a SpendableCAT object for inclusion in a spend bundle.

        A SpendableCAT packages all the information the CAT module needs
        to construct the full CAT solution, including ring linkage and
        conservation math.

        Parameters:
            coin:          The CAT coin to spend
            inner_puzzle:  The inner puzzle of this CAT coin (must match
                          what is curried into the on-chain CAT puzzle)
            inner_solution: Solution for the inner puzzle. For a standard
                          transaction, this is (delegated_puzzle, delegated_solution).
                          The delegated puzzle typically returns CREATE_COIN conditions.
            lineage_proof: Proof that this coin descended from a valid CAT.
                          Contains (parent_parent_id, parent_inner_puzzle_hash,
                          parent_amount).
            extra_delta:   Amount being minted (+) or burned (-). Must be 0
                          for normal transfers.
            limitations_program_reveal: The TAIL program. Only needed when
                          extra_delta != 0 (minting or melting).
            limitations_solution: Solution for the TAIL. Only needed when
                          extra_delta != 0.

        Returns:
            A SpendableCAT ready to be included in a spend bundle
        """
        return SpendableCAT(
            coin=coin,
            limitations_program_hash=self.tail_hash,
            inner_puzzle=inner_puzzle,
            inner_solution=inner_solution,
            lineage_proof=lineage_proof,
            extra_delta=extra_delta,
            limitations_program_reveal=limitations_program_reveal,
            limitations_solution=limitations_solution,
        )

    def build_spend_bundle(
        self,
        spendable_cats: List[SpendableCAT],
        signatures: List[G2Element] = None,
    ) -> SpendBundle:
        """
        Build a complete SpendBundle from a list of SpendableCATs.

        This is where the magic happens. The function:
        1. Creates the "ring" structure linking all CATs in the spend
        2. Computes running subtotals for the conservation check
        3. Assembles the full CAT solutions (inner solution + ring data +
           lineage proofs + subtotals)
        4. Combines everything with the aggregated signature

        The ring structure (for 3 coins being spent together):
            CAT_A --> CAT_B --> CAT_C --> CAT_A (circular)
            Each CAT verifies the previous and next coins in the ring.
            The running subtotal ensures total_in = total_out.

        Parameters:
            spendable_cats: List of SpendableCAT objects
            signatures:     List of BLS signatures (G2Element).
                           These get aggregated into a single signature.

        Returns:
            A complete SpendBundle ready to push to the mempool
        """
        # unsigned_spend_bundle_for_spendable_cats handles the complex
        # ring construction automatically. This is the key function that
        # saves you from building the ring by hand.
        unsigned_bundle = unsigned_spend_bundle_for_spendable_cats(
            CAT_MOD, spendable_cats
        )

        # Aggregate all BLS signatures into one
        if signatures:
            aggregated_sig = AugSchemeMPL.aggregate(signatures)
        else:
            aggregated_sig = G2Element()

        # Combine the coin spends with the aggregated signature
        return SpendBundle(
            coin_spends=unsigned_bundle.coin_spends,
            aggregated_signature=aggregated_sig,
        )

    # ----------------------------------------------------------
    # Transfer CATs
    # ----------------------------------------------------------

    def create_transfer_spend(
        self,
        coin: Coin,
        sender_inner_puzzle: Program,
        receiver_inner_puzzle_hash: bytes32,
        amount: uint64,
        lineage_proof: LineageProof,
        change_inner_puzzle_hash: Optional[bytes32] = None,
    ) -> SpendableCAT:
        """
        Create a spend to transfer CAT tokens to a new owner.

        This is the most common operation: sending tokens from one person
        to another. It creates:
        - A CREATE_COIN for the receiver (with the transferred amount)
        - A CREATE_COIN for change (if not sending the full balance)

        IMPORTANT: The puzzle hashes here are INNER puzzle hashes, not
        full CAT puzzle hashes. The CAT outer puzzle automatically wraps
        CREATE_COIN conditions, so you only specify the inner puzzle hash
        of the recipient.

        Parameters:
            coin:                      The CAT coin to spend
            sender_inner_puzzle:       The sender's inner puzzle (Program)
            receiver_inner_puzzle_hash: The receiver's inner puzzle hash (bytes32).
                                       This is derived from the receiver's XCH
                                       address, NOT a CAT address.
            amount:                    Amount of tokens to send
            lineage_proof:             Lineage proof for this coin
            change_inner_puzzle_hash:  Where to send change. Defaults to sender.

        Returns:
            A SpendableCAT for this transfer
        """
        conditions = []

        # --- CREATE_COIN for receiver ---
        # The amount of tokens to send
        conditions.append([51, receiver_inner_puzzle_hash, amount])

        # --- CREATE_COIN for change ---
        # If we are not sending the full amount, we need a change coin
        change_amount = coin.amount - amount
        if change_amount > 0:
            change_ph = change_inner_puzzle_hash or sender_inner_puzzle.get_tree_hash()
            conditions.append([51, change_ph, change_amount])

        # --- ASSERT_MY_AMOUNT ---
        # Security measure: assert that this coin actually has the amount we think.
        # If someone provides a wrong coin, this prevents unexpected behavior.
        conditions.append([73, coin.amount])

        # The inner solution is just the list of conditions.
        # For a standard transaction inner puzzle, you would use
        # solution_for_delegated_puzzle instead. This simplified version
        # assumes the inner puzzle directly takes conditions.
        inner_solution = Program.to(conditions)

        return self.create_spendable_cat(
            coin=coin,
            inner_puzzle=sender_inner_puzzle,
            inner_solution=inner_solution,
            lineage_proof=lineage_proof,
        )

    # ----------------------------------------------------------
    # Minting New CATs
    # ----------------------------------------------------------

    def create_mint_spend(
        self,
        genesis_coin: Coin,
        tail_program: Program,
        tail_solution: Program,
        amount: uint64,
        target_inner_puzzle: Program,
    ) -> Tuple[SpendableCAT, bytes32]:
        """
        Create a SpendableCAT for minting new tokens.

        Minting is the process of creating tokens that did not exist before.
        The TAIL program must approve the minting event.

        For a genesis-by-coin-id TAIL:
        - The genesis_coin must be the specific coin referenced in the TAIL
        - The genesis_coin is spent alongside the CAT creation
        - Since the genesis coin can only be spent once, minting is one-time

        For an authorized-minter TAIL:
        - The genesis_coin can be any coin
        - A signature from the authorized key must be included
        - Minting can happen multiple times

        Parameters:
            genesis_coin:        The coin used for the genesis event
            tail_program:        The compiled TAIL program (for reveal)
            tail_solution:       Solution for the TAIL
            amount:              Number of token mojos to mint
            target_inner_puzzle: Inner puzzle for the minted tokens
                                (determines who receives them)

        Returns:
            (SpendableCAT, cat_puzzle_hash) tuple
        """
        cat_puzzle = self.get_cat_puzzle(target_inner_puzzle)
        cat_puzzle_hash = cat_puzzle.get_tree_hash()

        # For minting, the lineage proof is special ("eve" lineage)
        # because the parent is not a CAT -- it is a regular coin.
        lineage_proof = LineageProof(
            parent_name=genesis_coin.name(),
            inner_puzzle_hash=None,  # Eve coins have no CAT parent
            amount=uint64(amount),
        )

        # The inner solution creates the minted coins
        inner_solution = Program.to([
            [51, target_inner_puzzle.get_tree_hash(), amount],  # CREATE_COIN
            [73, amount],                                        # ASSERT_MY_AMOUNT
        ])

        spendable = self.create_spendable_cat(
            coin=genesis_coin,
            inner_puzzle=target_inner_puzzle,
            inner_solution=inner_solution,
            lineage_proof=lineage_proof,
            extra_delta=amount,  # Positive = minting
            limitations_program_reveal=tail_program,
            limitations_solution=tail_solution,
        )

        return spendable, cat_puzzle_hash

    # ----------------------------------------------------------
    # Finding CAT Coins on Chain
    # ----------------------------------------------------------

    @staticmethod
    async def find_cat_coins(
        node_client: FullNodeRpcClient,
        cat_puzzle_hash: bytes32,
        include_spent: bool = False,
    ) -> List[Coin]:
        """
        Find CAT coins on the blockchain by their full puzzle hash.

        The puzzle hash must be the FULL CAT puzzle hash (outer + inner),
        not just the inner puzzle hash.

        Parameters:
            node_client:     Connected RPC client
            cat_puzzle_hash: The full CAT puzzle hash
            include_spent:   Whether to include already-spent coins

        Returns:
            List of Coin objects matching the puzzle hash
        """
        records = await node_client.get_coin_records_by_puzzle_hash(
            cat_puzzle_hash,
            include_spent_coins=include_spent,
        )
        return [record.coin for record in records]

    @staticmethod
    async def get_lineage_proof(
        node_client: FullNodeRpcClient,
        coin: Coin,
    ) -> Optional[LineageProof]:
        """
        Retrieve the lineage proof for a CAT coin from the blockchain.

        This looks up the parent coin's spend to extract the information
        needed for the lineage proof.

        Parameters:
            node_client: Connected RPC client
            coin:        The CAT coin that needs a lineage proof

        Returns:
            A LineageProof, or None if the parent spend cannot be found
        """
        # Find the parent coin record
        parent_record = await node_client.get_coin_record_by_name(
            coin.parent_coin_info
        )

        if parent_record is None or not parent_record.spent:
            return None

        # Get the parent's puzzle and solution
        parent_spend = await node_client.get_puzzle_and_solution(
            coin.parent_coin_info,
            parent_record.spent_block_index,
        )

        # Uncurry the parent's puzzle to extract the inner puzzle hash
        try:
            _, curried_args = parent_spend.puzzle_reveal.uncurry()
            args = list(curried_args.as_iter())
            # CAT puzzle has 3 curried args: (MOD_HASH, TAIL_HASH, INNER_PUZZLE)
            if len(args) >= 3:
                parent_inner_puzzle = args[2]
                return LineageProof(
                    parent_name=parent_record.coin.parent_coin_info,
                    inner_puzzle_hash=parent_inner_puzzle.get_tree_hash(),
                    amount=parent_record.coin.amount,
                )
        except Exception:
            pass

        return None

    # ----------------------------------------------------------
    # Parsing / Identifying CAT Coins
    # ----------------------------------------------------------

    @staticmethod
    def parse_cat_puzzle(puzzle_reveal: Program) -> Optional[Tuple[bytes32, Program]]:
        """
        Parse a puzzle reveal to determine if it is a CAT.

        If the puzzle is a CAT, extract the asset_id and inner puzzle.
        If not, return None.

        This is useful when scanning the blockchain for CAT activity.

        Parameters:
            puzzle_reveal: A puzzle Program from a coin spend

        Returns:
            (asset_id, inner_puzzle) if CAT, None otherwise

        In production, prefer using:
            from chia.wallet.cat_wallet.cat_utils import match_cat_puzzle
            matched = match_cat_puzzle(puzzle_reveal)
        """
        try:
            mod, curried_args = puzzle_reveal.uncurry()
            args_list = list(curried_args.as_iter())

            # CAT puzzles have exactly 3 curried arguments
            if len(args_list) == 3:
                mod_hash_arg = args_list[0]
                tail_hash_arg = args_list[1]
                inner_puzzle = args_list[2]

                # The TAIL hash (asset_id) is the second curried argument
                asset_id_bytes = tail_hash_arg.atom
                if asset_id_bytes and len(asset_id_bytes) == 32:
                    return (bytes32(asset_id_bytes), inner_puzzle)
        except Exception:
            pass

        return None


# ============================================================
# Example Usage
# ============================================================

async def example_complete_workflow():
    """
    Demonstrates a complete CAT workflow from start to finish.

    This example shows:
    1. Compiling a TAIL and computing the asset ID
    2. Creating a CATDriver
    3. Building the CAT puzzle with an inner puzzle
    4. Finding CAT coins (commented out -- needs a running node)
    5. Building a transfer spend bundle
    """

    print("=" * 60)
    print("CAT Driver - Complete Workflow Example")
    print("=" * 60)

    # ----------------------------------------------------------
    # Step 1: Compile a TAIL and get the asset ID
    # ----------------------------------------------------------
    print("\n--- Step 1: Compile TAIL and compute asset ID ---")

    # A simplified single-issuance TAIL (educational version)
    # In production, use the official genesis_by_coin_id.clvm
    tail_source = """
    (mod (GENESIS_ID Truths parent_is_cat lineage_proof delta inner_conditions _)
      (if parent_is_cat (x) ())
    )
    """

    tail_program = compile_tail(tail_source)
    genesis_coin_id = bytes32(b'\xab' * 32)  # In real use: a coin you control

    curried_tail, asset_id = compute_asset_id(tail_program, [genesis_coin_id])
    print(f"  TAIL compiled successfully")
    print(f"  Asset ID: {asset_id.hex()}")

    # ----------------------------------------------------------
    # Step 2: Create the driver
    # ----------------------------------------------------------
    print("\n--- Step 2: Create CATDriver ---")

    driver = CATDriver(tail_hash=asset_id)
    print(f"  Driver created for asset: {asset_id.hex()[:16]}...")

    # ----------------------------------------------------------
    # Step 3: Build CAT puzzle with inner puzzle
    # ----------------------------------------------------------
    print("\n--- Step 3: Build CAT puzzle ---")

    # Generate a key pair (in practice, use your wallet's keys)
    import secrets
    seed = secrets.token_bytes(32)
    sk = AugSchemeMPL.key_gen(seed)
    pk = sk.get_g1()

    # The inner puzzle controls who can spend the CAT tokens.
    # For a standard wallet, this would be puzzle_for_pk(pk).
    # For this demo, we use a simple puzzle:
    inner_source = "(mod (conditions) conditions)"
    inner_compiled = compile_clvm_text(inner_source, [])
    inner_puzzle = Program.fromhex(inner_compiled)

    cat_puzzle = driver.get_cat_puzzle(inner_puzzle)
    cat_puzzle_hash = cat_puzzle.get_tree_hash()
    cat_address = driver.get_cat_address(inner_puzzle, prefix="txch")

    print(f"  Inner puzzle hash: {inner_puzzle.get_tree_hash().hex()[:16]}...")
    print(f"  CAT puzzle hash:   {cat_puzzle_hash.hex()[:16]}...")
    print(f"  CAT address:       {cat_address}")

    # ----------------------------------------------------------
    # Step 4: Demonstrate transfer spend construction
    # ----------------------------------------------------------
    print("\n--- Step 4: Build a transfer spend ---")

    # Create a fake coin for demonstration
    fake_cat_coin = Coin(
        parent_coin_info=bytes32(b'\x01' * 32),
        puzzle_hash=cat_puzzle_hash,
        amount=uint64(1000),
    )

    # Create a fake lineage proof
    lineage = LineageProof(
        parent_name=bytes32(b'\x00' * 32),
        inner_puzzle_hash=inner_puzzle.get_tree_hash(),
        amount=uint64(1000),
    )

    # Build the transfer: send 600 tokens, keep 400 as change
    recipient_inner_ph = bytes32(b'\x03' * 32)
    transfer_amount = uint64(600)

    spendable = driver.create_transfer_spend(
        coin=fake_cat_coin,
        sender_inner_puzzle=inner_puzzle,
        receiver_inner_puzzle_hash=recipient_inner_ph,
        amount=transfer_amount,
        lineage_proof=lineage,
    )

    print(f"  Transfer: {transfer_amount} tokens to {recipient_inner_ph.hex()[:16]}...")
    print(f"  Change:   {fake_cat_coin.amount - transfer_amount} tokens back to sender")
    print(f"  SpendableCAT created successfully")

    # Build the spend bundle
    spend_bundle = driver.build_spend_bundle([spendable])
    print(f"  SpendBundle built with {len(spend_bundle.coin_spends)} coin spend(s)")
    print(f"  Serialized size: {len(bytes(spend_bundle))} bytes")

    # ----------------------------------------------------------
    # Step 5: Show how to connect to a real node
    # ----------------------------------------------------------
    print("\n--- Step 5: Connecting to a real node (reference) ---")
    print("""
    # To find real CAT coins and push transactions:

    config = load_config(DEFAULT_ROOT_PATH, "config.yaml")
    rpc_port = config["full_node"]["rpc_port"]

    client = await FullNodeRpcClient.create(
        "localhost", uint16(rpc_port), DEFAULT_ROOT_PATH, config
    )

    try:
        # Find CAT coins
        coins = await CATDriver.find_cat_coins(client, cat_puzzle_hash)
        print(f"Found {len(coins)} CAT coins")

        # Get lineage proof for a coin
        if coins:
            lineage = await CATDriver.get_lineage_proof(client, coins[0])

        # Push a spend bundle
        # result = await client.push_tx(signed_spend_bundle)
    finally:
        client.close()
        await client.await_closed()
    """)

    # ----------------------------------------------------------
    # Summary
    # ----------------------------------------------------------
    print("--- Summary ---")
    print(f"  Asset ID:       {asset_id.hex()[:32]}...")
    print(f"  CAT puzzle hash: {cat_puzzle_hash.hex()[:32]}...")
    print(f"  CAT address:     {cat_address}")
    print()
    print("  Key classes used:")
    print("    CATDriver        - Main driver for CAT operations")
    print("    SpendableCAT     - Packages all info for a CAT spend")
    print("    LineageProof     - Proves a coin's CAT lineage")
    print("    construct_cat_puzzle - Builds the full CAT puzzle")
    print("    unsigned_spend_bundle_for_spendable_cats - Builds the ring")
    print()
    print("  For standard operations (send/receive), use the wallet RPC:")
    print("    chia wallet add_token -id <asset_id> -n 'MyToken'")
    print("    chia wallet send -i <wallet_id> -a <amount> -t <address>")


if __name__ == "__main__":
    asyncio.run(example_complete_workflow())
