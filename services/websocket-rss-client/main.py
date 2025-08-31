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
            key_serializer=lambda k: k.encode('utf-8') if k else None
        )
        
        self.redis_client = redis.Redis.from_url(
            os.getenv('REDIS_URL', 'redis://redis:6379')
        )
        
        # RSS sources with WebSocket support
        self.rss_sources = {
            'cointelegraph': {
                'url': 'https://cointelegraph.com/rss',
                'trust_score': 0.85,
                'websocket_url': 'wss://cointelegraph.com/ws/rss',
                'polling_fallback': True
            },
            'theblock': {
                'url': 'https://www.theblock.co/rss.xml',
                'trust_score': 0.80,
                'websocket_url': 'wss://www.theblock.co/ws/rss',
                'polling_fallback': True
            },
            'decrypt': {
                'url': 'https://decrypt.co/feed',
                'trust_score': 0.75,
                'websocket_url': 'wss://decrypt.co/ws/feed',
                'polling_fallback': True
            }
        }
        
        # Event patterns for classification
        self.event_patterns = {
            'LISTING': [
                'will list', 'lists', 'listing', 'new listing', 'adds support',
                'trading pairs', 'spot trading', 'futures trading'
            ],
            'DELIST': [
                'delist', 'suspends', 'removes', 'discontinues', 'trading halt'
            ],
            'HACK': [
                'hack', 'exploit', 'drained', 'breach', 'security incident',
                'vulnerability', 'attack', 'compromised'
            ],
            'REGULATION': [
                'regulation', 'regulatory', 'sec', 'cfdc', 'compliance',
                'legal action', 'lawsuit', 'investigation'
            ],
            'FED_SPEECH': [
                'fed', 'federal reserve', 'jerome powell', 'rate hike',
                'monetary policy', 'inflation', 'interest rates'
            ],
            'POLITICAL_POST': [
                'trump', 'biden', 'election', 'political', 'government',
                'policy', 'legislation', 'congress'
            ],
            'TECHNICAL_UPGRADE': [
                'upgrade', 'update', 'mainnet', 'hard fork', 'protocol',
                'network', 'blockchain', 'smart contract'
            ],
            'PARTNERSHIP': [
                'partnership', 'collaboration', 'integration', 'alliance',
                'joint venture', 'merger', 'acquisition'
            ]
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
            async for message in websocket:
                try:
                    # Parse WebSocket message
                    data = json.loads(message)
                    
                    # Process real-time RSS entry
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
            entities = self.extract_entities(text)
            
            # Parse published time and convert to +7 timezone
            news_published_time = self.parse_published_time(entry.get('published', ''))
            
            # Create signal
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
    
    def extract_event_type(self, text: str) -> tuple:
        """Extract event type, direction, and severity from text"""
        text_lower = text.lower()
        
        for event_type, patterns in self.event_patterns.items():
            for pattern in patterns:
                if pattern.lower() in text_lower:
                    # Determine direction and severity based on event type
                    if event_type == 'LISTING':
                        return event_type, 'BULL', 0.9
                    elif event_type == 'DELIST':
                        return event_type, 'BEAR', 0.9
                    elif event_type == 'HACK':
                        return event_type, 'BEAR', 0.95
                    elif event_type == 'REGULATION':
                        return event_type, 'BEAR', 0.85
                    elif event_type == 'FED_SPEECH':
                        # Analyze sentiment for FED_SPEECH
                        if any(word in text_lower for word in ['hawkish', 'rate hike', 'tightening']):
                            return event_type, 'BEAR', 0.9
                        elif any(word in text_lower for word in ['dovish', 'rate cut', 'easing']):
                            return event_type, 'BULL', 0.9
                        else:
                            return event_type, 'NEUTRAL', 0.7
                    elif event_type == 'POLITICAL_POST':
                        return event_type, 'NEUTRAL', 0.6
                    elif event_type == 'TECHNICAL_UPGRADE':
                        return event_type, 'BULL', 0.7
                    elif event_type == 'PARTNERSHIP':
                        return event_type, 'BULL', 0.8
        
        return 'OTHER', 'UNKNOWN', 0.3
    
    def extract_entities(self, text: str) -> List[str]:
        """Extract entities from text (basic implementation)"""
        entities = []
        
        # Extract common crypto entities
        crypto_entities = ['BTC', 'ETH', 'SOL', 'ADA', 'DOT', 'LINK', 'UNI', 'AVAX']
        for entity in crypto_entities:
            if entity in text.upper():
                entities.append(entity)
        
        # Extract company names (basic)
        company_keywords = ['Binance', 'Coinbase', 'FTX', 'Kraken', 'Gemini']
        for company in company_keywords:
            if company in text:
                entities.append(company)
        
        return entities[:5]  # Limit to 5 entities
    
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
        return self.redis_client.exists(f"rss_hash:{content_hash}")
    
    def mark_as_processed(self, content_hash: str, ttl: int = 3600):
        """Mark content hash as processed in Redis"""
        self.redis_client.setex(f"rss_hash:{content_hash}", ttl, "1")
    
    async def publish_to_kafka(self, signal: Dict):
        """Publish signal to Kafka topic"""
        try:
            future = self.kafka_producer.send(
                'news.signals.v1',
                value=signal,
                key=signal['event_id'].encode()
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
