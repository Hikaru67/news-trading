import asyncio
import json
import logging
import websockets
import feedparser
import hashlib
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional
import aiohttp
from kafka import KafkaProducer
import redis
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class WebSocketRSSClient:
    """Real-time RSS client using WebSocket for push-based news ingestion"""
    
    def __init__(self):
        self.kafka_producer = KafkaProducer(
            bootstrap_servers=os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'kafka:29092'),
            value_serializer=lambda v: json.dumps(v).encode('utf-8'),
            key_serializer=lambda k: (
                k if isinstance(k, bytes) else (k.encode('utf-8') if k is not None else None)
            )
        )
        
        self.redis_client = redis.Redis.from_url(
            os.getenv('REDIS_URL', 'redis://redis:6379')
        )
        
        # RSS sources - Only BWEnews
        self.rss_sources = {
            'bwenews': {
                'url': 'https://rss-public.bwe-ws.com/',  # RSS feed
                'trust_score': 0.90,
                'websocket_url': None,  # Disable WebSocket, use RSS only
                'polling_fallback': True,
                'category': 'crypto_news',
                'language': 'en',
                'api_type': 'bwenews'  # Special handling for BWEnews format
            }
        }
        
        # Event patterns for classification - ONLY specific token events
        self.event_patterns = {
            'LISTING': [
                # Specific listing patterns only
                'will list', 'lists', 'listing', 'new listing', 'adds support for',
                'trading pairs', 'spot trading', 'futures trading', 'trading support for',
                'new trading', 'trading available', 'now trading', 'trading begins',
                'trading starts', 'trading launch', 'trading open', 'trading live',
                'trading active', 'trading enabled', 'trading launched',
                # Korean patterns
                '상장', '거래지원', '신규상장', '거래쌍', '스팟거래', '선물거래',
                '추가지원', '거래시작', '상장예정', '거래개시', '거래오픈', '거래활성화'
            ],
            'DELIST': [
                # Specific delisting patterns only
                'delist', 'suspends', 'removes', 'discontinues', 'trading halt',
                'trading suspension', 'delisting', 'removes support for',
                # Korean patterns
                '상장폐지', '거래중단', '거래정지', '지원중단', '제거', '중단'
            ],
            'HACK': [
                # Specific hack patterns only
                'hack', 'exploit', 'drained', 'breach', 'security incident',
                'vulnerability', 'attack', 'compromised', 'hacked', 'exploited',
                # Korean patterns
                '해킹', '침해', '보안사고', '취약점', '공격', '침입', '도난'
            ]
            # Removed all non-token specific events (REGULATION, FED_SPEECH, POLITICAL_POST, 
            # TECHNICAL_UPGRADE, PARTNERSHIP) to focus only on token-specific events
        }
        
        # WebSocket connections
        self.websocket_connections = {}
        self.running = False
        
    async def connect_websocket(self, source_name: str, source_config: Dict):
        """Connect to WebSocket RSS feed"""
        try:
            websocket_url = source_config.get('websocket_url')
            if not websocket_url:
                logger.warning(f"No WebSocket URL for {source_name}, using polling fallback")
                return None
                
            logger.info(f"Connecting to WebSocket: {websocket_url}")
            websocket = await websockets.connect(websocket_url)
            self.websocket_connections[source_name] = websocket
            
            # Start listening for real-time updates
            asyncio.create_task(self.listen_websocket(source_name, websocket))
            logger.info(f"Connected to WebSocket for {source_name}")
            return websocket
            
        except Exception as e:
            logger.error(f"Failed to connect to WebSocket for {source_name}: {e}")
            return None
    
    async def listen_websocket(self, source_name: str, websocket):
        """Listen for real-time WebSocket updates"""
        try:
            # Send ping to maintain connection (for BWEnews)
            if source_name == 'bwenews':
                await websocket.send('ping')
                
            async for message in websocket:
                try:
                    # Handle BWEnews ping/pong
                    if source_name == 'bwenews' and message == 'pong':
                        logger.debug(f"Received pong from {source_name}")
                        continue
                    
                    # Parse WebSocket message
                    data = json.loads(message)
                    
                    # Handle BWEnews API format
                    if source_name == 'bwenews':
                        await self.process_bwenews_message(source_name, data)
                    else:
                        # Process standard RSS entry
                        if 'rss_entry' in data:
                            await self.process_rss_entry(source_name, data['rss_entry'])
                        elif 'news_update' in data:
                            await self.process_news_update(source_name, data['news_update'])
                        
                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON from {source_name} WebSocket")
                except Exception as e:
                    logger.error(f"Error processing WebSocket message from {source_name}: {e}")
                    
        except websockets.exceptions.ConnectionClosed:
            logger.info(f"WebSocket connection closed for {source_name}")
        except Exception as e:
            logger.error(f"WebSocket error for {source_name}: {e}")
        finally:
            # Remove from active connections
            if source_name in self.websocket_connections:
                del self.websocket_connections[source_name]
    
    async def process_rss_entry(self, source_name: str, entry: Dict):
        """Process RSS entry from WebSocket"""
        try:
            # Create content hash for deduplication
            raw_content = f"{entry.get('title', '')} {entry.get('summary', '')}"
            content_hash = hashlib.sha256(raw_content.encode()).hexdigest()
            
            # Check for duplicates
            if self.is_duplicate(content_hash):
                logger.info(f"Duplicate RSS entry detected: {content_hash}")
                return None
            
            # Extract event information
            text = f"{entry.get('title', '')} {entry.get('summary', '')}"
            event_type, direction, severity = self.extract_event_type(text)
            entities = self.extract_entities(text, entry.get('coins_included', []))
            
            # Only process token-specific events (LISTING, DELIST, HACK) for non-bwenews sources.
            # For 'bwenews', allow all events to pass through.
            if source_name != 'bwenews' and event_type not in ['LISTING', 'DELIST', 'HACK']:
                logger.info(f"Skipping non-token-specific event: {event_type} from {source_name}")
                return None
            
            # Parse published time and convert to +7 timezone
            news_published_time = self.parse_published_time(entry.get('published', ''))
            
            # Get market cap data for tokens
            market_caps = self.get_market_caps(entities) if entities else {}
            
            # Create signal with new BWEnews format
            signal = {
                'event_id': content_hash,  # Use content hash for consistent deduplication
                'ts_iso': news_published_time,
                'source': source_name,
                'headline': entry.get('title', ''),
                'url': entry.get('link', ''),
                'event_type': event_type,
                'primary_entity': entities[0] if entities else '',
                'entities': entities,
                'severity': severity,
                'direction': direction,
                'confidence': self.rss_sources[source_name]['trust_score'],
                'raw_text': text,
                'market_caps': market_caps,  # Add market cap data
                'formatted_message': self.format_bwenews_message(
                    event_type, entry.get('title', ''), entry.get('summary', ''), 
                    entities, market_caps, entry.get('link', ''), news_published_time
                ),
                'extras': {
                    'content_hash': content_hash,
                    'source_url': entry.get('link', ''),
                    'published': entry.get('published', ''),
                    'ingestion_method': 'websocket',
                    'latency_ms': 0  # Real-time, no latency
                }
            }
            
            # Mark as processed
            self.mark_as_processed(content_hash)
            
            # Publish to Kafka
            await self.publish_to_kafka(signal)
            
            logger.info(f"Processed real-time RSS entry: {event_type} from {source_name}")
            
        except Exception as e:
            logger.error(f"Error processing RSS entry: {e}")
    
    async def process_news_update(self, source_name: str, update: Dict):
        """Process news update from WebSocket"""
        try:
            # Similar to RSS entry but for different format
            await self.process_rss_entry(source_name, update)
        except Exception as e:
            logger.error(f"Error processing news update: {e}")
    
    async def process_bwenews_message(self, source_name: str, data: Dict):
        """Process BWEnews API message format"""
        try:
            # BWEnews API format:
            # {
            #   "source_name": "BWENEWS",
            #   "news_title": "This is a test message news",
            #   "coins_included": ["BTC", "ETH", "SOL"],
            #   "url": "https://bwenews123.com/asdads",
            #   "timestamp": 1745770800
            # }
            
            # Convert BWEnews format to standard RSS entry format
            entry = {
                'title': data.get('news_title', ''),
                'summary': data.get('news_title', ''),  # Use title as summary
                'link': data.get('url', ''),
                'published': self.timestamp_to_iso(data.get('timestamp', 0)),
                'coins_included': data.get('coins_included', [])
            }
            
            # Process as standard RSS entry
            await self.process_rss_entry(source_name, entry)
            
        except Exception as e:
            logger.error(f"Error processing BWEnews message: {e}")
    
    def timestamp_to_iso(self, timestamp: int) -> str:
        """Convert Unix timestamp to ISO format"""
        try:
            from datetime import datetime, timezone
            dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
            return dt.isoformat()
        except:
            return datetime.now().isoformat()
    
    def get_market_caps(self, entities: List[str]) -> Dict[str, str]:
        """Get market cap data for entities (mock data for now)"""
        # Mock market cap data - in real implementation, this would call an API
        mock_market_caps = {
            'KTA': '$376M',
            'NOICE': '$7M',
            'BTC': '$1.2T',
            'ETH': '$400B',
            'SOL': '$50B'
        }
        
        market_caps = {}
        for entity in entities:
            if entity.upper() in mock_market_caps:
                market_caps[entity.upper()] = mock_market_caps[entity.upper()]
            else:
                # Generate mock market cap for unknown tokens
                market_caps[entity.upper()] = '$1M'  # Default mock value
        
        return market_caps
    
    def format_bwenews_message(self, event_type: str, title: str, summary: str, 
                              entities: List[str], market_caps: Dict[str, str], 
                              url: str, published_time: str) -> str:
        """Format BWEnews message in the requested format"""
        
        # Event type mapping
        event_mapping = {
            'LISTING': 'COINBASE LISTING',
            'DELIST': 'COINBASE DELIST',
            'HACK': 'SECURITY ALERT',
            'OTHER': 'BWEnews Alert'
        }
        
        event_title = event_mapping.get(event_type, 'BWEnews Alert')
        
        # Format entities with market caps
        entities_with_caps = []
        for entity in entities:
            cap = market_caps.get(entity.upper(), 'N/A')
            entities_with_caps.append(f"${entity}  MarketCap: {cap}")
        
        entities_text = '\n'.join(entities_with_caps) if entities_with_caps else 'N/A'
        
        # Format timestamp
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(published_time.replace('Z', '+00:00'))
            formatted_time = dt.strftime('%Y-%m-%d %H:%M:%S')
        except:
            formatted_time = published_time
        
        # Create formatted message
        message = f"""{event_title}: {title}
{url}

{event_title} 上新: {title}

{entities_text}
(Auto match could be wrong, 自动匹配可能不准确)
————————————
{formatted_time}
source: {url}"""
        
        return message
    
    def extract_event_type(self, text: str) -> tuple:
        """Extract event type, direction, and severity from text"""
        text_lower = text.lower()
        
        for event_type, patterns in self.event_patterns.items():
            for pattern in patterns:
                # Check both English and Korean patterns
                if pattern.lower() in text_lower or pattern in text:
                    # Determine direction and severity based on event type
                    if event_type == 'LISTING':
                        return event_type, 'BULL', 0.9
                    elif event_type == 'DELIST':
                        return event_type, 'BEAR', 0.9
                    elif event_type == 'HACK':
                        return event_type, 'BEAR', 0.95
                    else:
                        # Only token-specific events are processed
                        return event_type, 'NEUTRAL', 0.5
        
        return 'OTHER', 'UNKNOWN', 0.3
    
    def extract_entities(self, text: str, coins_included: List[str] = None) -> List[str]:
        """Extract entities from text - supports ANY token/coin"""
        entities = []
        
        # First, add coins from BWEnews coins_included field
        if coins_included:
            for coin in coins_included:
                if isinstance(coin, str) and len(coin) >= 2:
                    entities.append(coin.upper())
        
        # Extract ANY crypto token (2-10 uppercase letters)
        import re
        crypto_pattern = r'\b[A-Z]{2,10}\b'
        potential_tokens = re.findall(crypto_pattern, text.upper())
        
        # Filter out common non-crypto words
        exclude_words = {
            'THE', 'AND', 'FOR', 'ARE', 'BUT', 'NOT', 'YOU', 'ALL', 'CAN', 'HER', 'WAS', 'ONE', 'OUR',
            'HAD', 'BUT', 'HIS', 'HOW', 'ITS', 'MAY', 'NEW', 'NOW', 'OLD', 'SEE', 'TWO', 'WHO', 'BOY',
            'DID', 'MAN', 'MEN', 'PUT', 'SAY', 'SHE', 'TOO', 'USE', 'WAY', 'YET', 'GET', 'GO', 'IF',
            'IN', 'IS', 'IT', 'NO', 'OF', 'ON', 'OR', 'TO', 'UP', 'WE', 'AS', 'AT', 'BE', 'BY', 'DO',
            'GO', 'HE', 'IF', 'IN', 'IS', 'IT', 'ME', 'MY', 'NO', 'OF', 'ON', 'OR', 'SO', 'TO', 'UP',
            'US', 'WE', 'AM', 'AN', 'AS', 'AT', 'BE', 'BY', 'DO', 'GO', 'HE', 'IF', 'IN', 'IS', 'IT',
            'ME', 'MY', 'NO', 'OF', 'ON', 'OR', 'SO', 'TO', 'UP', 'US', 'WE', 'AM', 'AN', 'AS', 'AT'
        }
        
        for token in potential_tokens:
            if token not in exclude_words and len(token) >= 2:
                entities.append(token)
        
        # Extract exchange names
        exchanges = [
            'Binance', 'Coinbase', 'FTX', 'Kraken', 'Gemini', 'Bithumb', 'Upbit', 'Korbit',
            '빗썸', '업비트', '코빗', '바이낸스코리아', 'OKX', 'KuCoin', 'Huobi', 'Gate.io'
        ]
        for exchange in exchanges:
            if exchange in text:
                entities.append(exchange)
        
        # Remove duplicates and limit
        unique_entities = list(dict.fromkeys(entities))
        return unique_entities[:8]  # Increased limit for more tokens
    
    def parse_published_time(self, published: str) -> str:
        """Parse published time and convert to Vietnam timezone (+7)"""
        try:
            if published:
                # Try to parse RSS date format
                import email.utils
                parsed_date = email.utils.parsedate_to_datetime(published)
                # Convert to Vietnam timezone (+7)
                vietnam_tz = timezone(timedelta(hours=7))
                vietnam_time = parsed_date.astimezone(vietnam_tz)
                return vietnam_time.isoformat()
        except:
            pass
        
        # Fallback to current time
        vietnam_tz = timezone(timedelta(hours=7))
        return datetime.now(vietnam_tz).isoformat()
    
    def is_duplicate(self, content_hash: str) -> bool:
        """Check if content hash already exists in Redis"""
        # Use same key as signal-collector for unified deduplication
        return self.redis_client.exists(f"signal_hash:{content_hash}")
    
    def mark_as_processed(self, content_hash: str, ttl: int = 3600):
        """Mark content hash as processed in Redis"""
        # Use same key as signal-collector for unified deduplication
        self.redis_client.setex(f"signal_hash:{content_hash}", ttl, "1")
    
    async def publish_to_kafka(self, signal: Dict):
        """Publish signal to Kafka topic"""
        try:
            future = self.kafka_producer.send(
                'news.signals.v1',
                value=signal,
                key=signal['event_id']  # Remove .encode() - key_serializer handles encoding
            )
            await asyncio.get_event_loop().run_in_executor(None, future.get, 10)
            logger.info(f"Published real-time signal to Kafka: {signal['event_id']}")
        except Exception as e:
            logger.error(f"Error publishing to Kafka: {e}")
    
    async def start_websocket_mode(self):
        """Start WebSocket mode for real-time RSS feeds"""
        logger.info("Starting WebSocket RSS client...")
        self.running = True
        
        # Connect to all WebSocket RSS feeds
        for source_name, source_config in self.rss_sources.items():
            websocket = await self.connect_websocket(source_name, source_config)
            if not websocket and source_config.get('polling_fallback'):
                # Fallback to polling if WebSocket fails
                logger.info(f"Starting polling fallback for {source_name}")
                asyncio.create_task(self.polling_fallback(source_name, source_config))
        
        # Keep running
        while self.running:
            await asyncio.sleep(1)
    
    async def polling_fallback(self, source_name: str, source_config: Dict):
        """Polling fallback when WebSocket is not available"""
        while self.running and source_name not in self.websocket_connections:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(source_config['url']) as response:
                        if response.status == 200:
                            content = await response.text()
                            
                            # Check if it's JSON API or RSS XML
                            if source_config.get('category') == 'exchange_notice':
                                # Handle JSON API responses
                                try:
                                    data = json.loads(content)
                                    entries = data if isinstance(data, list) else data.get('data', [])
                                    
                                    for entry in entries:
                                        await self.process_rss_entry(source_name, {
                                            'title': entry.get('title', entry.get('subject', '')),
                                            'summary': entry.get('content', entry.get('body', '')),
                                            'link': entry.get('url', entry.get('link', '')),
                                            'published': entry.get('created_at', entry.get('date', ''))
                                        })
                                except json.JSONDecodeError:
                                    logger.error(f"Failed to parse JSON from {source_name}")
                            else:
                                # Handle RSS XML
                                feed = feedparser.parse(content)
                                
                                for entry in feed.entries:
                                    await self.process_rss_entry(source_name, {
                                        'title': entry.get('title', ''),
                                        'summary': entry.get('summary', ''),
                                        'link': entry.get('link', ''),
                                        'published': entry.get('published', '')
                                    })
                
                # Wait before next poll
                await asyncio.sleep(30)  # Poll every 30 seconds
                
            except Exception as e:
                logger.error(f"Polling fallback error for {source_name}: {e}")
                await asyncio.sleep(60)  # Wait longer on error
    
    async def stop(self):
        """Stop WebSocket RSS client"""
        logger.info("Stopping WebSocket RSS client...")
        self.running = False
        
        # Close all WebSocket connections
        for source_name, websocket in self.websocket_connections.items():
            try:
                await websocket.close()
                logger.info(f"Closed WebSocket connection for {source_name}")
            except Exception as e:
                logger.error(f"Error closing WebSocket for {source_name}: {e}")
        
        # Close Kafka producer
        self.kafka_producer.close()
        
        # Close Redis connection
        self.redis_client.close()

async def main():
    """Main entry point"""
    client = WebSocketRSSClient()
    
    try:
        await client.start_websocket_mode()
    except KeyboardInterrupt:
        logger.info("Shutting down WebSocket RSS client")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
    finally:
        await client.stop()

if __name__ == "__main__":
    asyncio.run(main())
