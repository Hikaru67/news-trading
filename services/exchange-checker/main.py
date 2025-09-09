#!/usr/bin/env python3
"""
Exchange Checker Service - Check token listings on MEXC, Bybit, Gate
Detects where tokens are listed (spot/perp) and creates trades automatically
"""

import asyncio
import json
import logging
import os
import time
import aiohttp
import redis
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
from kafka import KafkaConsumer, KafkaProducer
from prometheus_client import Counter, Histogram, start_http_server

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Prometheus metrics
TOKENS_CHECKED = Counter('exchange_tokens_checked_total', 'Total tokens checked for listings', ['exchange'])
LISTINGS_FOUND = Counter('exchange_listings_found_total', 'Total listings found', ['exchange', 'type'])
TRADES_CREATED = Counter('exchange_trades_created_total', 'Total trades created', ['exchange', 'action'])
CHECK_DURATION = Histogram('exchange_check_duration_seconds', 'Time spent checking exchanges')

class ExchangeChecker:
    """Service to check token listings across exchanges and create trades"""
    
    def __init__(self):
        # Redis for caching
        self.redis_url = os.getenv('REDIS_URL', 'redis://localhost:6379')
        self.redis_client = redis.from_url(self.redis_url)
        
        # Kafka setup
        self.kafka_servers = os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'localhost:9092').split(',')
        
        # Consumer for news signals
        self.consumer = KafkaConsumer(
            'news.signals.v1',
            bootstrap_servers=self.kafka_servers,
            value_deserializer=lambda m: json.loads(m.decode('utf-8')),
            group_id='exchange-checker-group',
            auto_offset_reset='latest',
            enable_auto_commit=True
        )
        
        # Producer for trade signals
        self.producer = KafkaProducer(
            bootstrap_servers=self.kafka_servers,
            value_serializer=lambda v: json.dumps(v).encode('utf-8')
        )
        
        # Exchange configurations
        self.exchanges = {
            'mexc': {
                'name': 'MEXC',
                'spot_api': 'https://api.mexc.com/api/v3/exchangeInfo',
                'perp_api': 'https://contract.mexc.com/api/v1/contract/detail',
                'trading_pairs': set(),
                'perp_contracts': set()
            },
            'bybit': {
                'name': 'Bybit',
                'spot_api': 'https://api.bybit.com/v5/market/instruments-info?category=spot',
                'perp_api': 'https://api.bybit.com/v5/market/instruments-info?category=linear',
                'trading_pairs': set(),
                'perp_contracts': set()
            },
            'gate': {
                'name': 'Gate.io',
                'spot_api': 'https://api.gateio.ws/api/v4/spot/currency_pairs',
                'perp_api': 'https://api.gateio.ws/api/v4/futures/usdt/contracts',
                'trading_pairs': set(),
                'perp_contracts': set()
            }
        }
        
        # Cache TTL (1 hour)
        self.cache_ttl = 3600
        
        # Rate limiting
        self.last_check_time = {}
        self.min_check_interval = 5  # seconds between checks

    async def fetch_exchange_data(self, exchange_key: str) -> bool:
        """Fetch trading pairs and contracts from exchange"""
        exchange = self.exchanges[exchange_key]
        
        try:
            async with aiohttp.ClientSession() as session:
                # Fetch spot trading pairs
                spot_pairs = await self.fetch_spot_pairs(session, exchange_key)
                if spot_pairs:
                    exchange['trading_pairs'] = set(spot_pairs)
                    logger.info(f"Fetched {len(spot_pairs)} spot pairs from {exchange['name']}")
                
                # Fetch perpetual contracts
                perp_contracts = await self.fetch_perp_contracts(session, exchange_key)
                if perp_contracts:
                    exchange['perp_contracts'] = set(perp_contracts)
                    logger.info(f"Fetched {len(perp_contracts)} perp contracts from {exchange['name']}")
                
                return True
                
        except Exception as e:
            logger.error(f"Error fetching data from {exchange['name']}: {e}")
            return False

    async def fetch_spot_pairs(self, session: aiohttp.ClientSession, exchange_key: str) -> List[str]:
        """Fetch spot trading pairs from exchange"""
        exchange = self.exchanges[exchange_key]
        
        try:
            if exchange_key == 'mexc':
                async with session.get(exchange['spot_api']) as response:
                    if response.status == 200:
                        data = await response.json()
                        pairs = []
                        for symbol in data.get('symbols', []):
                            if symbol.get('status') == 'ENABLED':
                                pairs.append(symbol['symbol'])
                        return pairs
            
            elif exchange_key == 'bybit':
                async with session.get(exchange['spot_api']) as response:
                    if response.status == 200:
                        data = await response.json()
                        pairs = []
                        for item in data.get('result', {}).get('list', []):
                            if item.get('status') == 'Trading':
                                pairs.append(item['symbol'])
                        return pairs
            
            elif exchange_key == 'gate':
                async with session.get(exchange['spot_api']) as response:
                    if response.status == 200:
                        data = await response.json()
                        pairs = []
                        for pair in data:
                            if pair.get('trade_status') == 'tradable':
                                pairs.append(pair['id'])
                        return pairs
                        
        except Exception as e:
            logger.error(f"Error fetching spot pairs from {exchange['name']}: {e}")
        
        return []

    async def fetch_perp_contracts(self, session: aiohttp.ClientSession, exchange_key: str) -> List[str]:
        """Fetch perpetual contracts from exchange"""
        exchange = self.exchanges[exchange_key]
        
        try:
            if exchange_key == 'mexc':
                async with session.get(exchange['perp_api']) as response:
                    if response.status == 200:
                        data = await response.json()
                        contracts = []
                        for contract in data.get('data', []):
                            if contract.get('state') == 1:  # Active
                                contracts.append(contract['symbol'])
                        return contracts
            
            elif exchange_key == 'bybit':
                async with session.get(exchange['perp_api']) as response:
                    if response.status == 200:
                        data = await response.json()
                        contracts = []
                        for item in data.get('result', {}).get('list', []):
                            if item.get('status') == 'Trading':
                                contracts.append(item['symbol'])
                        return contracts
            
            elif exchange_key == 'gate':
                async with session.get(exchange['perp_api']) as response:
                    if response.status == 200:
                        data = await response.json()
                        contracts = []
                        for contract in data:
                            if contract.get('trade_status') == 'tradable':
                                contracts.append(contract['name'])
                        return contracts
                        
        except Exception as e:
            logger.error(f"Error fetching perp contracts from {exchange['name']}: {e}")
        
        return []

    def extract_token_symbol(self, signal: Dict) -> Optional[str]:
        """Extract token symbol from signal"""
        try:
            # Try to get from primary_entity
            primary_entity = signal.get('primary_entity', '')
            if primary_entity and primary_entity != 'N/A':
                return primary_entity.upper()
            
            # Try to extract from headline
            headline = signal.get('headline', '')
            if '$' in headline:
                import re
                match = re.search(r'\$([A-Z0-9]+)', headline)
                if match:
                    return match.group(1)
            
            # Try to extract from entities
            entities = signal.get('entities', [])
            for entity in entities:
                if len(entity) <= 10 and entity.isalpha():
                    return entity.upper()
            
            return None
            
        except Exception as e:
            logger.error(f"Error extracting token symbol: {e}")
            return None

    def check_token_listings(self, token_symbol: str) -> Dict[str, Dict]:
        """Check where token is listed across exchanges"""
        listings = {}
        
        for exchange_key, exchange in self.exchanges.items():
            listings[exchange_key] = {
                'spot': False,
                'perp': False,
                'spot_pairs': [],
                'perp_contracts': []
            }
            
            # Check spot listings
            for pair in exchange['trading_pairs']:
                if token_symbol in pair.upper():
                    listings[exchange_key]['spot'] = True
                    listings[exchange_key]['spot_pairs'].append(pair)
            
            # Check perp listings
            for contract in exchange['perp_contracts']:
                if token_symbol in contract.upper():
                    listings[exchange_key]['perp'] = True
                    listings[exchange_key]['perp_contracts'].append(contract)
        
        return listings

    def create_trade_signal(self, signal: Dict, listings: Dict[str, Dict]) -> Optional[Dict]:
        """Create trade signal based on listings found"""
        try:
            event_type = signal.get('event_type', '')
            token_symbol = self.extract_token_symbol(signal)
            
            if not token_symbol:
                logger.warning(f"Could not extract token symbol from signal: {signal.get('event_id')}")
                return None
            
            # Determine trading action based on event type
            if event_type == 'LISTING':
                action = 'BUY'
                reason = 'New listing detected'
            elif event_type == 'DELIST':
                action = 'SELL'
                reason = 'Delisting detected'
            elif event_type == 'HACK':
                action = 'SELL'
                reason = 'Security incident detected'
            else:
                logger.info(f"Event type {event_type} not supported for auto-trading")
                return None
            
            # Find exchanges where token is listed
            available_exchanges = []
            for exchange_key, listing_info in listings.items():
                if listing_info['spot'] or listing_info['perp']:
                    available_exchanges.append({
                        'exchange': exchange_key,
                        'name': self.exchanges[exchange_key]['name'],
                        'spot_available': listing_info['spot'],
                        'perp_available': listing_info['perp'],
                        'spot_pairs': listing_info['spot_pairs'],
                        'perp_contracts': listing_info['perp_contracts']
                    })
            
            if not available_exchanges:
                logger.info(f"Token {token_symbol} not found on any supported exchange")
                return None
            
            # Create trade signal
            trade_signal = {
                'trade_id': f"auto_{signal.get('event_id', '')}_{int(time.time())}",
                'original_signal_id': signal.get('event_id', ''),
                'token_symbol': token_symbol,
                'action': action,
                'reason': reason,
                'event_type': event_type,
                'severity': signal.get('severity', 0),
                'confidence': signal.get('confidence', 0),
                'available_exchanges': available_exchanges,
                'created_at': datetime.now(timezone(timedelta(hours=7))).isoformat(),
                'status': 'PENDING'
            }
            
            return trade_signal
            
        except Exception as e:
            logger.error(f"Error creating trade signal: {e}")
            return None

    async def process_signal(self, signal: Dict):
        """Process news signal and check for token listings"""
        try:
            event_type = signal.get('event_type', '')
            
            # Only process high-impact events
            if event_type not in ['LISTING', 'DELIST', 'HACK']:
                return
            
            severity = signal.get('severity', 0)
            if severity < 0.7:  # Only high severity events
                return
            
            # Extract token symbol
            token_symbol = self.extract_token_symbol(signal)
            if not token_symbol:
                logger.warning(f"Could not extract token symbol from signal: {signal.get('event_id')}")
                return
            
            logger.info(f"Processing {event_type} signal for token: {token_symbol}")
            
            # Check if we need to refresh exchange data
            cache_key = f"exchange_data_refresh"
            if not self.redis_client.exists(cache_key):
                logger.info("Refreshing exchange data...")
                for exchange_key in self.exchanges.keys():
                    await self.fetch_exchange_data(exchange_key)
                    TOKENS_CHECKED.labels(exchange=exchange_key).inc()
                
                # Cache for 1 hour
                self.redis_client.setex(cache_key, self.cache_ttl, "1")
            
            # Check token listings
            with CHECK_DURATION.time():
                listings = self.check_token_listings(token_symbol)
            
            # Log findings
            for exchange_key, listing_info in listings.items():
                if listing_info['spot'] or listing_info['perp']:
                    exchange_name = self.exchanges[exchange_key]['name']
                    spot_count = len(listing_info['spot_pairs'])
                    perp_count = len(listing_info['perp_contracts'])
                    logger.info(f"Found {token_symbol} on {exchange_name}: {spot_count} spot pairs, {perp_count} perp contracts")
                    
                    LISTINGS_FOUND.labels(exchange=exchange_key, type='spot').inc(spot_count)
                    LISTINGS_FOUND.labels(exchange=exchange_key, type='perp').inc(perp_count)
            
            # Create trade signal
            trade_signal = self.create_trade_signal(signal, listings)
            if trade_signal:
                # Send to trade execution topic
                self.producer.send('trading.signals.v1', value=trade_signal)
                self.producer.flush()
                
                logger.info(f"Created trade signal: {trade_signal['trade_id']}")
                TRADES_CREATED.labels(exchange='multiple', action=trade_signal['action']).inc()
            
        except Exception as e:
            logger.error(f"Error processing signal: {e}")

    async def run(self):
        """Main run loop"""
        logger.info("Starting Exchange Checker Service")
        
        # Start Prometheus metrics server
        start_http_server(8004)
        
        # Initial exchange data fetch
        logger.info("Fetching initial exchange data...")
        for exchange_key in self.exchanges.keys():
            await self.fetch_exchange_data(exchange_key)
        
        # Process signals
        logger.info("Starting signal processing...")
        try:
            for message in self.consumer:
                try:
                    signal = message.value
                    await self.process_signal(signal)
                except Exception as e:
                    logger.error(f"Error processing message: {e}")
                    continue
                    
        except Exception as e:
            logger.error(f"Error in signal processing loop: {e}")
            raise

def main():
    """Main entry point"""
    checker = ExchangeChecker()
    
    try:
        asyncio.run(checker.run())
    except KeyboardInterrupt:
        logger.info("Shutting down Exchange Checker")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        raise

if __name__ == "__main__":
    main()
