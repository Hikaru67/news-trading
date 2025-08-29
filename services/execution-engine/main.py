#!/usr/bin/env python3
"""
Execution Engine Service - Phase 1 MVP
Consumes signals from Kafka and executes trades on Bybit
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Dict, Optional

import redis
import requests
from kafka import KafkaConsumer
from prometheus_client import Counter, Histogram, start_http_server
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Prometheus metrics
SIGNALS_CONSUMED = Counter('signals_consumed_total', 'Total signals consumed', ['event_type'])
TRADES_EXECUTED = Counter('trades_executed_total', 'Total trades executed', ['symbol', 'side'])
EXECUTION_TIME = Histogram('trade_execution_seconds', 'Time spent executing trades', ['symbol'])

class ExecutionEngine:
    def __init__(self):
        # Database connection
        self.db_url = os.getenv('DATABASE_URL', 'postgresql://trading_user:trading_pass@localhost:5432/news_trading')
        self.engine = create_engine(self.db_url)
        self.Session = sessionmaker(bind=self.engine)
        
        # Redis connection
        self.redis_url = os.getenv('REDIS_URL', 'redis://localhost:6379')
        self.redis_client = redis.from_url(self.redis_url)
        
        # Kafka consumer
        self.kafka_servers = os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'localhost:9092').split(',')
        self.consumer = KafkaConsumer(
            'news.signals.v1',
            bootstrap_servers=self.kafka_servers,
            group_id='execution-engine-1',
            auto_offset_reset='latest',
            enable_auto_commit=True,
            value_deserializer=lambda x: json.loads(x.decode('utf-8')),
            key_deserializer=lambda x: x.decode('utf-8') if x else None
        )
        
        # Bybit API configuration
        self.bybit_api_key = os.getenv('BYBIT_API_KEY')
        self.bybit_secret_key = os.getenv('BYBIT_SECRET_KEY')
        self.bybit_base_url = 'https://api-testnet.bybit.com'  # Testnet for Phase 1
        
        # Trading configuration
        self.default_user_id = self.get_default_user_id()
        self.position_size_pct = 0.001  # 0.1% of account size
        self.max_slippage = 0.001  # 0.1%
        
        # Playbook configuration
        self.playbooks = {
            'LISTING': {
                'action': 'BUY',
                'symbol_mapping': self.map_to_symbol,
                'position_size': self.calculate_position_size,
                'take_profit_pct': 0.02,  # 2%
                'stop_loss_pct': 0.01,    # 1%
                'timeout_seconds': 90
            }
        }

    def get_default_user_id(self) -> str:
        """Get default user ID from database"""
        try:
            with self.Session() as session:
                result = session.execute(
                    text("SELECT user_id FROM users WHERE username = 'test_user'")
                ).fetchone()
                return str(result[0]) if result else None
        except Exception as e:
            logger.error(f"Error getting default user: {e}")
            return None

    def map_to_symbol(self, entities: list) -> Optional[str]:
        """Map entities to trading symbol"""
        if not entities:
            return None
        
        # Simple mapping for Phase 1
        entity = entities[0].upper()
        
        # Common crypto symbols
        symbol_mapping = {
            'BTC': 'BTCUSDT',
            'ETH': 'ETHUSDT',
            'BNB': 'BNBUSDT',
            'ADA': 'ADAUSDT',
            'SOL': 'SOLUSDT',
            'DOT': 'DOTUSDT',
            'LINK': 'LINKUSDT',
            'MATIC': 'MATICUSDT',
            'AVAX': 'AVAXUSDT',
            'UNI': 'UNIUSDT'
        }
        
        return symbol_mapping.get(entity, f"{entity}USDT")

    def calculate_position_size(self, severity: float, confidence: float) -> float:
        """Calculate position size based on signal strength"""
        base_size = 10.0  # Base position size in USDT
        adjusted_size = base_size * severity * confidence
        return min(adjusted_size, 100.0)  # Cap at 100 USDT

    def check_risk_limits(self, user_id: str, symbol: str, side: str, quantity: float) -> bool:
        """Check risk limits before executing trade"""
        try:
            # Check daily loss limit
            daily_loss_key = f"daily_loss:{user_id}"
            current_loss = float(self.redis_client.get(daily_loss_key) or 0)
            
            with self.Session() as session:
                result = session.execute(
                    text("SELECT daily_loss_limit FROM users WHERE user_id = :user_id"),
                    {'user_id': user_id}
                ).fetchone()
                
                if result and current_loss >= result[0]:
                    logger.warning(f"Daily loss limit reached for user {user_id}")
                    return False
            
            # Check position cooldown
            cooldown_key = f"cooldown:{symbol}:{user_id}"
            if self.redis_client.exists(cooldown_key):
                logger.info(f"Position cooldown active for {symbol}")
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"Error checking risk limits: {e}")
            return False

    def create_order(self, user_id: str, symbol: str, side: str, quantity: float, order_type: str = 'MARKET') -> Optional[Dict]:
        """Create order in database"""
        try:
            with self.Session() as session:
                query = text("""
                    INSERT INTO orders (user_id, symbol, side, order_type, quantity, status, exchange)
                    VALUES (:user_id, :symbol, :side, :order_type, :quantity, 'PENDING', 'bybit')
                    RETURNING order_id
                """)
                
                result = session.execute(query, {
                    'user_id': user_id,
                    'symbol': symbol,
                    'side': side,
                    'order_type': order_type,
                    'quantity': quantity
                })
                
                order_id = result.fetchone()[0]
                session.commit()
                
                return {'order_id': str(order_id), 'symbol': symbol, 'side': side, 'quantity': quantity}
                
        except Exception as e:
            logger.error(f"Error creating order: {e}")
            return None

    def save_trade(self, order_id: str, symbol: str, side: str, quantity: float, price: float, fees: float = 0):
        """Save executed trade to database"""
        try:
            with self.Session() as session:
                query = text("""
                    INSERT INTO trades (order_id, symbol, side, quantity, price, fees, exchange)
                    VALUES (:order_id, :symbol, :side, :quantity, :price, :fees, 'bybit')
                """)
                
                session.execute(query, {
                    'order_id': order_id,
                    'symbol': symbol,
                    'side': side,
                    'quantity': quantity,
                    'price': price,
                    'fees': fees
                })
                
                session.commit()
                
        except Exception as e:
            logger.error(f"Error saving trade: {e}")

    def update_order_status(self, order_id: str, status: str, exchange_order_id: str = None):
        """Update order status in database"""
        try:
            with self.Session() as session:
                query = text("""
                    UPDATE orders 
                    SET status = :status, exchange_order_id = :exchange_order_id, updated_at = NOW()
                    WHERE order_id = :order_id
                """)
                
                session.execute(query, {
                    'order_id': order_id,
                    'status': status,
                    'exchange_order_id': exchange_order_id
                })
                
                session.commit()
                
        except Exception as e:
            logger.error(f"Error updating order status: {e}")

    def execute_playbook(self, signal: Dict) -> bool:
        """Execute trading playbook based on signal"""
        try:
            event_type = signal['event_type']
            
            if event_type not in self.playbooks:
                logger.info(f"No playbook for event type: {event_type}")
                return False
            
            playbook = self.playbooks[event_type]
            
            # Map to trading symbol
            symbol = playbook['symbol_mapping'](signal['entities'])
            if not symbol:
                logger.warning(f"Could not map entities to symbol: {signal['entities']}")
                return False
            
            # Calculate position size
            quantity = playbook['position_size'](signal['severity'], signal['confidence'])
            
            # Check risk limits
            if not self.check_risk_limits(self.default_user_id, symbol, playbook['action'], quantity):
                return False
            
            # Create order
            order = self.create_order(
                self.default_user_id,
                symbol,
                playbook['action'],
                quantity,
                'MARKET'
            )
            
            if not order:
                return False
            
            # Execute on exchange (simulated for Phase 1)
            success = self.execute_on_exchange(order)
            
            if success:
                # Set cooldown
                cooldown_key = f"cooldown:{symbol}:{self.default_user_id}"
                self.redis_client.setex(cooldown_key, 300, "1")  # 5 minutes
                
                TRADES_EXECUTED.labels(symbol=symbol, side=playbook['action']).inc()
                logger.info(f"Successfully executed {playbook['action']} {quantity} {symbol}")
            
            return success
            
        except Exception as e:
            logger.error(f"Error executing playbook: {e}")
            return False

    def execute_on_exchange(self, order: Dict) -> bool:
        """Execute order on Bybit (simulated for Phase 1)"""
        try:
            # Simulate exchange execution
            # In Phase 1, we'll simulate successful execution
            # In Phase 2, we'll add real Bybit API integration
            
            # Simulate execution delay
            time.sleep(0.1)
            
            # Simulate execution price (current market price + small spread)
            execution_price = 50000.0  # Simulated price
            if order['side'] == 'BUY':
                execution_price *= (1 + 0.0001)  # 1 bps spread
            else:
                execution_price *= (1 - 0.0001)
            
            # Update order status
            self.update_order_status(order['order_id'], 'FILLED', 'simulated_order_id')
            
            # Save trade
            self.save_trade(
                order['order_id'],
                order['symbol'],
                order['side'],
                order['quantity'],
                execution_price,
                0.1  # Simulated fees
            )
            
            logger.info(f"Simulated execution: {order['side']} {order['quantity']} {order['symbol']} @ {execution_price}")
            return True
            
        except Exception as e:
            logger.error(f"Error executing on exchange: {e}")
            self.update_order_status(order['order_id'], 'REJECTED')
            return False

    async def process_signals(self):
        """Process signals from Kafka"""
        logger.info("Starting signal processing")
        
        for message in self.consumer:
            try:
                signal = message.value
                logger.info(f"Received signal: {signal['event_type']} - {signal['headline']}")
                
                with EXECUTION_TIME.labels(symbol='unknown').time():
                    # Execute playbook
                    success = self.execute_playbook(signal)
                    
                    if success:
                        SIGNALS_CONSUMED.labels(event_type=signal['event_type']).inc()
                        logger.info(f"Successfully processed signal: {signal['event_id']}")
                    else:
                        logger.warning(f"Failed to process signal: {signal['event_id']}")
                
            except Exception as e:
                logger.error(f"Error processing signal: {e}")

    async def run(self):
        """Main run loop"""
        logger.info("Starting Execution Engine Service")
        
        # Start Prometheus metrics server
        start_http_server(8001)
        
        # Process signals
        await self.process_signals()

def main():
    """Main entry point"""
    engine = ExecutionEngine()
    
    try:
        asyncio.run(engine.run())
    except KeyboardInterrupt:
        logger.info("Shutting down Execution Engine")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        raise

if __name__ == "__main__":
    main()
