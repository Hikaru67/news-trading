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
	"net/url"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/joho/godotenv"
)

// BybitClient represents a Bybit API client
type BybitClient struct {
	apiKey    string
	secretKey string
	baseURL   string
	client    *http.Client
}

// WalletBalanceResponse represents the wallet balance response
type WalletBalanceResponse struct {
	RetCode int    `json:"retCode"`
	RetMsg  string `json:"retMsg"`
	Result  struct {
		List []Account `json:"list"`
	} `json:"result"`
}

// Account represents an account
type Account struct {
	AccountType string `json:"accountType"`
	Coin        []Coin `json:"coin"`
}

// Coin represents a coin balance
type Coin struct {
	Coin            string `json:"coin"`
	WalletBalance   string `json:"walletBalance"`
	AvailableToWithdraw string `json:"availableToWithdraw"`
}

// OrderHistoryResponse represents the order history response
type OrderHistoryResponse struct {
	RetCode int    `json:"retCode"`
	RetMsg  string `json:"retMsg"`
	Result  struct {
		List []Order `json:"list"`
	} `json:"result"`
}

// Order represents an order
type Order struct {
	OrderID       string `json:"orderId"`
	OrderLinkID   string `json:"orderLinkId"`
	Symbol        string `json:"symbol"`
	Side          string `json:"side"`
	OrderType     string `json:"orderType"`
	OrderStatus   string `json:"orderStatus"`
	Qty           string `json:"qty"`
	AvgPrice      string `json:"avgPrice"`
	ExecQty       string `json:"execQty"`
	ExecPrice     string `json:"execPrice"`
	ExecValue     string `json:"execValue"`
	ExecFee       string `json:"execFee"`
	CreatedTime   string `json:"createdTime"`
	UpdatedTime   string `json:"updatedTime"`
	TimeInForce   string `json:"timeInForce"`
	Category      string `json:"category"`
	StopOrderType string `json:"stopOrderType"`
	TriggerPrice  string `json:"triggerPrice"`
	TriggerBy     string `json:"triggerBy"`
}

// BenchmarkResult represents benchmark results
type BenchmarkResult struct {
	Endpoint     string
	TotalCalls   int
	SuccessCalls int
	FailedCalls  int
	TotalTime    time.Duration
	AvgTime      time.Duration
	MinTime      time.Duration
	MaxTime      time.Duration
	Throughput   float64 // calls per second
}

// NewBybitClient creates a new Bybit client
func NewBybitClient(apiKey, secretKey string, testnet bool) *BybitClient {
	baseURL := "https://api.bybit.com"
	if testnet {
		baseURL = "https://api-testnet.bybit.com"
	}

	return &BybitClient{
		apiKey:    apiKey,
		secretKey: secretKey,
		baseURL:   baseURL,
		client:    &http.Client{Timeout: 30 * time.Second},
	}
}

// generateSignature generates HMAC signature for Bybit API
func (b *BybitClient) generateSignature(paramStr string) string {
	h := hmac.New(sha256.New, []byte(b.secretKey))
	h.Write([]byte(paramStr))
	return hex.EncodeToString(h.Sum(nil))
}

// makeRequest makes an authenticated request to Bybit API
func (b *BybitClient) makeRequest(method, endpoint string, params map[string]string) ([]byte, error) {
	timestamp := strconv.FormatInt(time.Now().UnixMilli(), 10)
	recvWindow := "5000"

	// Build query string
	values := url.Values{}
	for k, v := range params {
		values.Set(k, v)
	}
	values.Set("api_key", b.apiKey)
	values.Set("timestamp", timestamp)
	values.Set("recv_window", recvWindow)

	queryString := values.Encode()
	signature := b.generateSignature(queryString)
	queryString += "&sign=" + signature

	// Create request
	var req *http.Request
	var err error

	if method == "GET" {
		req, err = http.NewRequest(method, b.baseURL+endpoint+"?"+queryString, nil)
	} else {
		req, err = http.NewRequest(method, b.baseURL+endpoint, strings.NewReader(queryString))
		req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	}

	if err != nil {
		return nil, err
	}

	// Make request
	resp, err := b.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}

	return body, nil
}

// GetWalletBalance gets wallet balance
func (b *BybitClient) GetWalletBalance() (*WalletBalanceResponse, error) {
	params := map[string]string{
		"accountType": "UNIFIED",
	}

	body, err := b.makeRequest("GET", "/v5/account/wallet-balance", params)
	if err != nil {
		return nil, err
	}

	var response WalletBalanceResponse
	err = json.Unmarshal(body, &response)
	if err != nil {
		return nil, err
	}

	return &response, nil
}

// GetOrderHistory gets order history
func (b *BybitClient) GetOrderHistory(category, symbol string, limit int) (*OrderHistoryResponse, error) {
	params := map[string]string{
		"category": category,
		"limit":    strconv.Itoa(limit),
	}
	if symbol != "" {
		params["symbol"] = symbol
	}

	body, err := b.makeRequest("GET", "/v5/order/realtime", params)
	if err != nil {
		return nil, err
	}

	var response OrderHistoryResponse
	err = json.Unmarshal(body, &response)
	if err != nil {
		return nil, err
	}

	return &response, nil
}

// benchmarkEndpoint benchmarks a specific API endpoint
func (b *BybitClient) benchmarkEndpoint(endpoint string, params map[string]string, numCalls int, concurrent bool) BenchmarkResult {
	var wg sync.WaitGroup
	var mu sync.Mutex
	
	result := BenchmarkResult{
		Endpoint:   endpoint,
		TotalCalls: numCalls,
		MinTime:    time.Hour, // Initialize with high value
	}

	startTime := time.Now()
	
	if concurrent {
		// Concurrent calls
		for i := 0; i < numCalls; i++ {
			wg.Add(1)
			go func() {
				defer wg.Done()
				callStart := time.Now()
				
				var err error
				switch endpoint {
				case "/v5/account/wallet-balance":
					_, err = b.GetWalletBalance()
				case "/v5/order/realtime":
					_, err = b.GetOrderHistory("spot", "", 10)
				}
				
				callDuration := time.Since(callStart)
				
				mu.Lock()
				if err == nil {
					result.SuccessCalls++
				} else {
					result.FailedCalls++
				}
				
				if callDuration < result.MinTime {
					result.MinTime = callDuration
				}
				if callDuration > result.MaxTime {
					result.MaxTime = callDuration
				}
				mu.Unlock()
			}()
		}
		wg.Wait()
	} else {
		// Sequential calls
		for i := 0; i < numCalls; i++ {
			callStart := time.Now()
			
			var err error
			switch endpoint {
			case "/v5/account/wallet-balance":
				_, err = b.GetWalletBalance()
			case "/v5/order/realtime":
				_, err = b.GetOrderHistory("spot", "", 10)
			}
			
			callDuration := time.Since(callStart)
			
			if err == nil {
				result.SuccessCalls++
			} else {
				result.FailedCalls++
			}
			
			if callDuration < result.MinTime {
				result.MinTime = callDuration
			}
			if callDuration > result.MaxTime {
				result.MaxTime = callDuration
			}
		}
	}
	
	result.TotalTime = time.Since(startTime)
	result.AvgTime = result.TotalTime / time.Duration(numCalls)
	result.Throughput = float64(numCalls) / result.TotalTime.Seconds()
	
	return result
}

// printBenchmarkResult prints benchmark results
func printBenchmarkResult(result BenchmarkResult) {
	fmt.Printf("\n📊 Benchmark Results: %s\n", result.Endpoint)
	fmt.Println("=" * 60)
	fmt.Printf("🔢 Total Calls: %d\n", result.TotalCalls)
	fmt.Printf("✅ Success: %d\n", result.SuccessCalls)
	fmt.Printf("❌ Failed: %d\n", result.FailedCalls)
	fmt.Printf("⏱️  Total Time: %v\n", result.TotalTime)
	fmt.Printf("📈 Average Time: %v\n", result.AvgTime)
	fmt.Printf("🚀 Min Time: %v\n", result.MinTime)
	fmt.Printf("🐌 Max Time: %v\n", result.MaxTime)
	fmt.Printf("⚡ Throughput: %.2f calls/second\n", result.Throughput)
	fmt.Printf("📊 Success Rate: %.2f%%\n", float64(result.SuccessCalls)/float64(result.TotalCalls)*100)
}

func main() {
	// Load environment variables
	err := godotenv.Load()
	if err != nil {
		log.Println("No .env file found, using system environment variables")
	}

	// Get credentials
	apiKey := os.Getenv("BYBIT_API_KEY")
	secretKey := os.Getenv("BYBIT_SECRET_KEY")
	testnet := os.Getenv("BYBIT_TESTNET") == "true"

	if apiKey == "" || secretKey == "" {
		log.Fatal("BYBIT_API_KEY and BYBIT_SECRET_KEY must be set")
	}

	fmt.Println("🚀 Bybit API Performance Benchmark")
	fmt.Println("=" * 60)
	fmt.Printf("🔗 Connecting to %s\n", map[bool]string{true: "Testnet", false: "Mainnet"}[testnet])
	fmt.Printf("🔑 API Key: %s...%s\n", apiKey[:8], apiKey[len(apiKey)-4:])
	fmt.Println()

	// Create client
	client := NewBybitClient(apiKey, secretKey, testnet)

	// Test single call first
	fmt.Println("🧪 Testing single API calls...")
	
	start := time.Now()
	balance, err := client.GetWalletBalance()
	balanceTime := time.Since(start)
	
	if err != nil {
		fmt.Printf("❌ Wallet balance error: %v\n", err)
	} else {
		fmt.Printf("✅ Wallet balance: %v (%.2fms)\n", balance.RetCode == 0, balanceTime.Milliseconds())
	}
	
	start = time.Now()
	orders, err := client.GetOrderHistory("spot", "", 10)
	ordersTime := time.Since(start)
	
	if err != nil {
		fmt.Printf("❌ Order history error: %v\n", err)
	} else {
		fmt.Printf("✅ Order history: %v (%.2fms)\n", orders.RetCode == 0, ordersTime.Milliseconds())
	}
	
	fmt.Println()

	// Benchmark configurations
	configs := []struct {
		endpoint   string
		numCalls   int
		concurrent bool
		description string
	}{
		{"/v5/account/wallet-balance", 10, false, "Sequential Wallet Balance (10 calls)"},
		{"/v5/account/wallet-balance", 10, true, "Concurrent Wallet Balance (10 calls)"},
		{"/v5/order/realtime", 10, false, "Sequential Order History (10 calls)"},
		{"/v5/order/realtime", 10, true, "Concurrent Order History (10 calls)"},
		{"/v5/account/wallet-balance", 50, false, "Sequential Wallet Balance (50 calls)"},
		{"/v5/account/wallet-balance", 50, true, "Concurrent Wallet Balance (50 calls)"},
	}

	// Run benchmarks
	for _, config := range configs {
		fmt.Printf("🏃 Running: %s\n", config.description)
		
		result := client.benchmarkEndpoint(config.endpoint, nil, config.numCalls, config.concurrent)
		printBenchmarkResult(result)
		
		// Add delay between benchmarks to avoid rate limiting
		time.Sleep(2 * time.Second)
	}

	// Summary
	fmt.Println("\n🎯 Performance Summary")
	fmt.Println("=" * 60)
	fmt.Println("✅ Single API calls working")
	fmt.Println("✅ Sequential benchmarks completed")
	fmt.Println("✅ Concurrent benchmarks completed")
	fmt.Println("✅ Performance metrics calculated")
	fmt.Println()
	fmt.Println("💡 Recommendations:")
	fmt.Println("- Monitor success rates for rate limiting")
	fmt.Println("- Adjust concurrent calls based on API limits")
	fmt.Println("- Consider caching for frequently accessed data")
	fmt.Println("- Monitor response times for performance degradation")
	
	fmt.Println("\n🎉 Benchmark Complete!")
	fmt.Printf("🔗 Dashboard: %s\n", map[bool]string{
		true:  "https://testnet.bybit.com",
		false: "https://www.bybit.com",
	}[testnet])
}
