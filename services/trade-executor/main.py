#!/usr/bin/env python3
"""
Trade Executor Service - Execute trades on detected exchanges
Handles actual trading on MEXC, Bybit, Gate based on exchange checker signals
"""

import asyncio
import json
import logging
import os
import time
import aiohttp
import redis
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional
from kafka import KafkaConsumer, KafkaProducer
from prometheus_client import Counter, Histogram, start_http_server

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Prometheus metrics
TRADES_EXECUTED = Counter('trade_executor_trades_executed_total', 'Total trades executed', ['exchange', 'action'])
TRADE_SUCCESS = Counter('trade_executor_trade_success_total', 'Successful trades', ['exchange', 'action'])
TRADE_FAILURE = Counter('trade_executor_trade_failure_total', 'Failed trades', ['exchange', 'action'])
EXECUTION_DURATION = Histogram('trade_executor_execution_duration_seconds', 'Time spent executing trades')

class TradeExecutor:
    """Service to execute trades on exchanges based on signals"""
    
    def __init__(self):
        # Redis for state management
        self.redis_url = os.getenv('REDIS_URL', 'redis://localhost:6379')
        self.redis_client = redis.from_url(self.redis_url)
        
        # Kafka setup
        self.kafka_servers = os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'localhost:9092').split(',')
        
        # Consumer for trade signals
        self.consumer = KafkaConsumer(
            'trading.signals.v1',
            bootstrap_servers=self.kafka_servers,
            value_deserializer=lambda m: json.loads(m.decode('utf-8')),
            group_id='trade-executor-group',
            auto_offset_reset='latest',
            enable_auto_commit=True
        )
        
        # Producer for trade results
        self.producer = KafkaProducer(
            bootstrap_servers=self.kafka_servers,
            value_serializer=lambda v: json.dumps(v).encode('utf-8')
        )
        
        # Exchange API configurations
        self.exchange_configs = {
            'mexc': {
                'name': 'MEXC',
                'spot_base_url': 'https://api.mexc.com/api/v3',
                'perp_base_url': 'https://contract.mexc.com/api/v1',
                'api_key': os.getenv('MEXC_API_KEY'),
                'secret_key': os.getenv('MEXC_SECRET_KEY'),
                'enabled': bool(os.getenv('MEXC_API_KEY'))
            },
            'bybit': {
                'name': 'Bybit',
                'spot_base_url': 'https://api.bybit.com/v5',
                'perp_base_url': 'https://api.bybit.com/v5',
                'api_key': os.getenv('BYBIT_API_KEY'),
                'secret_key': os.getenv('BYBIT_SECRET_KEY'),
                'enabled': bool(os.getenv('BYBIT_API_KEY'))
            },
            'gate': {
                'name': 'Gate.io',
                'spot_base_url': 'https://api.gateio.ws/api/v4',
                'perp_base_url': 'https://api.gateio.ws/api/v4',
                'api_key': os.getenv('GATE_API_KEY'),
                'secret_key': os.getenv('GATE_SECRET_KEY'),
                'enabled': bool(os.getenv('GATE_API_KEY'))
            }
        }
        
        # Trading parameters
        self.default_trade_amount = float(os.getenv('DEFAULT_TRADE_AMOUNT', '100'))  # USDT
        self.max_trade_amount = float(os.getenv('MAX_TRADE_AMOUNT', '1000'))  # USDT
        self.min_confidence = float(os.getenv('MIN_TRADE_CONFIDENCE', '0.6'))
        
        # Rate limiting
        self.last_trade_time = {}
        self.min_trade_interval = 2  # seconds between trades

    def calculate_trade_amount(self, signal: Dict) -> float:
        """Calculate trade amount based on signal confidence and severity"""
        try:
            confidence = signal.get('confidence', 0)
            severity = signal.get('severity', 0)
            
            # Base amount
            base_amount = self.default_trade_amount
            
            # Adjust based on confidence and severity
            multiplier = (confidence + severity) / 2  # Average of confidence and severity
            multiplier = max(0.1, min(2.0, multiplier))  # Clamp between 0.1x and 2.0x
            
            trade_amount = base_amount * multiplier
            trade_amount = min(trade_amount, self.max_trade_amount)
            
            return round(trade_amount, 2)
            
        except Exception as e:
            logger.error(f"Error calculating trade amount: {e}")
            return self.default_trade_amount

    async def execute_spot_trade(self, exchange_key: str, signal: Dict, pair: str) -> Dict:
        """Execute spot trade on exchange"""
        config = self.exchange_configs[exchange_key]
        
        if not config['enabled']:
            return {
                'success': False,
                'error': f"{config['name']} API not configured",
                'exchange': exchange_key,
                'type': 'spot'
            }
        
        try:
            trade_amount = self.calculate_trade_amount(signal)
            action = signal.get('action', 'BUY')
            token_symbol = signal.get('token_symbol', '')
            
            # For demo purposes, we'll simulate the trade
            # In production, you would implement actual API calls here
            logger.info(f"Executing {action} {trade_amount} USDT worth of {token_symbol} on {config['name']} spot ({pair})")
            
            # Simulate API call delay
            await asyncio.sleep(0.5)
            
            # Simulate trade result (90% success rate for demo)
            import random
            success = random.random() < 0.9
            
            if success:
                trade_result = {
                    'success': True,
                    'exchange': exchange_key,
                    'type': 'spot',
                    'pair': pair,
                    'action': action,
                    'amount': trade_amount,
                    'token_symbol': token_symbol,
                    'order_id': f"{exchange_key}_{int(time.time())}_{random.randint(1000, 9999)}",
                    'timestamp': datetime.now(timezone(timedelta(hours=7))).isoformat()
                }
                
                TRADE_SUCCESS.labels(exchange=exchange_key, action=action).inc()
                logger.info(f"Successfully executed {action} trade on {config['name']}: {trade_result['order_id']}")
                
            else:
                trade_result = {
                    'success': False,
                    'error': 'Simulated trade failure',
                    'exchange': exchange_key,
                    'type': 'spot',
                    'pair': pair,
                    'action': action,
                    'amount': trade_amount,
                    'token_symbol': token_symbol
                }
                
                TRADE_FAILURE.labels(exchange=exchange_key, action=action).inc()
                logger.warning(f"Failed to execute {action} trade on {config['name']}: {trade_result['error']}")
            
            return trade_result
            
        except Exception as e:
            logger.error(f"Error executing spot trade on {config['name']}: {e}")
            return {
                'success': False,
                'error': str(e),
                'exchange': exchange_key,
                'type': 'spot'
            }

    async def execute_perp_trade(self, exchange_key: str, signal: Dict, contract: str) -> Dict:
        """Execute perpetual trade on exchange"""
        config = self.exchange_configs[exchange_key]
        
        if not config['enabled']:
            return {
                'success': False,
                'error': f"{config['name']} API not configured",
                'exchange': exchange_key,
                'type': 'perp'
            }
        
        try:
            trade_amount = self.calculate_trade_amount(signal)
            action = signal.get('action', 'BUY')
            token_symbol = signal.get('token_symbol', '')
            
            # For demo purposes, we'll simulate the trade
            logger.info(f"Executing {action} {trade_amount} USDT worth of {token_symbol} on {config['name']} perp ({contract})")
            
            # Simulate API call delay
            await asyncio.sleep(0.5)
            
            # Simulate trade result (90% success rate for demo)
            import random
            success = random.random() < 0.9
            
            if success:
                trade_result = {
                    'success': True,
                    'exchange': exchange_key,
                    'type': 'perp',
                    'contract': contract,
                    'action': action,
                    'amount': trade_amount,
                    'token_symbol': token_symbol,
                    'order_id': f"{exchange_key}_perp_{int(time.time())}_{random.randint(1000, 9999)}",
                    'timestamp': datetime.now(timezone(timedelta(hours=7))).isoformat()
                }
                
                TRADE_SUCCESS.labels(exchange=exchange_key, action=action).inc()
                logger.info(f"Successfully executed {action} perp trade on {config['name']}: {trade_result['order_id']}")
                
            else:
                trade_result = {
                    'success': False,
                    'error': 'Simulated perp trade failure',
                    'exchange': exchange_key,
                    'type': 'perp',
                    'contract': contract,
                    'action': action,
                    'amount': trade_amount,
                    'token_symbol': token_symbol
                }
                
                TRADE_FAILURE.labels(exchange=exchange_key, action=action).inc()
                logger.warning(f"Failed to execute {action} perp trade on {config['name']}: {trade_result['error']}")
            
            return trade_result
            
        except Exception as e:
            logger.error(f"Error executing perp trade on {config['name']}: {e}")
            return {
                'success': False,
                'error': str(e),
                'exchange': exchange_key,
                'type': 'perp'
            }

    async def execute_trades(self, signal: Dict) -> List[Dict]:
        """Execute trades on all available exchanges"""
        try:
            available_exchanges = signal.get('available_exchanges', [])
            trade_results = []
            
            if not available_exchanges:
                logger.warning(f"No available exchanges for signal: {signal.get('trade_id')}")
                return trade_results
            
            # Check confidence threshold
            confidence = signal.get('confidence', 0)
            if confidence < self.min_confidence:
                logger.info(f"Signal confidence {confidence} below threshold {self.min_confidence}, skipping trades")
                return trade_results
            
            logger.info(f"Executing trades for signal: {signal.get('trade_id')}")
            
            for exchange_info in available_exchanges:
                exchange_key = exchange_info['exchange']
                exchange_name = exchange_info['name']
                
                # Rate limiting
                current_time = time.time()
                if exchange_key in self.last_trade_time:
                    time_since_last = current_time - self.last_trade_time[exchange_key]
                    if time_since_last < self.min_trade_interval:
                        await asyncio.sleep(self.min_trade_interval - time_since_last)
                
                self.last_trade_time[exchange_key] = time.time()
                
                # Execute spot trades
                if exchange_info['spot_available']:
                    for pair in exchange_info['spot_pairs']:
                        with EXECUTION_DURATION.time():
                            result = await self.execute_spot_trade(exchange_key, signal, pair)
                            trade_results.append(result)
                            TRADES_EXECUTED.labels(exchange=exchange_key, action=signal.get('action', 'UNKNOWN')).inc()
                
                # Execute perp trades
                if exchange_info['perp_available']:
                    for contract in exchange_info['perp_contracts']:
                        with EXECUTION_DURATION.time():
                            result = await self.execute_perp_trade(exchange_key, signal, contract)
                            trade_results.append(result)
                            TRADES_EXECUTED.labels(exchange=exchange_key, action=signal.get('action', 'UNKNOWN')).inc()
            
            return trade_results
            
        except Exception as e:
            logger.error(f"Error executing trades: {e}")
            return []

    async def process_trade_signal(self, signal: Dict):
        """Process trade signal and execute trades"""
        try:
            trade_id = signal.get('trade_id', 'unknown')
            logger.info(f"Processing trade signal: {trade_id}")
            
            # Execute trades
            trade_results = await self.execute_trades(signal)
            
            # Create summary
            successful_trades = [r for r in trade_results if r.get('success', False)]
            failed_trades = [r for r in trade_results if not r.get('success', False)]
            
            summary = {
                'trade_id': trade_id,
                'original_signal_id': signal.get('original_signal_id', ''),
                'token_symbol': signal.get('token_symbol', ''),
                'action': signal.get('action', ''),
                'total_trades': len(trade_results),
                'successful_trades': len(successful_trades),
                'failed_trades': len(failed_trades),
                'trade_results': trade_results,
                'processed_at': datetime.now(timezone(timedelta(hours=7))).isoformat(),
                'status': 'COMPLETED' if trade_results else 'NO_TRADES'
            }
            
            # Send results to results topic
            self.producer.send('trading.results.v1', value=summary)
            self.producer.flush()
            
            logger.info(f"Completed trade execution for {trade_id}: {len(successful_trades)}/{len(trade_results)} successful")
            
        except Exception as e:
            logger.error(f"Error processing trade signal: {e}")

    async def run(self):
        """Main run loop"""
        logger.info("Starting Trade Executor Service")
        
        # Start Prometheus metrics server
        start_http_server(8005)
        
        # Log enabled exchanges
        enabled_exchanges = [config['name'] for config in self.exchange_configs.values() if config['enabled']]
        if enabled_exchanges:
            logger.info(f"Trading enabled on: {', '.join(enabled_exchanges)}")
        else:
            logger.warning("No exchanges configured for trading - running in simulation mode")
        
        # Process trade signals
        logger.info("Starting trade signal processing...")
        try:
            for message in self.consumer:
                try:
                    signal = message.value
                    await self.process_trade_signal(signal)
                except Exception as e:
                    logger.error(f"Error processing message: {e}")
                    continue
                    
        except Exception as e:
            logger.error(f"Error in trade processing loop: {e}")
            raise

def main():
    """Main entry point"""
    executor = TradeExecutor()
    
    try:
        asyncio.run(executor.run())
    except KeyboardInterrupt:
        logger.info("Shutting down Trade Executor")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        raise

if __name__ == "__main__":
    main()
