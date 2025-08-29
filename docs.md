# News Trading System Documentation

## Table of Contents
1. [News Ingestion & Signal Service](#1-news-ingestion--signal-service)
2. [Ultra-Low-Latency Execution Service](#2-ultra-low-latency-execution-service)
3. [Data Contracts](#3-data-contracts)
4. [Deployment & DevOps](#4-deployment--devops)
5. [Code Skeleton](#5-code-skeleton)
6. [Backtest & Paper-Trading](#6-backtest--paper-trading)
7. [Risk Management & Safety](#7-risk-management--safety)
8. [Implementation Roadmap](#8-implementation-roadmap)

---

## 1. News Ingestion & Signal Service

### Overview
**Crawl → Normalize → Signal Generation**

### Objectives
- **Ultra-fast detection** of "price-moving" events:
  - New coin listings
  - Political tweets/Truth posts
  - Fed statements
  - Major hacks/exploits
  - Delistings/suspensions
  - ETF/SEC filings

- **Standardize events** with severity, confidence, entities → push to queue (Kafka/Redpanda) for Execution

### Architecture Components

#### Collectors (Python/Node)
- **Data Sources:**
  - RSS/Atom (exchange blogs, status pages)
  - WebSocket/Streaming (X/Twitter firehose/filtered stream)
  - HTML scraping (with watchdog diff)
  - Public APIs (Fed, Treasury, Coinbase/OKX/Binance Announcements)
  - On-chain alerts (etherscan push, sealevel, mev bots)

- **Features:**
  - Anti-duplication + content hash (SHA-256)
  - Rate limiting & retry with backoff

#### Normalizer
- Standardize to schema v1 (see below)
- Timezone conversion → UTC
- Entity extraction (coin tickers, people, organizations)

#### NLP Pipeline (Optional but Powerful)
- **Rule-based fast path** for "listing/delist" (regex + source trust list)
- **NER + sentiment + event type classifier** (distil model or LLM prompt) to assign:
  - `event_type`
  - `severity`
  - `direction` (bull/bear/unknown)
  - `confidence` (0–1)

**Latency target:** < 150 ms per event

#### Dedup & Correlator
- Merge similar news within 60–120s
- Increase confidence if multiple sources match

#### Feature Store / Cache
- **Redis** (short TTL) + **ClickHouse** (history & backtest storage)
- **PostgreSQL** (transactional data) + **MongoDB** (flexible document storage)

#### Publisher
- Push to Kafka topic `news.signals.v1` (keyed by primary_entity)
- Include idempotency key

### Message Schema (Protobuf/JSON)

```json
{
  "event_id": "uuid",
  "ts_iso": "2025-08-29T06:08:00Z",
  "source": "binance.announcements",
  "headline": "Binance Will List XYZ",
  "url": "https://...",
  "event_type": "LISTING|DELIST|FED_SPEECH|POLITICAL_POST|SEC_ACTION|HACK",
  "primary_entity": "XYZ",
  "entities": ["XYZ", "Binance"],
  "severity": 0.0-1.0,
  "direction": "BULL|BEAR|UNKNOWN",
  "confidence": 0.0-1.0,
  "raw_text": "...",
  "extras": {
    "listing_time": "immediate|T+5m",
    "market": "spot|perp"
  }
}
```

### Fast Detection Rules (Playbook Examples)

| Event Type | Detection Criteria | Action |
|------------|-------------------|---------|
| **Listing** (Top Exchanges) | Source ∈ {Binance, Coinbase, OKX, Bybit Announcements} & regex "Will List/Lists/Launches …" | `event_type=LISTING`, `severity≥0.85`, `direction=BULL` |
| **Fed/Chairman** | Keyphrase "rate hike/cut", "hawkish/dovish", "policy path" | Map severity by keywords |
| **Trump/Politicians** | Contains tickers/Bitcoin/ETF + verified account | Increase severity by source trust score |
| **Hack/Exploit** | Keywords "exploit", "drained", "bridge hacked" + estimated USD value | `direction=BEAR`, severity by USD value |

### SLO & Latency Budget
- **Ingestion to publish:** p95 < 400 ms (RSS/HTTP), < 200 ms (webhook/WS)
- **Dedup window:** 60–120s (balance noise reduction vs opportunity timing)

---

## 2. Ultra-Low-Latency Execution Service

### Objectives
- Receive signals from `news.signals.v1` → execute orders within < 30–80 ms (p95)
- Support multi-exchange, order routing, risk management & kill-switch

### Architecture Components

#### Consumer
- Kafka client (sarama/franz-go) with idempotent commit

#### Signal → Strategy Mapper
- Map `event_type` + `severity` → specific "playbook" (parameterized)

#### Market Data Snapshotter
- WebSocket depth/ticker per exchange (Binance/Bybit/OKX)
- Local NBBO style (select best liquidity & fees)

#### Risk Engine
- Max position per symbol
- Max leverage
- Max %Equity per event
- Daily loss limit
- Per-event cooldown
- Circuit-breaker by volatility/slippage

#### Order Router
- HMAC signer per exchange
- Time sync (NTP/PTP)
- REST + WS-trading
- Resend on 429/5xx with jitter
- Smart post-only/IOC/FOK by playbook

#### State Store
- **Redis** for hot state (positions, cooldowns)
- **PostgreSQL** for transactional data (trades, orders, user accounts)
- **MongoDB** for flexible document storage (signals, events, configurations)
- **ClickHouse** for time-series analytics (market data, performance metrics)

#### Monitoring
- Prometheus + Grafana
- Slack/Telegram alerts (latency spike, reject spike, fill ratio)

### Order Entry Playbooks (Examples)

#### LISTING on Binance
- **Direction:** LONG listed coin on perp "XYZUSDT" if available; if spot only: long "index basket" (BTC/ETH beta play) small
- **Params:** 
  - `order_type=IOC`
  - `qty = min(k·severity·liq, cap_per_trade)`
  - `slippage_max = 10 bps`
  - `take_profit = 20–60 bps`
  - `timeout_flat = 90s`
- **Exit:** Cancel if < 40% fill in 300ms; trailing take if momentum > threshold

#### FED_SPEECH hawkish
- **Action:** SHORT basket high-beta alts, hedge long BTC small (beta-neutral)

#### HACK/EXPLOIT
- **Action:** SHORT perp of affected project; if no perp → short basket of same sector

### Sizing & Risk

```python
position_size = min(
    account_equity * r,
    notional_cap[event],
    k * severity * liquidity_score
)
```

- **Cooldown:** 2–5 minutes per symbol after trade
- **Daily stop:** -X% equity or intraday VaR

---

## 3. Data Contracts

### Input (Execution receives)
- `news.signals.v1` (schema above)

### Output (Execution publishes)
- `trades.executions.v1` (orderId, status, fillQty, avgPx, slippage, latency)

### Feedback Loop
- **Strategy Learner** consumes signals + executions → re-weight playbook (offline/online)

---

## 4. Deployment & DevOps

### Infrastructure
- **Queue:** Redpanda/Kafka (`acks=all`, idempotent producer)
- **Storage:** ClickHouse (tick & events, cheap and fast) + Redis
- **Secrets:** Vault/KMS, rotate keys periodically; IP allowlist to exchanges
- **Time:** chrony + 3 NTP servers; log time drift

### Testing & Validation
- **Canary & Shadow trading:** Run parallel "paper" to validate new rules
- **Blue/Green deploy** for Execution
- **Chaos engineering:** Simulate 429/latency spike/WS drop

---

## 5. Code Skeleton

### Python: Collector (RSS/HTTP) → Kafka

```python
# pip install feedparser kafka-python fasttext regex
import hashlib
import json
import time
import uuid
import regex as re
import feedparser
from kafka import KafkaProducer
from datetime import datetime, timezone

producer = KafkaProducer(
    bootstrap_servers=["kafka:9092"],
    value_serializer=lambda v: json.dumps(v).encode()
)

TRUST = {"binance.ann": 0.98, "coinbase.blog": 0.95}

def norm(entry, source):
    raw = entry.get("title", "") + " " + entry.get("summary", "")
    eid = hashlib.sha256((source + raw).encode()).hexdigest()
    ts = datetime.now(timezone.utc).isoformat()
    text = (entry.get("title", "") + " " + entry.get("summary", "")).strip()
    evtype, direction, sev = "OTHER", "UNKNOWN", 0.3

    if re.search(r"\b(Will List|Lists|Listing)\b", text, flags=re.I):
        evtype, direction, sev = "LISTING", "BULL", 0.9
    if re.search(r"\b(delist|suspends)\b", text, flags=re.I):
        evtype, direction, sev = "DELIST", "BEAR", 0.9

    msg = {
        "event_id": str(uuid.uuid4()),
        "ts_iso": ts,
        "source": source,
        "headline": entry.get("title", ""),
        "url": entry.get("link", ""),
        "event_type": evtype,
        "primary_entity": "",  # TODO: extract ticker by regex map
        "entities": [],
        "severity": sev,
        "direction": direction,
        "confidence": TRUST.get(source, 0.6),
        "raw_text": text,
        "extras": {}
    }
    return msg

def run():
    feed = feedparser.parse("https://www.binance.com/en/support/announcement/rss")
    for e in feed.entries:
        m = norm(e, "binance.ann")
        producer.send("news.signals.v1", value=m, key=m["event_id"].encode())
    producer.flush()

if __name__ == "__main__":
    while True:
        run()
        time.sleep(5)
```

### Go: Execution (Kafka → Router → Exchange)

```go
// go get github.com/twmb/franz-go/pkg/kgo
// go get github.com/gorilla/websocket
package main

import (
    "context"
    "crypto/hmac"
    "crypto/sha256"
    "encoding/hex"
    "encoding/json"
    "fmt"
    "log"
    "net/http"
    "os"
    "time"

    "github.com/twmb/franz-go/pkg/kgo"
)

type Signal struct {
    EventID   string   `json:"event_id"`
    TsISO     string   `json:"ts_iso"`
    EventType string   `json:"event_type"`
    Direction string   `json:"direction"`
    Severity  float64  `json:"severity"`
    Entities  []string `json:"entities"`
    Extras    map[string]any `json:"extras"`
}

func signBinance(ts string, query string, secret string) string {
    mac := hmac.New(sha256.New, []byte(secret))
    mac.Write([]byte(query))
    return hex.EncodeToString(mac.Sum(nil))
}

func placeIOC(symbol string, side string, qty string) error {
    // minimal REST example (production: use ctx timeout, retry, jitter, error mapping)
    apiKey := os.Getenv("BINANCE_KEY")
    secret := os.Getenv("BINANCE_SECRET")
    ts := time.Now().UnixMilli()
    q := "symbol=" + symbol + "&side=" + side + "&type=LIMIT&timeInForce=IOC&quantity=" + qty + "&price=0&timestamp=" +
        fmt.Sprint(ts)
    sig := signBinance(fmt.Sprint(ts), q, secret)
    req, _ := http.NewRequest("POST", "https://api.binance.com/api/v3/order?"+q+"&signature="+sig, nil)
    req.Header.Set("X-MBX-APIKEY", apiKey)
    client := &http.Client{Timeout: 800 * time.Millisecond}
    resp, err := client.Do(req)
    if err != nil {
        return err
    }
    defer resp.Body.Close()
    if resp.StatusCode >= 300 {
        return fmt.Errorf("status %d", resp.StatusCode)
    }
    return nil
}

func main() {
    cl, err := kgo.NewClient(
        kgo.SeedBrokers("kafka:9092"),
        kgo.ConsumeTopics("news.signals.v1"),
        kgo.ConsumerGroup("exec-go-1"),
    )
    if err != nil {
        log.Fatal(err)
    }
    defer cl.Close()

    for {
        fetches := cl.PollFetches(context.Background())
        if fetches.IsClientClosed() {
            break
        }
        fetches.EachRecord(func(r *kgo.Record) {
            var s Signal
            if err := json.Unmarshal(r.Value, &s); err != nil {
                return
            }

            // Simple playbook: LISTING -> LONG small size
            if s.EventType == "LISTING" {
                symbol := mapToPerp(s.Entities) // e.g. "XYZUSDT"
                qty := sizeBySeverity(s.Severity)
                ctx, cancel := context.WithTimeout(context.Background(), 300*time.Millisecond)
                defer cancel()
                if err := placeIOC(symbol, "BUY", qty); err != nil {
                    // log + metrics + maybe retry with smaller qty
                }
            }
            // commit offset (franz-go will auto-commit if configured)
        })
    }
}
```

---

## 6. Backtest & Paper-Trading

### Approach
Use ClickHouse to store signals + mid/bbo + executions → recreate fills using simple matching model (IOC/FOK).

### Metrics
- **Fill ratio**
- **Arrival slippage vs mid**
- **p50/p95 time-to-fill**
- **PnL per event type**
- **PnL decay over time (alpha half-life)**

---

## 7. Risk Management & Safety

### Security
- **API Keys:** Minimal permissions; separate keys per exchange; withdraw limit = 0
- **Kill-switch:** If reject rate > 20% in 1 minute or latency > 500ms in 3 minutes → shutdown
- **Content guardrails:** Only news from allowlist sources get severity > 0.7
- **Failsafe:** When news is ambiguous (UNKNOWN) → paper only or size ≤ 0.1% equity

---

## 8. Implementation Roadmap

### Phase 1: MVP (Minimum Viable Product) - 4-6 weeks

#### 🎯 Objectives
- Validate core concept with basic news-to-trade pipeline
- Achieve < 500ms end-to-end latency
- Paper trading only

#### 📋 Technical Stack
- **Backend:** Python 3.11+ (FastAPI, asyncio)
- **Queue:** Apache Kafka (single broker)
- **Database:** 
  - **PostgreSQL/MySQL** (trades, orders, user accounts)
  - **MongoDB** (signals, events, configurations)
  - **Redis** (cache, hot state)
- **Infrastructure:** Docker + Docker Compose
- **Monitoring:** Basic logging + Prometheus

#### 🔧 Implementation Checklist

##### Data Collection (Week 1-2)
- [ ] **RSS Collector**
  - [ ] Binance Announcements RSS feed
  - [ ] Coinbase Blog RSS feed
  - [ ] Basic deduplication (SHA-256 hash)
  - [ ] Rate limiting (max 1 request/5s per source)
  - [ ] Error handling & retry logic

- [ ] **Signal Normalizer**
  - [ ] JSON schema validation
  - [ ] Timezone conversion (UTC)
  - [ ] Basic entity extraction (regex for tickers)
  - [ ] Event type classification (LISTING/DELIST only)

- [ ] **Kafka Producer**
  - [ ] Topic: `news.signals.v1`
  - [ ] Idempotent producer
  - [ ] Message serialization (JSON)

##### Execution Engine (Week 2-3)
- [ ] **Kafka Consumer**
  - [ ] Consumer group: `exec-mvp-1`
  - [ ] Manual offset management
  - [ ] Error handling & dead letter queue

- [ ] **Strategy Engine**
  - [ ] Single playbook: LISTING → LONG
  - [ ] Position sizing: fixed 0.1% account size
  - [ ] Basic risk checks (max 1 position per symbol)

- [ ] **Exchange Integration**
  - [ ] Bybit API (REST only)
  - [ ] Paper trading mode
  - [ ] Basic order placement (MARKET orders)
  - [ ] HMAC signature generation

##### Infrastructure (Week 3-4)
- [ ] **Docker Setup**
  - [ ] Multi-stage builds
  - [ ] Environment variables
  - [ ] Health checks
  - [ ] Logging configuration

- [ ] **Database Schema**
  - [ ] **PostgreSQL/MySQL Tables:**
    - [ ] `trades` (order_id, symbol, side, qty, price, status, ts)
    - [ ] `orders` (order_id, user_id, exchange, order_type, status)
    - [ ] `users` (user_id, api_keys, risk_limits, balance)
    - [ ] `audit_logs` (action, user_id, details, ts)
  - [ ] **MongoDB Collections:**
    - [ ] `signals` (event_id, ts, source, event_type, entities, raw_text)
    - [ ] `events` (processed signals with NLP results)
    - [ ] `configurations` (playbooks, risk_rules, exchange_settings)
  - [ ] **Redis Keys:**
    - [ ] `positions:{user_id}` (current positions)
    - [ ] `cooldowns:{symbol}` (trading cooldowns)
    - [ ] `cache:signals:{event_id}` (signal cache)

- [ ] **Basic Monitoring**
  - [ ] Prometheus metrics (latency, throughput)
  - [ ] Grafana dashboard
  - [ ] Slack alerts for errors

#### 📊 Success Metrics
- End-to-end latency < 500ms (p95)
- Signal processing rate > 100 events/minute
- Zero data loss (at-least-once delivery)
- 99% uptime

---

### Phase 2: Enhanced Features (V2) - 6-8 weeks

#### 🎯 Objectives
- Add multiple event types (Fed, SEC, Hacks)
- Multi-exchange support
- Real money trading (small amounts)
- Advanced risk management

#### 📋 Technical Stack
- **Backend:** Python 3.11+ + Go 1.21+ (for execution)
- **Queue:** Apache Kafka (3 brokers, replication)
- **Database:** 
  - **PostgreSQL/MySQL** (trades, orders, user accounts, audit logs)
  - **MongoDB** (signals, events, NLP results, configurations)
  - **ClickHouse** (time-series data, market data, analytics)
  - **Redis** (cache, hot state, session data)
- **Infrastructure:** Kubernetes (minikube for dev)
- **ML:** spaCy + transformers (sentiment analysis)

#### 🔧 Implementation Checklist

##### Enhanced Data Collection (Week 1-3)
- [ ] **Additional Sources**
  - [ ] Twitter/X API (filtered stream)
  - [ ] Fed Calendar API
  - [ ] SEC EDGAR filings
  - [ ] Etherscan alerts (major hacks)

- [ ] **NLP Pipeline**
  - [ ] spaCy NER for entity extraction
  - [ ] DistilBERT sentiment analysis
  - [ ] Custom event classifier
  - [ ] Confidence scoring

- [ ] **Advanced Deduplication**
  - [ ] Semantic similarity (cosine similarity)
  - [ ] Time-window correlation (120s)
  - [ ] Source credibility weighting

##### Multi-Exchange Execution (Week 3-5)
- [ ] **Exchange Connectors**
  - [ ] Binance (REST + WebSocket)
  - [ ] OKX (REST + WebSocket)
  - [ ] Bybit (existing)
  - [ ] Abstract exchange interface

- [ ] **Order Router**
  - [ ] Best execution logic
  - [ ] Smart order routing
  - [ ] Partial fills handling
  - [ ] Order state management

- [ ] **Risk Engine**
  - [ ] Position limits per symbol
  - [ ] Daily loss limits
  - [ ] Volatility-based sizing
  - [ ] Circuit breakers

##### Advanced Strategies (Week 5-7)
- [ ] **Event-Specific Playbooks**
  - [ ] FED_SPEECH → Market direction
  - [ ] HACK → Sector short
  - [ ] SEC_ACTION → Regulatory impact
  - [ ] POLITICAL_POST → Sentiment-based

- [ ] **Basket Trading**
  - [ ] Sector ETFs (DeFi, Layer1, etc.)
  - [ ] Beta-neutral strategies
  - [ ] Correlation-based hedging

- [ ] **Dynamic Sizing**
  - [ ] Severity-based position sizing
  - [ ] Liquidity-adjusted quantities
  - [ ] Market impact modeling

##### Infrastructure Upgrade (Week 7-8)
- [ ] **Kubernetes Deployment**
  - [ ] Helm charts
  - [ ] Horizontal pod autoscaling
  - [ ] Resource limits & requests
  - [ ] Rolling updates

- [ ] **Advanced Monitoring**
  - [ ] Distributed tracing (Jaeger)
  - [ ] Custom metrics (PnL, Sharpe ratio)
  - [ ] Alerting rules
  - [ ] Performance dashboards

- [ ] **Data Pipeline**
  - [ ] ClickHouse for time-series
  - [ ] Data retention policies
  - [ ] Backup strategies

#### 📊 Success Metrics
- End-to-end latency < 200ms (p95)
- Support 5+ event types
- Multi-exchange execution
- Real-time PnL tracking
- 99.9% uptime

---

### Phase 3: Production Scale (V3) - 8-10 weeks

#### 🎯 Objectives
- Machine learning optimization
- Cross-venue arbitrage
- Institutional-grade infrastructure
- Advanced analytics

#### 📋 Technical Stack
- **Backend:** Go (execution) + Python (ML)
- **Queue:** Apache Kafka (multi-DC)
- **Database:** 
  - **PostgreSQL/MySQL** (trades, orders, user accounts, audit logs)
  - **MongoDB** (signals, events, ML features, configurations)
  - **ClickHouse** (time-series data, market data, analytics)
  - **TimescaleDB** (hybrid time-series for complex queries)
  - **Redis** (cache, hot state, session data)
- **Infrastructure:** Kubernetes (production)
- **ML:** Ray + MLflow + Feature Store

#### 🔧 Implementation Checklist

##### Machine Learning Pipeline (Week 1-4)
- [ ] **Feature Engineering**
  - [ ] Market microstructure features
  - [ ] News sentiment features
  - [ ] Technical indicators
  - [ ] Cross-asset correlations

- [ ] **Model Training**
  - [ ] Online learning framework
  - [ ] A/B testing infrastructure
  - [ ] Model versioning (MLflow)
  - [ ] Automated retraining

- [ ] **Prediction Service**
  - [ ] Real-time inference
  - [ ] Model serving (Ray Serve)
  - [ ] Prediction caching
  - [ ] Confidence intervals

##### Advanced Execution (Week 4-6)
- [ ] **Latency Arbitrage**
  - [ ] Cross-exchange price differences
  - [ ] Smart order routing
  - [ ] Market making strategies
  - [ ] Statistical arbitrage

- [ ] **Position Management**
  - [ ] Cross-venue position netting
  - [ ] Dynamic hedging
  - [ ] Portfolio optimization
  - [ ] Risk decomposition

- [ ] **Order Types**
  - [ ] Iceberg orders
  - [ ] TWAP/VWAP algorithms
  - [ ] Smart order types
  - [ ] Dark pool integration

##### Production Infrastructure (Week 6-8)
- [ ] **Multi-Datacenter**
  - [ ] Active-active setup
  - [ ] Data replication
  - [ ] Disaster recovery
  - [ ] Geographic distribution

- [ ] **Security & Compliance**
  - [ ] SOC 2 compliance
  - [ ] Penetration testing
  - [ ] Audit logging
  - [ ] Key rotation

- [ ] **Performance Optimization**
  - [ ] Kernel bypass (DPDK)
  - [ ] FPGA acceleration
  - [ ] Custom protocols
  - [ ] Hardware optimization

##### Analytics & Reporting (Week 8-10)
- [ ] **Advanced Analytics**
  - [ ] Real-time dashboards
  - [ ] Performance attribution
  - [ ] Risk analytics
  - [ ] Regulatory reporting

- [ ] **Backtesting Framework**
  - [ ] Historical simulation
  - [ ] Monte Carlo analysis
  - [ ] Strategy comparison
  - [ ] Performance metrics

- [ ] **Business Intelligence**
  - [ ] Data warehouse
  - [ ] ETL pipelines
  - [ ] Custom reports
  - [ ] API for external access

#### 📊 Success Metrics
- End-to-end latency < 50ms (p95)
- Support 20+ event types
- Cross-venue execution
- ML-driven optimization
- 99.99% uptime
- Positive Sharpe ratio > 2.0

---

### Technology Stack Summary

| Component | Phase 1 | Phase 2 | Phase 3 |
|-----------|---------|---------|---------|
| **Backend** | Python | Python + Go | Go + Python (ML) |
| **Queue** | Kafka (single) | Kafka (clustered) | Kafka (multi-DC) |
| **Database** | PostgreSQL/MySQL + MongoDB + Redis | PostgreSQL/MySQL + MongoDB + ClickHouse + Redis | PostgreSQL/MySQL + MongoDB + ClickHouse + TimescaleDB + Redis |
| **Infrastructure** | Docker Compose | Kubernetes (dev) | Kubernetes (prod) |
| **ML/AI** | Rule-based | spaCy + BERT | Ray + MLflow |
| **Monitoring** | Basic Prometheus | Jaeger + Grafana | Full observability stack |
| **Security** | Basic auth | API keys + TLS | SOC 2 + compliance |

---

## 9. Data Storage Strategy

### Database Selection Rationale

#### 🗄️ **PostgreSQL/MySQL** - Transactional Data
**Lưu trữ:**
- **Trades & Orders:** Cần ACID compliance, foreign keys, complex joins
- **User Accounts:** Authentication, authorization, balance tracking
- **Audit Logs:** Compliance, regulatory reporting
- **Risk Limits:** Real-time validation, constraints

**PostgreSQL Ưu điểm:**
- ACID transactions (critical for financial data)
- Complex queries với JOINs
- Mature ecosystem, proven reliability
- Built-in constraints và validation
- Better JSON support
- Advanced indexing (GIN, GiST)
- Better concurrency handling

**MySQL Ưu điểm:**
- Simpler setup và administration
- Better performance cho read-heavy workloads
- More familiar cho nhiều developers
- Easier replication setup
- Lower resource usage

**Schema Example (PostgreSQL):**
```sql
-- Trades table
CREATE TABLE trades (
    trade_id UUID PRIMARY KEY,
    order_id UUID REFERENCES orders(order_id),
    symbol VARCHAR(20) NOT NULL,
    side CHAR(1) CHECK (side IN ('B', 'S')),
    quantity DECIMAL(20,8) NOT NULL,
    price DECIMAL(20,8) NOT NULL,
    executed_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    exchange VARCHAR(50) NOT NULL,
    fees DECIMAL(20,8) DEFAULT 0
);

-- Users table
CREATE TABLE users (
    user_id UUID PRIMARY KEY,
    api_key_hash VARCHAR(255) UNIQUE,
    daily_loss_limit DECIMAL(10,2),
    max_position_size DECIMAL(10,2),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

**Schema Example (MySQL):**
```sql
-- Trades table
CREATE TABLE trades (
    trade_id CHAR(36) PRIMARY KEY,
    order_id CHAR(36),
    symbol VARCHAR(20) NOT NULL,
    side ENUM('B', 'S') NOT NULL,
    quantity DECIMAL(20,8) NOT NULL,
    price DECIMAL(20,8) NOT NULL,
    executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    exchange VARCHAR(50) NOT NULL,
    fees DECIMAL(20,8) DEFAULT 0,
    FOREIGN KEY (order_id) REFERENCES orders(order_id)
);

-- Users table
CREATE TABLE users (
    user_id CHAR(36) PRIMARY KEY,
    api_key_hash VARCHAR(255) UNIQUE,
    daily_loss_limit DECIMAL(10,2),
    max_position_size DECIMAL(10,2),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

#### 📄 **MongoDB** - Document Storage
**Lưu trữ:**
- **Signals & Events:** Flexible schema, nested data, NLP results
- **Configurations:** Playbooks, risk rules, exchange settings
- **Raw News Data:** Unstructured content, metadata
- **ML Features:** Flexible feature storage, versioning

**Ưu điểm:**
- Schema flexibility (news data structure thay đổi)
- JSON native (perfect cho API responses)
- Horizontal scaling
- Rich querying với aggregation pipeline

**Schema Example:**
```javascript
// Signals collection
{
  "_id": ObjectId("..."),
  "event_id": "uuid-string",
  "ts_iso": "2025-01-15T10:30:00Z",
  "source": "binance.announcements",
  "headline": "Binance Will List XYZ",
  "event_type": "LISTING",
  "entities": ["XYZ", "Binance"],
  "nlp_results": {
    "sentiment": 0.85,
    "confidence": 0.92,
    "extracted_tickers": ["XYZ"],
    "entities": [
      {"text": "XYZ", "type": "CRYPTO", "confidence": 0.95}
    ]
  },
  "raw_text": "Full news content...",
  "metadata": {
    "source_credibility": 0.98,
    "processing_time_ms": 45
  }
}

// Configurations collection
{
  "_id": ObjectId("..."),
  "playbook_id": "listing_bull_v1",
  "event_type": "LISTING",
  "rules": [
    {
      "condition": "source IN ['binance', 'coinbase']",
      "action": "LONG",
      "position_size": "0.1%",
      "take_profit": "20bps"
    }
  ],
  "active": true,
  "version": "1.0.0"
}
```

#### ⚡ **Redis** - Cache & Hot State
**Lưu trữ:**
- **Current Positions:** Real-time position tracking
- **Trading Cooldowns:** Rate limiting, circuit breakers
- **Signal Cache:** Deduplication, recent events
- **Session Data:** User sessions, API rate limits

**Ưu điểm:**
- Sub-millisecond latency
- In-memory performance
- Rich data structures (hashes, sets, sorted sets)
- TTL support

**Usage Examples:**
```python
# Position tracking
redis.hset(f"position:{user_id}", "BTCUSDT", "0.5")

# Cooldown management
redis.setex(f"cooldown:{symbol}:{user_id}", 300, "1")  # 5 min cooldown

# Signal cache
redis.setex(f"signal:{event_id}", 3600, json.dumps(signal_data))
```

#### 📊 **ClickHouse** - Time-Series Analytics
**Lưu trữ:**
- **Market Data:** OHLCV, order book snapshots
- **Performance Metrics:** Latency, throughput, PnL
- **Historical Analysis:** Backtesting data, strategy performance
- **Real-time Analytics:** Aggregations, rollups

**Ưu điểm:**
- Columnar storage (perfect cho analytics)
- High compression ratio
- Fast aggregations
- Real-time inserts

### Data Flow Architecture

```
News Sources → Kafka → Signal Processor → MongoDB (signals)
                                    ↓
                              NLP Pipeline → MongoDB (events)
                                    ↓
                              Strategy Engine → PostgreSQL (trades)
                                    ↓
                              Analytics → ClickHouse (metrics)
                                    ↓
                              Cache Layer → Redis (hot data)
```

### PostgreSQL vs MySQL Comparison

#### 🔍 **Detailed Comparison**

| Feature | PostgreSQL | MySQL |
|---------|------------|-------|
| **Performance** | Better for complex queries, write-heavy | Better for read-heavy, simple queries |
| **JSON Support** | Native JSONB with indexing | JSON type (limited indexing) |
| **Concurrency** | MVCC, better for high concurrency | Row-level locking, simpler |
| **Data Types** | Rich types (UUID, arrays, custom) | Standard types, ENUMs |
| **Indexing** | GIN, GiST, BRIN indexes | B-tree, hash indexes |
| **Replication** | Logical + physical replication | Master-slave, group replication |
| **Setup** | More complex, more powerful | Simpler, easier to manage |
| **Community** | Strong open-source community | Large ecosystem, Oracle backing |

#### 💰 **Cost Impact**
- **Development Time:** MySQL faster setup, PostgreSQL more powerful
- **Hardware:** MySQL lower resource usage
- **Licensing:** Both open-source, no licensing costs
- **Maintenance:** MySQL simpler administration

#### 🚀 **Performance Impact for News Trading**

**PostgreSQL Advantages:**
- Better handling of complex JOINs (user positions + trade history)
- JSONB for storing flexible signal metadata
- Better concurrency for high-frequency trading
- Advanced indexing for time-series queries

**MySQL Advantages:**
- Faster for simple CRUD operations
- Lower memory footprint
- Easier replication setup
- Better for read-heavy analytics

#### 🔄 **Migration Considerations**

**From PostgreSQL to MySQL:**
```sql
-- PostgreSQL UUID to MySQL CHAR(36)
ALTER TABLE trades ALTER COLUMN trade_id TYPE CHAR(36);

-- PostgreSQL JSONB to MySQL JSON
ALTER TABLE signals ALTER COLUMN metadata TYPE JSON;

-- PostgreSQL TIMESTAMP WITH TIME ZONE to MySQL TIMESTAMP
ALTER TABLE trades ALTER COLUMN executed_at TYPE TIMESTAMP;
```

**Code Changes:**
```python
# PostgreSQL
import psycopg2
conn = psycopg2.connect("postgresql://user:pass@localhost/db")

# MySQL  
import mysql.connector
conn = mysql.connector.connect(host="localhost", user="user", password="pass", database="db")
```

### Migration Strategy

#### Phase 1: Start Simple
- **PostgreSQL/MySQL:** Core transactional data
- **MongoDB:** News signals (flexible schema)
- **Redis:** Basic caching

#### Phase 2: Add Analytics
- **ClickHouse:** Time-series data
- **MongoDB:** Expand to configurations, ML features

#### Phase 3: Scale & Optimize
- **TimescaleDB:** Hybrid time-series for complex queries
- **Redis Cluster:** Distributed caching
- **MongoDB Sharding:** Horizontal scaling

### Backup & Recovery

#### PostgreSQL/MySQL
- **PostgreSQL Backup:** pg_dump + WAL archiving
- **PostgreSQL Recovery:** Point-in-time recovery
- **PostgreSQL Replication:** Master-slave setup
- **MySQL Backup:** mysqldump + binary logs
- **MySQL Recovery:** Point-in-time recovery
- **MySQL Replication:** Master-slave, group replication

#### MongoDB
- **Backup:** mongodump + oplog
- **Recovery:** mongorestore
- **Replication:** Replica set

#### Redis
- **Backup:** RDB snapshots + AOF
- **Recovery:** Restore from snapshot
- **Replication:** Master-slave

#### ClickHouse
- **Backup:** ClickHouse backup tool
- **Recovery:** Restore from backup
- **Replication:** Multi-copy replication

### Risk Mitigation

#### Technical Risks
- **Latency spikes:** Circuit breakers, graceful degradation
- **Data loss:** Idempotent producers, dead letter queues
- **Exchange downtime:** Multi-exchange redundancy
- **Model drift:** Continuous monitoring, automated retraining

#### Business Risks
- **Regulatory changes:** Modular architecture, compliance framework
- **Market volatility:** Dynamic risk limits, position sizing
- **Competition:** Continuous innovation, patent protection
- **Operational risks:** 24/7 monitoring, incident response