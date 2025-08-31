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

	fmt.Println("🚀 Bybit API Client - Check Order History")
	fmt.Println("============================================================")
	fmt.Printf("🔗 Connecting to %s\n", map[bool]string{true: "Testnet", false: "Mainnet"}[testnet])
	fmt.Printf("🔑 API Key: %s...%s\n", apiKey[:8], apiKey[len(apiKey)-4:])
	fmt.Println()

	// Create client
	client := NewBybitClient(apiKey, secretKey, testnet)

	// Get wallet balance
	fmt.Println("📊 Account Balance:")
	fmt.Println("------------------------------")
	balance, err := client.GetWalletBalance()
	if err != nil {
		log.Printf("❌ Error getting balance: %v", err)
	} else {
		if balance.RetCode == 0 {
			for _, account := range balance.Result.List {
				if account.AccountType == "UNIFIED" {
					for _, coin := range account.Coin {
						if coin.WalletBalance != "0" {
							fmt.Printf("💰 %s: %s (Available: %s)\n", 
								coin.Coin, coin.WalletBalance, coin.AvailableToWithdraw)
						}
					}
				}
			}
		} else {
			fmt.Printf("❌ Error: %s\n", balance.RetMsg)
		}
	}
	fmt.Println()

	// Get order history
	fmt.Println("📋 Recent Orders:")
	fmt.Println("------------------------------")
	orders, err := client.GetOrderHistory("spot", "", 10)
	if err != nil {
		log.Printf("❌ Error getting orders: %v", err)
	} else {
		if orders.RetCode == 0 {
			if len(orders.Result.List) == 0 {
				fmt.Println("📭 No orders found")
			} else {
				for _, order := range orders.Result.List {
					status := "⏳"
					if order.OrderStatus == "Filled" {
						status = "✅"
					} else if order.OrderStatus == "Cancelled" {
						status = "❌"
					}

					fmt.Printf("%s %s: %s %s @ %s - %s\n",
						status,
						order.Symbol,
						order.Side,
						order.Qty,
						order.AvgPrice,
						order.OrderStatus)
				}
			}
		} else {
			fmt.Printf("❌ Error: %s\n", orders.RetMsg)
		}
	}
	fmt.Println()

	// Get specific symbol orders
	fmt.Println("📊 BTCUSDT Orders:")
	fmt.Println("------------------------------")
	btcOrders, err := client.GetOrderHistory("spot", "BTCUSDT", 5)
	if err != nil {
		log.Printf("❌ Error getting BTC orders: %v", err)
	} else {
		if btcOrders.RetCode == 0 {
			if len(btcOrders.Result.List) == 0 {
				fmt.Println("📭 No BTCUSDT orders found")
			} else {
				for _, order := range btcOrders.Result.List {
					status := "⏳"
					if order.OrderStatus == "Filled" {
						status = "✅"
					} else if order.OrderStatus == "Cancelled" {
						status = "❌"
					}

					fmt.Printf("%s %s: %s %s @ %s - %s (ID: %s)\n",
						status,
						order.Symbol,
						order.Side,
						order.Qty,
						order.AvgPrice,
						order.OrderStatus,
						order.OrderID)
				}
			}
		} else {
			fmt.Printf("❌ Error: %s\n", btcOrders.RetMsg)
		}
	}
	fmt.Println()

	fmt.Println("🎉 Bybit API Client Complete!")
	fmt.Printf("🔗 Dashboard: %s\n", map[bool]string{
		true:  "https://testnet.bybit.com",
		false: "https://www.bybit.com",
	}[testnet])
}
