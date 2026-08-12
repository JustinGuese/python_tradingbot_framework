"""
Utility modules for trading bots.

Import the concrete module you need — `utils.botclass`, `utils.db`,
`utils.data_service`, `utils.portfolio_manager`, `utils.config`, and so on.

This package deliberately re-exports nothing. It previously advertised a layered
structure (`utils.core`, `utils.data`, `utils.portfolio`, `utils.ai`) implemented
as four re-export-only subpackages, which gave every symbol two importable names
without moving any code; those have been removed. It also re-exported Bot,
DataService and the ORM models from this file, which had no consumers and meant
that importing a pure-function module like `utils.helpers` eagerly pulled in
yfinance, pandas, sqlalchemy and a database engine — about a second of startup
for a timezone helper.
"""
