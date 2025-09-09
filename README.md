# News Trading System - BWEnews Real-Time Flow

Real-time news-driven trading system using BWEnews (RSS + WebSocket) as the single source. Low-latency direct HTTP fanout sends signals concurrently to Telegram and the Exchange Checker, with an optional HTTP trade executor.

## 🚀 Features

- BWEnews Integration (RSS + WebSocket)
- Direct HTTP fanout: `bwenews-client` → `telegram-bot` and `exchange-checker`
- Telegram fast-ack mode (<1 ms HTTP response; background send) or full wait mode
- Exchange Checker: prioritizes CEX (MEXC > Bybit > Gate), prefers perp, symbol aliasing, price lookup
- Optional Trade Executor (HTTP intake). Simulated by default; can enable real mode with API keys
- Redis deduplication; Kafka retained for non-critical paths
- Prometheus + Grafana monitoring

## 📋 Prerequisites

- Docker and Docker Compose
- Python 3.11+
- At least 4GB RAM available

## 🛠️ Quick Start

### 1. Clone and Setup

```bash
git clone <repository-url>
cd news-trading
```

### 2. Environment Variables

Use `env.example` as reference and create a `.env` file. Key variables:

```
DIRECT_FANOUT_ENABLED=true
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHANNEL_ID=@your_channel_or_chat_id
TELEGRAM_FAST_ACK=true
TELEGRAM_HTTP_PORT=8013
EXCHANGE_CHECKER_HTTP_PORT=8014
TRADE_EXECUTOR_HTTP_PORT=8015
TRADE_EXECUTOR_HTTP_URL=http://trade-executor:8015/signal
INPUT_MODE=http
REAL_TRADE_ENABLED=false  # set true with API keys to enable real orders
```

### 3. Start Services

```bash
docker compose up -d
docker compose ps

# Logs
docker compose logs -f bwenews-client
docker compose logs -f exchange-checker
docker compose logs -f telegram-bot
docker compose logs -f trade-executor
```

### 4. Access Services

- Grafana: http://localhost:3001 (admin/admin)
- Prometheus: http://localhost:9091
- **PostgreSQL**: localhost:5432
- **Redis**: localhost:6379
- **Kafka**: localhost:9092

## 📊 Monitoring

Prometheus metrics are exposed by services; Grafana dashboards can be pointed to Prometheus at 9091.

## ⚙️ Modes

- Telegram fast-ack: returns immediately; disable by `TELEGRAM_FAST_ACK=false`.
- Trade Executor real mode: `REAL_TRADE_ENABLED=true` and set exchange API keys.

## 🔧 Configuration

### BWEnews Configuration

The system uses BWEnews as the primary news source:

- **RSS Feed**: https://rss-public.bwe-ws.com/
- **WebSocket API**: wss://bwenews-api.bwe-ws.com/ws
- **Documentation**: https://telegra.ph/BWEnews-API-documentation-06-19

The BWEnews client automatically handles both RSS polling and WebSocket real-time updates.

### Trading Strategies

Edit `services/execution-engine/main.py` to modify playbooks:

```python
self.playbooks = {
    'LISTING': {
        'action': 'BUY',
        'position_size': self.calculate_position_size,
        'take_profit_pct': 0.02,  # 2%
        'stop_loss_pct': 0.01,    # 1%
        'timeout_seconds': 90
    }
}
```

## 🧪 Testing

### Manual Testing

1. **Test BWEnews Collection**:
   ```bash
   # Check if signals are being collected from BWEnews
   docker-compose logs bwenews-client | grep "Processed"
   ```

2. **Test Telegram HTTP intake**:
   ```bash
   curl -s -X POST http://localhost:8013/signal -H 'Content-Type: application/json' -d '{"event_id":"test","ts_iso":"2025-01-01T00:00:00+07:00","source":"bench","headline":"TEST","url":"https://example.com","event_type":"LISTING","primary_entity":"TEST","entities":["UPBIT","LISTING","TEST"],"severity":0.9,"direction":"BULL","confidence":0.95}'
   ```

3. **Test Database**:
   ```bash
   # Connect to PostgreSQL
   docker-compose exec postgres psql -U trading_user -d news_trading
   
   # Check signals table
   SELECT COUNT(*) FROM signals;
   
   # Check trades table
   SELECT COUNT(*) FROM trades;
   ```

### Performance Check

Use `perf_test_e2e.py` to measure HTTP latencies for Telegram and Exchange Checker.

## 📈 Performance

### Expected Metrics

- **Signal Processing**: < 500ms end-to-end latency
- **Throughput**: > 100 signals/minute
- **Uptime**: > 99%
- **Data Loss**: 0% (at-least-once delivery)

### Monitoring Alerts

Set up alerts for:
- High latency (> 1 second)
- High error rates (> 5%)
- Service downtime
- Database connection issues

## 🔒 Security

### Phase 1 Security Features

- **API Key Management**: Secure storage of exchange API keys
- **Rate Limiting**: Built-in rate limiting for RSS feeds
- **Input Validation**: All inputs are validated and sanitized
- **Audit Logging**: Complete audit trail of all actions

### Best Practices

- Use environment variables for sensitive data
- Regularly rotate API keys
- Monitor for unusual activity
- Keep dependencies updated

## 🚨 Troubleshooting

### Common Issues

1. **Kafka Connection Issues**:
   ```bash
   # Check Kafka status
   docker-compose logs kafka
   
   # Restart Kafka
   docker-compose restart kafka
   ```

2. **Database Connection Issues**:
   ```bash
   # Check PostgreSQL status
   docker-compose logs postgres
   
   # Reset database
   docker-compose down -v
   docker-compose up -d
   ```

3. **High Memory Usage**:
   ```bash
   # Check resource usage
   docker stats
   
   # Increase memory limits in docker-compose.yml
   ```

### Log Analysis

```bash
# View all logs
docker-compose logs

# Filter by service
docker-compose logs signal-collector | grep ERROR

# Follow logs in real-time
docker-compose logs -f
```

## 🔄 Development

### Adding New Features

1. **New Signal Sources**:
   - Add RSS URL to `sources` configuration
   - Implement custom parsing if needed
   - Update tests

2. **New Trading Strategies**:
   - Add playbook to `playbooks` configuration
   - Implement strategy logic
   - Add risk management rules

3. **New Event Types**:
   - Add patterns to `event_patterns`
   - Update signal normalization
   - Add corresponding playbook

### Code Style

```bash
# Format code
black services/

# Lint code
flake8 services/

# Type checking
mypy services/
```

## 📚 Next Steps

### Phase 2 Enhancements

- Real exchange integration (Bybit API)
- Additional news sources (Twitter, Fed announcements)
- Advanced NLP processing
- Multi-exchange support
- Real-time market data

### Phase 3 Features

- Machine learning optimization
- Cross-venue arbitrage
- Advanced risk management
- Institutional-grade infrastructure

## 📞 Support

For issues and questions:
- Check the troubleshooting section
- Review logs for error messages
- Open an issue on GitHub
- Contact the development team

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.
