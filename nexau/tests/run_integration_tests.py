#!/usr/bin/env python3
"""
Script to run NexAU integration tests.

This script provides a convenient way to run integration tests with different
options and configurations.
"""

import os
import sys
import subprocess
import argparse
from pathlib import Path

# Add project root to Python path
project_root = Path("/gpfs/users/panchengjun/NexAU/nexau")
sys.path.insert(0, str(project_root))


def run_tests(test_pattern=None, verbose=False, e2e=False):
    """Run integration tests with specified options."""
    cmd = [
        sys.executable, "-m", "pytest",
        str(project_root / "tests"),
        "-v" if verbose else "",
        "--run-e2e" if e2e else "",
        "-k", test_pattern if test_pattern else ""
    ]
    
    # Remove empty arguments
    cmd = [arg for arg in cmd if arg]
    
    print(f"Running: {' '.join(cmd)}")
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=project_root)
        
        print("=" * 60)
        print("STDOUT:")
        print(result.stdout)
        
        if result.stderr:
            print("STDERR:")
            print(result.stderr)
        
        print("=" * 60)
        print(f"Exit code: {result.returncode}")
        
        return result.returncode == 0
        
    except Exception as e:
        print(f"Failed to run tests: {e}")
        return False


def main():
    """Main function to parse arguments and run tests."""
    parser = argparse.ArgumentParser(description="Run NexAU integration tests")
    parser.add_argument(
        "-v", "--verbose", 
        action="store_true", 
        help="Verbose output"
    )
    parser.add_argument(
        "--e2e", 
        action="store_true", 
        help="Run end-to-end tests (requires specific setup)"
    )
    parser.add_argument(
        "-k", "--test-pattern", 
        type=str, 
        help="Only run tests matching the pattern"
    )
    parser.add_argument(
        "--list", 
        action="store_true", 
        help="List available tests without running them"
    )
    
    args = parser.parse_args()
    
    if args.list:
        # List available tests
        cmd = [sys.executable, "-m", "pytest", "--collect-only", "-q", str(project_root / "tests")]
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=project_root)
        
        if result.returncode == 0:
            tests = [line for line in result.stdout.split('\n') if 'test_' in line and '::' in line]
            print(f"Found {len(tests)} integration tests:")
            for test in tests:
                print(f"  {test}")
        else:
            print("Failed to list tests:")
            print(result.stderr)
        return
    
    # Run tests
    success = run_tests(
        test_pattern=args.test_pattern,
        verbose=args.verbose,
        e2e=args.e2e
    )
    
    if success:
        print("✅ All tests passed!")
        sys.exit(0)
    else:
        print("❌ Some tests failed!")
        sys.exit(1)


if __name__ == "__main__":
    main()