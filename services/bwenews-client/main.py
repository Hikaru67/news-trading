#!/usr/bin/env python3
"""
BWEnews Client Service - Unified RSS and WebSocket client for BWEnews
Collects news from BWEnews RSS feed and WebSocket API
"""

import asyncio
import json
import logging
import hashlib
import time
import websockets
import feedparser
import aiohttp
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional
import os
from kafka import KafkaProducer
import redis
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class BWEnewsClient:
    """Unified BWEnews client for RSS and WebSocket news ingestion"""
    
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
        
        # BWEnews configuration
        self.rss_url = 'https://rss-public.bwe-ws.com/'
        self.websocket_url = 'wss://bwenews-api.bwe-ws.com/ws'
        self.trust_score = 0.90
        
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
                '추가지원', '거래시작', '상장예정', '거래개시', '거래오픈', '거래활성화',
                # Chinese patterns
                '上新', '上线', '交易', '上市', '支持'
            ],
            'DELIST': [
                # Specific delisting patterns only
                'delist', 'suspends', 'removes', 'discontinues', 'trading halt',
                'trading suspension', 'delisting', 'removes support for',
                # Korean patterns
                '상장폐지', '거래중단', '거래정지', '지원중단', '제거', '중단',
                # Chinese patterns
                '下架', '停止', '暂停', '移除'
            ],
            'HACK': [
                # Specific hack patterns only
                'hack', 'exploit', 'drained', 'breach', 'security incident',
                'vulnerability', 'attack', 'compromised', 'hacked', 'exploited',
                # Korean patterns
                '해킹', '침해', '보안사고', '취약점', '공격', '침입', '도난',
                # Chinese patterns
                '黑客', '攻击', '漏洞', '被盗', '安全事件'
            ]
        }
        
        # WebSocket connection
        self.websocket_connection = None
        self.running = False
        
    async def connect_websocket(self):
        """Connect to BWEnews WebSocket API"""
        try:
            logger.info(f"Connecting to BWEnews WebSocket: {self.websocket_url}")
            self.websocket_connection = await websockets.connect(self.websocket_url)
            
            # Start listening for real-time updates
            asyncio.create_task(self.listen_websocket())
            logger.info("Connected to BWEnews WebSocket")
            return True
            
        except Exception as e:
            logger.error(f"Failed to connect to BWEnews WebSocket: {e}")
            return False
    
    async def listen_websocket(self):
        """Listen for real-time WebSocket updates"""
        try:
            # Send ping to maintain connection
            await self.websocket_connection.send('ping')
            
            async for message in self.websocket_connection:
                try:
                    # Handle ping/pong
                    if message == 'pong':
                        logger.debug("Received pong from BWEnews WebSocket")
                        continue
                    
                    # Parse WebSocket message
                    data = json.loads(message)
                    
                    # Process BWEnews message
                    await self.process_bwenews_message(data)
                        
                except json.JSONDecodeError:
                    logger.warning("Invalid JSON from BWEnews WebSocket")
                except Exception as e:
                    logger.error(f"Error processing WebSocket message: {e}")
                    
        except websockets.exceptions.ConnectionClosed:
            logger.info("BWEnews WebSocket connection closed")
        except Exception as e:
            logger.error(f"BWEnews WebSocket error: {e}")
        finally:
            self.websocket_connection = None
    
    async def process_bwenews_message(self, data: Dict):
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
            
            # Convert BWEnews format to standard signal format
            signal = {
                'event_id': hashlib.sha256(data.get('news_title', '').encode()).hexdigest(),
                'ts_iso': self.timestamp_to_iso(data.get('timestamp', 0)),
                'source': 'bwenews_websocket',
                'headline': data.get('news_title', ''),
                'url': data.get('url', ''),
                'event_type': self.extract_event_type(data.get('news_title', '')),
                'primary_entity': data.get('coins_included', [''])[0] if data.get('coins_included') else '',
                'entities': data.get('coins_included', []),
                'severity': 0.9,  # High severity for WebSocket news
                'direction': self.get_direction(data.get('news_title', '')),
                'confidence': self.trust_score,
                'raw_text': data.get('news_title', ''),
                'extras': {
                    'source_name': data.get('source_name', 'BWENEWS'),
                    'coins_included': data.get('coins_included', []),
                    'ingestion_method': 'websocket',
                    'latency_ms': 0  # Real-time, no latency
                }
            }
            
            # Check for duplicates
            if not self.is_duplicate(signal['event_id']):
                # Mark as processed
                self.mark_as_processed(signal['event_id'])
                
                # Publish to Kafka
                await self.publish_to_kafka(signal)
                
                logger.info(f"Processed BWEnews WebSocket message: {signal['event_type']}")
            
        except Exception as e:
            logger.error(f"Error processing BWEnews message: {e}")
    
    async def poll_rss_feed(self):
        """Poll BWEnews RSS feed as fallback"""
        while self.running:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(self.rss_url) as response:
                        if response.status == 200:
                            content = await response.text()
                            feed = feedparser.parse(content)
                            
                            for entry in feed.entries:
                                await self.process_rss_entry(entry)
                
                # Wait before next poll (every 30 seconds)
                await asyncio.sleep(30)
                
            except Exception as e:
                logger.error(f"RSS polling error: {e}")
                await asyncio.sleep(60)  # Wait longer on error
    
    async def process_rss_entry(self, entry: Dict):
        """Process RSS entry from BWEnews"""
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
            event_type = self.extract_event_type(text)
            entities = self.extract_entities(text)
            
            # Parse published time and convert to +7 timezone
            news_published_time = self.parse_published_time(entry.get('published', ''))
            
            # Create signal
            signal = {
                'event_id': content_hash,
                'ts_iso': news_published_time,
                'source': 'bwenews_rss',
                'headline': entry.get('title', ''),
                'url': entry.get('link', ''),
                'event_type': event_type,
                'primary_entity': entities[0] if entities else '',
                'entities': entities,
                'severity': 0.8,  # High severity for RSS news
                'direction': self.get_direction(text),
                'confidence': self.trust_score,
                'raw_text': text,
                'extras': {
                    'content_hash': content_hash,
                    'source_url': entry.get('link', ''),
                    'published': entry.get('published', ''),
                    'ingestion_method': 'rss',
                    'latency_ms': 30000  # 30 second polling latency
                }
            }
            
            # Mark as processed
            self.mark_as_processed(content_hash)
            
            # Publish to Kafka
            await self.publish_to_kafka(signal)
            
            logger.info(f"Processed BWEnews RSS entry: {event_type}")
            
        except Exception as e:
            logger.error(f"Error processing RSS entry: {e}")
    
    def extract_event_type(self, text: str) -> str:
        """Extract event type from text"""
        text_lower = text.lower()
        
        for event_type, patterns in self.event_patterns.items():
            for pattern in patterns:
                if pattern.lower() in text_lower or pattern in text:
                    return event_type
        
        return 'OTHER'
    
    def get_direction(self, text: str) -> str:
        """Determine market direction based on event type"""
        event_type = self.extract_event_type(text)
        
        if event_type == 'LISTING':
            return 'BULL'
        elif event_type in ['DELIST', 'HACK']:
            return 'BEAR'
        else:
            return 'UNKNOWN'
    
    def extract_entities(self, text: str) -> List[str]:
        """Extract entities from text"""
        entities = []
        
        # Extract crypto tokens (2-10 uppercase letters)
        import re
        crypto_pattern = r'\b[A-Z]{2,10}\b'
        potential_tokens = re.findall(crypto_pattern, text.upper())
        
        # Filter out common non-crypto words
        exclude_words = {
            'THE', 'AND', 'FOR', 'ARE', 'BUT', 'NOT', 'YOU', 'ALL', 'CAN', 'HER', 'WAS', 'ONE', 'OUR',
            'HAD', 'BUT', 'HIS', 'HOW', 'ITS', 'MAY', 'NEW', 'NOW', 'OLD', 'SEE', 'TWO', 'WHO', 'BOY',
            'DID', 'MAN', 'MEN', 'PUT', 'SAY', 'SHE', 'TOO', 'USE', 'WAY', 'YET', 'GET', 'GO', 'IF',
            'IN', 'IS', 'IT', 'NO', 'OF', 'ON', 'OR', 'TO', 'UP', 'WE', 'AS', 'AT', 'BE', 'BY', 'DO'
        }
        
        for token in potential_tokens:
            if token not in exclude_words and len(token) >= 2:
                entities.append(token)
        
        # Remove duplicates and limit
        unique_entities = list(dict.fromkeys(entities))
        return unique_entities[:8]
    
    def parse_published_time(self, published: str) -> str:
        """Parse published time and convert to Vietnam timezone (+7)"""
        try:
            if published:
                import email.utils
                parsed_date = email.utils.parsedate_to_datetime(published)
                vietnam_tz = timezone(timedelta(hours=7))
                vietnam_time = parsed_date.astimezone(vietnam_tz)
                return vietnam_time.isoformat()
        except:
            pass
        
        # Fallback to current time
        vietnam_tz = timezone(timedelta(hours=7))
        return datetime.now(vietnam_tz).isoformat()
    
    def timestamp_to_iso(self, timestamp: int) -> str:
        """Convert Unix timestamp to ISO format"""
        try:
            dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
            vietnam_tz = timezone(timedelta(hours=7))
            vietnam_time = dt.astimezone(vietnam_tz)
            return vietnam_time.isoformat()
        except:
            vietnam_tz = timezone(timedelta(hours=7))
            return datetime.now(vietnam_tz).isoformat()
    
    def is_duplicate(self, content_hash: str) -> bool:
        """Check if content hash already exists in Redis"""
        return self.redis_client.exists(f"signal_hash:{content_hash}")
    
    def mark_as_processed(self, content_hash: str, ttl: int = 3600):
        """Mark content hash as processed in Redis"""
        self.redis_client.setex(f"signal_hash:{content_hash}", ttl, "1")
    
    async def publish_to_kafka(self, signal: Dict):
        """Publish signal to Kafka topic"""
        try:
            future = self.kafka_producer.send(
                'news.signals.v1',
                value=signal,
                key=signal['event_id']
            )
            await asyncio.get_event_loop().run_in_executor(None, future.get, 10)
            logger.info(f"Published BWEnews signal to Kafka: {signal['event_id']}")
        except Exception as e:
            logger.error(f"Error publishing to Kafka: {e}")
    
    async def start(self):
        """Start BWEnews client"""
        logger.info("Starting BWEnews client...")
        self.running = True
        
        # Try to connect to WebSocket first
        websocket_connected = await self.connect_websocket()
        
        # Always start RSS polling as fallback
        asyncio.create_task(self.poll_rss_feed())
        
        # Keep running
        while self.running:
            await asyncio.sleep(1)
    
    async def stop(self):
        """Stop BWEnews client"""
        logger.info("Stopping BWEnews client...")
        self.running = False
        
        # Close WebSocket connection
        if self.websocket_connection:
            try:
                await self.websocket_connection.close()
                logger.info("Closed BWEnews WebSocket connection")
            except Exception as e:
                logger.error(f"Error closing WebSocket: {e}")
        
        # Close Kafka producer
        self.kafka_producer.close()
        
        # Close Redis connection
        self.redis_client.close()

async def main():
    """Main entry point"""
    client = BWEnewsClient()
    
    try:
        await client.start()
    except KeyboardInterrupt:
        logger.info("Shutting down BWEnews client")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
    finally:
        await client.stop()

if __name__ == "__main__":
    asyncio.run(main())
