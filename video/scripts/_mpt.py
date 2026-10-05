"""Shared setup for the scripts that run inside MoneyPrinterTurbo's interpreter."""

import argparse
import os
import sys

VIDEO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def parser(description: str) -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=description)
    result.add_argument("--mpt", required=True, help="MoneyPrinterTurbo checkout")
    return result


def attach(mpt: str) -> None:
    """Makes MoneyPrinterTurbo's `app.services` importable; its config loader reads ./config.toml."""
    os.chdir(mpt)
    sys.path.insert(0, mpt)
