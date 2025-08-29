#!/usr/bin/env python3
"""
Signal Collector Service - Phase 1 MVP
Collects news from RSS feeds and publishes to Kafka
"""

import asyncio
import hashlib
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

import feedparser
import redis
from kafka import KafkaProducer
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
SIGNALS_PROCESSED = Counter('signals_processed_total', 'Total signals processed', ['source', 'event_type'])
SIGNALS_PUBLISHED = Counter('signals_published_total', 'Total signals published to Kafka', ['source'])
PROCESSING_TIME = Histogram('signal_processing_seconds', 'Time spent processing signals', ['source'])

class SignalCollector:
    def __init__(self):
        # Database connection
        self.db_url = os.getenv('DATABASE_URL', 'postgresql://trading_user:trading_pass@localhost:5432/news_trading')
        self.engine = create_engine(self.db_url)
        self.Session = sessionmaker(bind=self.engine)
        
        # Redis connection
        self.redis_url = os.getenv('REDIS_URL', 'redis://localhost:6379')
        self.redis_client = redis.from_url(self.redis_url)
        
        # Kafka producer
        self.kafka_servers = os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'localhost:9092').split(',')
        self.producer = KafkaProducer(
            bootstrap_servers=self.kafka_servers,
            value_serializer=lambda v: json.dumps(v).encode('utf-8'),
            key_serializer=lambda k: k.encode('utf-8') if k else None,
            acks='all',
            retries=3
        )
        
        # RSS sources configuration
        self.sources = {
            'binance': {
                'url': 'https://www.binance.com/en/support/announcement/rss',
                'trust_score': 0.98,
                'check_interval': 30  # seconds
            },
            'coinbase': {
                'url': 'https://blog.coinbase.com/feed',
                'trust_score': 0.95,
                'check_interval': 60  # seconds
            },
            'cryptonews': {
                'url': 'https://cryptonews.com/news/feed',
                'trust_score': 0.8,
                'check_interval': 120  # seconds
            }
        }
        
        # Event type patterns
        self.event_patterns = {
            'LISTING': [
                r'\b(Will List|Lists|Listing|Launches)\b',
                r'\b(New Trading Pair|New Asset)\b'
            ],
            'DELIST': [
                r'\b(Delist|Delisting|Removes|Suspends)\b',
                r'\b(Trading Halt|Trading Suspension)\b'
            ]
        }

    def extract_event_type(self, text: str) -> tuple[str, str, float]:
        """Extract event type, direction, and severity from text"""
        text_lower = text.lower()
        
        # Check for listing events
        for pattern in self.event_patterns['LISTING']:
            if any(keyword in text_lower for keyword in pattern.lower().split('|')):
                return 'LISTING', 'BULL', 0.9
        
        # Check for delisting events
        for pattern in self.event_patterns['DELIST']:
            if any(keyword in text_lower for keyword in pattern.lower().split('|')):
                return 'DELIST', 'BEAR', 0.9
        
        # Default
        return 'OTHER', 'UNKNOWN', 0.3

    def extract_entities(self, text: str) -> List[str]:
        """Extract entities (tickers) from text"""
        # Simple regex for crypto tickers (3-5 letter codes)
        import re
        ticker_pattern = r'\b[A-Z]{3,5}\b'
        entities = re.findall(ticker_pattern, text.upper())
        return list(set(entities))  # Remove duplicates

    def is_duplicate(self, content_hash: str) -> bool:
        """Check if content is duplicate using Redis"""
        return self.redis_client.exists(f"signal_hash:{content_hash}")

    def mark_as_processed(self, content_hash: str, ttl: int = 3600):
        """Mark content as processed in Redis"""
        self.redis_client.setex(f"signal_hash:{content_hash}", ttl, "1")

    def normalize_signal(self, entry: Dict, source: str) -> Optional[Dict]:
        """Normalize RSS entry to signal format"""
        try:
            # Create content hash for deduplication
            raw_content = f"{entry.get('title', '')} {entry.get('summary', '')}"
            content_hash = hashlib.sha256(raw_content.encode()).hexdigest()
            
            # Check for duplicates
            if self.is_duplicate(content_hash):
                logger.info(f"Duplicate signal detected: {content_hash}")
                return None
            
            # Extract event information
            text = f"{entry.get('title', '')} {entry.get('summary', '')}"
            event_type, direction, severity = self.extract_event_type(text)
            entities = self.extract_entities(text)
            
            # Create signal
            signal = {
                'event_id': str(uuid.uuid4()),
                'ts_iso': datetime.now(timezone.utc).isoformat(),
                'source': source,
                'headline': entry.get('title', ''),
                'url': entry.get('link', ''),
                'event_type': event_type,
                'primary_entity': entities[0] if entities else '',
                'entities': entities,
                'severity': severity,
                'direction': direction,
                'confidence': self.sources[source]['trust_score'],
                'raw_text': text,
                'extras': {
                    'content_hash': content_hash,
                    'source_url': entry.get('link', '')
                }
            }
            
            # Mark as processed
            self.mark_as_processed(content_hash)
            
            return signal
            
        except Exception as e:
            logger.error(f"Error normalizing signal: {e}")
            return None

    def save_signal_to_db(self, signal: Dict):
        """Save signal to PostgreSQL for tracking"""
        try:
            with self.Session() as session:
                query = text("""
                    INSERT INTO signals (event_id, source, event_type, headline, url, 
                                       severity, direction, confidence, raw_text)
                    VALUES (:event_id, :source, :event_type, :headline, :url,
                           :severity, :direction, :confidence, :raw_text)
                    ON CONFLICT (event_id) DO NOTHING
                """)
                
                session.execute(query, {
                    'event_id': signal['event_id'],
                    'source': signal['source'],
                    'event_type': signal['event_type'],
                    'headline': signal['headline'],
                    'url': signal['url'],
                    'severity': signal['severity'],
                    'direction': signal['direction'],
                    'confidence': signal['confidence'],
                    'raw_text': signal['raw_text']
                })
                session.commit()
                
        except Exception as e:
            logger.error(f"Error saving signal to database: {e}")

    def publish_to_kafka(self, signal: Dict):
        """Publish signal to Kafka topic"""
        try:
            future = self.producer.send(
                'news.signals.v1',
                value=signal,
                key=signal['event_id']
            )
            future.get(timeout=10)  # Wait for send to complete
            
            SIGNALS_PUBLISHED.labels(source=signal['source']).inc()
            logger.info(f"Published signal to Kafka: {signal['event_id']}")
            
        except Exception as e:
            logger.error(f"Error publishing to Kafka: {e}")

    async def collect_from_source(self, source_name: str, source_config: Dict):
        """Collect signals from a single RSS source"""
        while True:
            try:
                logger.info(f"Collecting from {source_name}")
                
                # Parse RSS feed with better error handling
                try:
                    response = requests.get(source_config['url'], timeout=10, headers={
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                    })
                    
                    if response.status_code == 200:
                        feed = feedparser.parse(response.content)
                    else:
                        logger.warning(f"HTTP {response.status_code} for {source_name}: {response.text[:100]}")
                        continue
                        
                except Exception as e:
                    logger.error(f"Error fetching RSS from {source_name}: {e}")
                    continue
                
                for entry in feed.entries:
                    with PROCESSING_TIME.labels(source=source_name).time():
                        # Normalize signal
                        signal = self.normalize_signal(entry, source_name)
                        
                        if signal:
                            # Save to database
                            self.save_signal_to_db(signal)
                            
                            # Publish to Kafka
                            self.publish_to_kafka(signal)
                            
                            # Update metrics
                            SIGNALS_PROCESSED.labels(
                                source=source_name, 
                                event_type=signal['event_type']
                            ).inc()
                            
                            logger.info(f"Processed signal: {signal['event_type']} from {source_name}")
                
                # Wait before next check
                await asyncio.sleep(source_config['check_interval'])
                
            except Exception as e:
                logger.error(f"Error collecting from {source_name}: {e}")
                await asyncio.sleep(30)  # Wait before retry

    async def run(self):
        """Main run loop"""
        logger.info("Starting Signal Collector Service")
        
        # Start Prometheus metrics server
        start_http_server(8000)
        
        # Create tasks for each source
        tasks = []
        for source_name, source_config in self.sources.items():
            task = asyncio.create_task(
                self.collect_from_source(source_name, source_config)
            )
            tasks.append(task)
        
        # Wait for all tasks
        await asyncio.gather(*tasks)

def main():
    """Main entry point"""
    collector = SignalCollector()
    
    try:
        asyncio.run(collector.run())
    except KeyboardInterrupt:
        logger.info("Shutting down Signal Collector")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        raise

if __name__ == "__main__":
    main()
