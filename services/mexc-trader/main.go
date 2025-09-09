package main

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"strconv"
	"strings"
	"time"
)

type MEXCClient struct {
	APIKey    string
	SecretKey string
	BaseURL   string
}

type TradingSignal struct {
	EventType   string
	Tokens      []string
	Title       string
	URL         string
	PublishedAt time.Time
	Action      string // BUY, SELL, HOLD
	Confidence  float64
}

type MEXCResponse struct {
	Code int         `json:"code"`
	Data interface{} `json:"data"`
	Msg  string      `json:"msg"`
}

type AccountInfo struct {
	Assets []Asset `json:"assets"`
}

type Asset struct {
	Asset     string `json:"asset"`
	Free      string `json:"free"`
	Locked    string `json:"locked"`
	Frozen    string `json:"frozen"`
	Converting string `json:"converting"`
}

type OrderResponse struct {
	OrderID string `json:"orderId"`
	Symbol  string `json:"symbol"`
	Status  string `json:"status"`
}

type MEXCTrader struct {
	Client       *MEXCClient
	LastCheck    time.Time
	Processed    map[string]bool
	MarketCaps   map[string]string
	BaseCurrency string
	TradeAmount  float64
}

func NewMEXCClient(apiKey, secretKey string) *MEXCClient {
	return &MEXCClient{
		APIKey:    apiKey,
		SecretKey: secretKey,
		BaseURL:   "https://api.mexc.com",
	}
}

func NewMEXCTrader(apiKey, secretKey string) *MEXCTrader {
	return &MEXCTrader{
		Client:       NewMEXCClient(apiKey, secretKey),
		LastCheck:    time.Now().Add(-1 * time.Hour),
		Processed:    make(map[string]bool),
		BaseCurrency: "USDT",
		TradeAmount:  10.0, // $10 per trade
		MarketCaps: map[string]string{
			"KTA":   "$376M",
			"NOICE": "$7M",
			"BTC":   "$1.2T",
			"ETH":   "$400B",
			"SOL":   "$50B",
			"DOGE":  "$25B",
			"ADA":   "$15B",
			"MATIC": "$8B",
			"AVAX":  "$12B",
			"DOT":   "$6B",
			"OPEN":  "$100M",
			"RED":   "$133M",
			"OMNI":  "$50M",
			"HYPE":  "$200M",
		},
	}
}

func (c *MEXCClient) generateSignature(params string) string {
	h := hmac.New(sha256.New, []byte(c.SecretKey))
	h.Write([]byte(params))
	return hex.EncodeToString(h.Sum(nil))
}

func (c *MEXCClient) makeRequest(method, endpoint string, params map[string]string) ([]byte, error) {
	url := c.BaseURL + endpoint
	
	// Add API key to params
	if params == nil {
		params = make(map[string]string)
	}
	params["api_key"] = c.APIKey
	params["timestamp"] = strconv.FormatInt(time.Now().UnixMilli(), 10)
	
	// Create query string
	var queryParts []string
	for k, v := range params {
		queryParts = append(queryParts, fmt.Sprintf("%s=%s", k, v))
	}
	queryString := strings.Join(queryParts, "&")
	
	// Generate signature
	signature := c.generateSignature(queryString)
	queryString += "&signature=" + signature
	
	// Make request
	var req *http.Request
	var err error
	
	if method == "GET" {
		req, err = http.NewRequest("GET", url+"?"+queryString, nil)
	} else {
		req, err = http.NewRequest(method, url, strings.NewReader(queryString))
		req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	}
	
	if err != nil {
		return nil, err
	}
	
	client := &http.Client{Timeout: 10 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	
	return io.ReadAll(resp.Body)
}

func (c *MEXCClient) GetAccountInfo() (*AccountInfo, error) {
	data, err := c.makeRequest("GET", "/api/v3/account", nil)
	if err != nil {
		return nil, err
	}
	
	var response MEXCResponse
	if err := json.Unmarshal(data, &response); err != nil {
		return nil, err
	}
	
	if response.Code != 0 {
		return nil, fmt.Errorf("API error: %s", response.Msg)
	}
	
	accountData, _ := json.Marshal(response.Data)
	var account AccountInfo
	if err := json.Unmarshal(accountData, &account); err != nil {
		return nil, err
	}
	
	return &account, nil
}

func (c *MEXCClient) PlaceOrder(symbol, side, orderType, quantity, price string) (*OrderResponse, error) {
	params := map[string]string{
		"symbol":   symbol,
		"side":     side,
		"type":     orderType,
		"quantity": quantity,
		"price":    price,
	}
	
	data, err := c.makeRequest("POST", "/api/v3/order", params)
	if err != nil {
		return nil, err
	}
	
	var response MEXCResponse
	if err := json.Unmarshal(data, &response); err != nil {
		return nil, err
	}
	
	if response.Code != 0 {
		return nil, fmt.Errorf("API error: %s", response.Msg)
	}
	
	orderData, _ := json.Marshal(response.Data)
	var order OrderResponse
	if err := json.Unmarshal(orderData, &order); err != nil {
		return nil, err
	}
	
	return &order, nil
}

func (t *MEXCTrader) getUSDTBalance() (float64, error) {
	account, err := t.Client.GetAccountInfo()
	if err != nil {
		return 0, err
	}
	
	for _, asset := range account.Assets {
		if asset.Asset == "USDT" {
			balance, _ := strconv.ParseFloat(asset.Free, 64)
			return balance, nil
		}
	}
	
	return 0, nil
}

func (t *MEXCTrader) executeTrade(signal *TradingSignal) {
	if signal.Action == "HOLD" {
		return
	}
	
	// Check USDT balance
	balance, err := t.getUSDTBalance()
	if err != nil {
		log.Printf("❌ Error getting balance: %v", err)
		return
	}
	
	if balance < t.TradeAmount {
		log.Printf("❌ Insufficient USDT balance: %.2f (need %.2f)", balance, t.TradeAmount)
		return
	}
	
	log.Printf("🚀 EXECUTING MEXC TRADE: %s %s", signal.Action, strings.Join(signal.Tokens, ", "))
	log.Printf("📊 Confidence: %.1f%%", signal.Confidence*100)
	log.Printf("💰 USDT Balance: %.2f", balance)
	log.Printf("📰 News: %s", signal.Title)
	log.Printf("🔗 Source: %s", signal.URL)
	
	for _, token := range signal.Tokens {
		marketCap := t.MarketCaps[token]
		if marketCap == "" {
			marketCap = "$1M"
		}
		
		log.Printf("💰 %s MarketCap: %s", token, marketCap)
		
		// Create trading pair
		symbol := token + t.BaseCurrency
		
		// Calculate quantity (simplified - in real trading you'd get current price)
		quantity := fmt.Sprintf("%.6f", t.TradeAmount/1.0) // Assuming $1 per token for demo
		
		var side string
		if signal.Action == "BUY" {
			side = "BUY"
		} else if signal.Action == "SELL" {
			side = "SELL"
		}
		
		// Place order
		order, err := t.Client.PlaceOrder(symbol, side, "MARKET", quantity, "0")
		if err != nil {
			log.Printf("❌ Failed to place %s order for %s: %v", side, token, err)
			continue
		}
		
		log.Printf("✅ %s ORDER PLACED: %s %s (OrderID: %s)", side, quantity, token, order.OrderID)
	}
	
	log.Printf("————————————")
}

func (t *MEXCTrader) processTradingSignal(signal *TradingSignal) {
	// Make trading decision based on signal
	switch signal.EventType {
	case "LISTING":
		if strings.Contains(strings.ToLower(signal.Title), "coinbase") {
			signal.Action = "BUY"
			signal.Confidence = 0.9
		} else {
			signal.Action = "BUY"
			signal.Confidence = 0.7
		}
		
	case "DELIST":
		signal.Action = "SELL"
		signal.Confidence = 0.8
		
	case "HACK":
		signal.Action = "SELL"
		signal.Confidence = 0.9
		
	default:
		signal.Action = "HOLD"
		signal.Confidence = 0.5
	}
	
	// Execute trade if confidence is high enough
	if signal.Confidence >= 0.7 {
		t.executeTrade(signal)
	} else {
		log.Printf("📊 Signal too weak: %s (confidence: %.1f%%)", signal.Title, signal.Confidence*100)
	}
}

func (t *MEXCTrader) startTrading() {
	log.Printf("🤖 MEXC BWEnews Trader started")
	log.Printf("📡 Exchange: MEXC")
	log.Printf("💰 Trade Amount: $%.2f per signal", t.TradeAmount)
	log.Printf("⏰ Check interval: 30 seconds")
	
	// Test API connection
	balance, err := t.getUSDTBalance()
	if err != nil {
		log.Printf("❌ Failed to connect to MEXC API: %v", err)
		log.Printf("⚠️  Please check your API credentials")
		return
	}
	
	log.Printf("✅ MEXC API connected successfully")
	log.Printf("💰 USDT Balance: %.2f", balance)
	
	// Simulate trading signals (in real implementation, this would come from BWEnews RSS)
	ticker := time.NewTicker(30 * time.Second)
	defer ticker.Stop()
	
	// Demo signal
	demoSignal := &TradingSignal{
		EventType:   "LISTING",
		Tokens:      []string{"KTA", "NOICE"},
		Title:       "COINBASE LISTING: Assets added to the roadmap today: Keeta (KTA) and Noice (NOICE)",
		URL:         "https://x.com/CoinbaseAssets/status/1964125377439109557",
		PublishedAt: time.Now(),
	}
	
	log.Printf("📊 Processing demo signal...")
	t.processTradingSignal(demoSignal)
	
	for {
		select {
		case <-ticker.C:
			// In real implementation, fetch from BWEnews RSS here
			log.Printf("⏰ Checking for new trading signals...")
		}
	}
}

func main() {
	log.SetFlags(log.LstdFlags | log.Lshortfile)
	
	// Get API credentials from environment
	apiKey := os.Getenv("MEXC_API_KEY")
	secretKey := os.Getenv("MEXC_SECRET_KEY")
	
	if apiKey == "" || secretKey == "" {
		log.Printf("❌ MEXC API credentials not found")
		log.Printf("Please set MEXC_API_KEY and MEXC_SECRET_KEY environment variables")
		log.Printf("Demo mode: Using dummy credentials")
		apiKey = "demo_api_key"
		secretKey = "demo_secret_key"
	}
	
	trader := NewMEXCTrader(apiKey, secretKey)
	
	// Start trading
	trader.startTrading()
}
