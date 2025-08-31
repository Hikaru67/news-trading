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
import requests
from datetime import datetime, timezone, timedelta
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
        
        # RSS sources configuration (only working sources)
        self.sources = {
            'cointelegraph': {
                'url': 'https://cointelegraph.com/rss',
                'trust_score': 0.85,
                'check_interval': 120  # seconds
            },
            'decrypt': {
                'url': 'https://decrypt.co/feed',
                'trust_score': 0.8,
                'check_interval': 120  # seconds
            },
            'theblock': {
                'url': 'https://www.theblock.co/rss.xml',
                'trust_score': 0.85,
                'check_interval': 120  # seconds
            }
        }
        
        # Event type patterns - ONLY HIGH IMPACT EVENTS
        self.event_patterns = {
            'LISTING': [
                r'\b(Will List|Lists|Listing|New Trading Pair|New Asset|New Token)\b',
                r'\b(Coming Soon|Available for Trading|Now Live|Trading Starts)\b',
                r'\b(Spot Trading|Futures Trading|Margin Trading)\b',
                r'\b(Adds|Added|New Coin|New Cryptocurrency)\b'
            ],
            'DELIST': [
                r'\b(Delist|Delisting|Removes|Suspends|Discontinues|Terminates)\b',
                r'\b(Trading Halt|Trading Suspension|Trading Stop)\b',
                r'\b(No Longer Available|Removed from Trading)\b'
            ],
            'FED_SPEECH': [
                r'\b(Fed|Federal Reserve|Jerome Powell|Powell Speech)\b',
                r'\b(Rate Hike|Rate Cut|Interest Rate|Monetary Policy)\b',
                r'\b(Hawkish|Dovish|Inflation|CPI|Employment)\b'
            ],
            'REGULATION': [
                r'\b(Ban|Banned|Illegal|Criminal|Arrest|Indictment)\b',
                r'\b(Shutdown|Shut Down|Cease|Ceased|Stop|Stopped)\b',
                r'\b(Enforcement|Investigation|Settlement|Fine|Penalty)\b'
            ],
            'HACK': [
                r'\b(Hack|Hacked|Exploit|Exploited|Breach|Breached)\b',
                r'\b(Drain|Drained|Stolen|Theft|Compromised)\b',
                r'\b(Bridge Hack|Protocol Hack|Smart Contract Bug)\b'
            ]
        }

    def extract_event_type(self, text: str) -> tuple[str, str, float]:
        """Extract event type, direction, and severity from text - ONLY HIGH IMPACT"""
        text_lower = text.lower()
        
        # Check for listing events (HIGH IMPACT)
        for pattern in self.event_patterns['LISTING']:
            if any(keyword in text_lower for keyword in pattern.lower().split('|')):
                return 'LISTING', 'BULL', 0.9
        
        # Check for delisting events (HIGH IMPACT)
        for pattern in self.event_patterns['DELIST']:
            if any(keyword in text_lower for keyword in pattern.lower().split('|')):
                return 'DELIST', 'BEAR', 0.9
        
        # Check for Fed speech events (HIGH IMPACT)
        for pattern in self.event_patterns['FED_SPEECH']:
            if any(keyword in text_lower for keyword in pattern.lower().split('|')):
                return 'FED_SPEECH', 'UNKNOWN', 0.8
        
        # Check for regulation events (HIGH IMPACT - only serious ones)
        for pattern in self.event_patterns['REGULATION']:
            if any(keyword in text_lower for keyword in pattern.lower().split('|')):
                return 'REGULATION', 'BEAR', 0.9
        
        # Check for hack events (HIGH IMPACT)
        for pattern in self.event_patterns['HACK']:
            if any(keyword in text_lower for keyword in pattern.lower().split('|')):
                return 'HACK', 'BEAR', 0.9
        
        # Default - LOW IMPACT, IGNORE
        return 'OTHER', 'UNKNOWN', 0.1

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
            
            # Parse published time from RSS entry and convert to +7 timezone
            news_published_time = None
            try:
                published = entry.get('published', '')
                if published:
                    # Try to parse RSS date format
                    import email.utils
                    parsed_date = email.utils.parsedate_to_datetime(published)
                    # Convert to Vietnam timezone (+7)
                    vietnam_tz = timezone(timedelta(hours=7))
                    vietnam_time = parsed_date.astimezone(vietnam_tz)
                    news_published_time = vietnam_time.isoformat()
                    
                    # Check if news is from today (Vietnam time)
                    now_vietnam = datetime.now(vietnam_tz)
                    if vietnam_time.date() < now_vietnam.date():
                        logger.info(f"Skipping old news from {vietnam_time.date()}")
                        return None
                else:
                    vietnam_tz = timezone(timedelta(hours=7))
                    news_published_time = datetime.now(vietnam_tz).isoformat()
            except:
                vietnam_tz = timezone(timedelta(hours=7))
                news_published_time = datetime.now(vietnam_tz).isoformat()
            
            # Create signal
            signal = {
                'event_id': content_hash,  # Use content hash for consistent deduplication
                'ts_iso': news_published_time,  # Use news published time
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
                    'source_url': entry.get('link', ''),
                    'published': entry.get('published', '')  # Store original published time
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
                            # Filter by severity (only process VERY HIGH severity signals)
                            severity = signal.get('severity', 0)
                            if severity >= 0.8:  # Only process signals with severity >= 0.8 (HIGH IMPACT)
                                # Save to database
                                self.save_signal_to_db(signal)
                                
                                # Publish to Kafka
                                self.publish_to_kafka(signal)
                                
                                # Update metrics
                                SIGNALS_PROCESSED.labels(
                                    source=source_name, 
                                    event_type=signal['event_type']
                                ).inc()
                                
                                logger.info(f"Processed HIGH IMPACT signal: {signal['event_type']} from {source_name} (severity: {severity:.2f})")
                            else:
                                logger.info(f"Skipped low impact signal: {signal['event_type']} from {source_name} (severity: {severity:.2f})")
                
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
