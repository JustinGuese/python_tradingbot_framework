import json
from datetime import datetime, timedelta, timezone
from os import environ
from typing import Optional

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from utils.botclass import Bot


class AIHedgeFundBot(Bot):
    """
    Bot that rebalances portfolio based on trading decisions from AI hedge fund.
    Reads decisions from ai_hedge_fund database and rebalances accordingly.
    """
    
    def __init__(self):
        super().__init__("AIHedgeFundBot", symbol=None)
    
    def _get_ai_hedge_fund_session(self) -> Session:
        """
        Create a database session for the ai_hedge_fund database.
        
        Note: This creates a SEPARATE connection to the ai_hedge_fund database.
        The base Bot class continues to use the main postgres database (via utils/db.py)
        for portfolio operations. Only this method uses the ai_hedge_fund database.
        
        Returns:
            SQLAlchemy Session connected to ai_hedge_fund database
        """
        # Get the base POSTGRES_URI (points to main postgres database)
        # We read it but don't modify the environment variable
        # Format: postgres:password@host:port/database
        base_uri = environ.get("POSTGRES_URI", "")
        if not base_uri:
            raise ValueError("POSTGRES_URI environment variable not set")
        
        # Create a modified URI that points to ai_hedge_fund database instead
        # This is only used locally for this connection, doesn't affect the environment variable
        if "/" in base_uri:
            parts = base_uri.rsplit("/", 1)
            ai_hedge_fund_uri = parts[0] + "/ai_hedge_fund"
        else:
            # Fallback if URI format is unexpected - append database name
            ai_hedge_fund_uri = base_uri + "/ai_hedge_fund"
        
        # Create a separate engine for ai_hedge_fund database
        # This doesn't affect the base Bot class's connection to main postgres database
        database_url = "postgresql+psycopg2://" + ai_hedge_fund_uri
        engine = create_engine(
            database_url,
            pool_pre_ping=True,
            pool_recycle=3600,
            connect_args={
                "keepalives": 1,
                "keepalives_idle": 30,
                "keepalives_interval": 10,
                "keepalives_count": 5,
            },
        )
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        return SessionLocal()
    
    def _get_latest_trading_decisions(self) -> Optional[dict]:
        """
        Query ai_hedge_fund database for latest trading decisions.
        
        Returns:
            Dictionary with trading_decisions JSON if found and recent (within 1 day), None otherwise
        """
        try:
            session = self._get_ai_hedge_fund_session()
            try:
                # Query for latest entry
                result = session.execute(
                    text("""
                        SELECT trading_decisions, created_at 
                        FROM hedge_fund_flow_run_cycles 
                        ORDER BY created_at DESC 
                        LIMIT 1
                    """)
                ).fetchone()
                
                if not result:
                    print("No trading decisions found in database")
                    return None
                
                trading_decisions_json, created_at = result
                
                # Check if created_at is within 1 day
                if created_at:
                    # Use timezone-aware UTC datetime for comparison
                    now_utc = datetime.now(timezone.utc)
                    one_day_ago = now_utc - timedelta(days=1)
                    
                    # Make created_at timezone-aware if it's naive (assume UTC)
                    if created_at.tzinfo is None:
                        created_at = created_at.replace(tzinfo=timezone.utc)
                    
                    if created_at < one_day_ago:
                        print(f"Latest trading decisions are too old (created_at: {created_at})")
                        return None
                
                # Parse JSON if it's a string
                if isinstance(trading_decisions_json, str):
                    trading_decisions = json.loads(trading_decisions_json)
                else:
                    trading_decisions = trading_decisions_json
                
                print(f"Found recent trading decisions from {created_at}")
                return trading_decisions
                
            finally:
                session.close()
                
        except Exception as e:
            print(f"Error querying ai_hedge_fund database: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def _convert_decisions_to_weights(self, trading_decisions: dict) -> dict:
        """
        Convert trading decisions to portfolio weights.
        
        Args:
            trading_decisions: Dictionary mapping symbols to decision dicts
                            Format: {"AAPL": {"action": "buy", "quantity": 76, ...}, ...}
        
        Returns:
            Dictionary mapping symbols to weights (sums to 1.0)
        """
        buy_symbols = []
        short_symbols = []
        
        # Separate buy and short symbols
        for symbol, decision in trading_decisions.items():
            action = decision.get("action", "").lower()
            if action == "buy":
                buy_symbols.append(symbol)
            elif action == "short":
                short_symbols.append(symbol)
        
        if not buy_symbols:
            print("Warning: No buy symbols found in trading decisions")
            return {}
        
        # Calculate equal weight for all buy symbols
        weight_per_buy = 1.0 / len(buy_symbols)
        
        # Create weights dictionary
        weights = {}
        for symbol in buy_symbols:
            weights[symbol] = weight_per_buy
        
        # Short symbols get 0 weight (will be sold)
        for symbol in short_symbols:
            weights[symbol] = 0.0
        
        print(f"Portfolio weights: {len(buy_symbols)} buy symbols (each {weight_per_buy:.2%}), "
              f"{len(short_symbols)} short symbols (0%)")
        
        return weights
    
    def makeOneIteration(self):
        """
        Execute rebalancing based on AI hedge fund trading decisions.
        
        Returns:
            0: Rebalancing completed (no traditional buy/sell signal)
        """
        print("Fetching trading decisions from AI hedge fund database...")
        
        try:
            # Get latest trading decisions
            trading_decisions = self._get_latest_trading_decisions()
            
            if not trading_decisions:
                print("No valid trading decisions found, skipping rebalancing")
                return 0
            
            # Convert decisions to portfolio weights
            weights = self._convert_decisions_to_weights(trading_decisions)
            
            if not weights:
                print("Warning: Could not convert decisions to weights")
                return 0
            
            # Verify weights sum to 1.0 (only buy symbols should have positive weight)
            total_weight = sum(w for w in weights.values() if w > 0)
            if abs(total_weight - 1.0) > 0.001:
                print(f"Warning: Buy symbol weights sum to {total_weight:.4f}, expected 1.0")
                # Normalize if needed
                if total_weight > 0:
                    weights = {k: (v / total_weight if v > 0 else 0.0) for k, v in weights.items()}
                else:
                    print("Error: No positive weights to normalize")
                    return 0
            
            print("Rebalancing portfolio based on AI hedge fund decisions...")
            print(f"Buy symbols: {[s for s, w in weights.items() if w > 0]}")
            print(f"Short symbols (to sell): {[s for s, w in weights.items() if w == 0 and s in trading_decisions]}")

            # Rebalance portfolio using base class method with onlyOver50USD=True
            # This will filter out assets with target value <= $50 and redistribute weights
            self.rebalancePortfolio(weights, onlyOver50USD=True)
            
            print("Portfolio rebalancing completed successfully")
            return 0
        
        except Exception as e:
            print(f"Error during portfolio rebalancing: {e}")
            import traceback
            traceback.print_exc()
            raise


bot = AIHedgeFundBot()

# # doesnt make sense for this one , bot.local_development()
bot.run()

