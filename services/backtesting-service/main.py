import asyncio
import json
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
from kafka import KafkaProducer, KafkaConsumer
import redis
import os
from dotenv import load_dotenv
import numpy as np
from dataclasses import dataclass, asdict
from enum import Enum
import pandas as pd
from scipy import stats

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class BacktestResult(Enum):
    """Backtest result types"""
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    PARTIAL = "PARTIAL"

@dataclass
class BacktestMetrics:
    """Backtest performance metrics"""
    total_return: float
    annualized_return: float
    volatility: float
    sharpe_ratio: float
    max_drawdown: float
    win_rate: float
    profit_factor: float
    calmar_ratio: float
    sortino_ratio: float
    var_95: float
    var_99: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    avg_win: float
    avg_loss: float
    largest_win: float
    largest_loss: float
    consecutive_wins: int
    consecutive_losses: int

@dataclass
class HistoricalTrade:
    """Historical trade data"""
    trade_id: str
    symbol: str
    side: str
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float
    commission: float
    net_pnl: float
    holding_period: float
    signal_type: str
    signal_confidence: float

class BacktestingService:
    """Backtesting service for historical performance analysis"""
    
    def __init__(self):
        self.kafka_producer = KafkaProducer(
            bootstrap_servers=os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'kafka:29092'),
            value_serializer=lambda v: json.dumps(v, default=str).encode('utf-8'),
            key_serializer=lambda k: k.encode('utf-8') if k else None
        )
        
        self.kafka_consumer = KafkaConsumer(
            'trading.orders.v1',
            bootstrap_servers=os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'kafka:29092'),
            value_deserializer=lambda m: json.loads(m.decode('utf-8')),
            group_id='backtesting_service',
            auto_offset_reset='latest'
        )
        
        self.redis_client = redis.Redis.from_url(
            os.getenv('REDIS_URL', 'redis://redis:6379')
        )
        
        # Historical data storage
        self.historical_trades: List[HistoricalTrade] = []
        self.historical_signals: List[Dict] = []
        self.market_data: Dict[str, List[Dict]] = {}
        
        # Backtesting parameters
        self.backtest_params = {
            'start_date': '2024-01-01',
            'end_date': '2024-12-31',
            'initial_capital': 10000.0,
            'commission_rate': 0.001,  # 0.1%
            'slippage': 0.0005,  # 0.05%
            'position_sizing': 'fixed',  # fixed, kelly, optimal_f
            'risk_per_trade': 0.02,  # 2% risk per trade
            'max_positions': 5,
            'correlation_threshold': 0.7
        }
        
        # Performance tracking
        self.current_capital = self.backtest_params['initial_capital']
        self.peak_capital = self.current_capital
        self.drawdown_series = []
        self.returns_series = []
        
        self.running = False
        
    async def start_backtesting(self):
        """Start backtesting service"""
        logger.info("Starting Backtesting Service...")
        self.running = True
        
        try:
            while self.running:
                try:
                    # Poll for messages
                    message_batch = self.kafka_consumer.poll(timeout_ms=1000, max_records=10)
                    
                    for tp, messages in message_batch.items():
                        for message in messages:
                            if not self.running:
                                break
                                
                            try:
                                # Process trading order
                                order = message.value
                                await self.process_trading_order(order)
                                
                            except Exception as e:
                                logger.error(f"Error processing order: {e}")
                    
                    # Small delay to prevent busy waiting
                    await asyncio.sleep(0.1)
                    
                except Exception as e:
                    logger.error(f"Error in message polling: {e}")
                    await asyncio.sleep(1)
                    
        except Exception as e:
            logger.error(f"Kafka consumer error: {e}")
        finally:
            await self.stop()
    
    async def process_trading_order(self, order: Dict):
        """Process trading order for backtesting"""
        try:
            logger.info(f"Processing order: {order['order_id']} - {order['symbol']} {order['side']}")
            
            # Store order for historical analysis
            await self.store_historical_order(order)
            
            # Simulate order execution
            trade_result = await self.simulate_order_execution(order)
            
            if trade_result:
                # Update performance metrics
                await self.update_performance_metrics(trade_result)
                
                # Store historical trade
                await self.store_historical_trade(trade_result)
                
                # Run periodic backtesting
                if len(self.historical_trades) % 10 == 0:  # Every 10 trades
                    await self.run_periodic_backtest()
                    
        except Exception as e:
            logger.error(f"Error processing trading order: {e}")
    
    async def store_historical_order(self, order: Dict):
        """Store historical order data"""
        try:
            # Store in Redis for persistence
            order_key = f"historical_order:{order['order_id']}"
            self.redis_client.setex(
                order_key, 
                86400 * 30,  # 30 days TTL
                json.dumps(order, default=str)
            )
            
            # Store in memory
            self.historical_signals.append(order)
            
            logger.debug(f"Stored historical order: {order['order_id']}")
            
        except Exception as e:
            logger.error(f"Error storing historical order: {e}")
    
    async def simulate_order_execution(self, order: Dict) -> Optional[Dict]:
        """Simulate order execution for backtesting"""
        try:
            # Get market data for the symbol
            symbol = order['symbol']
            market_data = await self.get_market_data(symbol)
            
            if not market_data:
                logger.warning(f"No market data for {symbol}")
                return None
            
            # Simulate execution with slippage and commission
            execution_price = self.calculate_execution_price(order, market_data)
            commission = self.calculate_commission(order, execution_price)
            
            # Create trade result
            trade_result = {
                'trade_id': f"trade_{order['order_id']}",
                'symbol': symbol,
                'side': order['side'],
                'entry_time': datetime.now(),
                'exit_time': datetime.now() + timedelta(hours=1),  # Simulate 1 hour holding
                'entry_price': execution_price,
                'exit_price': execution_price,  # For now, same as entry
                'quantity': float(order['quantity']),
                'pnl': 0.0,  # Will be calculated later
                'commission': commission,
                'net_pnl': -commission,  # Initial net PnL is negative due to commission
                'holding_period': 1.0,  # 1 hour
                'signal_type': order.get('signal_type', 'UNKNOWN'),
                'signal_confidence': order.get('signal_confidence', 0.5)
            }
            
            return trade_result
            
        except Exception as e:
            logger.error(f"Order execution simulation error: {e}")
            return None
    
    async def get_market_data(self, symbol: str) -> Optional[List[Dict]]:
        """Get historical market data for symbol"""
        try:
            # Check cache first
            cache_key = f"market_data:{symbol}"
            cached_data = self.redis_client.get(cache_key)
            
            if cached_data:
                return json.loads(cached_data)
            
            # Generate mock market data if not available
            mock_data = self.generate_mock_market_data(symbol)
            
            # Cache for 1 hour
            self.redis_client.setex(
                cache_key,
                3600,  # 1 hour
                json.dumps(mock_data, default=str)
            )
            
            return mock_data
            
        except Exception as e:
            logger.error(f"Market data fetch error: {e}")
            return None
    
    def generate_mock_market_data(self, symbol: str) -> List[Dict]:
        """Generate mock market data for backtesting"""
        try:
            # Generate 100 days of mock data
            data = []
            base_price = 100.0
            
            # Different base prices for different symbols
            symbol_prices = {
                'BTCUSDT': 50000.0,
                'ETHUSDT': 3000.0,
                'SOLUSDT': 100.0,
                'ADAUSDT': 0.5,
                'DOTUSDT': 7.0,
                'LINKUSDT': 15.0,
                'UNIUSDT': 8.0,
                'AVAXUSDT': 25.0,
                'BNBUSDT': 300.0,
                'COINUSDT': 150.0
            }
            
            base_price = symbol_prices.get(symbol, 100.0)
            
            for i in range(100):
                # Generate price with random walk
                if i == 0:
                    price = base_price
                else:
                    # Random walk with 0.5% daily volatility
                    change = np.random.normal(0, 0.005)
                    price = data[i-1]['close'] * (1 + change)
                
                # Generate OHLC data
                high = price * (1 + abs(np.random.normal(0, 0.01)))
                low = price * (1 - abs(np.random.normal(0, 0.01)))
                open_price = price * (1 + np.random.normal(0, 0.005))
                close_price = price
                
                # Ensure high >= max(open, close) and low <= min(open, close)
                high = max(high, open_price, close_price)
                low = min(low, open_price, close_price)
                
                data.append({
                    'date': (datetime.now() - timedelta(days=100-i)).isoformat(),
                    'open': round(open_price, 4),
                    'high': round(high, 4),
                    'low': round(low, 4),
                    'close': round(close_price, 4),
                    'volume': np.random.randint(1000, 10000)
                })
            
            return data
            
        except Exception as e:
            logger.error(f"Mock market data generation error: {e}")
            return []
    
    def calculate_execution_price(self, order: Dict, market_data: List[Dict]) -> float:
        """Calculate execution price with slippage"""
        try:
            current_price = market_data[-1]['close']
            
            # Apply slippage based on order type
            if order['order_type'] == 'MARKET':
                # Market orders have higher slippage
                slippage = self.backtest_params['slippage'] * 2
            else:
                # Limit orders have lower slippage
                slippage = self.backtest_params['slippage']
            
            # Apply slippage based on side
            if order['side'] == 'BUY':
                execution_price = current_price * (1 + slippage)
            else:
                execution_price = current_price * (1 - slippage)
            
            return round(execution_price, 4)
            
        except Exception as e:
            logger.error(f"Execution price calculation error: {e}")
            return 100.0  # Default price
    
    def calculate_commission(self, order: Dict, execution_price: float) -> float:
        """Calculate commission for order"""
        try:
            order_value = float(order['quantity']) * execution_price
            commission = order_value * self.backtest_params['commission_rate']
            return round(commission, 4)
            
        except Exception as e:
            logger.error(f"Commission calculation error: {e}")
            return 0.0
    
    async def update_performance_metrics(self, trade_result: Dict):
        """Update performance metrics from trade result"""
        try:
            # Update capital
            self.current_capital += trade_result['net_pnl']
            
            # Update peak capital
            if self.current_capital > self.peak_capital:
                self.peak_capital = self.current_capital
            
            # Calculate drawdown
            drawdown = (self.peak_capital - self.current_capital) / self.peak_capital
            self.drawdown_series.append(drawdown)
            
            # Calculate return
            return_rate = trade_result['net_pnl'] / self.backtest_params['initial_capital']
            self.returns_series.append(return_rate)
            
            logger.debug(f"Updated performance: Capital: {self.current_capital:.2f}, Drawdown: {drawdown:.4f}")
            
        except Exception as e:
            logger.error(f"Performance metrics update error: {e}")
    
    async def store_historical_trade(self, trade_result: Dict):
        """Store historical trade data"""
        try:
            # Create HistoricalTrade object
            trade = HistoricalTrade(
                trade_id=trade_result['trade_id'],
                symbol=trade_result['symbol'],
                side=trade_result['side'],
                entry_time=trade_result['entry_time'],
                exit_time=trade_result['exit_time'],
                entry_price=trade_result['entry_price'],
                exit_price=trade_result['exit_price'],
                quantity=trade_result['quantity'],
                pnl=trade_result['pnl'],
                commission=trade_result['commission'],
                net_pnl=trade_result['net_pnl'],
                holding_period=trade_result['holding_period'],
                signal_type=trade_result['signal_type'],
                signal_confidence=trade_result['signal_confidence']
            )
            
            # Store in memory
            self.historical_trades.append(trade)
            
            # Store in Redis
            trade_key = f"historical_trade:{trade_result['trade_id']}"
            self.redis_client.setex(
                trade_key,
                86400 * 30,  # 30 days TTL
                json.dumps(asdict(trade), default=str)
            )
            
            logger.debug(f"Stored historical trade: {trade_result['trade_id']}")
            
        except Exception as e:
            logger.error(f"Error storing historical trade: {e}")
    
    async def run_periodic_backtest(self):
        """Run periodic backtesting analysis"""
        try:
            logger.info("Running periodic backtesting analysis...")
            
            # Calculate performance metrics
            metrics = await self.calculate_performance_metrics()
            
            # Generate backtest report
            report = await self.generate_backtest_report(metrics)
            
            # Publish report to Kafka
            await self.publish_backtest_report(report)
            
            # Store metrics in Redis
            await self.store_backtest_metrics(metrics)
            
            logger.info("Periodic backtesting completed")
            
        except Exception as e:
            logger.error(f"Periodic backtesting error: {e}")
    
    async def calculate_performance_metrics(self) -> BacktestMetrics:
        """Calculate comprehensive performance metrics"""
        try:
            if not self.returns_series:
                return self.create_empty_metrics()
            
            returns = np.array(self.returns_series)
            
            # Basic metrics
            total_return = (self.current_capital - self.backtest_params['initial_capital']) / self.backtest_params['initial_capital']
            total_trades = len(self.historical_trades)
            
            # Calculate winning/losing trades
            winning_trades = [t for t in self.historical_trades if t.net_pnl > 0]
            losing_trades = [t for t in self.historical_trades if t.net_pnl < 0]
            
            winning_trades_count = len(winning_trades)
            losing_trades_count = len(losing_trades)
            
            # Win rate
            win_rate = winning_trades_count / total_trades if total_trades > 0 else 0.0
            
            # Average win/loss
            avg_win = np.mean([t.net_pnl for t in winning_trades]) if winning_trades else 0.0
            avg_loss = np.mean([t.net_pnl for t in losing_trades]) if losing_trades else 0.0
            
            # Largest win/loss
            largest_win = max([t.net_pnl for t in winning_trades]) if winning_trades else 0.0
            largest_loss = min([t.net_pnl for t in losing_trades]) if losing_trades else 0.0
            
            # Profit factor
            total_wins = sum([t.net_pnl for t in winning_trades])
            total_losses = abs(sum([t.net_pnl for t in losing_trades]))
            profit_factor = total_wins / total_losses if total_losses > 0 else float('inf')
            
            # Volatility and Sharpe ratio
            volatility = np.std(returns) * np.sqrt(252) if len(returns) > 1 else 0.0  # Annualized
            sharpe_ratio = np.mean(returns) / np.std(returns) * np.sqrt(252) if np.std(returns) > 0 else 0.0
            
            # Sortino ratio (downside deviation)
            downside_returns = returns[returns < 0]
            downside_deviation = np.std(downside_returns) * np.sqrt(252) if len(downside_returns) > 1 else 0.0
            sortino_ratio = np.mean(returns) / downside_deviation * np.sqrt(252) if downside_deviation > 0 else 0.0
            
            # Maximum drawdown
            max_drawdown = max(self.drawdown_series) if self.drawdown_series else 0.0
            
            # Calmar ratio
            annualized_return = total_return * 252 / len(returns) if len(returns) > 0 else 0.0
            calmar_ratio = annualized_return / abs(max_drawdown) if max_drawdown != 0 else 0.0
            
            # Value at Risk
            var_95 = np.percentile(returns, 5) if len(returns) > 0 else 0.0
            var_99 = np.percentile(returns, 1) if len(returns) > 0 else 0.0
            
            # Consecutive wins/losses
            consecutive_wins = self.calculate_consecutive_wins()
            consecutive_losses = self.calculate_consecutive_losses()
            
            return BacktestMetrics(
                total_return=total_return,
                annualized_return=annualized_return,
                volatility=volatility,
                sharpe_ratio=sharpe_ratio,
                max_drawdown=max_drawdown,
                win_rate=win_rate,
                profit_factor=profit_factor,
                calmar_ratio=calmar_ratio,
                sortino_ratio=sortino_ratio,
                var_95=var_95,
                var_99=var_99,
                total_trades=total_trades,
                winning_trades=winning_trades_count,
                losing_trades=losing_trades_count,
                avg_win=avg_win,
                avg_loss=avg_loss,
                largest_win=largest_win,
                largest_loss=largest_loss,
                consecutive_wins=consecutive_wins,
                consecutive_losses=consecutive_losses
            )
            
        except Exception as e:
            logger.error(f"Performance metrics calculation error: {e}")
            return self.create_empty_metrics()
    
    def create_empty_metrics(self) -> BacktestMetrics:
        """Create empty metrics when no data available"""
        return BacktestMetrics(
            total_return=0.0,
            annualized_return=0.0,
            volatility=0.0,
            sharpe_ratio=0.0,
            max_drawdown=0.0,
            win_rate=0.0,
            profit_factor=0.0,
            calmar_ratio=0.0,
            sortino_ratio=0.0,
            var_95=0.0,
            var_99=0.0,
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            avg_win=0.0,
            avg_loss=0.0,
            largest_win=0.0,
            largest_loss=0.0,
            consecutive_wins=0,
            consecutive_losses=0
        )
    
    def calculate_consecutive_wins(self) -> int:
        """Calculate consecutive winning trades"""
        try:
            max_consecutive = 0
            current_consecutive = 0
            
            for trade in self.historical_trades:
                if trade.net_pnl > 0:
                    current_consecutive += 1
                    max_consecutive = max(max_consecutive, current_consecutive)
                else:
                    current_consecutive = 0
            
            return max_consecutive
            
        except Exception as e:
            logger.error(f"Consecutive wins calculation error: {e}")
            return 0
    
    def calculate_consecutive_losses(self) -> int:
        """Calculate consecutive losing trades"""
        try:
            max_consecutive = 0
            current_consecutive = 0
            
            for trade in self.historical_trades:
                if trade.net_pnl < 0:
                    current_consecutive += 1
                    max_consecutive = max(max_consecutive, current_consecutive)
                else:
                    current_consecutive = 0
            
            return max_consecutive
            
        except Exception as e:
            logger.error(f"Consecutive losses calculation error: {e}")
            return 0
    
    async def generate_backtest_report(self, metrics: BacktestMetrics) -> Dict:
        """Generate comprehensive backtest report"""
        try:
            report = {
                'report_id': f"backtest_report_{int(time.time())}",
                'timestamp': datetime.now().isoformat(),
                'backtest_period': {
                    'start_date': self.backtest_params['start_date'],
                    'end_date': self.backtest_params['end_date'],
                    'total_days': 365  # Mock value
                },
                'capital_metrics': {
                    'initial_capital': self.backtest_params['initial_capital'],
                    'final_capital': self.current_capital,
                    'peak_capital': self.peak_capital,
                    'total_return': metrics.total_return,
                    'annualized_return': metrics.annualized_return
                },
                'risk_metrics': {
                    'volatility': metrics.volatility,
                    'max_drawdown': metrics.max_drawdown,
                    'var_95': metrics.var_95,
                    'var_99': metrics.var_99,
                    'sharpe_ratio': metrics.sharpe_ratio,
                    'sortino_ratio': metrics.sortino_ratio,
                    'calmar_ratio': metrics.calmar_ratio
                },
                'trading_metrics': {
                    'total_trades': metrics.total_trades,
                    'winning_trades': metrics.winning_trades,
                    'losing_trades': metrics.losing_trades,
                    'win_rate': metrics.win_rate,
                    'profit_factor': metrics.profit_factor,
                    'avg_win': metrics.avg_win,
                    'avg_loss': metrics.avg_loss,
                    'largest_win': metrics.largest_win,
                    'largest_loss': metrics.largest_loss,
                    'consecutive_wins': metrics.consecutive_wins,
                    'consecutive_losses': metrics.consecutive_losses
                },
                'performance_grade': self.calculate_performance_grade(metrics),
                'recommendations': self.generate_recommendations(metrics)
            }
            
            return report
            
        except Exception as e:
            logger.error(f"Backtest report generation error: {e}")
            return {}
    
    def calculate_performance_grade(self, metrics: BacktestMetrics) -> str:
        """Calculate performance grade (A-F)"""
        try:
            score = 0
            
            # Sharpe ratio scoring
            if metrics.sharpe_ratio >= 2.0:
                score += 25
            elif metrics.sharpe_ratio >= 1.5:
                score += 20
            elif metrics.sharpe_ratio >= 1.0:
                score += 15
            elif metrics.sharpe_ratio >= 0.5:
                score += 10
            else:
                score += 5
            
            # Win rate scoring
            if metrics.win_rate >= 0.7:
                score += 25
            elif metrics.win_rate >= 0.6:
                score += 20
            elif metrics.win_rate >= 0.5:
                score += 15
            elif metrics.win_rate >= 0.4:
                score += 10
            else:
                score += 5
            
            # Drawdown scoring
            if metrics.max_drawdown <= 0.05:
                score += 25
            elif metrics.max_drawdown <= 0.10:
                score += 20
            elif metrics.max_drawdown <= 0.15:
                score += 15
            elif metrics.max_drawdown <= 0.20:
                score += 10
            else:
                score += 5
            
            # Profit factor scoring
            if metrics.profit_factor >= 2.0:
                score += 25
            elif metrics.profit_factor >= 1.5:
                score += 20
            elif metrics.profit_factor >= 1.2:
                score += 15
            elif metrics.profit_factor >= 1.0:
                score += 10
            else:
                score += 5
            
            # Grade assignment
            if score >= 90:
                return 'A'
            elif score >= 80:
                return 'B'
            elif score >= 70:
                return 'C'
            elif score >= 60:
                return 'D'
            else:
                return 'F'
                
        except Exception as e:
            logger.error(f"Performance grade calculation error: {e}")
            return 'F'
    
    def generate_recommendations(self, metrics: BacktestMetrics) -> List[str]:
        """Generate trading recommendations based on metrics"""
        try:
            recommendations = []
            
            # Sharpe ratio recommendations
            if metrics.sharpe_ratio < 1.0:
                recommendations.append("Consider improving risk-adjusted returns by reducing volatility or increasing returns")
            
            # Win rate recommendations
            if metrics.win_rate < 0.5:
                recommendations.append("Focus on improving signal quality to increase win rate")
            
            # Drawdown recommendations
            if metrics.max_drawdown > 0.15:
                recommendations.append("Implement stricter risk management to reduce maximum drawdown")
            
            # Profit factor recommendations
            if metrics.profit_factor < 1.5:
                recommendations.append("Work on improving risk-reward ratio per trade")
            
            # Consecutive losses recommendations
            if metrics.consecutive_losses > 5:
                recommendations.append("Consider implementing circuit breakers after consecutive losses")
            
            # General recommendations
            if metrics.total_trades < 30:
                recommendations.append("Increase sample size for more reliable backtesting results")
            
            if not recommendations:
                recommendations.append("Strategy performance is good, consider gradual position sizing increases")
            
            return recommendations
            
        except Exception as e:
            logger.error(f"Recommendations generation error: {e}")
            return ["Error generating recommendations"]
    
    async def publish_backtest_report(self, report: Dict):
        """Publish backtest report to Kafka"""
        try:
            future = self.kafka_producer.send(
                'backtesting.reports.v1',
                value=report,
                key=report['report_id'].encode()
            )
            await asyncio.get_event_loop().run_in_executor(None, future.get, 10)
            logger.info(f"Published backtest report: {report['report_id']}")
        except Exception as e:
            logger.error(f"Error publishing backtest report: {e}")
    
    async def store_backtest_metrics(self, metrics: BacktestMetrics):
        """Store backtest metrics in Redis"""
        try:
            metrics_key = f"backtest_metrics:{int(time.time())}"
            self.redis_client.setex(
                metrics_key,
                86400 * 7,  # 7 days TTL
                json.dumps(asdict(metrics), default=str)
            )
            
            logger.debug("Stored backtest metrics in Redis")
            
        except Exception as e:
            logger.error(f"Error storing backtest metrics: {e}")
    
    async def stop(self):
        """Stop backtesting service"""
        logger.info("Stopping Backtesting Service...")
        self.running = False
        
        # Close Kafka connections
        self.kafka_producer.close()
        self.kafka_consumer.close()
        
        # Close Redis connection
        self.redis_client.close()

async def main():
    """Main entry point"""
    service = BacktestingService()
    
    try:
        await service.start_backtesting()
    except KeyboardInterrupt:
        logger.info("Shutting down Backtesting Service")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
    finally:
        await service.stop()

if __name__ == "__main__":
    asyncio.run(main())
