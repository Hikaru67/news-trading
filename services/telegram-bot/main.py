#!/usr/bin/env python3
"""
Telegram Bot Service - Send crawled information to Telegram channel
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
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
        
        # Message templates
        self.message_templates = {
            'LISTING': {
                'emoji': '🚀',
                'title': 'NEW LISTING ALERT',
                'color': '🟢'
            },
            'DELIST': {
                'emoji': '⚠️',
                'title': 'DELISTING ALERT',
                'color': '🔴'
            },
            'FED_SPEECH': {
                'emoji': '🏦',
                'title': 'FED ANNOUNCEMENT',
                'color': '🟡'
            },
            'POLITICAL_POST': {
                'emoji': '🏛️',
                'title': 'POLITICAL NEWS',
                'color': '🔵'
            },
            'HACK': {
                'emoji': '💥',
                'title': 'SECURITY ALERT',
                'color': '🔴'
            },
            'OTHER': {
                'emoji': '📰',
                'title': 'CRYPTO NEWS',
                'color': '⚪'
            }
        }
        
        # Rate limiting
        self.last_message_time = 0
        self.min_interval = 2  # seconds between messages

    def format_message(self, signal: Dict) -> str:
        """Format signal into Telegram message"""
        try:
            event_type = signal.get('event_type', 'OTHER')
            template = self.message_templates.get(event_type, self.message_templates['OTHER'])
            
            # Format timestamp
            ts = signal.get('ts_iso', '')
            if ts:
                try:
                    dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                    formatted_time = dt.strftime('%Y-%m-%d %H:%M:%S UTC')
                except:
                    formatted_time = ts
            else:
                formatted_time = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
            
            # Format entities
            entities = signal.get('entities', [])
            entities_text = ', '.join(entities[:3]) if entities else 'N/A'
            
            # Format confidence
            confidence = signal.get('confidence', 0)
            confidence_bar = '🟢' * int(confidence * 5) + '⚪' * (5 - int(confidence * 5))
            
            # Build message
            message = f"""
{template['emoji']} *{template['title']}* {template['emoji']}

📰 *Headline:*
{signal.get('headline', 'N/A')}

🏢 *Source:* {signal.get('source', 'N/A')}
🎯 *Entities:* {entities_text}
📊 *Confidence:* {confidence_bar} ({confidence:.1%})
⏰ *Time:* {formatted_time}

🔗 *Link:* {signal.get('url', 'N/A')}

{template['color']} *Event Type:* {event_type}
📈 *Direction:* {signal.get('direction', 'UNKNOWN')}
⚠️ *Severity:* {signal.get('severity', 0):.1f}
"""
            
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
                'parse_mode': 'Markdown',
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
        """Determine if message should be sent based on filters"""
        try:
            # Always send LISTING and DELIST events
            event_type = signal.get('event_type', '')
            if event_type in ['LISTING', 'DELIST']:
                return True
            
            # Send FED_SPEECH and HACK events with high confidence
            if event_type in ['FED_SPEECH', 'HACK']:
                confidence = signal.get('confidence', 0)
                return confidence >= 0.7
            
            # Send POLITICAL_POST with medium confidence
            if event_type == 'POLITICAL_POST':
                confidence = signal.get('confidence', 0)
                return confidence >= 0.6
            
            # Send OTHER events with high confidence
            if event_type == 'OTHER':
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
                        
                        # Send to Telegram
                        if self.send_telegram_message(formatted_message):
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
