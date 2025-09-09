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
import aiohttp
from kafka import KafkaConsumer
from prometheus_client import Counter, Histogram, start_http_server
from os import getenv
try:
    from aiohttp import web
except Exception:
    web = None

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
        self.input_mode = getenv('INPUT_MODE', 'kafka').lower()  # 'kafka' or 'http'
        self.api_base = os.getenv('TELEGRAM_API_BASE', 'https://api.telegram.org')
        self.fast_ack = os.getenv('TELEGRAM_FAST_ACK', 'true').lower() == 'true'
        
        if not self.bot_token or not self.channel_id:
            logger.warning("Telegram bot token or channel ID not configured")
            self.enabled = False
        else:
            self.enabled = True
        
        # Redis for deduplication
        self.redis_url = os.getenv('REDIS_URL', 'redis://localhost:6379')
        self.redis_client = redis.from_url(self.redis_url)
        
        # Kafka consumer (only if INPUT_MODE=kafka)
        self.consumer = None
        if self.input_mode == 'kafka':
            self.kafka_servers = os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'localhost:9092').split(',')
            # Tune consumer for lower end-to-end latency
            self.consumer = KafkaConsumer(
                'news.signals.v1',
                bootstrap_servers=self.kafka_servers,
                value_deserializer=lambda m: json.loads(m.decode('utf-8')),
                group_id='telegram-bot-group',
                auto_offset_reset='latest',
                enable_auto_commit=True,
                fetch_max_wait_ms=100,
                fetch_min_bytes=1
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
        
        # Shared HTTP session for async sends (HTTP mode)
        self.http_session = None
        # Runtime counters
        self.sent_count = 0

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
            
            # Build message in template:
            # "Open {{direction}} ${{token}} when ${{actor}} in ${{cex}} at price ${{price}}"

            # Direction
            event_to_side = {
                'LISTING': 'Long',
                'DELIST': 'Short',
                'HACK': 'Short'
            }
            direction = event_to_side.get(event_type, 'Long')

            # Token
            token = (primary_entity or '').upper()
            if not token and entities:
                for ent in entities:
                    if ent.isalpha() and 2 <= len(ent) <= 10:
                        token = ent.upper()
                        break
            if not token:
                token = 'TOKEN'

            # Actor (e.g., "listing coinbase", "delist binance")
            actor_exchange_map = {
                'BYBIT': 'bybit',
                'MEXC': 'mexc',
                'GATE': 'gate',
                'GATEIO': 'gate',
                'GATE.IO': 'gate',
                'BINANCE': 'binance',
                'UPBIT': 'upbit',
                'OKX': 'okx',
                'COINBASE': 'coinbase'
            }
            detected_actor_cex = None
            text_for_exchange = f"{clean_headline} {' '.join(entities)}".upper()
            for key, name in actor_exchange_map.items():
                if key in text_for_exchange:
                    detected_actor_cex = name
                    break
            verb = 'listing' if event_type == 'LISTING' else ('delist' if event_type == 'DELIST' else event_type.lower())
            actor = f"{verb} {detected_actor_cex}" if detected_actor_cex else verb

            # CEX to open trade: prefer best from exchange-checker if provided via extras.best_cex
            cex = None
            try:
                best = signal.get('extras', {}).get('best_cex')
                if best and isinstance(best, dict):
                    # Expect fields: exchange (mexc/bybit/gate), market (perp/spot), price
                    ex_map = {'mexc':'MEXC','bybit':'Bybit','gate':'Gate'}
                    cex = ex_map.get(best.get('exchange', '').lower())
                    if best.get('price') is not None:
                        signal.setdefault('price', best.get('price'))
            except Exception:
                pass
            if not cex:
                # fallback detection from text
                preferred_cex = ['MEXC', 'BYBIT', 'GATE']
                detected_cex_title = None
                for key, name in {'BYBIT':'Bybit','MEXC':'MEXC','GATE':'Gate','GATEIO':'Gate','GATE.IO':'Gate','BINANCE':'Binance','UPBIT':'Upbit','OKX':'OKX','COINBASE':'Coinbase'}.items():
                    if key in text_for_exchange:
                        detected_cex_title = name
                        break
                for pref in preferred_cex:
                    if pref in text_for_exchange:
                        cex = {'MEXC':'MEXC','BYBIT':'Bybit','GATE':'Gate'}[pref]
                        break
                if not cex:
                    cex = detected_cex_title or 'Bybit'

            # Price
            price = None
            try:
                price = signal.get('price') or signal.get('extras', {}).get('price')
            except Exception:
                price = None
            price_str = f"{price}" if price is not None else "market"

            message = f"Open {direction} ${token} when {actor} in {cex} at price ${price_str}"
            
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
            url = f"{self.api_base}/bot{self.bot_token}/sendMessage"
            url = f"{self.api_base}/bot{self.bot_token}/sendMessage"
            data = {
                'chat_id': self.channel_id,
                'text': message,
                'disable_web_page_preview': True
            }
            
            send_start = time.time()
            with MESSAGE_SEND_TIME.time():
                response = requests.post(url, json=data, timeout=10)
                
                if response.status_code == 200:
                    result = response.json()
                    if result.get('ok'):
                        self.last_message_time = time.time()
                        self.sent_count += 1
                        logger.info(
                            f"Telegram send success | http_ms={(self.last_message_time - send_start)*1000:.1f} | sent_total={self.sent_count}"
                        )
                        return True
                    else:
                        logger.error(f"Telegram API error: {result}")
                        return False
                else:
                    logger.error(
                        f"HTTP error {response.status_code}: {response.text} | http_ms={(time.time()-send_start)*1000:.1f}"
                    )
                    return False
                    
        except Exception as e:
            logger.error(f"Error sending Telegram message: {e}")
            return False

    async def async_send_telegram_message(self, message: str) -> bool:
        """Async send using shared aiohttp session (HTTP mode)."""
        if not self.enabled:
            logger.warning("Telegram bot not enabled")
            return False
        if self.http_session is None:
            logger.error("HTTP session not initialized for async send")
            return False
        try:
            current_time = time.time()
            if current_time - self.last_message_time < self.min_interval:
                await asyncio.sleep(self.min_interval - (current_time - self.last_message_time))
            url = f"{self.api_base}/bot{self.bot_token}/sendMessage"
            data = {
                'chat_id': self.channel_id,
                'text': message,
                'disable_web_page_preview': True
            }
            send_start = time.time()
            timeout = aiohttp.ClientTimeout(total=4)
            async with self.http_session.post(url, json=data, timeout=timeout) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    if result.get('ok'):
                        self.last_message_time = time.time()
                        self.sent_count += 1
                        logger.info(
                            f"Telegram send success (async) | http_ms={(self.last_message_time - send_start)*1000:.1f} | sent_total={self.sent_count}"
                        )
                        return True
                    logger.error(f"Telegram API error: {result}")
                    return False
                text = await resp.text()
                logger.error(
                    f"HTTP error {resp.status}: {text} | http_ms={(time.time()-send_start)*1000:.1f}"
                )
                return False
        except Exception as e:
            logger.error(f"Error sending Telegram message (async): {e}")
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
                    t_receive = time.time()
                    signal = message.value
                    # Instrumentation: measure end-to-end from signal ts to now
                    ts_iso = signal.get('ts_iso')
                    ingest_delay_ms = None
                    if ts_iso:
                        try:
                            dt = datetime.fromisoformat(ts_iso)
                            ingest_delay_ms = (datetime.now(timezone(timedelta(hours=7))) - dt).total_seconds()*1000
                        except Exception:
                            pass
                    if ingest_delay_ms is not None:
                        logger.info(f"Latency | on_receive_ms=0.0 | ingest_delay_ms={ingest_delay_ms:.1f}")
                    
                    # Check if we should send this message
                    t_filter_start = time.time()
                    if self.should_send_message(signal):
                        t_after_filter = time.time()
                        logger.info(
                            f"Latency | filter_ms={(t_after_filter - t_filter_start)*1000:.1f} | since_receive_ms={(t_after_filter - t_receive)*1000:.1f}"
                        )
                        # Format message
                        t_format_start = time.time()
                        formatted_message = self.format_message(signal)
                        t_after_format = time.time()
                        logger.info(
                            f"Latency | format_ms={(t_after_format - t_format_start)*1000:.1f} | since_receive_ms={(t_after_format - t_receive)*1000:.1f}"
                        )
                        
                        # Skip if no template found (not a high impact event)
                        if formatted_message is None:
                            continue
                        
                        # Send to Telegram
                        t_send_start = time.time()
                        ok = False
                        if self.http_session is None:
                            try:
                                timeout = aiohttp.ClientTimeout(total=4)
                                connector = aiohttp.TCPConnector(limit=100, ttl_dns_cache=300)
                                self.http_session = aiohttp.ClientSession(timeout=timeout, connector=connector)
                            except Exception:
                                self.http_session = None
                        if self.http_session is not None:
                            try:
                                ok = await self.async_send_telegram_message(formatted_message)
                            except Exception as e:
                                logger.error(f"Async send failed in Kafka mode: {e}")
                        if not ok:
                            ok = self.send_telegram_message(formatted_message)
                        if ok:
                            t_after_send = time.time()
                            logger.info(
                                f"Latency | send_ms={(t_after_send - t_send_start)*1000:.1f} | end_to_end_ms={(t_after_send - t_receive)*1000:.1f}"
                            )
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
        logger.info(f"Starting Telegram Bot Service (mode={self.input_mode})")
        
        # Start Prometheus metrics server
        start_http_server(8003)
        
        if self.input_mode == 'kafka':
            # Process signals from Kafka
            await self.process_signals()
        else:
            # HTTP intake mode
            if web is None:
                raise RuntimeError("aiohttp is required for HTTP mode. Add 'aiohttp' to requirements.txt")

            app = web.Application()

            async def handle_health(request):
                return web.json_response({"status": "ok"})

            async def handle_signal(request):
                t_receive = time.time()
                try:
                    signal = await request.json()
                except Exception:
                    return web.json_response({"ok": False, "error": "invalid_json"}, status=400)

                # Filtering
                t_filter_start = time.time()
                if not self.should_send_message(signal):
                    return web.json_response({"ok": True, "sent": False, "reason": "filtered"})
                t_after_filter = time.time()

                # Formatting
                t_format_start = time.time()
                formatted_message = self.format_message(signal)
                if formatted_message is None:
                    return web.json_response({"ok": True, "sent": False, "reason": "no_template"})
                t_after_format = time.time()

                # Send
                if self.fast_ack:
                    # schedule send and acknowledge immediately
                    if self.http_session is None:
                        timeout = aiohttp.ClientTimeout(total=4)
                        connector = aiohttp.TCPConnector(limit=100, ttl_dns_cache=300)
                        self.http_session = aiohttp.ClientSession(timeout=timeout, connector=connector)
                    async def _bg_send():
                        try:
                            sent_bg = await self.async_send_telegram_message(formatted_message)
                            if sent_bg:
                                signal_id = signal.get('event_id', '')
                                self.mark_signal_sent(signal_id)
                                MESSAGES_SENT.labels(
                                    source=signal.get('source', 'unknown'),
                                    event_type=signal.get('event_type', 'unknown')
                                ).inc()
                        except Exception as e:
                            logger.error(f"background send failed: {e}")
                    asyncio.create_task(_bg_send())

                    metrics = {
                        "filter_ms": round((t_after_filter - t_filter_start) * 1000, 1),
                        "format_ms": round((t_after_format - t_format_start) * 1000, 1),
                        "queued": True,
                        "end_to_end_ms": round((time.time() - t_receive) * 1000, 1)
                    }
                    logger.info(f"HTTP Intake Latency (fast_ack) | {metrics}")
                    return web.json_response({"ok": True, "queued": True, "metrics": metrics, "sent_total": self.sent_count})
                else:
                    t_send_start = time.time()
                    sent = False
                    if self.http_session is None:
                        # initialize session lazily
                        timeout = aiohttp.ClientTimeout(total=4)
                        connector = aiohttp.TCPConnector(limit=100, ttl_dns_cache=300)
                        self.http_session = aiohttp.ClientSession(timeout=timeout, connector=connector)
                    try:
                        sent = await self.async_send_telegram_message(formatted_message)
                    except Exception as e:
                        logger.error(f"async send failed, fallback to sync: {e}")
                        sent = self.send_telegram_message(formatted_message)
                    t_after_send = time.time()

                    metrics = {
                        "filter_ms": round((t_after_filter - t_filter_start) * 1000, 1),
                        "format_ms": round((t_after_format - t_format_start) * 1000, 1),
                        "send_ms": round((t_after_send - t_send_start) * 1000, 1),
                        "end_to_end_ms": round((t_after_send - t_receive) * 1000, 1)
                    }
                    logger.info(f"HTTP Intake Latency | {metrics}")

                    if sent:
                        signal_id = signal.get('event_id', '')
                        self.mark_signal_sent(signal_id)
                        MESSAGES_SENT.labels(
                            source=signal.get('source', 'unknown'),
                            event_type=signal.get('event_type', 'unknown')
                        ).inc()

                    return web.json_response({"ok": True, "sent": bool(sent), "metrics": metrics, "sent_total": self.sent_count})

            app.router.add_get('/health', handle_health)
            app.router.add_post('/signal', handle_signal)

            port = int(getenv('TELEGRAM_HTTP_PORT', '8013'))
            logger.info(f"HTTP intake listening on 0.0.0.0:{port}")
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, '0.0.0.0', port)
            await site.start()

            # Keep running
            try:
                while True:
                    await asyncio.sleep(3600)
            finally:
                if self.http_session:
                    await self.http_session.close()

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
