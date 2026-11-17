"""Tests for TradingView trend bots Helm registration and module parity (Step S3)."""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path

import pytest
import yaml

from tradingbot.utils.botclass import Bot

HELM_VALUES_FILE = Path(__file__).resolve().parent.parent / "helm" / "tradingbots" / "values.yaml"
HOURLY_BOTS = [
    "supertrendmtfbot",
    "ultimatemabot",
    "alphatrendbot",
    "slingshotchandelierbot",
]
DAILY_BOTS = [
    "tvtrenddailybot",
    "supertrenddailybot",
    "ultimatemadailybot",
    "alphatrenddailybot",
    "slingshotchandelierdailybot",
]
EXPECTED_BOTS = [*HOURLY_BOTS, *DAILY_BOTS]


def _load_helm_bots() -> dict[str, dict]:
    """Parse helm/tradingbots/values.yaml and return a dictionary of bot entries by name."""
    assert HELM_VALUES_FILE.exists(), f"Helm values file not found at {HELM_VALUES_FILE}"
    with open(HELM_VALUES_FILE, "r", encoding="utf-8") as f:
        values = yaml.safe_load(f)
    assert "bots" in values, "values.yaml does not contain a 'bots' key"
    bots_list = values["bots"]
    assert isinstance(bots_list, list), "'bots' in values.yaml must be a list"
    return {b["name"]: b for b in bots_list if isinstance(b, dict) and "name" in b}


def test_every_bot_module_imports_and_class_name_equals_helm_entry_lowercased():
    """Test 1: Every bot module imports and its class name equals its helm entry's name lower-cased."""
    helm_bots = _load_helm_bots()

    for bot_name in EXPECTED_BOTS:
        assert bot_name in helm_bots, f"Bot '{bot_name}' not found in helm/tradingbots/values.yaml"
        entry = helm_bots[bot_name]

        # Ensure module imports
        module_path = f"tradingbot.{bot_name}"
        module = importlib.import_module(module_path)
        assert module is not None, f"Failed to import module {module_path}"

        # Find Bot subclass defined in this module
        bot_classes = [
            cls
            for _, cls in inspect.getmembers(module, inspect.isclass)
            if issubclass(cls, Bot) and cls is not Bot and cls.__module__ == module.__name__
        ]
        assert len(bot_classes) == 1, (
            f"Expected exactly 1 Bot class in {module_path}, found {[c.__name__ for c in bot_classes]}"
        )

        bot_cls = bot_classes[0]
        assert bot_cls.__name__.lower() == entry["name"].lower(), (
            f"Class name '{bot_cls.__name__}'.lower() does not match helm entry name '{entry['name']}'"
        )


def test_none_of_helm_entries_has_suspend_true():
    """Test 2: None of the TV trend helm entries has suspend: true."""
    helm_bots = _load_helm_bots()

    for bot_name in EXPECTED_BOTS:
        assert bot_name in helm_bots, f"Bot '{bot_name}' not found in helm/tradingbots/values.yaml"
        entry = helm_bots[bot_name]
        is_suspended = entry.get("suspend", False)
        assert is_suspended is not True, f"Helm entry '{bot_name}' must not have suspend: true"


def test_helm_entries_have_correct_schedule():
    """Test 3: Helm entries have the expected schedule ('5 13-20 * * 1-5' for hourly, '5 21 * * 1-5' for daily)."""
    helm_bots = _load_helm_bots()

    for bot_name in HOURLY_BOTS:
        entry = helm_bots[bot_name]
        schedule = entry.get("schedule")
        assert schedule == "5 13-20 * * 1-5", (
            f"Hourly bot '{bot_name}' has unexpected schedule '{schedule}', expected '5 13-20 * * 1-5'"
        )

    for bot_name in DAILY_BOTS:
        entry = helm_bots[bot_name]
        schedule = entry.get("schedule")
        assert schedule == "5 21 * * 1-5", (
            f"Daily bot '{bot_name}' has unexpected schedule '{schedule}', expected '5 21 * * 1-5'"
        )
