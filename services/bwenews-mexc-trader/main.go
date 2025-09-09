package main

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/xml"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"regexp"
	"strconv"
	"strings"
	"time"
)

type RSS struct {
	XMLName xml.Name `xml:"rss"`
	Channel Channel  `xml:"channel"`
}

type Channel struct {
	Title       string `xml:"title"`
	Description string `xml:"description"`
	Items       []Item `xml:"item"`
}

type Item struct {
	Title       string `xml:"title"`
	Description string `xml:"description"`
	Link        string `xml:"link"`
	PubDate     string `xml:"pubDate"`
}

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

type BWEnewsMEXCTrader struct {
	RSSURL      string
	MEXCClient  *MEXCClient
	LastCheck   time.Time
	Processed   map[string]bool
	MarketCaps  map[string]string
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

func NewBWEnewsMEXCTrader(apiKey, secretKey string) *BWEnewsMEXCTrader {
	return &BWEnewsMEXCTrader{
		RSSURL:       "https://rss-public.bwe-ws.com/",
		MEXCClient:   NewMEXCClient(apiKey, secretKey),
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
	
	if params == nil {
		params = make(map[string]string)
	}
	params["api_key"] = c.APIKey
	params["timestamp"] = strconv.FormatInt(time.Now().UnixMilli(), 10)
	
	var queryParts []string
	for k, v := range params {
		queryParts = append(queryParts, fmt.Sprintf("%s=%s", k, v))
	}
	queryString := strings.Join(queryParts, "&")
	
	signature := c.generateSignature(queryString)
	queryString += "&signature=" + signature
	
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

func (t *BWEnewsMEXCTrader) extractTokens(text string) []string {
	tokens := make(map[string]bool)
	
	dollarPattern := regexp.MustCompile(`\$([A-Z]{2,10})\b`)
	matches := dollarPattern.FindAllStringSubmatch(text, -1)
	for _, match := range matches {
		if len(match) > 1 {
			tokens[match[1]] = true
		}
	}
	
	parenPattern := regexp.MustCompile(`\(([A-Z]{2,10})\)`)
	matches = parenPattern.FindAllStringSubmatch(text, -1)
	for _, match := range matches {
		if len(match) > 1 {
			tokens[match[1]] = true
		}
	}
	
	var result []string
	for token := range tokens {
		result = append(result, token)
	}
	
	return result
}

func (t *BWEnewsMEXCTrader) classifyEventType(text string) string {
	textLower := strings.ToLower(text)
	
	if strings.Contains(textLower, "listing") || strings.Contains(textLower, "coinbase") || 
	   strings.Contains(textLower, "roadmap") || strings.Contains(textLower, "added") {
		return "LISTING"
	}
	
	if strings.Contains(textLower, "delist") || strings.Contains(textLower, "removed") {
		return "DELIST"
	}
	
	if strings.Contains(textLower, "hack") || strings.Contains(textLower, "stolen") || 
	   strings.Contains(textLower, "breach") {
		return "HACK"
	}
	
	return "OTHER"
}

func (t *BWEnewsMEXCTrader) fetchRSSFeed() (*RSS, error) {
	resp, err := http.Get(t.RSSURL)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}
	
	var rss RSS
	err = xml.Unmarshal(body, &rss)
	if err != nil {
		return nil, err
	}
	
	return &rss, nil
}

func (t *BWEnewsMEXCTrader) makeTradingDecision(signal *TradingSignal) {
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
}

func (t *BWEnewsMEXCTrader) executeTrade(signal *TradingSignal) {
	if signal.Action == "HOLD" {
		return
	}
	
	log.Printf("🚀 EXECUTING MEXC TRADE: %s %s", signal.Action, strings.Join(signal.Tokens, ", "))
	log.Printf("📊 Confidence: %.1f%%", signal.Confidence*100)
	log.Printf("📰 News: %s", signal.Title)
	log.Printf("🔗 Source: %s", signal.URL)
	
	for _, token := range signal.Tokens {
		marketCap := t.MarketCaps[token]
		if marketCap == "" {
			marketCap = "$1M"
		}
		
		log.Printf("💰 %s MarketCap: %s", token, marketCap)
		
		quantity := fmt.Sprintf("%.6f", t.TradeAmount/1.0)
		
		var side string
		if signal.Action == "BUY" {
			side = "BUY"
		} else if signal.Action == "SELL" {
			side = "SELL"
		}
		
		// In demo mode, just log the trade
		log.Printf("✅ %s ORDER: %s %s (Demo mode - not executed)", side, quantity, token)
	}
	
	log.Printf("————————————")
}

func (t *BWEnewsMEXCTrader) processRSSFeed() error {
	rss, err := t.fetchRSSFeed()
	if err != nil {
		return fmt.Errorf("failed to fetch RSS feed: %v", err)
	}
	
	log.Printf("📡 Checking BWEnews RSS feed...")
	
	for _, item := range rss.Channel.Items {
		if t.Processed[item.Link] {
			continue
		}
		
		publishedAt := time.Now()
		if item.PubDate != "" {
			if parsed, err := time.Parse(time.RFC1123Z, item.PubDate); err == nil {
				publishedAt = parsed
			}
		}
		
		if publishedAt.Before(t.LastCheck) {
			continue
		}
		
		text := item.Title + " " + item.Description
		tokens := t.extractTokens(text)
		eventType := t.classifyEventType(text)
		
		signal := &TradingSignal{
			EventType:   eventType,
			Tokens:      tokens,
			Title:       item.Title,
			URL:         item.Link,
			PublishedAt: publishedAt,
		}
		
		t.makeTradingDecision(signal)
		
		if signal.Confidence >= 0.7 {
			t.executeTrade(signal)
		} else {
			log.Printf("📊 Signal too weak: %s (confidence: %.1f%%)", signal.Title, signal.Confidence*100)
		}
		
		t.Processed[item.Link] = true
	}
	
	t.LastCheck = time.Now()
	return nil
}

func (t *BWEnewsMEXCTrader) startTrading() {
	log.Printf("🤖 BWEnews MEXC Trader started")
	log.Printf("📡 RSS URL: %s", t.RSSURL)
	log.Printf("🏦 Exchange: MEXC")
	log.Printf("💰 Trade Amount: $%.2f per signal", t.TradeAmount)
	log.Printf("⏰ Check interval: 30 seconds")
	
	ticker := time.NewTicker(30 * time.Second)
	defer ticker.Stop()
	
	// Initial check
	if err := t.processRSSFeed(); err != nil {
		log.Printf("❌ Error processing RSS feed: %v", err)
	}
	
	for {
		select {
		case <-ticker.C:
			if err := t.processRSSFeed(); err != nil {
				log.Printf("❌ Error processing RSS feed: %v", err)
			}
		}
	}
}

func main() {
	log.SetFlags(log.LstdFlags | log.Lshortfile)
	
	apiKey := os.Getenv("MEXC_API_KEY")
	secretKey := os.Getenv("MEXC_SECRET_KEY")
	
	if apiKey == "" || secretKey == "" {
		log.Printf("⚠️  MEXC API credentials not found - running in demo mode")
		apiKey = "demo_api_key"
		secretKey = "demo_secret_key"
	}
	
	trader := NewBWEnewsMEXCTrader(apiKey, secretKey)
	trader.startTrading()
}
