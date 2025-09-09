package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"os"
	"strings"
	"time"

	"github.com/Shopify/sarama"
	"github.com/go-redis/redis/v8"
)

// Signal represents a trading signal from Kafka
type Signal struct {
	EventID       string    `json:"event_id"`
	TsISO         string    `json:"ts_iso"`
	Source        string    `json:"source"`
	Headline      string    `json:"headline"`
	URL           string    `json:"url"`
	EventType     string    `json:"event_type"`
	PrimaryEntity string    `json:"primary_entity"`
	Entities      []string  `json:"entities"`
	Severity      float64   `json:"severity"`
	Direction     string    `json:"direction"`
	Confidence    float64   `json:"confidence"`
	RawText       string    `json:"raw_text"`
}

// Order represents a trading order (in-memory only)
type Order struct {
	OrderID         string
	UserID          string
	Symbol          string
	Side            string
	OrderType       string
	Quantity        float64
	Status          string
	Exchange        string
	ExchangeOrderID string
	CreatedAt       time.Time
}

// Trade represents an executed trade (in-memory only)
type Trade struct {
	TradeID         string
	OrderID         string
	Symbol          string
	Side            string
	Quantity        float64
	Price           float64
	Fees            float64
	Exchange        string
	CreatedAt       time.Time
}

// Playbook represents a trading strategy
type Playbook struct {
	Action           string
	TakeProfitPct    float64
	StopLossPct      float64
	TimeoutSeconds   int
}

// ExecutionEngine represents the main trading engine
type ExecutionEngine struct {
	redis        *redis.Client
	consumer     sarama.Consumer
	playbooks    map[string]Playbook
	defaultUser  string
	positionSize float64
	maxSlippage  float64
}

// NewExecutionEngine creates a new execution engine
func NewExecutionEngine() (*ExecutionEngine, error) {

	// Redis connection
	redisURL := os.Getenv("REDIS_URL")
	if redisURL == "" {
		redisURL = "redis://localhost:6379"
	}
	
	opt, err := redis.ParseURL(redisURL)
	if err != nil {
		return nil, fmt.Errorf("failed to parse Redis URL: %v", err)
	}
	
	redis := redis.NewClient(opt)

	// Kafka consumer
	kafkaServers := os.Getenv("KAFKA_BOOTSTRAP_SERVERS")
	if kafkaServers == "" {
		kafkaServers = "localhost:9092"
	}
	
	config := sarama.NewConfig()
	config.Consumer.Return.Errors = true
	config.Consumer.Offsets.Initial = sarama.OffsetNewest
	
	consumer, err := sarama.NewConsumer(strings.Split(kafkaServers, ","), config)
	if err != nil {
		return nil, fmt.Errorf("failed to create Kafka consumer: %v", err)
	}

	// Playbooks configuration
	playbooks := map[string]Playbook{
		"LISTING": {
			Action:         "BUY",
			TakeProfitPct:  0.02,
			StopLossPct:    0.01,
			TimeoutSeconds: 90,
		},
		"DELIST": {
			Action:         "SELL",
			TakeProfitPct:  0.01,
			StopLossPct:    0.02,
			TimeoutSeconds: 60,
		},
		"HACK": {
			Action:         "SELL",
			TakeProfitPct:  0.015,
			StopLossPct:    0.025,
			TimeoutSeconds: 120,
		},
		"REGULATION": {
			Action:         "SELL",
			TakeProfitPct:  0.01,
			StopLossPct:    0.03,
			TimeoutSeconds: 180,
		},
		"FED_SPEECH": {
			Action:         "BOTH",
			TakeProfitPct:  0.015,
			StopLossPct:    0.02,
			TimeoutSeconds: 300,
		},
	}

	log.Println("Go Execution Engine initialized (Simulation Mode - Ready for Real Trading)")

	return &ExecutionEngine{
		redis:        redis,
		consumer:     consumer,
		playbooks:    playbooks,
		defaultUser:  "test_user",
		positionSize: 10.0,
		maxSlippage:  0.001,
	}, nil
}

// mapToSymbol maps entities to trading symbols - supports ANY token
func (e *ExecutionEngine) mapToSymbol(entities []string) string {
	if len(entities) == 0 {
		return ""
	}

	// Enhanced symbol mapping for major tokens
	symbolMapping := map[string]string{
		"BTC": "BTCUSDT", "ETH": "ETHUSDT", "BNB": "BNBUSDT", "ADA": "ADAUSDT",
		"SOL": "SOLUSDT", "DOT": "DOTUSDT", "LINK": "LINKUSDT", "MATIC": "MATICUSDT",
		"AVAX": "AVAXUSDT", "UNI": "UNIUSDT", "DOGE": "DOGEUSDT", "SHIB": "SHIBUSDT",
		"XRP": "XRPUSDT", "LTC": "LTCUSDT", "BCH": "BCHUSDT", "ETC": "ETCUSDT",
		"ATOM": "ATOMUSDT", "NEAR": "NEARUSDT", "FTM": "FTMUSDT", "ALGO": "ALGOUSDT",
		"VET": "VETUSDT", "ICP": "ICPUSDT", "FIL": "FILUSDT", "TRX": "TRXUSDT",
		"EOS": "EOSUSDT", "XLM": "XLMUSDT", "AAVE": "AAVEUSDT", "SUSHI": "SUSHIUSDT",
		"COMP": "COMPUSDT", "MKR": "MKRUSDT", "SNX": "SNXUSDT", "YFI": "YFIUSDT",
		"CRV": "CRVUSDT", "1INCH": "1INCHUSDT", "BAL": "BALUSDT", "ZRX": "ZRXUSDT",
	}

	// Try to find the first valid crypto token
	for _, entity := range entities {
		entity = strings.ToUpper(strings.TrimSpace(entity))
		
		// Skip non-crypto entities
		if e.isExchangeName(entity) {
			continue
		}
		
		// Check if it's a known token
		if symbol, exists := symbolMapping[entity]; exists {
			return symbol
		}
		
		// For unknown tokens, create USDT pair (support 2-10 chars)
		if len(entity) >= 2 && len(entity) <= 10 {
			return entity + "USDT"
		}
	}
	
	// Fallback to first entity
	if len(entities) > 0 {
		entity := strings.ToUpper(strings.TrimSpace(entities[0]))
		if len(entity) >= 2 && len(entity) <= 10 {
			return entity + "USDT"
		}
	}
	
	return ""
}

// isExchangeName checks if entity is an exchange name
func (e *ExecutionEngine) isExchangeName(entity string) bool {
	exchanges := []string{
		"BINANCE", "COINBASE", "FTX", "KRAKEN", "GEMINI", "BITHUMB", "UPBIT", "KORBIT",
		"OKX", "KUCOIN", "HUOBI", "GATE", "BYBIT", "BITFINEX", "BITSTAMP", "POLONIEX",
	}
	
	for _, exchange := range exchanges {
		if strings.Contains(entity, exchange) {
			return true
		}
	}
	return false
}

// calculatePositionSize calculates position size based on signal strength
func (e *ExecutionEngine) calculatePositionSize(severity, confidence float64) float64 {
	adjustedSize := e.positionSize * severity * confidence
	if adjustedSize > 100.0 {
		return 100.0
	}
	return adjustedSize
}

// determineFedAction determines BUY/SELL action for FED_SPEECH
func (e *ExecutionEngine) determineFedAction(signal *Signal) string {
	headline := strings.ToLower(signal.Headline)
	
	// Check direction first
	if signal.Direction == "BULL" {
		return "BUY"
	} else if signal.Direction == "BEAR" {
		return "SELL"
	}
	
	// Check keywords
	bullishKeywords := []string{"rate cut", "dovish", "stimulus", "easing", "accommodative"}
	bearishKeywords := []string{"rate hike", "hawkish", "tightening", "inflation", "restrictive"}
	
	for _, keyword := range bullishKeywords {
		if strings.Contains(headline, keyword) {
			return "BUY"
		}
	}
	
	for _, keyword := range bearishKeywords {
		if strings.Contains(headline, keyword) {
			return "SELL"
		}
	}
	
	// Default to SELL for FED_SPEECH
	return "SELL"
}

// checkRiskLimits checks risk limits before executing trade
func (e *ExecutionEngine) checkRiskLimits(userID, symbol, side string, quantity float64) bool {
	ctx := context.Background()
	
	// Check cooldown
	cooldownKey := fmt.Sprintf("cooldown:%s:%s", symbol, userID)
	exists, err := e.redis.Exists(ctx, cooldownKey).Result()
	if err != nil {
		log.Printf("Error checking cooldown: %v", err)
		return false
	}
	if exists > 0 {
		log.Printf("Position cooldown active for %s", symbol)
		return false
	}
	
	return true
}

// simulateExecution simulates order execution
func (e *ExecutionEngine) simulateExecution(order *Order) error {
	time.Sleep(100 * time.Millisecond)
	
	// Simulate execution price
	executionPrice := 50000.0
	if order.Side == "BUY" {
		executionPrice *= 1.0001
	} else {
		executionPrice *= 0.9999
	}
	
	// Update order status
	order.Status = "FILLED"
	order.ExchangeOrderID = "simulated_order_id"
	
	// Log execution (no database save)
	log.Printf("Go Engine - Simulated execution: %s %.2f %s @ %.2f (order: %s)", 
		order.Side, order.Quantity, order.Symbol, executionPrice, order.OrderID)
	
	return nil
}

// executePlaybook executes trading playbook
func (e *ExecutionEngine) executePlaybook(signal *Signal) error {
	playbook, exists := e.playbooks[signal.EventType]
	if !exists {
		log.Printf("No playbook for event type: %s", signal.EventType)
		return nil
	}
	
	// Map to trading symbol
	symbol := e.mapToSymbol(signal.Entities)
	if symbol == "" {
		log.Printf("Could not map entities to symbol: %v", signal.Entities)
		return nil
	}
	
	// Calculate position size
	quantity := e.calculatePositionSize(signal.Severity, signal.Confidence)
	
	// Determine action
	action := playbook.Action
	if signal.EventType == "FED_SPEECH" && action == "BOTH" {
		action = e.determineFedAction(signal)
		if action == "" {
			log.Printf("Could not determine action for FED_SPEECH: %s", signal.Headline)
			return nil
		}
	}
	
	// Check risk limits
	if !e.checkRiskLimits(e.defaultUser, symbol, action, quantity) {
		return nil
	}
	
	// Create order (in-memory only)
	order := &Order{
		OrderID:   fmt.Sprintf("order_%s", signal.EventID),
		UserID:    e.defaultUser,
		Symbol:    symbol,
		Side:      action,
		OrderType: "MARKET",
		Quantity:  quantity,
		Status:    "PENDING",
		Exchange:  "bybit",
		CreatedAt: time.Now(),
	}
	
	// Execute order (simulation mode for now)
	err := e.simulateExecution(order)
	if err != nil {
		log.Printf("Failed to execute order: %v", err)
		order.Status = "REJECTED"
		return err
	}
	
	// Set cooldown
	ctx := context.Background()
	cooldownKey := fmt.Sprintf("cooldown:%s:%s", symbol, e.defaultUser)
	e.redis.SetEX(ctx, cooldownKey, "1", 5*time.Minute)
	
	log.Printf("Go Engine - Successfully executed %s %.2f %s", action, quantity, symbol)
	return nil
}

// processSignals processes signals from Kafka
func (e *ExecutionEngine) processSignals() error {
	partitionConsumer, err := e.consumer.ConsumePartition("news.signals.v1", 0, sarama.OffsetNewest)
	if err != nil {
		return fmt.Errorf("failed to create partition consumer: %v", err)
	}
	defer partitionConsumer.Close()
	
	log.Println("Go Execution Engine - Starting signal processing")
	
	for {
		select {
		case message := <-partitionConsumer.Messages():
			var signal Signal
			if err := json.Unmarshal(message.Value, &signal); err != nil {
				log.Printf("Error unmarshaling signal: %v", err)
				continue
			}
			
			// Filter by event type (only process token-specific events)
			if signal.EventType != "LISTING" && signal.EventType != "DELIST" && signal.EventType != "HACK" {
				log.Printf("Skipped non-token-specific event: %s (severity: %.2f)", signal.EventType, signal.Severity)
				continue
			}
			
			// Filter by severity (only process VERY HIGH severity signals)
			if signal.Severity < 0.8 {
				log.Printf("Skipped low impact signal: %s (severity: %.2f)", signal.EventType, signal.Severity)
				continue
			}
			
			log.Printf("Go Engine - Received signal: %s - %s (severity: %.2f)", 
				signal.EventType, signal.Headline, signal.Severity)
			
			if err := e.executePlaybook(&signal); err != nil {
				log.Printf("Error executing playbook: %v", err)
			}
			
		case err := <-partitionConsumer.Errors():
			log.Printf("Kafka error: %v", err)
		}
	}
}

func main() {
	log.Println("Starting Execution Engine Service (Go)")
	
	engine, err := NewExecutionEngine()
	if err != nil {
		log.Fatalf("Failed to create execution engine: %v", err)
	}
	defer engine.consumer.Close()
	
	if err := engine.processSignals(); err != nil {
		log.Fatalf("Failed to process signals: %v", err)
	}
}
