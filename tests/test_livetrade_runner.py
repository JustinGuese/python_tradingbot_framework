"""
Tests for the shared live-trade runner and the broker registry.

These cover the behaviour that used to be copy-pasted (and therefore drift-prone)
across the five livetrade_<broker>.py entry scripts: env parsing, exit codes, and
the equity snapshot that previously ran for Hyperliquid only.
"""

from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from tradingbot.livetrade import runner as runner_mod
from tradingbot.livetrade.broker import LiveBroker
from tradingbot.livetrade.registry import REGISTRY, BrokerSpec, ConfigError
from tradingbot.livetrade.runner import RunConfig, load_config, run

LIVETRADE_ENV = [
    "LIVETRADE_BOT_WEIGHTS",
    "LIVETRADE_MIN_ORDER_USD",
    "LIVETRADE_DRY_RUN",
    "LIVETRADE_PORTFOLIO_FRACTION",
]


class FakeBroker(LiveBroker):
    """Minimal concrete LiveBroker; records lifecycle calls."""

    def __init__(self, name="fake"):
        self.name = name
        self.connected = False
        self.disconnected = False
        self.synced = False

    def connect(self, readonly: bool = False) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.disconnected = True

    def get_cash(self) -> float:
        return 1000.0

    def get_positions(self) -> dict[str, float]:
        return {}

    def get_total_equity(self) -> float:
        return 1000.0

    def place_order(self, broker_symbol, quantity, side, symbol_type=None) -> None:
        pass

    def map_symbol(self, yf_symbol):
        return None

    def search_symbol(self, query):
        return []


@pytest.fixture
def spec():
    return BrokerSpec(
        key="fake",
        logger_name="livetrade_fake",
        build=lambda: FakeBroker(),
        required_env=("FAKE_KEY",),
    )


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """The runner reads process env; a stray real value would leak into asserts."""
    for name in LIVETRADE_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def no_db(monkeypatch):
    """Bot-name validation hits Postgres; these tests are about the runner."""
    monkeypatch.setattr(runner_mod, "validate_bot_names", lambda weights: None)


@pytest.fixture
def captured_equity(monkeypatch):
    calls = []
    monkeypatch.setattr(runner_mod, "record_live_equity", lambda b, w=None: calls.append((b, w)))
    return calls


# --------------------------------------------------------------------------- #
# Config parsing
# --------------------------------------------------------------------------- #
def test_load_config_uses_spec_defaults_when_env_is_unset(spec):
    config = load_config(spec)
    assert config == RunConfig(
        bot_weights={"AdaptiveMeanReversionBot": 1.0},
        min_order_usd=50.0,
        dry_run=True,
        portfolio_fraction=1.0,
    )


def test_env_overrides_spec_defaults(spec, monkeypatch):
    monkeypatch.setenv("LIVETRADE_BOT_WEIGHTS", '{"XAUZenbotTreeBot": 1.0}')
    monkeypatch.setenv("LIVETRADE_MIN_ORDER_USD", "12.5")
    monkeypatch.setenv("LIVETRADE_DRY_RUN", "false")
    monkeypatch.setenv("LIVETRADE_PORTFOLIO_FRACTION", "0.5")

    config = load_config(spec)

    assert config.bot_weights == {"XAUZenbotTreeBot": 1.0}
    assert config.min_order_usd == 12.5
    assert config.dry_run is False
    assert config.portfolio_fraction == 0.5


@pytest.mark.parametrize(
    ("var", "value"),
    [
        ("LIVETRADE_BOT_WEIGHTS", "not json"),
        ("LIVETRADE_MIN_ORDER_USD", "fifty"),
        ("LIVETRADE_PORTFOLIO_FRACTION", "half"),
        ("LIVETRADE_PORTFOLIO_FRACTION", "0"),
        ("LIVETRADE_PORTFOLIO_FRACTION", "1.5"),
        ("LIVETRADE_PORTFOLIO_FRACTION", "-0.3"),
    ],
)
def test_malformed_config_raises(spec, monkeypatch, var, value):
    monkeypatch.setenv(var, value)
    with pytest.raises(ConfigError):
        load_config(spec)


def test_bot_weights_must_be_an_object_not_a_list(spec, monkeypatch):
    # json.loads("[1,2]") succeeds, so without the isinstance check this reached
    # the copier and failed much later with an unrelated error.
    monkeypatch.setenv("LIVETRADE_BOT_WEIGHTS", "[1, 2]")
    with pytest.raises(ConfigError):
        load_config(spec)


def test_min_order_usd_is_guarded_like_portfolio_fraction(spec, monkeypatch):
    monkeypatch.setenv("LIVETRADE_MIN_ORDER_USD", "")
    with pytest.raises(ConfigError):
        load_config(spec)


# --------------------------------------------------------------------------- #
# Exit codes
# --------------------------------------------------------------------------- #
def test_missing_credentials_exit_2_and_never_build_the_broker(spec, monkeypatch):
    monkeypatch.delenv("FAKE_KEY", raising=False)
    built = []
    spec = replace(spec, build=lambda: built.append(1) or FakeBroker())

    assert run(spec) == 2
    assert built == [], "credentials must be checked before the adapter is constructed"


def test_bad_config_exits_2_not_0(spec, monkeypatch, no_db):
    monkeypatch.setenv("FAKE_KEY", "x")
    monkeypatch.setenv("LIVETRADE_PORTFOLIO_FRACTION", "3")

    # The pre-refactor scripts returned bare on every error path, so Kubernetes
    # saw exit 0 and reported success on a day the bot never traded.
    assert run(spec) == 2


def test_unknown_bot_names_exit_2(spec, monkeypatch):
    monkeypatch.setenv("FAKE_KEY", "x")

    def boom(weights):
        raise ConfigError("Unknown bot names")

    monkeypatch.setattr(runner_mod, "validate_bot_names", boom)
    assert run(spec) == 2


def test_successful_sync_exits_0(spec, monkeypatch, no_db, captured_equity):
    monkeypatch.setenv("FAKE_KEY", "x")
    copier = MagicMock()
    monkeypatch.setattr(runner_mod, "LiveTradeCopier", lambda **kw: copier)

    assert run(spec) == 0
    copier.sync.assert_called_once()


def test_sync_failure_exits_1_distinct_from_config_failure(spec, monkeypatch, no_db, captured_equity):
    monkeypatch.setenv("FAKE_KEY", "x")
    copier = MagicMock()
    copier.sync.side_effect = RuntimeError("broker exploded")
    monkeypatch.setattr(runner_mod, "LiveTradeCopier", lambda **kw: copier)

    assert run(spec) == 1


# --------------------------------------------------------------------------- #
# Lifecycle and equity recording
# --------------------------------------------------------------------------- #
def test_broker_is_connected_and_always_disconnected(spec, monkeypatch, no_db, captured_equity):
    monkeypatch.setenv("FAKE_KEY", "x")
    broker = FakeBroker()
    spec = replace(spec, build=lambda: broker)
    copier = MagicMock()
    copier.sync.side_effect = RuntimeError("boom")
    monkeypatch.setattr(runner_mod, "LiveTradeCopier", lambda **kw: copier)

    run(spec)

    assert broker.connected
    assert broker.disconnected, "a failed sync must still release the broker session"


def test_equity_is_recorded_even_when_sync_fails(spec, monkeypatch, no_db, captured_equity):
    # A failed sync is exactly when the published track record most needs a point.
    monkeypatch.setenv("FAKE_KEY", "x")
    copier = MagicMock()
    copier.sync.side_effect = RuntimeError("boom")
    monkeypatch.setattr(runner_mod, "LiveTradeCopier", lambda **kw: copier)

    run(spec)

    assert len(captured_equity) == 1


def test_equity_is_recorded_before_disconnect(spec, monkeypatch, no_db):
    """IBKR can only answer get_total_equity() while its session is open."""
    monkeypatch.setenv("FAKE_KEY", "x")
    broker = FakeBroker()
    spec = replace(spec, build=lambda: broker)
    monkeypatch.setattr(runner_mod, "LiveTradeCopier", lambda **kw: MagicMock())

    order = []
    monkeypatch.setattr(runner_mod, "record_live_equity", lambda b, w=None: order.append("record"))
    original_disconnect = broker.disconnect

    def tracked_disconnect():
        order.append("disconnect")
        original_disconnect()

    broker.disconnect = tracked_disconnect
    run(spec)

    assert order == ["record", "disconnect"]


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #
def test_every_registered_broker_has_a_distinct_logger_name():
    names = [s.logger_name for s in REGISTRY.values()]
    assert len(names) == len(set(names))


@pytest.mark.parametrize("key", sorted(REGISTRY))
def test_registered_defaults_are_parseable(key, monkeypatch):
    """A typo in a default would only surface in production, on the one run where
    the corresponding env var happened to be unset."""
    for name in LIVETRADE_ENV:
        monkeypatch.delenv(name, raising=False)
    config = load_config(REGISTRY[key])
    assert config.bot_weights
    assert config.min_order_usd > 0
    assert 0 < config.portfolio_fraction <= 1


def test_hyperliquid_keeps_its_deliberate_095_fraction(monkeypatch):
    # Not cosmetic: at 1.0 the copier's get_cash()*0.98 clamp scales down every
    # order and the account sits on the margin boundary.
    for name in LIVETRADE_ENV:
        monkeypatch.delenv(name, raising=False)
    assert load_config(REGISTRY["hyperliquid"]).portfolio_fraction == 0.95


def test_missing_credentials_name_the_variables(monkeypatch):
    # Explicitly cleared: run() calls load_dotenv(), which populates the real .env
    # into os.environ for the rest of the process, so a developer with credentials
    # on disk would otherwise see this pass for the wrong reason.
    for name in REGISTRY["collective2"].required_env:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ConfigError, match="COLLECTIVE2_API_KEY"):
        REGISTRY["collective2"].check_required_env()
