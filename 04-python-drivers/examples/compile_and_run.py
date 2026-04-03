"""
compile_and_run.py
==================
Demonstrates how to compile a ChiaLisp puzzle from source,
run it locally with different solutions, and inspect the output.

This is the first thing you should learn -- running puzzles locally
lets you test everything before touching the blockchain.

Requirements:
    pip install chia-dev-tools

Usage:
    python compile_and_run.py
"""

from clvm_tools_rs import compile_clvm_text
from chia.types.blockchain_format.program import Program


# =============================================================================
# STEP 1: Define some ChiaLisp puzzles as source strings
# =============================================================================

# A simple puzzle that adds two numbers
ADD_PUZZLE = """
(mod (A B)
  (+ A B)
)
"""

# A password-locked puzzle that outputs CREATE_COIN conditions
PASSWORD_PUZZLE = """
(mod (PASSWORD RECIPIENT_PUZZLE_HASH AMOUNT)
  ; The password is 0xcafef00d
  ; If correct, create a new coin at RECIPIENT_PUZZLE_HASH with AMOUNT
  (if (= PASSWORD 0xcafef00d)
    (list
      (list 51 RECIPIENT_PUZZLE_HASH AMOUNT)   ; condition 51 = CREATE_COIN
    )
    (x)  ; raise an error if password is wrong
  )
)
"""

# A puzzle that uses conditions to assert things
CONDITIONAL_PUZZLE = """
(mod (MY_AMOUNT RECIPIENT_PH SEND_AMOUNT)
  ; Produce multiple conditions
  (list
    (list 51 RECIPIENT_PH SEND_AMOUNT)                  ; CREATE_COIN
    (list 51 RECIPIENT_PH (* SEND_AMOUNT 2))            ; Another CREATE_COIN (double)
    (list 73 MY_AMOUNT)                                   ; ASSERT_MY_AMOUNT
  )
)
"""


# =============================================================================
# STEP 2: Compile each puzzle
# =============================================================================

def compile_puzzle(source: str, name: str) -> Program:
    """
    Compile a ChiaLisp source string into a Program object.

    Parameters:
        source: The ChiaLisp source code as a string
        name:   A human-readable name for logging

    Returns:
        A Program object representing the compiled CLVM bytecode
    """
    print(f"\n{'='*60}")
    print(f"Compiling: {name}")
    print(f"{'='*60}")

    # compile_clvm_text returns the compiled CLVM as a hex string.
    # The second argument is a list of search paths for include files.
    # If your puzzle uses (include ...), those files need to be in one
    # of these directories.
    compiled_hex = compile_clvm_text(source, [])

    print(f"Compiled hex: {compiled_hex[:80]}...")

    # Convert the hex string to a Program object
    puzzle = Program.fromhex(compiled_hex)

    # Show the puzzle hash -- this is what identifies this puzzle on-chain
    puzzle_hash = puzzle.get_tree_hash()
    print(f"Puzzle hash:  {puzzle_hash.hex()}")

    return puzzle


# =============================================================================
# STEP 3: Run puzzles with solutions and inspect output
# =============================================================================

def run_puzzle(puzzle: Program, solution_values: list, name: str):
    """
    Run a compiled puzzle with the given solution values.

    Parameters:
        puzzle:          A compiled Program object
        solution_values: A Python list that will be converted to a CLVM solution
        name:            A human-readable name for logging
    """
    print(f"\n--- Running {name} ---")
    print(f"Solution: {solution_values}")

    # Convert the Python list to a CLVM Program (the solution)
    solution = Program.to(solution_values)
    print(f"Solution (CLVM): {solution}")

    try:
        # Run the puzzle with the solution.
        # This is exactly what the blockchain does when validating a spend,
        # except the blockchain also checks the output conditions.
        result = puzzle.run(solution)

        print(f"Result: {result}")
        print(f"Result as Python: {result.as_python()}")

        # If the result is a list of conditions, parse them
        try:
            for i, condition in enumerate(result.as_iter()):
                # Each condition is a list: (condition_code, arg1, arg2, ...)
                parts = list(condition.as_iter())
                code = parts[0].as_int()
                print(f"  Condition {i}: code={code}, args={[p.as_python() for p in parts[1:]]}")
        except Exception:
            # Not a list of conditions, just a simple value
            pass

    except Exception as e:
        print(f"ERROR (expected if testing failure case): {e}")


# =============================================================================
# STEP 4: Demonstrate currying
# =============================================================================

def demonstrate_currying():
    """
    Show how currying works with a practical example.
    """
    print(f"\n{'='*60}")
    print(f"Demonstrating Currying")
    print(f"{'='*60}")

    # This puzzle takes a curried "multiplier" and a runtime "value"
    # It returns multiplier * value
    source = """
    (mod (MULTIPLIER value)
      ; MULTIPLIER is curried in at puzzle creation time
      ; value comes from the solution at spend time
      (* MULTIPLIER value)
    )
    """

    compiled_hex = compile_clvm_text(source, [])
    base_puzzle = Program.fromhex(compiled_hex)

    print(f"\nBase puzzle hash: {base_puzzle.get_tree_hash().hex()}")

    # Curry in MULTIPLIER = 10
    curried_puzzle_10 = base_puzzle.curry(Program.to(10))
    print(f"Curried (x10) puzzle hash: {curried_puzzle_10.get_tree_hash().hex()}")

    # Curry in MULTIPLIER = 100
    curried_puzzle_100 = base_puzzle.curry(Program.to(100))
    print(f"Curried (x100) puzzle hash: {curried_puzzle_100.get_tree_hash().hex()}")

    # Notice: each curried version has a DIFFERENT puzzle hash.
    # They are essentially different puzzles from the blockchain's perspective.

    # Run the curried puzzles
    # When running a curried puzzle, the solution only needs the NON-curried args
    solution = Program.to([5])  # value = 5

    result_10 = curried_puzzle_10.run(solution)
    print(f"\n10 * 5 = {result_10}")  # Should be 50

    result_100 = curried_puzzle_100.run(solution)
    print(f"100 * 5 = {result_100}")  # Should be 500

    # Uncurry to recover the original puzzle and curried arguments
    mod, args = curried_puzzle_10.uncurry()
    print(f"\nUncurried mod hash: {mod.get_tree_hash().hex()}")
    print(f"Uncurried args: {list(args.as_iter())}")
    # The mod hash should match the base puzzle hash
    print(f"Matches base puzzle: {mod.get_tree_hash() == base_puzzle.get_tree_hash()}")


# =============================================================================
# STEP 5: Demonstrate reading a puzzle from a .clsp file
# =============================================================================

def demonstrate_file_compilation():
    """
    Show how to compile a puzzle from a .clsp file on disk.
    """
    print(f"\n{'='*60}")
    print(f"Compiling from a file")
    print(f"{'='*60}")

    # Write a temporary puzzle file
    import tempfile
    import os

    puzzle_source = """
    ; greeting.clsp
    ; Returns a greeting with a customizable name
    (mod (NAME)
      (list 1 NAME)
    )
    """

    # Write to a temp file
    temp_dir = tempfile.mkdtemp()
    puzzle_path = os.path.join(temp_dir, "greeting.clsp")

    with open(puzzle_path, "w") as f:
        f.write(puzzle_source)

    print(f"Wrote puzzle to: {puzzle_path}")

    # Read and compile
    with open(puzzle_path, "r") as f:
        source = f.read()

    compiled_hex = compile_clvm_text(source, [temp_dir])
    puzzle = Program.fromhex(compiled_hex)

    print(f"Compiled successfully!")
    print(f"Puzzle hash: {puzzle.get_tree_hash().hex()}")

    # Run it
    solution = Program.to([b"World"])
    result = puzzle.run(solution)
    print(f"Result: {result}")

    # Clean up
    os.remove(puzzle_path)
    os.rmdir(temp_dir)


# =============================================================================
# MAIN
# =============================================================================

def main():
    print("ChiaLisp Compile and Run Demo")
    print("=" * 60)

    # --- Compile puzzles ---
    add_puzzle = compile_puzzle(ADD_PUZZLE, "Add Puzzle")
    password_puzzle = compile_puzzle(PASSWORD_PUZZLE, "Password Puzzle")
    conditional_puzzle = compile_puzzle(CONDITIONAL_PUZZLE, "Conditional Puzzle")

    # --- Run the add puzzle ---
    run_puzzle(add_puzzle, [3, 7], "Add 3 + 7")
    run_puzzle(add_puzzle, [100, 200], "Add 100 + 200")

    # --- Run the password puzzle ---
    # Correct password (0xcafef00d)
    recipient_ph = bytes.fromhex("a" * 64)  # Fake puzzle hash for demo
    run_puzzle(
        password_puzzle,
        [0xcafef00d, recipient_ph, 1000],
        "Password puzzle (correct password)"
    )

    # Wrong password -- should fail
    run_puzzle(
        password_puzzle,
        [0xdeadbeef, recipient_ph, 1000],
        "Password puzzle (wrong password)"
    )

    # --- Run the conditional puzzle ---
    run_puzzle(
        conditional_puzzle,
        [5000, recipient_ph, 1000],
        "Conditional puzzle"
    )

    # --- Currying demo ---
    demonstrate_currying()

    # --- File compilation demo ---
    demonstrate_file_compilation()

    print("\n" + "=" * 60)
    print("All demos complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
