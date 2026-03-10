## Utils Package Layout

The `tradingbot.utils` package is organised into **domain-focused subpackages** while
retaining backwards-compatible imports:

- `utils.core`: core infrastructure
  - `Bot` base class and `PortfolioManager`
  - database models (`BotModel`, `Trade`, `HistoricData`, `RunLog`, `PortfolioWorth`)
  - DB session helpers (`SessionLocal`, `get_db_session`)
  - backtesting and hyperparameter tuning helpers
  - shared constants and low-level helpers
- `utils.data`: data access
  - `DataService` (Yahoo Finance + Postgres caching)
  - stock fundamentals loader for news, earnings, and insider trades
- `utils.portfolio`: portfolio and strategy logic
  - regime classification and regime tilts
  - earnings/insider-based scoring and tilting
  - Sharpe-ratio-based optimisation helpers
  - portfolio worth time-series + performance metrics
  - sentiment adapters and the canonical `TRADEABLE` universe
- `utils.ai`: AI helpers
  - thin wrappers around the LangChain/OpenRouter tooling used by `Bot.run_ai*`
- `utils.telegram_monitor`: Telegram integration (optional)
  - Telegram channel monitoring and message summarization
  - Uses Telethon for Telegram API access
  - AI-powered extraction of trading symbols from messages

### Import guidelines

For new code, prefer importing from the **subpackages**:

```python
from utils.core import Bot
from utils.data import DataService
from utils.portfolio import TRADEABLE, regime_compute_weights
from utils.ai import run_ai_with_tools
```

The historical flat modules under `tradingbot.utils` remain in place and are
re-exported where useful, so existing imports like `from utils.botclass import Bot`
continue to function. Over time, bots can migrate to the subpackage imports to make
module boundaries clearer.+

