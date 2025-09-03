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
import feedparser
from bs4 import BeautifulSoup

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class FedCalendarClient:
    """Federal Reserve Calendar client for economic events"""
    
    def __init__(self):
        self.kafka_producer = KafkaProducer(
            bootstrap_servers=os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'kafka:29092'),
            value_serializer=lambda v: json.dumps(v).encode('utf-8'),
            key_serializer=lambda k: k.encode('utf-8') if k else None
        )
        
        self.redis_client = redis.Redis.from_url(
            os.getenv('REDIS_URL', 'redis://redis:6379')
        )
        
        # Fed Calendar sources
        self.fed_sources = {
            'federal_reserve': {
                'url': 'https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm',
                'trust_score': 0.95,
                'type': 'official'
            },
            'fomc_calendar': {
                'url': 'https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm',
                'trust_score': 0.95,
                'type': 'official'
            },
            'economic_calendar': {
                'url': 'https://www.investing.com/economic-calendar/',
                'trust_score': 0.80,
                'type': 'third_party'
            }
        }
        
        # Economic event patterns
        self.event_patterns = {
            'FED_MEETING': [
                'fomc meeting', 'federal open market committee', 'rate decision',
                'monetary policy', 'interest rate decision'
            ],
            'FED_SPEECH': [
                'fed chair', 'jerome powell', 'federal reserve chair',
                'fed governor', 'central bank speech'
            ],
            'ECONOMIC_DATA': [
                'cpi', 'inflation', 'employment', 'gdp', 'retail sales',
                'manufacturing', 'housing', 'consumer confidence'
            ],
            'REGULATORY_ANNOUNCEMENT': [
                'banking regulation', 'financial regulation', 'supervision',
                'stress test', 'capital requirements'
            ]
        }
        
        # Event severity mapping
        self.event_severity = {
            'FED_MEETING': 0.95,      # Highest impact
            'FED_SPEECH': 0.85,       # High impact
            'ECONOMIC_DATA': 0.75,    # Medium impact
            'REGULATORY_ANNOUNCEMENT': 0.70  # Lower impact
        }
        
        self.running = False
        
    async def start_monitoring(self):
        """Start monitoring Fed calendar events"""
        logger.info("Starting Fed Calendar monitoring...")
        self.running = True
        
        while self.running:
            try:
                # Check for new events
                await self.check_fed_events()
                
                # Wait before next check
                await asyncio.sleep(300)  # Check every 5 minutes
                
            except Exception as e:
                logger.error(f"Fed calendar monitoring error: {e}")
                await asyncio.sleep(600)  # Wait longer on error
    
    async def check_fed_events(self):
        """Check for new Fed calendar events"""
        try:
            for source_name, source_config in self.fed_sources.items():
                try:
                    events = await self.fetch_fed_events(source_name, source_config)
                    if events:
                        for event in events:
                            await self.process_fed_event(source_name, event)
                            
                except Exception as e:
                    logger.error(f"Failed to fetch events from {source_name}: {e}")
                    
        except Exception as e:
            logger.error(f"Failed to check Fed events: {e}")
    
    async def fetch_fed_events(self, source_name: str, source_config: Dict) -> List[Dict]:
        """Fetch Fed calendar events from source"""
        try:
            async with aiohttp.ClientSession() as session:
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                }
                
                async with session.get(source_config['url'], headers=headers) as response:
                    if response.status == 200:
                        content = await response.text()
                        return await self.parse_fed_events(source_name, content, source_config)
                    else:
                        logger.warning(f"Failed to fetch {source_name}: {response.status}")
                        return []
                        
        except Exception as e:
            logger.error(f"Error fetching from {source_name}: {e}")
            return []
    
    async def parse_fed_events(self, source_name: str, content: str, source_config: Dict) -> List[Dict]:
        """Parse Fed calendar events from HTML content"""
        events = []
        
        try:
            if source_config['type'] == 'official':
                # Parse official Fed calendar
                events = await self.parse_official_fed_calendar(content)
            else:
                # Parse third-party calendar
                events = await self.parse_third_party_calendar(content)
                
        except Exception as e:
            logger.error(f"Error parsing {source_name} events: {e}")
            
        return events
    
    async def parse_official_fed_calendar(self, content: str) -> List[Dict]:
        """Parse official Federal Reserve calendar"""
        events = []
        
        try:
            soup = BeautifulSoup(content, 'html.parser')
            
            # Look for FOMC meeting dates
            fomc_sections = soup.find_all('div', class_='fomc-meeting')
            for section in fomc_sections:
                try:
                    # Extract meeting date
                    date_elem = section.find('span', class_='meeting-date')
                    if date_elem:
                        meeting_date = date_elem.get_text(strip=True)
                        
                        # Extract meeting details
                        details_elem = section.find('div', class_='meeting-details')
                        details = details_elem.get_text(strip=True) if details_elem else ''
                        
                        events.append({
                            'date': meeting_date,
                            'type': 'FED_MEETING',
                            'details': details,
                            'source': 'official_fed'
                        })
                        
                except Exception as e:
                    logger.warning(f"Error parsing FOMC meeting: {e}")
            
            # Look for Fed speeches
            speech_sections = soup.find_all('div', class_='fed-speech')
            for section in speech_sections:
                try:
                    # Extract speech date
                    date_elem = section.find('span', class_='speech-date')
                    if date_elem:
                        speech_date = date_elem.get_text(strip=True)
                        
                        # Extract speaker and topic
                        speaker_elem = section.find('span', class_='speaker')
                        topic_elem = section.find('span', class_='topic')
                        
                        speaker = speaker_elem.get_text(strip=True) if speaker_elem else ''
                        topic = topic_elem.get_text(strip=True) if topic_elem else ''
                        
                        events.append({
                            'date': speech_date,
                            'type': 'FED_SPEECH',
                            'details': f"{speaker}: {topic}",
                            'source': 'official_fed'
                        })
                        
                except Exception as e:
                    logger.warning(f"Error parsing Fed speech: {e}")
                    
        except Exception as e:
            logger.error(f"Error parsing official Fed calendar: {e}")
            
        return events
    
    async def parse_third_party_calendar(self, content: str) -> List[Dict]:
        """Parse third-party economic calendar"""
        events = []
        
        try:
            soup = BeautifulSoup(content, 'html.parser')
            
            # Look for economic events
            event_rows = soup.find_all('tr', class_='economic-event')
            for row in event_rows:
                try:
                    # Extract event date
                    date_elem = row.find('td', class_='event-date')
                    if date_elem:
                        event_date = date_elem.get_text(strip=True)
                        
                        # Extract event type and details
                        type_elem = row.find('td', class_='event-type')
                        details_elem = row.find('td', class_='event-details')
                        
                        event_type = type_elem.get_text(strip=True) if type_elem else ''
                        details = details_elem.get_text(strip=True) if details_elem else ''
                        
                        # Map to our event types
                        mapped_type = self.map_event_type(event_type)
                        
                        if mapped_type:
                            events.append({
                                'date': event_date,
                                'type': mapped_type,
                                'details': details,
                                'source': 'third_party'
                            })
                        
                except Exception as e:
                    logger.warning(f"Error parsing economic event: {e}")
                    
        except Exception as e:
            logger.error(f"Error parsing third-party calendar: {e}")
            
        return events
    
    def map_event_type(self, event_type: str) -> Optional[str]:
        """Map third-party event type to our classification"""
        event_lower = event_type.lower()
        
        if any(word in event_lower for word in ['fomc', 'fed', 'federal reserve']):
            return 'FED_MEETING'
        elif any(word in event_lower for word in ['speech', 'testimony', 'interview']):
            return 'FED_SPEECH'
        elif any(word in event_lower for word in ['cpi', 'inflation', 'employment', 'gdp']):
            return 'ECONOMIC_DATA'
        elif any(word in event_lower for word in ['regulation', 'supervision', 'stress test']):
            return 'REGULATORY_ANNOUNCEMENT'
        
        return None
    
    async def process_fed_event(self, source_name: str, event: Dict):
        """Process Fed calendar event"""
        try:
            # Create content hash for deduplication
            event_content = f"{event['date']} {event['type']} {event['details']}"
            content_hash = hashlib.sha256(event_content.encode()).hexdigest()
            
            # Check for duplicates
            if self.is_duplicate(content_hash):
                logger.debug(f"Duplicate Fed event detected: {content_hash}")
                return None
            
            # Parse event date and convert to +7 timezone
            event_time = self.parse_event_date(event['date'])
            
            # Determine direction based on event type
            direction = self.determine_event_direction(event['type'], event['details'])
            
            # Get severity from mapping
            severity = self.event_severity.get(event['type'], 0.5)
            
            # Create signal
            signal = {
                'event_id': content_hash,
                'ts_iso': event_time,
                'source': f"fed_calendar_{source_name}",
                'headline': f"Fed Event: {event['type']} - {event['details'][:50]}...",
                'url': f"https://www.federalreserve.gov/events/{content_hash}",
                'event_type': event['type'],
                'primary_entity': 'FED',
                'entities': ['FED', 'FOMC', 'ECONOMIC'],
                'severity': severity,
                'direction': direction,
                'confidence': self.fed_sources[source_name]['trust_score'],
                'raw_text': event_content,
                'extras': {
                    'content_hash': content_hash,
                    'event_date': event['date'],
                    'event_details': event['details'],
                    'source_type': self.fed_sources[source_name]['type'],
                    'ingestion_method': 'fed_calendar',
                    'latency_ms': 0,  # Calendar events are scheduled
                    'platform': 'fed_calendar'
                }
            }
            
            # Mark as processed
            self.mark_as_processed(content_hash)
            
            # Publish to Kafka
            await self.publish_to_kafka(signal)
            
            logger.info(f"Processed Fed event: {event['type']} (severity: {severity})")
            
        except Exception as e:
            logger.error(f"Error processing Fed event: {e}")
    
    def determine_event_direction(self, event_type: str, details: str) -> str:
        """Determine market direction based on event type and details"""
        details_lower = details.lower()
        
        if event_type == 'FED_MEETING':
            # Analyze meeting details for rate decision hints
            if any(word in details_lower for word in ['hawkish', 'rate hike', 'tightening', 'inflation concern']):
                return 'BEAR'
            elif any(word in details_lower for word in ['dovish', 'rate cut', 'easing', 'growth concern']):
                return 'BULL'
            else:
                return 'NEUTRAL'
                
        elif event_type == 'FED_SPEECH':
            # Analyze speech content for sentiment
            if any(word in details_lower for word in ['hawkish', 'inflation', 'tightening']):
                return 'BEAR'
            elif any(word in details_lower for word in ['dovish', 'growth', 'easing']):
                return 'BULL'
            else:
                return 'NEUTRAL'
                
        elif event_type == 'ECONOMIC_DATA':
            # Economic data is usually neutral
            return 'NEUTRAL'
            
        elif event_type == 'REGULATORY_ANNOUNCEMENT':
            # Regulatory news is usually bearish
            return 'BEAR'
        
        return 'NEUTRAL'
    
    def parse_event_date(self, date_str: str) -> str:
        """Parse event date and convert to Vietnam timezone (+7)"""
        try:
            # Try to parse various date formats
            date_formats = [
                '%B %d, %Y',      # January 15, 2024
                '%B %d %Y',       # January 15 2024
                '%Y-%m-%d',       # 2024-01-15
                '%m/%d/%Y',       # 01/15/2024
                '%d/%m/%Y'        # 15/01/2024
            ]
            
            for fmt in date_formats:
                try:
                    parsed_date = datetime.strptime(date_str.strip(), fmt)
                    # Convert to Vietnam timezone (+7)
                    vietnam_tz = timezone(timedelta(hours=7))
                    vietnam_time = parsed_date.replace(tzinfo=timezone.utc).astimezone(vietnam_tz)
                    return vietnam_time.isoformat()
                except ValueError:
                    continue
                    
        except Exception as e:
            logger.warning(f"Failed to parse date '{date_str}': {e}")
        
        # Fallback to current time
        vietnam_tz = timezone(timedelta(hours=7))
        return datetime.now(vietnam_tz).isoformat()
    
    def is_duplicate(self, content_hash: str) -> bool:
        """Check if content hash already exists in Redis"""
        # Use same key as signal-collector for unified deduplication
        return self.redis_client.exists(f"signal_hash:{content_hash}")
    
    def mark_as_processed(self, content_hash: str, ttl: int = 86400):  # 24 hours
        """Mark content hash as processed in Redis"""
        # Use same key as signal-collector for unified deduplication
        self.redis_client.setex(f"signal_hash:{content_hash}", ttl, "1")
    
    async def publish_to_kafka(self, signal: Dict):
        """Publish signal to Kafka topic"""
        try:
            future = self.kafka_producer.send(
                'news.signals.v1',
                value=signal,
                key=signal['event_id'].encode()
            )
            await asyncio.get_event_loop().run_in_executor(None, future.get, 10)
            logger.info(f"Published Fed event to Kafka: {signal['event_id']}")
        except Exception as e:
            logger.error(f"Error publishing to Kafka: {e}")
    
    async def stop(self):
        """Stop Fed calendar client"""
        logger.info("Stopping Fed calendar client...")
        self.running = False
        
        # Close Kafka producer
        self.kafka_producer.close()
        
        # Close Redis connection
        self.redis_client.close()

async def main():
    """Main entry point"""
    client = FedCalendarClient()
    
    try:
        await client.start_monitoring()
    except KeyboardInterrupt:
        logger.info("Shutting down Fed calendar client")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
    finally:
        await client.stop()

if __name__ == "__main__":
    asyncio.run(main())
