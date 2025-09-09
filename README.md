# News Trading System - Phase 1 MVP

A real-time news-driven trading system that collects cryptocurrency news from RSS feeds and executes trades based on predefined strategies.

## 🚀 Features

- **BWEnews Integration**: Real-time news from BWEnews RSS feed and WebSocket API
- **Signal Processing**: Detects listing/delisting/hack events and normalizes them into trading signals
- **Kafka Integration**: Uses Apache Kafka for reliable message queuing
- **PostgreSQL Database**: Stores trades, orders, and audit logs
- **Redis Caching**: Handles deduplication and hot state management
- **AI/ML Processing**: BERT sentiment analysis and ML event classification
- **Multi-Exchange Trading**: Bybit and MEXC exchange integration
- **Monitoring**: Prometheus metrics and Grafana dashboards

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

Create a `.env` file in the root directory:

```bash
# Bybit API (optional for Phase 1 - using simulated trading)
BYBIT_API_KEY=your_api_key_here
BYBIT_SECRET_KEY=your_secret_key_here

# Database (defaults are fine for local development)
DATABASE_URL=postgresql://trading_user:trading_pass@postgres:5432/news_trading
REDIS_URL=redis://redis:6379
KAFKA_BOOTSTRAP_SERVERS=kafka:29092
```

### 3. Start Services

```bash
# Start all services
docker-compose up -d

# Check service status
docker-compose ps

# View logs
docker-compose logs -f bwenews-client
docker-compose logs -f execution-engine
```

### 4. Access Services

- **Grafana Dashboard**: http://localhost:3000 (admin/admin)
- **Prometheus**: http://localhost:9090
- **PostgreSQL**: localhost:5432
- **Redis**: localhost:6379
- **Kafka**: localhost:9092

## 📊 Monitoring

### Prometheus Metrics

The system exposes the following metrics:

- `signals_processed_total`: Total signals processed by source and event type
- `signals_published_total`: Total signals published to Kafka
- `signals_consumed_total`: Total signals consumed by execution engine
- `trades_executed_total`: Total trades executed by symbol and side
- `signal_processing_seconds`: Time spent processing signals
- `trade_execution_seconds`: Time spent executing trades

### Grafana Dashboards

Import the provided dashboards to monitor:
- Signal processing performance
- Trading activity
- System health
- Error rates

## 🗄️ Database Schema

### Core Tables

- **users**: User accounts and risk limits
- **orders**: Trading orders
- **trades**: Executed trades
- **signals**: Processed news signals
- **audit_logs**: System audit trail
- **risk_limits**: User risk management

### Sample Queries

```sql
-- View recent signals
SELECT event_type, headline, processed_at 
FROM signals 
ORDER BY processed_at DESC 
LIMIT 10;

-- View trading activity
SELECT symbol, side, quantity, price, executed_at 
FROM trades 
ORDER BY executed_at DESC 
LIMIT 10;

-- Check user positions
SELECT symbol, SUM(CASE WHEN side = 'B' THEN quantity ELSE -quantity END) as net_position
FROM trades 
WHERE user_id = 'your_user_id'
GROUP BY symbol;
```

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

2. **Test Signal Processing**:
   ```bash
   # Check if signals are being processed
   docker-compose logs execution-engine | grep "Received signal"
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

### Automated Testing

```bash
# Run tests
pytest tests/

# Run with coverage
pytest --cov=services tests/
```

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
