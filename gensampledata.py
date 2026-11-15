#!/usr/bin/env python3
"""Compatibility entry point. New users should use the gensampledata command."""

from sampledata.legacy import main

if __name__ == "__main__":
    raise SystemExit(main())
