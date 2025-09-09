package main

import (
	"encoding/xml"
	"fmt"
	"io"
	"log"
	"net/http"
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

type MEXCTicker struct {
	Symbol             string `json:"symbol"`
	PriceChange        string `json:"priceChange"`
	PriceChangePercent string `json:"priceChangePercent"`
	WeightedAvgPrice   string `json:"weightedAvgPrice"`
	PrevClosePrice     string `json:"prevClosePrice"`
	LastPrice          string `json:"lastPrice"`
	LastQty            string `json:"lastQty"`
	BidPrice           string `json:"bidPrice"`
	AskPrice           string `json:"askPrice"`
	OpenPrice          string `json:"openPrice"`
	HighPrice          string `json:"highPrice"`
	LowPrice           string `json:"lowPrice"`
	Volume             string `json:"volume"`
	QuoteVolume        string `json:"quoteVolume"`
	OpenTime           int64  `json:"openTime"`
	CloseTime          int64  `json:"closeTime"`
	Count              int    `json:"count"`
}

type FlowTest struct {
	RSSURL string
}

func NewFlowTest() *FlowTest {
	return &FlowTest{
		RSSURL: "https://rss-public.bwe-ws.com/",
	}
}

func (ft *FlowTest) getBWEnewsLastMessage() (map[string]interface{}, time.Duration, error) {
	fmt.Println("📡 Fetching BWEnews RSS feed...")
	start := time.Now()
	
	resp, err := http.Get(ft.RSSURL)
	if err != nil {
		return nil, 0, err
	}
	defer resp.Body.Close()
	
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, 0, err
	}
	
	var rss RSS
	err = xml.Unmarshal(body, &rss)
	if err != nil {
		return nil, 0, err
	}
	
	if len(rss.Channel.Items) == 0 {
		return nil, 0, fmt.Errorf("no items found in RSS feed")
	}
	
	lastItem := rss.Channel.Items[0]
	duration := time.Since(start)
	
	fmt.Printf("✅ RSS fetch completed in %.2fs\n", duration.Seconds())
	fmt.Printf("📰 Latest news: %s...\n", truncateString(lastItem.Title, 100))
	fmt.Printf("🔗 Link: %s\n", lastItem.Link)
	fmt.Printf("📅 Published: %s\n", lastItem.PubDate)
	
	return map[string]interface{}{
		"title":       lastItem.Title,
		"description": lastItem.Description,
		"link":        lastItem.Link,
		"pub_date":    lastItem.PubDate,
	}, duration, nil
}

func (ft *FlowTest) detectTokens(text string) ([]string, time.Duration) {
	fmt.Println("🔍 Detecting tokens...")
	start := time.Now()
	
	tokens := make(map[string]bool)
	
	// Pattern 1: $TOKEN format
	dollarPattern := regexp.MustCompile(`\$([A-Z]{2,10})\b`)
	matches := dollarPattern.FindAllStringSubmatch(text, -1)
	for _, match := range matches {
		if len(match) > 1 {
			tokens[match[1]] = true
		}
	}
	
	// Pattern 2: (TOKEN) format
	parenPattern := regexp.MustCompile(`\(([A-Z]{2,10})\)`)
	matches = parenPattern.FindAllStringSubmatch(text, -1)
	for _, match := range matches {
		if len(match) > 1 {
			tokens[match[1]] = true
		}
	}
	
	// Pattern 3: Known crypto names
	cryptoNames := map[string]string{
		"bitcoin":   "BTC",
		"ethereum":  "ETH",
		"solana":    "SOL",
		"dogecoin":  "DOGE",
		"cardano":   "ADA",
		"polygon":   "MATIC",
		"avalanche": "AVAX",
		"polkadot":  "DOT",
		"keeta":     "KTA",
		"noice":     "NOICE",
		"omni":      "OMNI",
		"open":      "OPEN",
		"binance":   "BNB",
	}
	
	textLower := strings.ToLower(text)
	for name, symbol := range cryptoNames {
		if strings.Contains(textLower, name) {
			tokens[symbol] = true
		}
	}
	
	var result []string
	for token := range tokens {
		result = append(result, token)
	}
	
	duration := time.Since(start)
	fmt.Printf("✅ Token detection completed in %.2fs\n", duration.Seconds())
	fmt.Printf("🎯 Found tokens: %v\n", result)
	
	return result, duration
}

func (ft *FlowTest) getMEXCTokenInfo(symbol string) (map[string]interface{}, time.Duration, error) {
	fmt.Printf("🏦 Getting MEXC info for %s...\n", symbol)
	start := time.Now()
	
	url := fmt.Sprintf("https://api.mexc.com/api/v3/ticker/24hr?symbol=%sUSDT", symbol)
	
	resp, err := http.Get(url)
	if err != nil {
		return nil, 0, err
	}
	defer resp.Body.Close()
	
	_, err = io.ReadAll(resp.Body)
	if err != nil {
		return nil, 0, err
	}
	
	// Parse JSON response - simplified for demo
	duration := time.Since(start)
	fmt.Printf("✅ MEXC API call completed in %.2fs\n", duration.Seconds())
	
	// For demo purposes, return mock data
	return map[string]interface{}{
		"symbol":     symbol,
		"price":      "1.00",
		"change_24h": "0.00",
		"volume":     "1000000",
		"high_24h":   "1.10",
		"low_24h":    "0.90",
	}, duration, nil
}

func truncateString(s string, maxLen int) string {
	if len(s) <= maxLen {
		return s
	}
	return s[:maxLen] + "..."
}

func main() {
	log.SetFlags(log.LstdFlags | log.Lshortfile)
	
	fmt.Println("🚀 Starting BWEnews -> Token Detection -> MEXC flow test (Go)")
	fmt.Println(strings.Repeat("=", 60))
	
	totalStart := time.Now()
	
	ft := NewFlowTest()
	
	// Step 1: Get last BWEnews message
	fmt.Println("\n📡 STEP 1: Get last BWEnews message")
	fmt.Println(strings.Repeat("-", 40))
	newsData, rssTime, err := ft.getBWEnewsLastMessage()
	if err != nil {
		log.Printf("❌ Failed to get BWEnews data: %v", err)
		return
	}
	
	// Step 2: Detect tokens
	fmt.Println("\n🔍 STEP 2: Detect tokens")
	fmt.Println(strings.Repeat("-", 40))
	text := newsData["title"].(string) + " " + newsData["description"].(string)
	tokens, detectionTime := ft.detectTokens(text)
	
	if len(tokens) == 0 {
		fmt.Println("❌ No tokens detected")
		return
	}
	
	// Step 3: Get MEXC info for each token
	fmt.Println("\n🏦 STEP 3: Get MEXC token info")
	fmt.Println(strings.Repeat("-", 40))
	
	var mexcResults []map[string]interface{}
	var totalMEXCTime time.Duration
	
	for _, token := range tokens {
		result, apiTime, err := ft.getMEXCTokenInfo(token)
		if err != nil {
			result = map[string]interface{}{
				"symbol": token,
				"error":  err.Error(),
			}
		}
		mexcResults = append(mexcResults, result)
		totalMEXCTime += apiTime
		time.Sleep(100 * time.Millisecond) // Small delay between API calls
	}
	
	// Summary
	totalTime := time.Since(totalStart)
	
	fmt.Println("\n📊 SUMMARY")
	fmt.Println(strings.Repeat("=", 60))
	fmt.Printf("⏱️  Total execution time: %.2fs\n", totalTime.Seconds())
	fmt.Printf("📡 RSS fetch time: %.2fs\n", rssTime.Seconds())
	fmt.Printf("🔍 Token detection time: %.2fs\n", detectionTime.Seconds())
	fmt.Printf("🏦 Total MEXC API time: %.2fs\n", totalMEXCTime.Seconds())
	
	fmt.Printf("\n📰 News: %s...\n", truncateString(newsData["title"].(string), 80))
	fmt.Printf("🎯 Tokens found: %v\n", tokens)
	
	fmt.Println("\n💰 MEXC Token Info:")
	for _, result := range mexcResults {
		if err, exists := result["error"]; exists {
			fmt.Printf("  ❌ %s: %v\n", result["symbol"], err)
		} else {
			fmt.Printf("  ✅ %s: $%s (%s%%)\n", result["symbol"], result["price"], result["change_24h"])
		}
	}
	
	fmt.Printf("\n⚡ Performance:\n")
	fmt.Printf("  - RSS fetch: %.2fs\n", rssTime.Seconds())
	fmt.Printf("  - Token detection: %.2fs\n", detectionTime.Seconds())
	fmt.Printf("  - MEXC API calls: %.2fs\n", totalMEXCTime.Seconds())
	fmt.Printf("  - Total: %.2fs\n", totalTime.Seconds())
	
	// Calculate efficiency
	processingTime := totalTime - totalMEXCTime
	fmt.Printf("\n🎯 Efficiency:\n")
	fmt.Printf("  - Processing time: %.2fs\n", processingTime.Seconds())
	fmt.Printf("  - API time: %.2fs\n", totalMEXCTime.Seconds())
	fmt.Printf("  - Processing ratio: %.1f%%\n", processingTime.Seconds()/totalTime.Seconds()*100)
}
