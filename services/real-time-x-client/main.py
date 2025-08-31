import asyncio
import json
import logging
import hashlib
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional
import aiohttp
from kafka import KafkaProducer
import redis
import os
from dotenv import load_dotenv
import tweepy
from tweepy import StreamingClient, StreamRule

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class RealTimeXClient:
    """Real-time X (Twitter) client using official API v2 streaming"""
    
    def __init__(self):
        self.kafka_producer = KafkaProducer(
            bootstrap_servers=os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'kafka:29092'),
            value_serializer=lambda v: json.dumps(v).encode('utf-8'),
            key_serializer=lambda k: k.encode('utf-8') if k else None
        )
        
        self.redis_client = redis.Redis.from_url(
            os.getenv('REDIS_URL', 'redis://redis:6379')
        )
        
        # X API credentials
        self.bearer_token = os.getenv('X_BEARER_TOKEN')
        self.api_key = os.getenv('X_API_KEY')
        self.api_secret = os.getenv('X_API_SECRET')
        self.access_token = os.getenv('X_ACCESS_TOKEN')
        self.access_token_secret = os.getenv('X_ACCESS_TOKEN_SECRET')
        
        # Initialize X API client
        self.x_client = None
        self.streaming_client = None
        self.initialize_x_client()
        
        # Important accounts to monitor
        self.important_accounts = [
            'VitalikButerin', 'cz_binance', 'SBF_FTX', 'elonmusk',
            'federalreserve', 'SECGov', 'CFTC', 'USTreasury',
            'Coinbase', 'Binance', 'Kraken', 'Gemini'
        ]
        
        # Keywords to monitor
        self.monitor_keywords = [
            'bitcoin', 'ethereum', 'crypto', 'blockchain', 'defi',
            'nft', 'metaverse', 'web3', 'token', 'coin',
            'listing', 'delist', 'hack', 'exploit', 'regulation',
            'sec', 'cfdc', 'fed', 'rate hike', 'inflation'
        ]
        
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
        
        self.running = False
        
    def initialize_x_client(self):
        """Initialize X API client"""
        try:
            if self.bearer_token:
                # Use Bearer token for read-only access
                self.x_client = tweepy.Client(bearer_token=self.bearer_token)
                logger.info("Initialized X API client with Bearer token")
            elif self.api_key and self.api_secret and self.access_token and self.access_token_secret:
                # Use OAuth 1.0a for full access
                auth = tweepy.OAuthHandler(self.api_key, self.api_secret)
                auth.set_access_token(self.access_token, self.access_token_secret)
                self.x_client = tweepy.Client(
                    consumer_key=self.api_key,
                    consumer_secret=self.api_secret,
                    access_token=self.access_token,
                    access_token_secret=self.access_token_secret
                )
                logger.info("Initialized X API client with OAuth 1.0a")
            else:
                logger.warning("No X API credentials provided, using fallback mode")
                self.x_client = None
                
        except Exception as e:
            logger.error(f"Failed to initialize X API client: {e}")
            self.x_client = None
    
    async def start_streaming(self):
        """Start real-time X streaming"""
        logger.info("Starting real-time X streaming...")
        self.running = True
        
        if self.x_client:
            try:
                # Start filtered stream
                await self.start_filtered_stream()
            except Exception as e:
                logger.error(f"Failed to start filtered stream: {e}")
                # Fallback to polling
                await self.start_polling_fallback()
        else:
            # Use polling fallback
            await self.start_polling_fallback()
    
    async def start_filtered_stream(self):
        """Start filtered stream for real-time tweets"""
        try:
            # Create streaming client
            class TweetStreamListener(StreamingClient):
                def __init__(self, bearer_token, parent_client):
                    super().__init__(bearer_token)
                    self.parent_client = parent_client
                
                def on_tweet(self, tweet):
                    asyncio.create_task(self.parent_client.process_tweet(tweet))
                
                def on_error(self, status):
                    logger.error(f"X streaming error: {status}")
            
            # Start filtered stream
            stream = TweetStreamListener(self.bearer_token, self)
            
            # Add rules for filtering
            rules = []
            for keyword in self.monitor_keywords:
                rules.append(StreamRule(f'"{keyword}"'))
            
            # Add rules for important accounts
            for account in self.important_accounts:
                rules.append(StreamRule(f'from:{account}'))
            
            # Add rules
            for rule in rules:
                try:
                    stream.add_rules(rule)
                except Exception as e:
                    logger.warning(f"Failed to add rule {rule}: {e}")
            
            # Start streaming
            stream.filter(tweet_fields=['created_at', 'author_id', 'text'])
            
        except Exception as e:
            logger.error(f"Failed to start filtered stream: {e}")
            raise
    
    async def start_polling_fallback(self):
        """Start polling fallback when streaming is not available"""
        logger.info("Starting X polling fallback...")
        
        while self.running:
            try:
                # Poll important accounts
                await self.poll_important_accounts()
                
                # Poll keyword searches
                await self.poll_keyword_searches()
                
                # Wait before next poll
                await asyncio.sleep(60)  # Poll every minute
                
            except Exception as e:
                logger.error(f"Polling fallback error: {e}")
                await asyncio.sleep(120)  # Wait longer on error
    
    async def poll_important_accounts(self):
        """Poll important accounts for new tweets"""
        if not self.x_client:
            return
            
        try:
            for username in self.important_accounts:
                try:
                    # Get user ID
                    user = self.x_client.get_user(username=username)
                    if not user.data:
                        continue
                    
                    user_id = user.data.id
                    
                    # Get recent tweets
                    tweets = self.x_client.get_users_tweets(
                        user_id,
                        max_results=10,
                        tweet_fields=['created_at', 'text']
                    )
                    
                    if tweets.data:
                        for tweet in tweets.data:
                            await self.process_tweet(tweet)
                            
                except Exception as e:
                    logger.warning(f"Failed to poll account {username}: {e}")
                    
        except Exception as e:
            logger.error(f"Failed to poll important accounts: {e}")
    
    async def poll_keyword_searches(self):
        """Poll keyword searches for new tweets"""
        if not self.x_client:
            return
            
        try:
            for keyword in self.monitor_keywords:
                try:
                    # Search recent tweets
                    tweets = self.x_client.search_recent_tweets(
                        query=keyword,
                        max_results=10,
                        tweet_fields=['created_at', 'text', 'author_id']
                    )
                    
                    if tweets.data:
                        for tweet in tweets.data:
                            await self.process_tweet(tweet)
                            
                except Exception as e:
                    logger.warning(f"Failed to search keyword {keyword}: {e}")
                    
        except Exception as e:
            logger.error(f"Failed to poll keyword searches: {e}")
    
    async def process_tweet(self, tweet):
        """Process incoming tweet"""
        try:
            # Create content hash for deduplication
            tweet_text = tweet.text
            content_hash = hashlib.sha256(tweet_text.encode()).hexdigest()
            
            # Check for duplicates
            if self.is_duplicate(content_hash):
                logger.debug(f"Duplicate tweet detected: {content_hash}")
                return None
            
            # Extract event information
            event_type, direction, severity = self.extract_event_type(tweet_text)
            entities = self.extract_entities(tweet_text)
            
            # Parse tweet time and convert to +7 timezone
            tweet_time = self.parse_tweet_time(tweet.created_at)
            
            # Create signal
            signal = {
                'event_id': content_hash,
                'ts_iso': tweet_time,
                'source': 'x_twitter',
                'headline': tweet_text[:100] + '...' if len(tweet_text) > 100 else tweet_text,
                'url': f"https://twitter.com/user/status/{tweet.id}",
                'event_type': event_type,
                'primary_entity': entities[0] if entities else '',
                'entities': entities,
                'severity': severity,
                'direction': direction,
                'confidence': 0.8,  # X posts have high confidence
                'raw_text': tweet_text,
                'extras': {
                    'content_hash': content_hash,
                    'tweet_id': str(tweet.id),
                    'author_id': str(tweet.author_id) if hasattr(tweet, 'author_id') else '',
                    'ingestion_method': 'x_streaming',
                    'latency_ms': 0,  # Real-time, no latency
                    'platform': 'x_twitter'
                }
            }
            
            # Mark as processed
            self.mark_as_processed(content_hash)
            
            # Publish to Kafka
            await self.publish_to_kafka(signal)
            
            logger.info(f"Processed real-time tweet: {event_type} (severity: {severity})")
            
        except Exception as e:
            logger.error(f"Error processing tweet: {e}")
    
    def extract_event_type(self, text: str) -> tuple:
        """Extract event type, direction, and severity from tweet text"""
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
        """Extract entities from tweet text"""
        entities = []
        
        # Extract common crypto entities
        crypto_entities = ['BTC', 'ETH', 'SOL', 'ADA', 'DOT', 'LINK', 'UNI', 'AVAX']
        for entity in crypto_entities:
            if entity in text.upper():
                entities.append(entity)
        
        # Extract company names
        company_keywords = ['Binance', 'Coinbase', 'FTX', 'Kraken', 'Gemini']
        for company in company_keywords:
            if company in text:
                entities.append(company)
        
        return entities[:5]  # Limit to 5 entities
    
    def parse_tweet_time(self, tweet_time) -> str:
        """Parse tweet time and convert to Vietnam timezone (+7)"""
        try:
            if tweet_time:
                # Convert to Vietnam timezone (+7)
                vietnam_tz = timezone(timedelta(hours=7))
                vietnam_time = tweet_time.astimezone(vietnam_tz)
                return vietnam_time.isoformat()
        except:
            pass
        
        # Fallback to current time
        vietnam_tz = timezone(timedelta(hours=7))
        return datetime.now(vietnam_tz).isoformat()
    
    def is_duplicate(self, content_hash: str) -> bool:
        """Check if content hash already exists in Redis"""
        return self.redis_client.exists(f"x_hash:{content_hash}")
    
    def mark_as_processed(self, content_hash: str, ttl: int = 3600):
        """Mark content hash as processed in Redis"""
        self.redis_client.setex(f"x_hash:{content_hash}", ttl, "1")
    
    async def publish_to_kafka(self, signal: Dict):
        """Publish signal to Kafka topic"""
        try:
            future = self.kafka_producer.send(
                'news.signals.v1',
                value=signal,
                key=signal['event_id'].encode()
            )
            await asyncio.get_event_loop().run_in_executor(None, future.get, 10)
            logger.info(f"Published X signal to Kafka: {signal['event_id']}")
        except Exception as e:
            logger.error(f"Error publishing to Kafka: {e}")
    
    async def stop(self):
        """Stop real-time X client"""
        logger.info("Stopping real-time X client...")
        self.running = False
        
        # Close Kafka producer
        self.kafka_producer.close()
        
        # Close Redis connection
        self.redis_client.close()

async def main():
    """Main entry point"""
    client = RealTimeXClient()
    
    try:
        await client.start_streaming()
    except KeyboardInterrupt:
        logger.info("Shutting down real-time X client")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
    finally:
        await client.stop()

if __name__ == "__main__":
    asyncio.run(main())
