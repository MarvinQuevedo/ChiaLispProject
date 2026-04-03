"""
signature_driver.py
===================
A complete driver for a signature-locked coin.

This is the most realistic example because nearly all real Chia puzzles
use BLS signatures for security. The standard Chia wallet itself is
just a signature-locked coin.

This example covers:
  1. BLS key generation (private key -> public key)
  2. Creating a puzzle that requires a signature
  3. Currying the public key into the puzzle
  4. Building a solution with conditions
  5. Signing the spend (AGG_SIG_ME)
  6. Creating and verifying the SpendBundle

Requirements:
    pip install chia-dev-tools

Usage:
    python signature_driver.py
"""

import secrets
import asyncio
from typing import Optional

# --- Chia type imports ---
from chia.types.blockchain_format.program import Program
from chia.types.blockchain_format.coin import Coin
from chia.types.blockchain_format.sized_bytes import bytes32
from chia.types.spend_bundle import SpendBundle
from chia.types.coin_spend import CoinSpend
from chia.util.ints import uint64, uint16

# --- RPC imports ---
from chia.rpc.full_node_rpc_client import FullNodeRpcClient
from chia.util.config import load_config
from chia.util.default_root_path import DEFAULT_ROOT_PATH

# --- Address encoding ---
from chia.util.bech32m import encode_puzzle_hash

# --- BLS signature imports ---
# These are the core cryptographic primitives for Chia signatures
from blspy import (
    AugSchemeMPL,   # The signing scheme Chia uses (Augmented, Minimal PK Length)
    PrivateKey,      # A BLS12-381 private key (32 bytes)
    G1Element,       # A BLS12-381 public key (48 bytes)
    G2Element,       # A BLS12-381 signature (96 bytes)
)

# --- Compiler ---
from clvm_tools_rs import compile_clvm_text


# =============================================================================
# GENESIS CHALLENGE CONSTANTS
# =============================================================================
# The genesis challenge is appended to AGG_SIG_ME messages to prevent
# cross-network replay attacks. Each network has a different one.

MAINNET_GENESIS_CHALLENGE = bytes.fromhex(
    "ccd5bb71183532bff220ba46c268991a3ff07eb358e8255a65c30a2dce0e5fbb"
)

TESTNET11_GENESIS_CHALLENGE = bytes.fromhex(
    "37a90eb5185a9c4439a91ddc98bbadce7b4feba060d50116a067de66bf236615"
)


# =============================================================================
# THE PUZZLE
# =============================================================================
# This puzzle implements a "pay to delegated puzzle" pattern:
#
# 1. A PUBLIC_KEY is curried in at puzzle creation time
# 2. At spend time, the spender provides:
#    - A "delegated puzzle" (any puzzle they want to run)
#    - A solution for that delegated puzzle
# 3. The puzzle runs the delegated puzzle to get conditions
# 4. It adds an AGG_SIG_ME condition requiring a signature from PUBLIC_KEY
#    on the hash of the delegated puzzle
#
# This means: whoever holds the private key for PUBLIC_KEY controls the coin.
# They can make the coin do anything (any conditions) as long as they sign it.
#
# This is a simplified version of how the standard Chia wallet puzzle works.

SIGNATURE_PUZZLE_SOURCE = """
; sig_locked.clsp
; A coin that can only be spent by the holder of a specific private key.
;
; Curried parameters:
;   PUBLIC_KEY - the BLS public key (G1Element, 48 bytes)
;
; Solution parameters:
;   delegated_puzzle  - a puzzle that returns the desired conditions
;   delegated_solution - the solution for the delegated puzzle

(mod (PUBLIC_KEY delegated_puzzle delegated_solution)

  ; Import the sha256tree operator (hashes a CLVM tree)
  (include condition_codes.clib)

  (defun sha256tree (TREE)
    (if (l TREE)
      (sha256 2 (sha256tree (f TREE)) (sha256tree (r TREE)))
      (sha256 1 TREE)
    )
  )

  ; Run the delegated puzzle to get the desired conditions
  (c
    ; First condition: AGG_SIG_ME requiring signature on delegated_puzzle hash
    ; Condition code 50 = AGG_SIG_ME
    ; This means: "A signature from PUBLIC_KEY on sha256tree(delegated_puzzle)
    ;              must be included in the SpendBundle"
    (list 50 PUBLIC_KEY (sha256tree delegated_puzzle))

    ; Remaining conditions: whatever the delegated puzzle returns
    (a delegated_puzzle delegated_solution)
  )
)
"""

# Since the include might not be available, here is a self-contained version:
SIGNATURE_PUZZLE_SELF_CONTAINED = """
; sig_locked_standalone.clsp
; Self-contained version (no includes needed)

(mod (PUBLIC_KEY delegated_puzzle delegated_solution)

  ; sha256tree: hash a CLVM tree structure
  ; This is how we get a unique hash for any CLVM program.
  ; Pairs are hashed as: sha256(2, sha256tree(left), sha256tree(right))
  ; Atoms are hashed as: sha256(1, atom)
  (defun sha256tree (TREE)
    (if (l TREE)
      (sha256 2 (sha256tree (f TREE)) (sha256tree (r TREE)))
      (sha256 1 TREE)
    )
  )

  ; Run the delegated puzzle and prepend the AGG_SIG_ME condition
  (c
    (list 50 PUBLIC_KEY (sha256tree delegated_puzzle))
    (a delegated_puzzle delegated_solution)
  )
)
"""


# =============================================================================
# BLS KEY MANAGEMENT
# =============================================================================

class BLSKeyPair:
    """
    A BLS12-381 key pair (private key + public key).

    BLS keys in Chia:
    - Private key: 32 bytes, used for signing
    - Public key (G1Element): 48 bytes, used for verification and currying
    - Signature (G2Element): 96 bytes, proves knowledge of private key
    """

    def __init__(self, private_key: PrivateKey):
        self.private_key = private_key
        self.public_key = private_key.get_g1()  # Derive the G1 public key

    @classmethod
    def generate(cls) -> "BLSKeyPair":
        """
        Generate a new random key pair.

        In production, you would derive keys from a mnemonic (seed phrase)
        using the HD key derivation scheme. For testing/learning, random
        generation is fine.
        """
        # Generate 32 bytes of cryptographically secure randomness
        seed = secrets.token_bytes(32)

        # Derive a BLS private key from the seed
        private_key = AugSchemeMPL.key_gen(seed)

        return cls(private_key)

    @classmethod
    def from_bytes(cls, private_key_bytes: bytes) -> "BLSKeyPair":
        """Reconstruct a key pair from private key bytes."""
        private_key = PrivateKey.from_bytes(private_key_bytes)
        return cls(private_key)

    def sign(self, message: bytes) -> G2Element:
        """
        Sign a message with the private key.

        IMPORTANT: For AGG_SIG_ME, the message you pass here should be:
            message_data + coin_id + genesis_challenge

        The full node verifies:
            AugSchemeMPL.verify(public_key, message, signature)

        With AugSchemeMPL, the verify function internally prepends the
        public key to the message before verification. This means the
        sign function also internally prepends the public key. You do NOT
        need to manually prepend it.
        """
        return AugSchemeMPL.sign(self.private_key, message)

    def verify(self, message: bytes, signature: G2Element) -> bool:
        """Verify a signature."""
        return AugSchemeMPL.verify(self.public_key, message, signature)

    def __str__(self):
        return (
            f"BLSKeyPair(\n"
            f"  private_key: {bytes(self.private_key).hex()[:32]}...\n"
            f"  public_key:  {bytes(self.public_key).hex()[:32]}...\n"
            f")"
        )


# =============================================================================
# SIGNATURE COIN DRIVER
# =============================================================================

class SignatureCoinDriver:
    """
    Driver for the signature-locked coin puzzle.

    This driver handles the complete lifecycle:
    - Key management
    - Puzzle compilation and currying
    - Spend bundle construction with proper signing
    """

    def __init__(
        self,
        key_pair: BLSKeyPair,
        network: str = "testnet",  # "testnet" or "mainnet"
    ):
        self.key_pair = key_pair
        self.network = network
        self.client: Optional[FullNodeRpcClient] = None

        # Select genesis challenge based on network
        if network == "mainnet":
            self.genesis_challenge = MAINNET_GENESIS_CHALLENGE
            self.address_prefix = "xch"
        else:
            self.genesis_challenge = TESTNET11_GENESIS_CHALLENGE
            self.address_prefix = "txch"

        # --- Compile the puzzle ---
        print("[Init] Compiling signature puzzle...")
        compiled_hex = compile_clvm_text(SIGNATURE_PUZZLE_SELF_CONTAINED, [])
        self.base_puzzle = Program.fromhex(compiled_hex)

        # --- Curry in the public key ---
        print("[Init] Currying public key...")
        self.curried_puzzle = self.base_puzzle.curry(
            Program.to(bytes(self.key_pair.public_key))
        )

        # --- Derive address ---
        self.puzzle_hash = self.curried_puzzle.get_tree_hash()
        self.address = encode_puzzle_hash(self.puzzle_hash, self.address_prefix)

        print(f"[Init] Puzzle hash: {self.puzzle_hash.hex()[:32]}...")
        print(f"[Init] Address: {self.address}")

    def build_spend_bundle(
        self,
        coin: Coin,
        conditions: list,
    ) -> SpendBundle:
        """
        Build a signed SpendBundle for the given coin.

        Parameters:
            coin:       The Coin to spend
            conditions: A list of conditions to output, e.g.:
                        [[51, recipient_ph, amount], [52, fee]]

        Returns:
            A signed SpendBundle

        The process:
        1. Create a "delegated puzzle" that returns the desired conditions
        2. Hash the delegated puzzle (this is what we sign)
        3. Build the AGG_SIG_ME message: hash + coin_id + genesis_challenge
        4. Sign it with our private key
        5. Package everything into a SpendBundle
        """
        print(f"\n[Spend] Building spend for coin {coin.name().hex()[:16]}...")

        # ---------------------------------------------------------------
        # STEP 1: Create the delegated puzzle
        # ---------------------------------------------------------------
        # The delegated puzzle is a simple "quote" -- it just returns
        # the conditions we want. In CLVM, (q . X) always returns X.
        #
        # (1 . conditions_list) is the compiled form of (q . conditions_list)
        #
        # This is the standard pattern: the delegated puzzle is (q . conditions)
        # and the delegated solution is ignored (we pass 0).

        conditions_program = Program.to(conditions)
        delegated_puzzle = Program.to((1, conditions))  # (q . conditions)
        delegated_solution = Program.to(0)  # Not used by a quote

        print(f"  Delegated puzzle: (q . <{len(conditions)} conditions>)")

        # ---------------------------------------------------------------
        # STEP 2: Build the solution
        # ---------------------------------------------------------------
        # Our puzzle expects: (delegated_puzzle, delegated_solution)
        # (PUBLIC_KEY is curried, so it's not in the solution)
        solution = Program.to([
            delegated_puzzle,
            delegated_solution,
        ])

        # ---------------------------------------------------------------
        # STEP 3: Test locally
        # ---------------------------------------------------------------
        print(f"  Testing puzzle execution locally...")
        try:
            result = self.curried_puzzle.run(solution)
            print(f"  Local test passed! Conditions produced:")
            for cond in result.as_iter():
                parts = list(cond.as_iter())
                code = parts[0].as_int()
                print(f"    Code {code}: {[p.as_python() for p in parts[1:]]}")
        except Exception as e:
            raise RuntimeError(f"  Puzzle failed locally: {e}")

        # ---------------------------------------------------------------
        # STEP 4: Compute the message to sign
        # ---------------------------------------------------------------
        # For AGG_SIG_ME (condition 50), we need to sign:
        #   sha256tree(delegated_puzzle) + coin_id + genesis_challenge
        #
        # The puzzle outputs: (50 PUBLIC_KEY sha256tree(delegated_puzzle))
        # The blockchain verifies: AugSchemeMPL.verify(
        #     PUBLIC_KEY,
        #     sha256tree(delegated_puzzle) + coin_id + genesis_challenge,
        #     signature
        # )

        # Compute sha256tree of the delegated puzzle
        # Program.get_tree_hash() does exactly this
        delegated_puzzle_hash = delegated_puzzle.get_tree_hash()

        coin_id = coin.name()

        # The full message for AGG_SIG_ME
        message = delegated_puzzle_hash + coin_id + self.genesis_challenge

        print(f"  Message to sign:")
        print(f"    delegated_puzzle_hash: {delegated_puzzle_hash.hex()[:16]}...")
        print(f"    coin_id:              {coin_id.hex()[:16]}...")
        print(f"    genesis_challenge:    {self.genesis_challenge.hex()[:16]}...")
        print(f"    total message length: {len(message)} bytes")

        # ---------------------------------------------------------------
        # STEP 5: Sign the message
        # ---------------------------------------------------------------
        signature = self.key_pair.sign(message)
        print(f"  Signature: {bytes(signature).hex()[:32]}...")

        # Verify the signature locally (belt and suspenders)
        is_valid = self.key_pair.verify(message, signature)
        print(f"  Signature valid: {is_valid}")
        if not is_valid:
            raise RuntimeError("Signature verification failed!")

        # ---------------------------------------------------------------
        # STEP 6: Create the CoinSpend and SpendBundle
        # ---------------------------------------------------------------
        coin_spend = CoinSpend(
            coin,
            self.curried_puzzle,  # Full puzzle reveal
            solution
        )

        spend_bundle = SpendBundle(
            [coin_spend],
            signature  # The BLS signature (G2Element)
        )

        print(f"  SpendBundle created!")
        return spend_bundle

    async def connect(self):
        """Connect to the full node."""
        config = load_config(DEFAULT_ROOT_PATH, "config.yaml")
        rpc_port = config["full_node"]["rpc_port"]
        self.client = await FullNodeRpcClient.create(
            "localhost", uint16(rpc_port), DEFAULT_ROOT_PATH, config
        )

    async def disconnect(self):
        """Disconnect from the full node."""
        if self.client:
            self.client.close()
            await self.client.await_closed()

    async def find_coins(self) -> list:
        """Find unspent coins at our address."""
        records = await self.client.get_coin_records_by_puzzle_hash(
            self.puzzle_hash, include_spent_coins=False
        )
        return [r.coin for r in records]


# =============================================================================
# DEMONSTRATION
# =============================================================================

def main():
    print("=" * 60)
    print("Signature-Locked Coin Driver")
    print("=" * 60)

    # ---------------------------------------------------------------
    # 1. Generate a key pair
    # ---------------------------------------------------------------
    print("\n--- Key Generation ---")
    key_pair = BLSKeyPair.generate()
    print(key_pair)

    # You can also recreate from bytes:
    # saved_sk_bytes = bytes(key_pair.private_key)
    # key_pair = BLSKeyPair.from_bytes(saved_sk_bytes)

    # ---------------------------------------------------------------
    # 2. Initialize the driver
    # ---------------------------------------------------------------
    print("\n--- Driver Initialization ---")
    driver = SignatureCoinDriver(key_pair, network="testnet")

    # ---------------------------------------------------------------
    # 3. Create a fake coin for offline demo
    # ---------------------------------------------------------------
    print("\n--- Offline Spend Demo ---")
    fake_coin = Coin(
        parent_coin_info=bytes32(b'\x01' * 32),
        puzzle_hash=driver.puzzle_hash,
        amount=uint64(1_000_000_000_000)  # 1 XCH
    )
    print(f"Demo coin ID: {fake_coin.name().hex()}")
    print(f"Demo coin amount: {fake_coin.amount} mojos (1 XCH)")

    # ---------------------------------------------------------------
    # 4. Build a spend that sends 0.5 XCH somewhere and keeps 0.5 XCH as change
    # ---------------------------------------------------------------
    recipient_ph = bytes32(b'\xab' * 32)
    send_amount = 500_000_000_000     # 0.5 XCH
    fee = 50_000_000                   # 0.00005 XCH
    change_amount = fake_coin.amount - send_amount - fee

    conditions = [
        [51, recipient_ph, send_amount],          # CREATE_COIN to recipient
        [51, driver.puzzle_hash, change_amount],   # CREATE_COIN change back to us
        [52, fee],                                  # RESERVE_FEE
    ]

    print(f"\nConditions:")
    print(f"  Send {send_amount} mojos to recipient")
    print(f"  Change {change_amount} mojos back to self")
    print(f"  Fee: {fee} mojos")
    print(f"  Total: {send_amount + change_amount + fee} = {fake_coin.amount} (balanced)")

    spend_bundle = driver.build_spend_bundle(fake_coin, conditions)

    # ---------------------------------------------------------------
    # 5. Show the result
    # ---------------------------------------------------------------
    print(f"\n--- Result ---")
    print(f"SpendBundle created successfully!")
    print(f"  Coin spends: {len(spend_bundle.coin_spends)}")
    print(f"  Has signature: {spend_bundle.aggregated_signature != G2Element()}")
    print(f"  Serialized size: {len(bytes(spend_bundle))} bytes")

    # ---------------------------------------------------------------
    # 6. Demonstrate signature aggregation (multiple spends)
    # ---------------------------------------------------------------
    print(f"\n--- Signature Aggregation Demo ---")
    print("If you had multiple coins to spend in one bundle:")

    # Generate a second key pair
    key_pair_2 = BLSKeyPair.generate()
    driver_2 = SignatureCoinDriver(key_pair_2, network="testnet")

    fake_coin_2 = Coin(
        parent_coin_info=bytes32(b'\x02' * 32),
        puzzle_hash=driver_2.puzzle_hash,
        amount=uint64(500_000_000_000)
    )

    # Build individual spends
    sb1 = driver.build_spend_bundle(fake_coin, conditions)
    sb2 = driver_2.build_spend_bundle(
        fake_coin_2,
        [[51, recipient_ph, fake_coin_2.amount]]
    )

    # Aggregate the signatures
    agg_sig = AugSchemeMPL.aggregate([
        sb1.aggregated_signature,
        sb2.aggregated_signature,
    ])

    # Create the combined SpendBundle
    combined_bundle = SpendBundle(
        sb1.coin_spends + sb2.coin_spends,  # All coin spends
        agg_sig                                # One aggregated signature
    )

    print(f"\nCombined SpendBundle:")
    print(f"  Coin spends: {len(combined_bundle.coin_spends)}")
    print(f"  Aggregated signature: {bytes(combined_bundle.aggregated_signature).hex()[:32]}...")
    print(f"  Serialized size: {len(bytes(combined_bundle))} bytes")

    print("\n" + "=" * 60)
    print("Demo complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
