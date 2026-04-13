#!/usr/bin/env python3
"""Test script to check Python syntax."""

import sys

print(f"Python version: {sys.version}")
print(f"Python path: {sys.path}")

# Test basic syntax
try:
    print("Testing basic Python syntax...")
    x = 1 + 1
    print(f"1 + 1 = {x}")
    print("Python syntax is working!")
except Exception as e:
    print(f"Error: {e}")
    sys.exit(1)
