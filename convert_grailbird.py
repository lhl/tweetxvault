#!/usr/bin/env python3
"""Backward-compatible wrapper for the packaged Grailbird converter."""

from tweetxvault.grailbird import main


if __name__ == "__main__":
    raise SystemExit(main())
