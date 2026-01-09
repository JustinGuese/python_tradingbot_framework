from pypfopt import EfficientFrontier, expected_returns, risk_models
from utils.botclass import Bot

TRADEABLE = [
    "GLD", "AAPL", "MSFT", "GOOG", "TSLA", "AMD", "AMZN", "DG", "KDP", "LLY",
    "NOC", "NVDA", "PGR", "TEAM", "UNH", "WM", "URTH", "IWDA.AS", "EEM",
    "XAIX.DE", "BTEC.L", "L0CK.DE", "2B76.DE", "W1TA.DE", "RENW.DE", "BNXG.DE",
    "BTC-USD", "ETH-USD", "AVAX-USD", "TMF", "FAS", "TQQQ", "QQQ", "UUP",
    "META", "PYPL", "ADBE", "UPRO", "BSV", "SQQQ", "NTSX", "DBMF", "VDE", "VNQ",
    "VHT", "VFH", "VOX", "VPU", "VAW", "VGT", "VIS", "VDC", "VCR", "VLUE",
    "FNDX", "VTV", "RWL", "DBA", "SHV", "DBB", "DBO", "URA", "WOOD", "DBE"
]


class SharpePortfolioOptWeeklyBot(Bot):
    """
    Bot that rebalances weekly to a portfolio optimized for maximum Sharpe ratio.
    Uses PyPortfolioOpt to calculate optimal weights and rebalancePortfolio to execute trades.
    """
    
    def __init__(self):
        super().__init__("SharpePortfolioOptWeeklyBot", symbol=None)
        self.tradeable_symbols = TRADEABLE
    
    def makeOneIteration(self):
        """
        Execute weekly rebalancing based on Sharpe ratio optimization.
        
        Returns:
            0: Rebalancing completed (no traditional buy/sell signal)
        """
        print("Fetching market data for portfolio optimization...")
        
        # Download data for all tradeable symbols using base class method
        try:
            # Get data in long format (symbol, timestamp, open, high, low, close, volume)
            data_long = self.getYFDataMultiple(
                self.tradeable_symbols, 
                interval="1d", 
                period="3mo", 
                saveToDB=True
            )
            
            if data_long.empty:
                print("Warning: No data fetched for optimization")
                return 0
            
            # Convert to wide format for PyPortfolioOpt using base class method
            df = self.convertToWideFormat(data_long, value_column="close", fill_method="both")
            
            if df.empty:
                print("Warning: No valid data after cleaning")
                return 0
            
            print(f"Calculating optimal portfolio weights for {len(df.columns)} assets...")
            
            # Calculate expected returns and sample covariance
            mu = expected_returns.mean_historical_return(df)
            S = risk_models.sample_cov(df)
            
            # Optimize for maximal Sharpe ratio with 20% max weight per asset
            ef = EfficientFrontier(mu, S, weight_bounds=(0, 0.2))
            ef.max_sharpe()
            cleaned_weights = ef.clean_weights()
            
            # Get portfolio performance metrics
            exp_return, volatility, sharpe = ef.portfolio_performance(verbose=True)
            
            # Sort dict descending by value and remove zero weights
            cleaned_weights = dict(sorted(cleaned_weights.items(), key=lambda item: item[1], reverse=True))
            cleaned_weights = {k: v for k, v in cleaned_weights.items() if v != 0}
            
            if not cleaned_weights:
                print("Warning: Optimization returned no non-zero weights")
                return 0
            
            print(f"Optimized portfolio contains {len(cleaned_weights)} assets")
            print(f"Expected return: {exp_return:.2%}, Volatility: {volatility:.2%}, Sharpe: {sharpe:.2f}")
            
            # Normalize weights to sum to 1.0 (required by rebalancePortfolio)
            total_weight = sum(cleaned_weights.values())
            if total_weight == 0:
                print("Warning: Total weight is zero, cannot rebalance")
                return 0
            
            normalized_weights = {k: v / total_weight for k, v in cleaned_weights.items()}
            
            # Add USD weight if needed to make weights sum to 1.0
            # For now, we'll assume all cash should be invested (USD weight = 0)
            # If you want to keep some cash, you can add: normalized_weights["USD"] = 0.1
            # and renormalize the other weights accordingly
            
            print("Rebalancing portfolio to target weights...")
            print(f"Top 5 holdings: {dict(list(sorted(normalized_weights.items(), key=lambda x: x[1], reverse=True))[:5])}")
            
            # Rebalance portfolio using base class method with onlyOver50USD=True
            # This will filter out assets with target value <= $50 and redistribute weights
            self.rebalancePortfolio(normalized_weights, onlyOver50USD=True)
            
            print("Weekly rebalancing completed successfully")
            return 0
        
        except Exception as e:
            print(f"Error during portfolio optimization: {e}")
            import traceback
            traceback.print_exc()
            raise


bot = SharpePortfolioOptWeeklyBot()

# bot.local_development()
bot.run()
