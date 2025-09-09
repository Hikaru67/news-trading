package main

import (
	"encoding/xml"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"regexp"
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

type TradingSignal struct {
	EventType   string
	Tokens      []string
	Title       string
	URL         string
	PublishedAt time.Time
	Action      string // BUY, SELL, HOLD
	Confidence  float64
}

type BWEnewsTrader struct {
	RSSURL     string
	LastCheck  time.Time
	Processed  map[string]bool
	MarketCaps map[string]string
}

func NewBWEnewsTrader() *BWEnewsTrader {
	return &BWEnewsTrader{
		RSSURL:    "https://rss-public.bwe-ws.com/",
		LastCheck: time.Now().Add(-1 * time.Hour), // Start from 1 hour ago
		Processed: make(map[string]bool),
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

func (t *BWEnewsTrader) extractTokens(text string) []string {
	tokens := make(map[string]bool)
	
	// Pattern for $TOKEN format
	dollarPattern := regexp.MustCompile(`\$([A-Z]{2,10})\b`)
	matches := dollarPattern.FindAllStringSubmatch(text, -1)
	for _, match := range matches {
		if len(match) > 1 {
			tokens[match[1]] = true
		}
	}
	
	// Pattern for TOKEN in parentheses
	parenPattern := regexp.MustCompile(`\(([A-Z]{2,10})\)`)
	matches = parenPattern.FindAllStringSubmatch(text, -1)
	for _, match := range matches {
		if len(match) > 1 {
			tokens[match[1]] = true
		}
	}
	
	// Convert map to slice
	var result []string
	for token := range tokens {
		result = append(result, token)
	}
	
	return result
}

func (t *BWEnewsTrader) classifyEventType(text string) string {
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

func (t *BWEnewsTrader) makeTradingDecision(signal *TradingSignal) {
	switch signal.EventType {
	case "LISTING":
		// COINBASE listing = strong buy signal
		if strings.Contains(strings.ToLower(signal.Title), "coinbase") {
			signal.Action = "BUY"
			signal.Confidence = 0.9
		} else {
			signal.Action = "BUY"
			signal.Confidence = 0.7
		}
		
	case "DELIST":
		// Delisting = sell signal
		signal.Action = "SELL"
		signal.Confidence = 0.8
		
	case "HACK":
		// Hack = sell signal
		signal.Action = "SELL"
		signal.Confidence = 0.9
		
	default:
		// Other news = hold
		signal.Action = "HOLD"
		signal.Confidence = 0.5
	}
}

func (t *BWEnewsTrader) executeTrade(signal *TradingSignal) {
	if signal.Action == "HOLD" {
		return
	}
	
	log.Printf("🚀 EXECUTING TRADE: %s %s", signal.Action, strings.Join(signal.Tokens, ", "))
	log.Printf("📊 Confidence: %.1f%%", signal.Confidence*100)
	log.Printf("📰 News: %s", signal.Title)
	log.Printf("🔗 Source: %s", signal.URL)
	
	// Here you would integrate with your trading API
	// For now, just log the decision
	for _, token := range signal.Tokens {
		marketCap := t.MarketCaps[token]
		if marketCap == "" {
			marketCap = "$1M"
		}
		
		log.Printf("💰 %s MarketCap: %s", token, marketCap)
		
		// Simulate trade execution
		if signal.Action == "BUY" {
			log.Printf("✅ BUY ORDER: %s at market price", token)
		} else if signal.Action == "SELL" {
			log.Printf("❌ SELL ORDER: %s at market price", token)
		}
	}
	
	log.Printf("————————————")
}

func (t *BWEnewsTrader) fetchRSSFeed() (*RSS, error) {
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

func (t *BWEnewsTrader) processRSSFeed() error {
	rss, err := t.fetchRSSFeed()
	if err != nil {
		return fmt.Errorf("failed to fetch RSS feed: %v", err)
	}
	
	log.Printf("📡 Checking BWEnews RSS feed...")
	
	for _, item := range rss.Channel.Items {
		// Skip if already processed
		if t.Processed[item.Link] {
			continue
		}
		
		// Parse published time
		publishedAt := time.Now()
		if item.PubDate != "" {
			if parsed, err := time.Parse(time.RFC1123Z, item.PubDate); err == nil {
				publishedAt = parsed
			}
		}
		
		// Skip old items
		if publishedAt.Before(t.LastCheck) {
			continue
		}
		
		// Extract tokens and classify event
		text := item.Title + " " + item.Description
		tokens := t.extractTokens(text)
		eventType := t.classifyEventType(text)
		
		// Create trading signal
		signal := &TradingSignal{
			EventType:   eventType,
			Tokens:      tokens,
			Title:       item.Title,
			URL:         item.Link,
			PublishedAt: publishedAt,
		}
		
		// Make trading decision
		t.makeTradingDecision(signal)
		
		// Execute trade if confidence is high enough
		if signal.Confidence >= 0.7 {
			t.executeTrade(signal)
		} else {
			log.Printf("📊 Signal too weak: %s (confidence: %.1f%%)", signal.Title, signal.Confidence*100)
		}
		
		// Mark as processed
		t.Processed[item.Link] = true
	}
	
	t.LastCheck = time.Now()
	return nil
}

func (t *BWEnewsTrader) startTrading() {
	log.Printf("🤖 BWEnews Trader started")
	log.Printf("📡 RSS URL: %s", t.RSSURL)
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
	
	// Get RSS URL from environment or use default
	rssURL := os.Getenv("BWE_RSS_URL")
	if rssURL == "" {
		rssURL = "https://rss-public.bwe-ws.com/"
	}
	
	trader := NewBWEnewsTrader()
	trader.RSSURL = rssURL
	
	// Start trading
	trader.startTrading()
}