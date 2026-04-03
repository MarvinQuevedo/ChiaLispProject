"""
create_coin_driver.py
=====================
A COMPLETE driver that demonstrates the full lifecycle of a coin:
  1. Compile a ChiaLisp puzzle
  2. Curry parameters into it
  3. Derive the address
  4. Find coins at that address on the blockchain
  5. Build a spend bundle
  6. Push it to the mempool (commented out for safety)

This is the most important example in this chapter.
Read every comment carefully.

Requirements:
    pip install chia-dev-tools
    A running Chia node (full_node) synced to testnet or mainnet

Usage:
    python create_coin_driver.py
"""

import asyncio
from typing import Optional

# --- Chia type imports ---
# These are the core data structures you will use in every driver.
from chia.types.blockchain_format.program import Program
from chia.types.blockchain_format.coin import Coin
from chia.types.blockchain_format.sized_bytes import bytes32
from chia.types.spend_bundle import SpendBundle
from chia.types.coin_spend import CoinSpend
from chia.util.ints import uint64, uint16

# --- RPC imports ---
# For talking to the Chia full node
from chia.rpc.full_node_rpc_client import FullNodeRpcClient
from chia.util.config import load_config
from chia.util.default_root_path import DEFAULT_ROOT_PATH

# --- Address encoding ---
from chia.util.bech32m import encode_puzzle_hash, decode_puzzle_hash

# --- BLS signatures ---
# Even if our puzzle does not need signing, we need G2Element for the
# empty signature in the SpendBundle.
from blspy import G2Element

# --- Compiler ---
from clvm_tools_rs import compile_clvm_text


# =============================================================================
# THE PUZZLE
# =============================================================================
#
# This is a simple "password + destination" puzzle.
# To spend a coin locked by this puzzle, you must provide:
#   1. The correct password (curried in as a hash for security)
#   2. A recipient puzzle hash (where to send the coins)
#   3. The amount to send
#
# In a real application, you would NEVER use a simple password puzzle
# because anyone who sees the spend on-chain can read the password from
# the solution. This is for educational purposes only.

PUZZLE_SOURCE = """
; password_coin.clsp
; A coin locked by a password hash.
;
; Curried parameters:
;   PASSWORD_HASH - the sha256 hash of the password
;
; Solution parameters:
;   password              - the actual password (will be hashed and compared)
;   recipient_puzzle_hash - where to send the coins
;   amount                - how much to send

(mod (PASSWORD_HASH password recipient_puzzle_hash amount)
  ; Verify the password
  (if (= (sha256 password) PASSWORD_HASH)
    ; Password correct -- create a new coin at the recipient's puzzle hash
    (list
      (list 51 recipient_puzzle_hash amount)   ; CREATE_COIN condition
    )
    ; Password wrong -- fail
    (x "wrong password")
  )
)
"""


# =============================================================================
# DRIVER CLASS
# =============================================================================

class PasswordCoinDriver:
    """
    A driver for the password-locked coin puzzle.

    This class encapsulates all the logic needed to:
    - Create (lock) a password coin
    - Find it on the blockchain
    - Spend (unlock) it
    """

    def __init__(self, password: str, network_prefix: str = "txch"):
        """
        Initialize the driver.

        Parameters:
            password:       The password string that locks/unlocks the coin
            network_prefix: "txch" for testnet, "xch" for mainnet
        """
        self.password = password
        self.network_prefix = network_prefix
        self.client: Optional[FullNodeRpcClient] = None

        # ---------------------------------------------------------------
        # STEP 1: Compile the puzzle
        # ---------------------------------------------------------------
        # compile_clvm_text takes the source code and a list of include paths.
        # It returns a hex string of the compiled CLVM bytecode.
        print("[Step 1] Compiling puzzle...")
        compiled_hex = compile_clvm_text(PUZZLE_SOURCE, [])
        self.base_puzzle = Program.fromhex(compiled_hex)
        print(f"  Base puzzle compiled. Hash: {self.base_puzzle.get_tree_hash().hex()[:16]}...")

        # ---------------------------------------------------------------
        # STEP 2: Curry the password hash into the puzzle
        # ---------------------------------------------------------------
        # We do NOT curry the raw password -- that would be visible on-chain.
        # Instead, we curry the HASH of the password. The spender must provide
        # the actual password in the solution, and the puzzle hashes it and
        # compares to the curried hash.
        print("[Step 2] Currying password hash...")
        self.password_hash = Program.to(password.encode()).get_tree_hash()
        # Actually, for our puzzle we use sha256 inside CLVM.
        # Let's compute it the same way CLVM would:
        import hashlib
        self.password_hash = hashlib.sha256(password.encode()).digest()

        self.curried_puzzle = self.base_puzzle.curry(
            Program.to(self.password_hash)
        )
        print(f"  Password hash: {self.password_hash.hex()[:16]}...")
        print(f"  Curried puzzle hash: {self.curried_puzzle.get_tree_hash().hex()[:16]}...")

        # ---------------------------------------------------------------
        # STEP 3: Derive the address
        # ---------------------------------------------------------------
        self.puzzle_hash = self.curried_puzzle.get_tree_hash()
        self.address = encode_puzzle_hash(self.puzzle_hash, self.network_prefix)
        print(f"[Step 3] Address: {self.address}")

    # -------------------------------------------------------------------
    # RPC CONNECTION
    # -------------------------------------------------------------------

    async def connect(self):
        """Connect to the local Chia full node via RPC."""
        print("\n[RPC] Connecting to full node...")
        config = load_config(DEFAULT_ROOT_PATH, "config.yaml")
        rpc_port = config["full_node"]["rpc_port"]

        self.client = await FullNodeRpcClient.create(
            "localhost",
            uint16(rpc_port),
            DEFAULT_ROOT_PATH,
            config
        )
        print("[RPC] Connected!")

    async def disconnect(self):
        """Close the RPC connection."""
        if self.client:
            self.client.close()
            await self.client.await_closed()
            print("[RPC] Disconnected.")

    # -------------------------------------------------------------------
    # STEP 4: FIND COINS
    # -------------------------------------------------------------------

    async def find_coins(self) -> list:
        """
        Find all unspent coins locked by our puzzle.

        Returns:
            A list of Coin objects
        """
        if not self.client:
            raise RuntimeError("Not connected. Call connect() first.")

        print(f"\n[Step 4] Searching for coins at puzzle hash: {self.puzzle_hash.hex()[:16]}...")

        # get_coin_records_by_puzzle_hash returns CoinRecord objects.
        # Each CoinRecord wraps a Coin and adds metadata (spent status, block height).
        coin_records = await self.client.get_coin_records_by_puzzle_hash(
            self.puzzle_hash,
            include_spent_coins=False  # We only want unspent coins
        )

        coins = []
        for record in coin_records:
            coin = record.coin
            coins.append(coin)
            print(f"  Found coin:")
            print(f"    ID:     {coin.name().hex()}")
            print(f"    Amount: {coin.amount} mojos ({coin.amount / 1_000_000_000_000:.12f} XCH)")
            print(f"    Parent: {coin.parent_coin_info.hex()[:16]}...")

        if not coins:
            print("  No coins found. You need to send XCH to this address first:")
            print(f"  {self.address}")
            print(f"  Use: chia wallet send -t {self.address} -a 0.001")

        return coins

    # -------------------------------------------------------------------
    # STEP 5-9: BUILD AND SUBMIT THE SPEND
    # -------------------------------------------------------------------

    def build_spend_bundle(
        self,
        coin: Coin,
        recipient_address: str,
        amount: Optional[int] = None
    ) -> SpendBundle:
        """
        Build a SpendBundle to spend a password-locked coin.

        Parameters:
            coin:              The Coin object to spend
            recipient_address: The bech32m address to send funds to (xch1... or txch1...)
            amount:            Amount in mojos to send (defaults to full coin amount)

        Returns:
            A SpendBundle ready to be pushed to the mempool
        """
        print(f"\n[Step 5] Building spend for coin {coin.name().hex()[:16]}...")

        # Default to sending the full amount
        if amount is None:
            amount = coin.amount

        # ---------------------------------------------------------------
        # STEP 5a: Decode the recipient address to a puzzle hash
        # ---------------------------------------------------------------
        # decode_puzzle_hash converts "txch1abc..." back to a bytes32 puzzle hash
        recipient_puzzle_hash = decode_puzzle_hash(recipient_address)
        print(f"  Recipient puzzle hash: {recipient_puzzle_hash.hex()[:16]}...")

        # ---------------------------------------------------------------
        # STEP 6: Build the solution
        # ---------------------------------------------------------------
        # Our puzzle expects: (password recipient_puzzle_hash amount)
        # Remember: PASSWORD_HASH is curried, so it's not in the solution.
        # The solution only contains the non-curried parameters.
        solution = Program.to([
            self.password.encode(),   # The actual password (bytes)
            recipient_puzzle_hash,     # Where to send the coins
            amount                     # How much to send
        ])
        print(f"  Solution built.")

        # ---------------------------------------------------------------
        # STEP 6b: TEST LOCALLY before sending to blockchain
        # ---------------------------------------------------------------
        # This is CRUCIAL. If the puzzle fails locally, it will also fail
        # on-chain, and you might lose your transaction fee.
        print(f"  Testing locally...")
        try:
            result = self.curried_puzzle.run(solution)
            print(f"  Local test passed! Output conditions:")
            for condition in result.as_iter():
                parts = list(condition.as_iter())
                code = parts[0].as_int()
                args = [p.as_python() for p in parts[1:]]
                print(f"    Condition code={code}, args={args}")
        except Exception as e:
            raise RuntimeError(f"Puzzle failed locally: {e}. Fix before sending!")

        # ---------------------------------------------------------------
        # STEP 7: Create the CoinSpend
        # ---------------------------------------------------------------
        # A CoinSpend ties together:
        #   - The coin being spent
        #   - The puzzle reveal (the full puzzle program)
        #   - The solution (the arguments to the puzzle)
        coin_spend = CoinSpend(
            coin,                   # The coin to spend
            self.curried_puzzle,    # The full curried puzzle (puzzle reveal)
            solution                # The solution
        )
        print(f"  CoinSpend created.")

        # ---------------------------------------------------------------
        # STEP 8: Handle signing
        # ---------------------------------------------------------------
        # Our password puzzle does NOT use AGG_SIG conditions, so we do
        # not need a real signature. We use the "empty" G2 element.
        #
        # If your puzzle outputs AGG_SIG_ME or AGG_SIG_UNSAFE, you would
        # need to create a real BLS signature here. See signature_driver.py
        # for an example of that.
        signature = G2Element()
        print(f"  No signature needed (no AGG_SIG conditions).")

        # ---------------------------------------------------------------
        # STEP 9: Create the SpendBundle
        # ---------------------------------------------------------------
        # The SpendBundle packages everything together:
        #   - A list of CoinSpend objects (we have just one)
        #   - An aggregated BLS signature
        spend_bundle = SpendBundle(
            [coin_spend],   # List of coin spends
            signature       # Aggregated signature (empty in our case)
        )
        print(f"  SpendBundle created!")
        print(f"  Bundle ID: {spend_bundle.name().hex()[:16]}...")

        return spend_bundle

    # -------------------------------------------------------------------
    # STEP 10: PUSH TO MEMPOOL
    # -------------------------------------------------------------------

    async def push_spend(self, spend_bundle: SpendBundle) -> dict:
        """
        Push a SpendBundle to the mempool.

        Parameters:
            spend_bundle: The SpendBundle to submit

        Returns:
            The response from the full node
        """
        if not self.client:
            raise RuntimeError("Not connected. Call connect() first.")

        print(f"\n[Step 10] Pushing spend bundle to mempool...")
        result = await self.client.push_tx(spend_bundle)
        print(f"  Result: {result}")
        return result


# =============================================================================
# MAIN - Putting it all together
# =============================================================================

async def main():
    """
    Full demonstration of the password coin driver.

    To actually run this on testnet:
    1. Change the password to something unique
    2. Run the script once to get the address
    3. Send some TXCH to that address
    4. Run the script again to find and spend the coin
    """

    print("=" * 60)
    print("Password Coin Driver - Complete Example")
    print("=" * 60)

    # --- Initialize the driver ---
    # This compiles the puzzle, curries the password, and derives the address.
    driver = PasswordCoinDriver(
        password="my_secret_password_123",
        network_prefix="txch"  # Use "xch" for mainnet
    )

    print(f"\n{'='*60}")
    print(f"SUMMARY")
    print(f"{'='*60}")
    print(f"Address:     {driver.address}")
    print(f"Puzzle hash: {driver.puzzle_hash.hex()}")
    print(f"")
    print(f"To create a coin, send TXCH to the address above.")
    print(f"To spend it, this driver will provide the password in the solution.")

    # --- Connect to the node and find coins ---
    # Uncomment the block below when you have a running Chia node

    """
    try:
        await driver.connect()

        # Find coins at our puzzle's address
        coins = await driver.find_coins()

        if coins:
            # Take the first coin and spend it
            coin = coins[0]

            # Build the spend bundle
            # Replace with a real recipient address!
            recipient = "txch1your_recipient_address_here"
            spend_bundle = driver.build_spend_bundle(
                coin=coin,
                recipient_address=recipient,
                amount=coin.amount  # Send the full amount
            )

            # Push it!
            # WARNING: This will actually spend the coin. Uncomment only
            # when you are sure everything is correct.
            # result = await driver.push_spend(spend_bundle)
            # print(f"Transaction result: {result}")

    finally:
        await driver.disconnect()
    """

    # --- Demonstrate the spend bundle construction offline ---
    print(f"\n{'='*60}")
    print(f"OFFLINE DEMO (no node needed)")
    print(f"{'='*60}")

    # Create a fake coin for demonstration purposes
    # In real usage, you would get this from the blockchain (Step 4 above)
    fake_parent = bytes32(b'\x01' * 32)
    fake_coin = Coin(
        parent_coin_info=fake_parent,
        puzzle_hash=driver.puzzle_hash,
        amount=uint64(1_000_000_000)  # 0.001 XCH in mojos
    )
    print(f"\nFake coin for demo:")
    print(f"  ID:     {fake_coin.name().hex()}")
    print(f"  Amount: {fake_coin.amount} mojos")

    # Build a spend bundle (this works offline, no node needed)
    # We need a valid recipient address - let's use a dummy one
    # In practice, use a real address!
    dummy_puzzle_hash = bytes32(b'\xab' * 32)
    dummy_address = encode_puzzle_hash(dummy_puzzle_hash, "txch")

    spend_bundle = driver.build_spend_bundle(
        coin=fake_coin,
        recipient_address=dummy_address,
        amount=fake_coin.amount
    )

    print(f"\nSpend bundle summary:")
    print(f"  Number of coin spends: {len(spend_bundle.coin_spends)}")
    print(f"  Aggregated signature:  {'(empty)' if spend_bundle.aggregated_signature == G2Element() else '(present)'}")

    # Show the serialized spend bundle (this is what gets sent to the node)
    serialized = bytes(spend_bundle)
    print(f"  Serialized size: {len(serialized)} bytes")


if __name__ == "__main__":
    asyncio.run(main())
