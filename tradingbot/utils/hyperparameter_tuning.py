"""Hyperparameter tuning functionality for trading bots."""

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import product
from typing import Any, Dict, List, Optional, Type

try:
    from tqdm import tqdm
    TQDM_AVAILABLE = True
except ImportError:
    TQDM_AVAILABLE = False
    # Create a dummy tqdm class if not available
    class tqdm:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def update(self, n=1):
            pass

from .backtest import _get_backtest_period, backtest_bot
from .botclass import Bot


def _evaluate_params(
    bot_class: Type[Bot],
    params: Dict[str, Any],
    initial_capital: float,
    objective: str,
    shared_data: Optional[Any],
    idx: int,
    total: int,
    verbose: bool,
) -> Optional[Dict[str, Any]]:
    """
    Helper function to evaluate a single parameter combination.
    Used for parallel execution in hyperparameter tuning.
    
    Args:
        bot_class: Bot class to instantiate
        params: Parameter combination to test
        initial_capital: Starting capital for backtest
        objective: Metric to optimize ("sharpe_ratio" or "yearly_return")
        shared_data: Pre-fetched DataFrame to reuse (avoids redundant data fetching)
        idx: Index of this parameter combination (for progress tracking)
        total: Total number of combinations (for progress tracking)
        verbose: Whether to print progress information
    
    Returns:
        Dictionary with results (params, score, metrics) or None if evaluation failed
    """
    try:
        if verbose:
            print(f"[{idx}/{total}] Testing params: {params}")
        
        # Create bot instance with these parameters
        bot = bot_class(**params)
        
        # Backtest with pre-fetched data if available
        results = backtest_bot(
            bot,
            initial_capital=initial_capital,
            save_to_db=False,  # Data already saved, no need to save again
            data=shared_data  # Reuse pre-fetched data
        )
        score = results[objective]
        
        result_entry = {
            "params": params.copy(),
            "score": score,
            "yearly_return": results["yearly_return"],
            "sharpe_ratio": results["sharpe_ratio"],
            "nrtrades": results["nrtrades"],
            "maxdrawdown": results["maxdrawdown"],
        }
        
        if verbose:
            print(f"[{idx}/{total}] Score ({objective}): {score:.4f}, "
                  f"Return: {results['yearly_return']:.2%}, "
                  f"Trades: {results['nrtrades']}, "
                  f"Drawdown: {results['maxdrawdown']:.2%}")
        
        return result_entry
    
    except Exception as e:
        if verbose:
            print(f"[{idx}/{total}] Error: {e}")
        return None


def tune_hyperparameters(
    bot_class: Type[Bot],
    param_grid: Dict[str, List[Any]],
    objective: str = "sharpe_ratio",
    initial_capital: float = 10000.0,
    verbose: bool = True,
    n_jobs: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Tune hyperparameters for a trading bot using grid search.
    
    Args:
        bot_class: Bot class (not instance) to tune. Must be a subclass of Bot.
        param_grid: Dictionary mapping parameter names to lists of values to try.
                    e.g., {"adx_threshold": [15, 20, 25], "rsi_buy": [65, 70, 75]}
        objective: Metric to maximize. Must be one of:
                   - "sharpe_ratio" (default): Risk-adjusted returns
                   - "yearly_return": Absolute returns
        initial_capital: Starting capital in USD for backtests (default: $10,000)
        verbose: If True, print progress information (default: True)
        n_jobs: Number of parallel jobs to run. If None, uses number of CPU cores.
                Set to 1 for sequential execution (default: None = auto-detect)
    
    Returns:
        Dictionary with keys:
        - best_params: Best parameter combination found
        - best_score: Best objective value achieved
        - all_results: List of dictionaries, each containing:
          - params: Parameter combination
          - score: Objective value
          - yearly_return: Yearly return
          - sharpe_ratio: Sharpe ratio
          - nrtrades: Number of trades
          - maxdrawdown: Maximum drawdown
    
    Raises:
        ValueError: If objective is not recognized or param_grid is empty
        TypeError: If bot_class is not a Bot subclass
    
    Example:
        >>> from tradingbot.gptbasedstrategytabased import gptbasedstrategytabased
        >>> param_grid = {
        ...     "adx_threshold": [15, 20, 25],
        ...     "rsi_buy": [65, 70, 75],
        ... }
        >>> results = tune_hyperparameters(
        ...     gptbasedstrategytabased,
        ...     param_grid,
        ...     objective="sharpe_ratio"
        ... )
        >>> print(f"Best params: {results['best_params']}")
        >>> print(f"Best Sharpe: {results['best_score']:.2f}")
    """
    # Validate inputs
    if not issubclass(bot_class, Bot):
        raise TypeError(f"bot_class must be a subclass of Bot, got {type(bot_class)}")
    
    if objective not in ["sharpe_ratio", "yearly_return"]:
        raise ValueError(f"objective must be 'sharpe_ratio' or 'yearly_return', got '{objective}'")
    
    if not param_grid:
        raise ValueError("param_grid cannot be empty")
    
    # Generate all parameter combinations
    param_names = list(param_grid.keys())
    param_values = list(param_grid.values())
    combinations = list(product(*param_values))
    
    total_combinations = len(combinations)
    
    # Determine number of parallel jobs
    if n_jobs is None:
        n_jobs = os.cpu_count() or 1
    n_jobs = max(1, int(n_jobs))  # Ensure at least 1
    
    if verbose:
        print(f"Testing {total_combinations} parameter combinations...")
        print(f"Objective: {objective}")
        print(f"Parameter grid: {param_grid}")
        print(f"Parallel jobs: {n_jobs}")
        print()
    
    # Pre-fetch historical data once to avoid repeated yfinance downloads
    # Create a temporary bot instance with default parameters to fetch data
    if verbose:
        print("Pre-fetching historical data (this will be reused for all parameter combinations)...")
    
    try:
        # Create a temporary bot with default parameters to get symbol/interval/period
        temp_bot = bot_class()
        
        # Determine appropriate period based on interval (respects Yahoo Finance limits)
        backtest_period = _get_backtest_period(temp_bot.interval)
        
        # Fetch data with TA indicators once and save to DB
        # This ensures subsequent backtests can reuse DB data
        shared_data = temp_bot.getYFDataWithTA(
            interval=temp_bot.interval,
            period=backtest_period,
            saveToDB=True  # Save to DB so future runs can reuse it
        )
        
        if verbose:
            print(f"Loaded {len(shared_data)} data points for {temp_bot.symbol} "
                  f"(interval={temp_bot.interval}, period={backtest_period})")
            print()
    except Exception as e:
        if verbose:
            print(f"Warning: Could not pre-fetch data: {e}")
            print("Will fetch data individually for each parameter combination (slower)")
        shared_data = None
    
    best_score = float('-inf')
    best_params = None
    all_results = []
    
    # Prepare parameter combinations with indices
    param_combinations = [
        (idx + 1, dict(zip(param_names, combo)))
        for idx, combo in enumerate(combinations)
    ]
    
    # Execute in parallel or sequentially
    if n_jobs > 1:
        # Parallel execution
        if verbose:
            print(f"Running {total_combinations} backtests in parallel ({n_jobs} workers)...")
            print()
        
        # Create progress bar
        progress_bar = tqdm(
            total=total_combinations,
            desc="Hyperparameter tuning",
            unit="combination",
            disable=not verbose or not TQDM_AVAILABLE,
        )
        
        with ThreadPoolExecutor(max_workers=n_jobs) as executor:
            # Submit all tasks
            future_to_params = {
                executor.submit(
                    _evaluate_params,
                    bot_class,
                    params,
                    initial_capital,
                    objective,
                    shared_data,
                    idx,
                    total_combinations,
                    False,  # Disable verbose in parallel to avoid print conflicts
                ): (idx, params)
                for idx, params in param_combinations
            }
            
            # Collect results as they complete
            for future in as_completed(future_to_params):
                idx, params = future_to_params[future]
                try:
                    result_entry = future.result()
                    if result_entry is not None:
                        all_results.append(result_entry)
                        
                        if result_entry["score"] > best_score:
                            best_score = result_entry["score"]
                            best_params = result_entry["params"].copy()
                            if verbose:
                                progress_bar.set_postfix({
                                    "best_score": f"{best_score:.4f}",
                                    "best_idx": idx
                                })
                    
                    progress_bar.update(1)
                except Exception as e:
                    if verbose:
                        progress_bar.write(f"[{idx}/{total_combinations}] Error: {e}")
                    progress_bar.update(1)
        
        progress_bar.close()
    else:
        # Sequential execution (original behavior)
        # Create progress bar
        progress_bar = tqdm(
            total=total_combinations,
            desc="Hyperparameter tuning",
            unit="combination",
            disable=not verbose or not TQDM_AVAILABLE,
        )
        
        for idx, params in param_combinations:
            result_entry = _evaluate_params(
                bot_class,
                params,
                initial_capital,
                objective,
                shared_data,
                idx,
                total_combinations,
                verbose,
            )
            
            if result_entry is not None:
                all_results.append(result_entry)
                
                if result_entry["score"] > best_score:
                    best_score = result_entry["score"]
                    best_params = result_entry["params"].copy()
                    if verbose:
                        progress_bar.set_postfix({
                            "best_score": f"{best_score:.4f}",
                            "best_idx": idx
                        })
            
            progress_bar.update(1)
            
            if verbose and not TQDM_AVAILABLE:
                print()
        
        progress_bar.close()
    
    if best_params is None:
        raise ValueError("No valid parameter combinations found. Check your param_grid and bot_class.")
    
    if verbose:
        print("=" * 60)
        print(f"Best parameters: {best_params}")
        print(f"Best {objective}: {best_score:.4f}")
        print("=" * 60)
    
    return {
        "best_params": best_params,
        "best_score": best_score,
        "all_results": all_results,
    }


def get_default_param_grid(bot_name: Optional[str] = None) -> Dict[str, List[Any]]:
    """
    Get a default parameter grid for common bots.
    
    This provides reasonable search spaces for hyperparameter tuning.
    Can be customized or extended for specific bots.
    
    Args:
        bot_name: Optional bot name to get bot-specific defaults.
                  If None, returns a generic grid.
    
    Returns:
        Dictionary mapping parameter names to lists of values.
    
    Example:
        >>> grid = get_default_param_grid("gptbasedstrategytabased")
        >>> # Customize the grid
        >>> grid["adx_threshold"] = [15, 20, 25, 30]
    """
    if bot_name == "gptbasedstrategytabased" or bot_name == "GptBasedStrategyBTCTabased":
        return {
            "adx_threshold": [15, 20, 25, 30],
            "rsi_buy": [65, 70, 75],
            "rsi_sell": [25, 30, 35],
            "bbp_buy_low": [0.2, 0.3, 0.4],
            "bbp_buy_high": [0.6, 0.7, 0.8],
            "mfi_buy": [75, 80, 85],
            "mfi_sell": [15, 20, 25],
        }
    
    # Generic/default grid
    return {
        "adx_threshold": [15, 20, 25],
        "rsi_buy": [65, 70, 75],
        "rsi_sell": [25, 30, 35],
    }
