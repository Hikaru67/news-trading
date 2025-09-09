#!/usr/bin/env python3
"""
Telegram Bot Service - Send crawled information to Telegram channel
"""

import asyncio
import json
import logging
import os
import time
import redis
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

import requests
from kafka import KafkaConsumer
from prometheus_client import Counter, Histogram, start_http_server

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Prometheus metrics
MESSAGES_SENT = Counter('telegram_messages_sent_total', 'Total messages sent to Telegram', ['source', 'event_type'])
MESSAGE_SEND_TIME = Histogram('telegram_message_send_seconds', 'Time spent sending messages to Telegram')

class TelegramBot:
    def __init__(self):
        # Telegram configuration
        self.bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.channel_id = os.getenv('TELEGRAM_CHANNEL_ID')
        
        if not self.bot_token or not self.channel_id:
            logger.warning("Telegram bot token or channel ID not configured")
            self.enabled = False
        else:
            self.enabled = True
        
        # Redis for deduplication
        self.redis_url = os.getenv('REDIS_URL', 'redis://localhost:6379')
        self.redis_client = redis.from_url(self.redis_url)
        
        # Kafka consumer
        self.kafka_servers = os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'localhost:9092').split(',')
        self.consumer = KafkaConsumer(
            'news.signals.v1',
            bootstrap_servers=self.kafka_servers,
            value_deserializer=lambda m: json.loads(m.decode('utf-8')),
            group_id='telegram-bot-group',
            auto_offset_reset='latest',
            enable_auto_commit=True
        )
        
        # Message templates - ONLY TOKEN-SPECIFIC EVENTS
        self.message_templates = {
            'LISTING': {
                'title': 'Listing'
            },
            'DELIST': {
                'title': 'Delist'
            },
            'HACK': {
                'title': 'Hack'
            }
        }
        
        # Rate limiting
        self.last_message_time = 0
        self.min_interval = 2  # seconds between messages
        
        # Deduplication
        self.sent_signals_ttl = 86400  # 24 hours in seconds

    def is_signal_sent(self, signal_id: str) -> bool:
        """Check if signal was already sent to Telegram"""
        return self.redis_client.exists(f"telegram_sent:{signal_id}")

    def mark_signal_sent(self, signal_id: str):
        """Mark signal as sent to Telegram"""
        self.redis_client.setex(f"telegram_sent:{signal_id}", self.sent_signals_ttl, "1")

    def format_message(self, signal: Dict) -> str:
        """Format signal into BWEnews-style Telegram message"""
        try:
            event_type = signal.get('event_type', 'OTHER')
            template = self.message_templates.get(event_type)
            
            # If no template found, skip this signal (it's not a high impact event)
            if not template:
                logger.info(f"No template for event type: {event_type} - skipping")
                return None
            
            # Format timestamp (already in +7 timezone from signal-collector)
            ts = signal.get('ts_iso', '')
            if ts:
                try:
                    dt = datetime.fromisoformat(ts)
                    formatted_time = dt.strftime('%Y-%m-%d %H:%M:%S')
                except:
                    formatted_time = ts
            else:
                vietnam_tz = timezone(timedelta(hours=7))
                formatted_time = datetime.now(vietnam_tz).strftime('%Y-%m-%d %H:%M:%S')
            
            # Get signal data
            headline = signal.get('headline', 'N/A')
            url = signal.get('url', 'N/A')
            primary_entity = signal.get('primary_entity', '')
            entities = signal.get('entities', [])
            
            # Clean headline for Telegram (remove HTML entities and escape special characters)
            import html
            import re
            clean_headline = html.unescape(headline)
            # Escape special HTML characters for Telegram
            clean_headline = clean_headline.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            
            # Extract market cap if available in headline
            market_cap = "N/A"
            if "$" in clean_headline and "MarketCap" in clean_headline:
                try:
                    # Try to extract market cap from headline
                    match = re.search(r'\$(\d+[KMB]?)', clean_headline)
                    if match:
                        market_cap = f"${match.group(1)}"
                except:
                    pass
            
            # Build BWEnews-style message (simplified format)
            message = f"""📰 Headline:
{clean_headline}

${primary_entity}  MarketCap: {market_cap}
(Auto match could be wrong, 自动匹配可能不准确)
————————————
{formatted_time}
source: {url}"""
            
            # Debug logging
            logger.info(f"Formatted message: {repr(message)}")
            
            return message.strip()
            
        except Exception as e:
            logger.error(f"Error formatting message: {e}")
            return f"Error formatting signal: {e}"

    def send_telegram_message(self, message: str) -> bool:
        """Send message to Telegram channel"""
        if not self.enabled:
            logger.warning("Telegram bot not enabled")
            return False
        
        try:
            # Rate limiting
            current_time = time.time()
            if current_time - self.last_message_time < self.min_interval:
                time.sleep(self.min_interval - (current_time - self.last_message_time))
            
            # Send message
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            data = {
                'chat_id': self.channel_id,
                'text': message,
                'disable_web_page_preview': True
            }
            
            with MESSAGE_SEND_TIME.time():
                response = requests.post(url, json=data, timeout=10)
                
                if response.status_code == 200:
                    result = response.json()
                    if result.get('ok'):
                        self.last_message_time = time.time()
                        logger.info(f"Message sent to Telegram successfully")
                        return True
                    else:
                        logger.error(f"Telegram API error: {result}")
                        return False
                else:
                    logger.error(f"HTTP error {response.status_code}: {response.text}")
                    return False
                    
        except Exception as e:
            logger.error(f"Error sending Telegram message: {e}")
            return False

    def should_send_message(self, signal: Dict) -> bool:
        """Determine if message should be sent based on filters - ONLY HIGH IMPACT"""
        try:
            # Check if signal was already sent
            signal_id = signal.get('event_id', '')
            if self.is_signal_sent(signal_id):
                logger.info(f"Signal already sent to Telegram: {signal_id}")
                return False
            
            # Check severity first (only send VERY HIGH severity signals)
            severity = signal.get('severity', 0)
            if severity < 0.8:  # Skip low impact signals
                return False
            
            # Only send CRITICAL events that can move markets immediately
            event_type = signal.get('event_type', '')
            if event_type in ['LISTING', 'DELIST', 'HACK', 'REGULATION']:
                return True
            
            # Send FED_SPEECH events with high confidence
            if event_type == 'FED_SPEECH':
                confidence = signal.get('confidence', 0)
                return confidence >= 0.8
            
            return False
            
        except Exception as e:
            logger.error(f"Error checking message filter: {e}")
            return False

    async def process_signals(self):
        """Process signals from Kafka and send to Telegram"""
        logger.info("Starting Telegram Bot - Processing signals...")
        
        try:
            for message in self.consumer:
                try:
                    signal = message.value
                    
                    # Check if we should send this message
                    if self.should_send_message(signal):
                        # Format message
                        formatted_message = self.format_message(signal)
                        
                        # Skip if no template found (not a high impact event)
                        if formatted_message is None:
                            continue
                        
                        # Send to Telegram
                        if self.send_telegram_message(formatted_message):
                            # Mark as sent
                            signal_id = signal.get('event_id', '')
                            self.mark_signal_sent(signal_id)
                            
                            # Update metrics
                            MESSAGES_SENT.labels(
                                source=signal.get('source', 'unknown'),
                                event_type=signal.get('event_type', 'unknown')
                            ).inc()
                            
                            logger.info(f"Sent signal to Telegram: {signal.get('event_type')} from {signal.get('source')}")
                        else:
                            logger.warning(f"Failed to send signal to Telegram: {signal.get('event_id')}")
                    else:
                        logger.debug(f"Skipped signal (filtered): {signal.get('event_type')} from {signal.get('source')}")
                        
                except Exception as e:
                    logger.error(f"Error processing signal: {e}")
                    continue
                    
        except Exception as e:
            logger.error(f"Error in signal processing loop: {e}")
            raise

    async def run(self):
        """Main run loop"""
        logger.info("Starting Telegram Bot Service")
        
        # Start Prometheus metrics server
        start_http_server(8003)
        
        # Process signals
        await self.process_signals()

def main():
    """Main entry point"""
    bot = TelegramBot()
    
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        logger.info("Shutting down Telegram Bot")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        raise

if __name__ == "__main__":
    main()
