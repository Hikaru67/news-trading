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
from aiohttp import web
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

    async def fetch_exchange_data(self, exchange_key: str, session: aiohttp.ClientSession) -> bool:
        """Fetch trading pairs and contracts from exchange"""
        exchange = self.exchanges[exchange_key]
        
        try:
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
        token_upper = (token_symbol or '').upper()
        # Generate alias candidates for matching across exchanges
        alias_candidates = [
            token_upper,
            f"{token_upper}USDT",
            f"{token_upper}_USDT",
            f"{token_upper}USD",
            f"{token_upper}_USD",
            f"{token_upper}USDC",
            f"{token_upper}_USDC",
        ]
        
        for exchange_key, exchange in self.exchanges.items():
            listings[exchange_key] = {
                'spot': False,
                'perp': False,
                'spot_pairs': [],
                'perp_contracts': []
            }
            
            # Check spot listings
            for pair in exchange['trading_pairs']:
                up = pair.upper()
                if any(alias == up or alias in up for alias in alias_candidates):
                    listings[exchange_key]['spot'] = True
                    listings[exchange_key]['spot_pairs'].append(pair)
            
            # Check perp listings
            for contract in exchange['perp_contracts']:
                up = contract.upper()
                if any(alias == up or alias in up for alias in alias_candidates):
                    listings[exchange_key]['perp'] = True
                    listings[exchange_key]['perp_contracts'].append(contract)
        
        return listings

    def choose_best_cex(self, token_symbol: str) -> Optional[Dict]:
        """Choose best CEX and market (prefer perp over spot) with priority: mexc, bybit, gate"""
        listings = self.check_token_listings(token_symbol)
        priority = ['mexc', 'bybit', 'gate']
        for ex in priority:
            info = listings.get(ex, {})
            # Prefer perp
            if info.get('perp') and info.get('perp_contracts'):
                return {
                    'exchange': ex,
                    'market': 'perp',
                    'symbols': info.get('perp_contracts', [])
                }
            if info.get('spot') and info.get('spot_pairs'):
                return {
                    'exchange': ex,
                    'market': 'spot',
                    'symbols': info.get('spot_pairs', [])
                }
        return None

    async def fetch_price(self, session: aiohttp.ClientSession, exchange_key: str, market: str, token_symbol: str, symbols: List[str]) -> Optional[float]:
        """Fetch approximate price in USDT for the chosen market/symbol."""
        try:
            # Short TTL cache to reduce duplicate network calls
            cache_symbol = None
            for s in symbols:
                if 'USDT' in s.upper():
                    cache_symbol = s
                    break
            if not cache_symbol:
                cache_symbol = f"{token_symbol.upper()}USDT"
            cache_key = f"px:{exchange_key}:{market}:{cache_symbol}"
            cached = self.redis_client.get(cache_key)
            if cached:
                try:
                    return float(cached)
                except Exception:
                    pass

            # Try to construct a USDT pair/contract
            # Pick first symbol that contains USDT
            usdt_symbol = None
            for s in symbols:
                if 'USDT' in s.upper():
                    usdt_symbol = s
                    break
            # Fallback to token + USDT
            if not usdt_symbol:
                usdt_symbol = f"{token_symbol.upper()}USDT"

            if exchange_key == 'mexc':
                common_headers = {
                    'User-Agent': 'news-trading-bot/1.0',
                    'Accept': 'application/json'
                }
                if market == 'spot':
                    url = f"https://api.mexc.com/api/v3/ticker/price?symbol={usdt_symbol}"
                    logger.info(f"[price] MEXC spot symbol={usdt_symbol} url=curl -s '{url}'")
                    async with session.get(url, headers=common_headers, timeout=5) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            price = float(data.get('price'))
                            self.redis_client.setex(cache_key, 3, str(price))
                            return price
                        else:
                            try:
                                text = await resp.text()
                            except Exception:
                                text = '<no-body>'
                            logger.error(f"[price] MEXC spot HTTP {resp.status} body={text[:200]}")
                else:
                    # Perp price endpoint
                    # MEXC perp requires underscore: e.g., KTA_USDT
                    mexc_perp_symbol = usdt_symbol if '_' in usdt_symbol else usdt_symbol.replace('USDT', '_USDT')
                    url = f"https://contract.mexc.com/api/v1/contract/ticker?symbol={mexc_perp_symbol}"
                    logger.info(f"[price] MEXC perp symbol={mexc_perp_symbol} url=curl -s '{url}'")
                    async with session.get(url, headers=common_headers, timeout=5) as resp:
                        if resp.status == 200:
                            try:
                                data = await resp.json()
                            except Exception as je:
                                text = await resp.text()
                                logger.error(f"[price] MEXC perp JSON parse error: {je} body={text[:200]}")
                                return None
                            arr = data.get('data', [])
                            if isinstance(arr, dict):
                                # Some APIs return object instead of list
                                last_price = arr.get('lastPrice') or arr.get('last')
                                if last_price is not None:
                                    price = float(last_price)
                                    self.redis_client.setex(cache_key, 3, str(price))
                                    return price
                            if isinstance(arr, list) and arr:
                                last_price = arr[0].get('lastPrice') or arr[0].get('last')
                                if last_price is not None:
                                    price = float(last_price)
                                    self.redis_client.setex(cache_key, 3, str(price))
                                    return price
                        else:
                            try:
                                text = await resp.text()
                            except Exception:
                                text = '<no-body>'
                            logger.error(f"[price] MEXC perp HTTP {resp.status} body={text[:200]}")
            elif exchange_key == 'bybit':
                if market == 'spot':
                    url = f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={usdt_symbol}"
                else:
                    url = f"https://api.bybit.com/v5/market/tickers?category=linear&symbol={usdt_symbol}"
                logger.info(f"[price] Bybit {market} symbol={usdt_symbol} url=curl -s '{url}'")
                async with session.get(url, timeout=3) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        lst = data.get('result', {}).get('list', [])
                        if lst:
                            price = float(lst[0].get('lastPrice'))
                            self.redis_client.setex(cache_key, 3, str(price))
                            return price
            elif exchange_key == 'gate':
                if market == 'spot':
                    # Gate spot uses pair like KTA_USDT
                    pair = usdt_symbol.replace('USDT', '_USDT')
                    url = f"https://api.gateio.ws/api/v4/spot/tickers?currency_pair={pair}"
                    logger.info(f"[price] Gate spot symbol={pair} url=curl -s '{url}'")
                    async with session.get(url, timeout=3) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if isinstance(data, list) and data:
                                price = float(data[0].get('last'))
                                self.redis_client.setex(cache_key, 3, str(price))
                                return price
                else:
                    url = "https://api.gateio.ws/api/v4/futures/usdt/tickers"
                    logger.info(f"[price] Gate perp list url=curl -s '{url}' (filter symbol={usdt_symbol})")
                    async with session.get(url, timeout=3) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            for item in data:
                                if item.get('contract') == usdt_symbol:
                                    price = float(item.get('last'))
                                    self.redis_client.setex(cache_key, 3, str(price))
                                    return price
        except Exception as e:
            logger.error(f"Price fetch error: {e}")
        return None

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
        """Main run loop supporting Kafka and HTTP intake"""
        logger.info("Starting Exchange Checker Service")
        
        # Start Prometheus metrics server
        start_http_server(8004)
        
        # Shared HTTP session
        timeout = aiohttp.ClientTimeout(total=5)
        connector = aiohttp.TCPConnector(limit=100, ttl_dns_cache=300)
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as shared_session:
            # Initial exchange data fetch
            logger.info("Fetching initial exchange data...")
            for exchange_key in self.exchanges.keys():
                await self.fetch_exchange_data(exchange_key, shared_session)

        mode = os.getenv('INPUT_MODE', 'kafka').lower()
        if mode == 'http':
            app = web.Application()

            async def handle_health(request):
                return web.json_response({"status": "ok"})

            async def handle_signal(request):
                try:
                    signal = await request.json()
                except Exception:
                    return web.json_response({"ok": False, "error": "invalid_json"}, status=400)

                # Process signal (Kafka path, metrics, etc.)
                await self.process_signal(signal)

                # Compute best CEX + price and attach to signal.extras.best_cex
                token = self.extract_token_symbol(signal) or ''
                best = None
                price_val = None
                try:
                    best = self.choose_best_cex(token) if token else None
                    if best:
                        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session2:
                            price_val = await self.fetch_price(session2, best['exchange'], best['market'], token, best.get('symbols', []))
                            # If current best is not MEXC, probe MEXC perp to prefer it when available
                            if best.get('exchange') != 'mexc':
                                try:
                                    mexc_price = await self.fetch_price(session2, 'mexc', 'perp', token, [f"{token.upper()}_USDT"])
                                    if mexc_price is not None:
                                        logger.info(f"[best_cex] Overriding to MEXC perp due to availability, token={token}")
                                        best = {
                                            'exchange': 'mexc',
                                            'market': 'perp',
                                            'symbols': [f"{token.upper()}_USDT"],
                                        }
                                        price_val = mexc_price
                                except Exception as mexc_probe_e:
                                    logger.error(f"MEXC priority probe error: {mexc_probe_e}")
                    # Fallback: if no best or no price, probe MEXC perp directly with TOKEN_USDT underscore format
                    if (not best or price_val is None) and token:
                        try:
                            async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session_fallback:
                                fb_price = await self.fetch_price(session_fallback, 'mexc', 'perp', token, [f"{token.upper()}_USDT"])
                                if fb_price is not None:
                                    best = {
                                        'exchange': 'mexc',
                                        'market': 'perp',
                                        'symbols': [f"{token.upper()}_USDT"],
                                    }
                                    price_val = fb_price
                        except Exception as fb_e:
                            logger.error(f"MEXC fallback error: {fb_e}")
                except Exception as e:
                    logger.error(f"best_cex computation error: {e}")

                extras = signal.get('extras') or {}
                if best:
                    best_payload = {
                        'exchange': best['exchange'],
                        'market': best['market'],
                        'symbols': best.get('symbols', []),
                        'price': price_val
                    }
                    extras['best_cex'] = best_payload
                signal['extras'] = extras

                # Optionally forward to Telegram directly
                tele_forwarded = False
                tele_url = os.getenv('TELEGRAM_HTTP_URL')
                if tele_url:
                    try:
                        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session3:
                            async with session3.post(tele_url, json=signal, timeout=10) as resp:
                                tele_forwarded = resp.status == 200
                    except Exception as e:
                        logger.error(f"Error forwarding to telegram: {e}")

                # Optionally forward to trade-executor directly
                trade_forwarded = False
                trade_url = os.getenv('TRADE_EXECUTOR_HTTP_URL')
                if trade_url:
                    trade_signal = self.create_trade_signal(signal, self.check_token_listings(token) if token else {})
                    if trade_signal:
                        try:
                            async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session4:
                                async with session4.post(trade_url, json=trade_signal, timeout=10) as resp:
                                    trade_forwarded = resp.status == 200
                        except Exception as e:
                            logger.error(f"Error forwarding to trade executor: {e}")

                return web.json_response({"ok": True, "tele_forwarded": tele_forwarded, "trade_forwarded": trade_forwarded, "best_cex": extras.get('best_cex')})

            app.router.add_get('/health', handle_health)
            app.router.add_post('/signal', handle_signal)

            port = int(os.getenv('EXCHANGE_CHECKER_HTTP_PORT', '8014'))
            logger.info(f"HTTP intake listening on 0.0.0.0:{port}")
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, '0.0.0.0', port)
            await site.start()
            while True:
                await asyncio.sleep(3600)
        else:
            # Process signals from Kafka
            logger.info("Starting signal processing (Kafka)...")
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
