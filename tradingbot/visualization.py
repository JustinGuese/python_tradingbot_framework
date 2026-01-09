"""FastAPI application for visualizing bot portfolio performance."""

import math
import os
import secrets
from typing import Annotated

import pandas as pd
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from starlette.requests import Request
from starlette.templating import Jinja2Templates
from utils.db import Bot, Trade, get_db_session
from utils.portfolio_worth_calculator import (
    calculate_performance_metrics,
    get_portfolio_worth_history,
)

app = FastAPI(title="Trading Bot Portfolio Visualization")

# HTTP Basic Auth setup
security = HTTPBasic()

# Get credentials from environment variables
BASIC_AUTH_USERNAME = os.getenv("BASIC_AUTH_USERNAME", "admin")
BASIC_AUTH_PASSWORD = os.getenv("BASIC_AUTH_PASSWORD", "changeme")


def get_current_username(
    credentials: Annotated[HTTPBasicCredentials, Depends(security)],
):
    """Verify HTTP Basic Auth credentials."""
    current_username_bytes = credentials.username.encode("utf8")
    correct_username_bytes = BASIC_AUTH_USERNAME.encode("utf8")
    is_correct_username = secrets.compare_digest(
        current_username_bytes, correct_username_bytes
    )
    current_password_bytes = credentials.password.encode("utf8")
    correct_password_bytes = BASIC_AUTH_PASSWORD.encode("utf8")
    is_correct_password = secrets.compare_digest(
        current_password_bytes, correct_password_bytes
    )
    if not (is_correct_username and is_correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


# Get the directory where this file is located
template_dir = os.path.join(os.path.dirname(__file__), "templates")
templates = Jinja2Templates(directory=template_dir)


def clean_numeric_value(value):
    """
    Convert NaN, inf, and -inf values to 0.0 for consistent display.
    FastAPI's jsonable_encoder will handle None, but we want 0.0 for display.

    Args:
        value: Numeric value (can be float, int, np.float64, etc.)

    Returns:
        Cleaned value (0.0 if NaN/inf, otherwise the original value as float)
    """
    if value is None:
        return 0.0

    # Convert to float first to handle numpy types
    try:
        float_val = float(value)
    except (ValueError, TypeError):
        return 0.0

    # Check for NaN or inf
    if math.isnan(float_val) or math.isinf(float_val):
        return 0.0

    return float_val


@app.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    username: Annotated[str, Depends(get_current_username)],
):
    """Overview page with DataTables showing all bots."""
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/bot/{bot_name}", response_class=HTMLResponse)
async def bot_detail(
    request: Request,
    bot_name: str,
    username: Annotated[str, Depends(get_current_username)],
):
    """Detail page for a specific bot showing quantstats metrics."""
    return templates.TemplateResponse(
        "bot_detail.html", {"request": request, "bot_name": bot_name}
    )


@app.get("/api/bots")
async def get_bots(username: Annotated[str, Depends(get_current_username)]):
    """JSON API endpoint for bot list with performance metrics."""

    with get_db_session() as session:
        bots = session.query(Bot).all()
        bot_data = []

        for bot in bots:
            try:
                # Get portfolio worth history
                worth_history = get_portfolio_worth_history(bot.name)

                if worth_history.empty:
                    # No data yet
                    bot_data.append(
                        {
                            "name": bot.name,
                            "current_worth": bot.portfolio.get("USD", 0),
                            "total_return": 0.0,
                            "annualized_return": 0.0,
                            "sharpe_ratio": 0.0,
                            "sortino_ratio": 0.0,
                            "max_drawdown": 0.0,
                            "volatility": 0.0,
                            "total_trades": 0,
                            "start_date": bot.created_at.isoformat()
                            if bot.created_at
                            else None,
                        }
                    )
                    continue

                # Set date as index for quantstats
                worth_series = worth_history.set_index("date")["portfolio_worth"]

                # Calculate metrics
                metrics = calculate_performance_metrics(worth_series)

                # Get total trades
                trade_count = session.query(Trade).filter_by(bot_name=bot.name).count()

                # Get start date
                start_date = worth_history["date"].min()

                # Clean all numeric values to handle NaN/inf
                current_worth = round(clean_numeric_value(worth_series.iloc[-1]), 2)

                # Clean all metrics values to ensure no NaN/inf values
                cleaned_metrics = {
                    "total_return": round(
                        clean_numeric_value(metrics.get("total_return", 0.0)), 2
                    ),
                    "annualized_return": round(
                        clean_numeric_value(metrics.get("annualized_return", 0.0)), 2
                    ),
                    "sharpe_ratio": round(
                        clean_numeric_value(metrics.get("sharpe_ratio", 0.0)), 2
                    ),
                    "sortino_ratio": round(
                        clean_numeric_value(metrics.get("sortino_ratio", 0.0)), 2
                    ),
                    "calmar_ratio": round(
                        clean_numeric_value(metrics.get("calmar_ratio", 0.0)), 2
                    ),
                    "max_drawdown": round(
                        clean_numeric_value(metrics.get("max_drawdown", 0.0)), 2
                    ),
                    "volatility": round(
                        clean_numeric_value(metrics.get("volatility", 0.0)), 2
                    ),
                }

                bot_data.append(
                    {
                        "name": bot.name,
                        "current_worth": current_worth,
                        **cleaned_metrics,
                        "total_trades": trade_count,
                        "start_date": start_date.isoformat()
                        if pd.notna(start_date)
                        else None,
                    }
                )
            except Exception as e:
                # Skip bots with errors
                print(f"Error processing bot {bot.name}: {e}")
                continue

        # Use FastAPI's jsonable_encoder to handle any remaining NaN/inf values
        return jsonable_encoder({"bots": bot_data})


@app.get("/api/bot/{bot_name}/performance")
async def get_bot_performance(
    bot_name: str,
    username: Annotated[str, Depends(get_current_username)],
):
    """JSON API endpoint for detailed bot performance data."""
    # Verify bot exists and capture holdings while session is active
    with get_db_session() as session:
        bot = session.query(Bot).filter_by(name=bot_name).first()
        if not bot:
            raise HTTPException(status_code=404, detail="Bot not found")
        current_holdings = dict(bot.portfolio) if bot.portfolio else {}

    # Get portfolio worth history
    worth_history = get_portfolio_worth_history(bot_name)

    if worth_history.empty:
        raise HTTPException(status_code=404, detail="No performance data available")

    # Get trades
    with get_db_session() as session:
        trades = (
            session.query(Trade)
            .filter_by(bot_name=bot_name)
            .order_by(Trade.timestamp)
            .all()
        )

        trades_data = [
            {
                "timestamp": trade.timestamp.isoformat(),
                "symbol": trade.symbol,
                "is_buy": trade.isBuy,
                "quantity": clean_numeric_value(trade.quantity),
                "price": clean_numeric_value(trade.price),
                "profit": clean_numeric_value(trade.profit)
                if trade.profit is not None
                else None,
            }
            for trade in trades
        ]

    # Calculate returns series for quantstats
    worth_series = worth_history.set_index("date")["portfolio_worth"]
    returns = worth_series.pct_change().dropna()

    # Calculate metrics and clean NaN values
    metrics = calculate_performance_metrics(worth_series)
    cleaned_metrics = {
        key: round(clean_numeric_value(value), 2) for key, value in metrics.items()
    }

    # Clean returns list to handle NaN values
    cleaned_returns = [clean_numeric_value(val) for val in returns.tolist()]

    response_data = {
        "bot_name": bot_name,
        "portfolio_worth_history": [
            {
                "date": row["date"].isoformat(),
                "portfolio_worth": clean_numeric_value(row["portfolio_worth"]),
                "holdings": row["holdings"],
            }
            for _, row in worth_history.iterrows()
        ],
        "returns": cleaned_returns,
        "returns_dates": returns.index.strftime("%Y-%m-%d").tolist(),
        "current_holdings": current_holdings,
        "metrics": cleaned_metrics,
        "trades": trades_data,
    }

    # Use FastAPI's jsonable_encoder to handle any remaining NaN/inf values
    return jsonable_encoder(response_data)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
