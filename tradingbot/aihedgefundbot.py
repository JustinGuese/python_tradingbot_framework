import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from utils.core import Bot
from utils.db import _database_url

logger = logging.getLogger(__name__)


class AIHedgeFundBot(Bot):
    """
    Bot that rebalances portfolio based on trading decisions from AI hedge fund.
    Reads decisions from ai_hedge_fund database and rebalances accordingly.
    """
    
    _ai_hedge_fund_engine = None
    _SessionLocal = None
    
    def __init__(self):
        super().__init__("AIHedgeFundBot", symbol=None)
    
    @classmethod
    def _get_ai_hedge_fund_session(cls) -> Session:
        """
        Create a database session for the ai_hedge_fund database.
        
        Note: This creates a SEPARATE connection to the ai_hedge_fund database.
        The base Bot class continues to use the main postgres database (via utils/db.py)
        for portfolio operations. Only this method uses the ai_hedge_fund database.
        
        Returns:
            SQLAlchemy Session connected to ai_hedge_fund database
        """
        if cls._ai_hedge_fund_engine is None:
            # Get the base database URL (points to main postgres database)
            base_url = _database_url()
            
            # Create a modified URL that points to ai_hedge_fund database instead
            if "/" in base_url:
                parts = base_url.rsplit("/", 1)
                ai_hedge_fund_url = parts[0] + "/ai_hedge_fund"
            else:
                ai_hedge_fund_url = base_url + "/ai_hedge_fund"
            
            cls._ai_hedge_fund_engine = create_engine(
                ai_hedge_fund_url,
                pool_pre_ping=True,
                pool_recycle=3600,
                connect_args={
                    "keepalives": 1,
                    "keepalives_idle": 30,
                    "keepalives_interval": 10,
                    "keepalives_count": 5,
                },
            )
            cls._SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=cls._ai_hedge_fund_engine)
            
        return cls._SessionLocal()
    
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
                    logger.info("No trading decisions found in database")
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
                        logger.warning(f"Latest trading decisions are too old (created_at: {created_at})")
                        return None
                
                # Parse JSON if it's a string
                if isinstance(trading_decisions_json, str):
                    trading_decisions = json.loads(trading_decisions_json)
                else:
                    trading_decisions = trading_decisions_json
                
                logger.info(f"Found recent trading decisions from {created_at}")
                return trading_decisions
                
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"Error querying ai_hedge_fund database: {e}")
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
            logger.warning("No buy symbols found in trading decisions")
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
        
        logger.info(f"Portfolio weights: {len(buy_symbols)} buy symbols (each {weight_per_buy:.2%}), "
              f"{len(short_symbols)} short symbols (0%)")
        
        return weights
    
    def makeOneIteration(self):
        """
        Execute rebalancing based on AI hedge fund trading decisions.
        
        Returns:
            0: Rebalancing completed (no traditional buy/sell signal)
        """
        logger.info("Fetching trading decisions from AI hedge fund database...")
        
        try:
            # Get latest trading decisions
            trading_decisions = self._get_latest_trading_decisions()
            
            if not trading_decisions:
                logger.info("No valid trading decisions found, skipping rebalancing")
                return 0
            
            # Convert decisions to portfolio weights
            weights = self._convert_decisions_to_weights(trading_decisions)
            
            if not weights:
                logger.warning("Could not convert decisions to weights")
                return 0
            
            # Verify weights sum to 1.0 (only buy symbols should have positive weight)
            total_weight = sum(w for w in weights.values() if w > 0)
            if abs(total_weight - 1.0) > 0.001:
                logger.warning(f"Buy symbol weights sum to {total_weight:.4f}, expected 1.0")
                # Normalize if needed
                if total_weight > 0:
                    weights = {k: (v / total_weight if v > 0 else 0.0) for k, v in weights.items()}
                else:
                    logger.error("No positive weights to normalize")
                    return 0
            
            logger.info("Rebalancing portfolio based on AI hedge fund decisions...")
            logger.info(f"Buy symbols: {[s for s, w in weights.items() if w > 0]}")
            logger.info(f"Short symbols (to sell): {[s for s, w in weights.items() if w == 0 and s in trading_decisions]}")

            # Rebalance portfolio using base class method with onlyOver50USD=True
            # This will filter out assets with target value <= $50 and redistribute weights
            self.rebalancePortfolio(weights, onlyOver50USD=True)
            
            logger.info("Portfolio rebalancing completed successfully")
            return 0
        
        except Exception as e:
            logger.error(f"Error during portfolio rebalancing: {e}")
            import traceback
            traceback.print_exc()
            raise


bot = AIHedgeFundBot()

# # doesnt make sense for this one , bot.local_development()
bot.run()

