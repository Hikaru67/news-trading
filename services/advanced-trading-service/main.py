import asyncio
import json
import logging
import hashlib
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

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class OrderType(Enum):
    """Advanced order types"""
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"
    TRAILING_STOP = "TRAILING_STOP"
    OCO = "OCO"  # One-Cancels-Other
    TIME_BASED = "TIME_BASED"
    CONDITIONAL = "CONDITIONAL"

class OrderStatus(Enum):
    """Order status"""
    PENDING = "PENDING"
    PARTIAL_FILLED = "PARTIAL_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"

class RiskLevel(Enum):
    """Risk levels"""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    EXTREME = "EXTREME"

@dataclass
class Order:
    """Advanced order structure"""
    order_id: str
    symbol: str
    side: str  # BUY/SELL
    order_type: OrderType
    quantity: float
    price: Optional[float] = None
    stop_price: Optional[float] = None
    take_profit_price: Optional[float] = None
    trailing_stop_percent: Optional[float] = None
    time_in_force: str = "GTC"  # Good Till Cancelled
    expiry_time: Optional[datetime] = None
    conditions: Optional[Dict] = None
    status: OrderStatus = OrderStatus.PENDING
    filled_quantity: float = 0.0
    avg_fill_price: float = 0.0
    created_at: datetime = None
    updated_at: datetime = None
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now()
        if self.updated_at is None:
            self.updated_at = datetime.now()

@dataclass
class Position:
    """Position tracking"""
    symbol: str
    side: str  # LONG/SHORT
    quantity: float
    avg_entry_price: float
    current_price: float
    unrealized_pnl: float
    realized_pnl: float
    total_pnl: float
    margin_used: float
    leverage: float
    created_at: datetime
    updated_at: datetime

@dataclass
class Portfolio:
    """Portfolio management"""
    total_value: float
    available_balance: float
    margin_used: float
    unrealized_pnl: float
    realized_pnl: float
    total_pnl: float
    daily_pnl: float
    daily_return: float
    max_drawdown: float
    sharpe_ratio: float
    positions: List[Position]
    risk_metrics: Dict
    updated_at: datetime

class AdvancedTradingService:
    """Advanced trading service with multiple order types and portfolio management"""
    
    def __init__(self):
        self.kafka_producer = KafkaProducer(
            bootstrap_servers=os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'kafka:29092'),
            value_serializer=lambda v: json.dumps(v, default=str).encode('utf-8'),
            key_serializer=lambda k: k.encode('utf-8') if k else None
        )
        
        self.kafka_consumer = KafkaConsumer(
            'ai.enhanced.signals.v1',
            bootstrap_servers=os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'kafka:29092'),
            value_deserializer=lambda m: json.loads(m.decode('utf-8')),
            group_id='advanced_trading_service',
            auto_offset_reset='latest'
        )
        
        self.redis_client = redis.Redis.from_url(
            os.getenv('REDIS_URL', 'redis://redis:6379')
        )
        
        # Portfolio state
        self.portfolio = Portfolio(
            total_value=10000.0,  # Starting with $10,000
            available_balance=10000.0,
            margin_used=0.0,
            unrealized_pnl=0.0,
            realized_pnl=0.0,
            total_pnl=0.0,
            daily_pnl=0.0,
            daily_return=0.0,
            max_drawdown=0.0,
            sharpe_ratio=0.0,
            positions=[],
            risk_metrics={},
            updated_at=datetime.now()
        )
        
        # Active orders
        self.active_orders: Dict[str, Order] = {}
        
        # Order history
        self.order_history: List[Order] = []
        
        # Risk management parameters
        self.risk_limits = {
            'max_position_size': 0.2,  # 20% of portfolio per position
            'max_daily_loss': 0.05,    # 5% daily loss limit
            'max_drawdown': 0.15,      # 15% max drawdown
            'max_leverage': 3.0,       # 3x leverage max
            'position_correlation_limit': 0.7  # 70% correlation limit
        }
        
        # Market data cache
        self.market_data_cache = {}
        
        self.running = False
        
    async def start_trading(self):
        """Start advanced trading service"""
        logger.info("Starting Advanced Trading Service...")
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
                                # Process enhanced signal
                                signal = message.value
                                await self.process_trading_signal(signal)
                                
                            except Exception as e:
                                logger.error(f"Error processing signal: {e}")
                    
                    # Small delay to prevent busy waiting
                    await asyncio.sleep(0.1)
                    
                except Exception as e:
                    logger.error(f"Error in message polling: {e}")
                    await asyncio.sleep(1)
                    
        except Exception as e:
            logger.error(f"Kafka consumer error: {e}")
        finally:
            await self.stop()
    
    async def process_trading_signal(self, signal: Dict):
        """Process trading signal and execute advanced orders"""
        try:
            logger.info(f"Processing signal: {signal['event_type']} for {signal['primary_entity']}")
            
            # 1. Risk assessment
            risk_assessment = await self.assess_risk(signal)
            if not risk_assessment['can_trade']:
                logger.warning(f"Risk limit exceeded: {risk_assessment['reason']}")
                return
            
            # 2. Strategy selection
            strategy = await self.select_trading_strategy(signal)
            
            # 3. Order generation
            orders = await self.generate_orders(signal, strategy)
            
            # 4. Risk validation
            validated_orders = await self.validate_orders(orders)
            
            # 5. Order execution
            for order in validated_orders:
                await self.execute_order(order)
            
            # 6. Portfolio update
            await self.update_portfolio()
            
            # 7. Risk monitoring
            await self.monitor_risk()
            
        except Exception as e:
            logger.error(f"Error processing trading signal: {e}")
    
    async def assess_risk(self, signal: Dict) -> Dict:
        """Assess risk before trading"""
        try:
            risk_assessment = {
                'can_trade': True,
                'risk_level': RiskLevel.LOW,
                'reason': None,
                'risk_score': 0.0
            }
            
            # Check daily loss limit
            if self.portfolio.daily_pnl < -(self.portfolio.total_value * self.risk_limits['max_daily_loss']):
                risk_assessment['can_trade'] = False
                risk_assessment['reason'] = 'Daily loss limit exceeded'
                risk_assessment['risk_level'] = RiskLevel.EXTREME
                return risk_assessment
            
            # Check max drawdown
            if self.portfolio.max_drawdown > self.risk_limits['max_drawdown']:
                risk_assessment['can_trade'] = False
                risk_assessment['reason'] = 'Max drawdown exceeded'
                risk_assessment['risk_level'] = RiskLevel.HIGH
                return risk_assessment
            
            # Calculate risk score based on signal
            signal_risk = self.calculate_signal_risk(signal)
            risk_assessment['risk_score'] = signal_risk
            
            # Determine risk level
            if signal_risk < 0.3:
                risk_assessment['risk_level'] = RiskLevel.LOW
            elif signal_risk < 0.6:
                risk_assessment['risk_level'] = RiskLevel.MEDIUM
            elif signal_risk < 0.8:
                risk_assessment['risk_level'] = RiskLevel.HIGH
            else:
                risk_assessment['risk_level'] = RiskLevel.EXTREME
                risk_assessment['can_trade'] = False
                risk_assessment['reason'] = 'Signal risk too high'
            
            return risk_assessment
            
        except Exception as e:
            logger.error(f"Risk assessment error: {e}")
            return {'can_trade': False, 'reason': 'Risk assessment failed'}
    
    def calculate_signal_risk(self, signal: Dict) -> float:
        """Calculate risk score for signal"""
        try:
            risk_score = 0.0
            
            # Base risk from severity
            risk_score += signal.get('severity', 0.5) * 0.4
            
            # AI confidence adjustment
            ai_confidence = signal.get('ai_confidence', 0.5)
            risk_score += (1.0 - ai_confidence) * 0.3
            
            # Event type risk
            event_risk = {
                'HACK': 0.9,
                'REGULATION': 0.8,
                'DELIST': 0.7,
                'FED_SPEECH': 0.6,
                'LISTING': 0.4,
                'PARTNERSHIP': 0.3,
                'TECHNICAL_UPGRADE': 0.3,
                'POLITICAL_POST': 0.5
            }
            event_type = signal.get('event_type', 'OTHER')
            risk_score += event_risk.get(event_type, 0.5) * 0.3
            
            return min(1.0, max(0.0, risk_score))
            
        except Exception as e:
            logger.error(f"Signal risk calculation error: {e}")
            return 0.5
    
    async def select_trading_strategy(self, signal: Dict) -> Dict:
        """Select trading strategy based on signal"""
        try:
            strategy = {
                'order_types': [],
                'position_size': 0.0,
                'entry_price': 0.0,
                'stop_loss': 0.0,
                'take_profit': 0.0,
                'timeframe': 'SHORT_TERM'
            }
            
            event_type = signal.get('event_type', 'OTHER')
            direction = signal.get('direction', 'NEUTRAL')
            severity = signal.get('severity', 0.5)
            
            # Strategy selection based on event type
            if event_type == 'LISTING':
                strategy['order_types'] = [OrderType.LIMIT, OrderType.STOP_LOSS]
                strategy['position_size'] = self.calculate_position_size(severity, 0.1)
                strategy['timeframe'] = 'MEDIUM_TERM'
                
            elif event_type == 'HACK':
                if direction == 'BEAR':
                    strategy['order_types'] = [OrderType.MARKET, OrderType.STOP_LOSS]
                    strategy['position_size'] = self.calculate_position_size(severity, 0.15)
                    strategy['timeframe'] = 'SHORT_TERM'
                    
            elif event_type == 'REGULATION':
                if direction == 'BEAR':
                    strategy['order_types'] = [OrderType.LIMIT, OrderType.STOP_LOSS]
                    strategy['position_size'] = self.calculate_position_size(severity, 0.12)
                    strategy['timeframe'] = 'MEDIUM_TERM'
                    
            elif event_type == 'FED_SPEECH':
                strategy['order_types'] = [OrderType.LIMIT, OrderType.OCO]
                strategy['position_size'] = self.calculate_position_size(severity, 0.08)
                strategy['timeframe'] = 'SHORT_TERM'
                
            else:
                # Default strategy
                strategy['order_types'] = [OrderType.LIMIT]
                strategy['position_size'] = self.calculate_position_size(severity, 0.05)
                strategy['timeframe'] = 'SHORT_TERM'
            
            return strategy
            
        except Exception as e:
            logger.error(f"Strategy selection error: {e}")
            return {'order_types': [OrderType.LIMIT], 'position_size': 0.05}
    
    def calculate_position_size(self, severity: float, base_size: float) -> float:
        """Calculate position size based on severity and risk limits"""
        try:
            # Base position size
            position_size = base_size * severity
            
            # Apply risk limits
            max_size = self.risk_limits['max_position_size']
            position_size = min(position_size, max_size)
            
            # Adjust based on portfolio value
            portfolio_value = self.portfolio.total_value
            if portfolio_value > 0:
                position_size = min(position_size, portfolio_value * 0.1)  # Max 10% per trade
            
            return position_size
            
        except Exception as e:
            logger.error(f"Position size calculation error: {e}")
            return 0.05  # Default 5%
    
    async def generate_orders(self, signal: Dict, strategy: Dict) -> List[Order]:
        """Generate orders based on strategy"""
        try:
            orders = []
            symbol = self.map_entity_to_symbol(signal.get('primary_entity', ''))
            
            if not symbol:
                logger.warning(f"No symbol mapping for entity: {signal.get('primary_entity', '')}")
                return []
            
            # Get current market price
            current_price = await self.get_market_price(symbol)
            if not current_price:
                logger.warning(f"Could not get market price for {symbol}")
                return []
            
            # Calculate order parameters
            quantity = strategy['position_size'] / current_price
            direction = signal.get('direction', 'NEUTRAL')
            
            # Generate main order
            if OrderType.LIMIT in strategy['order_types']:
                limit_price = self.calculate_limit_price(current_price, direction)
                main_order = Order(
                    order_id=f"order_{symbol}_{int(time.time())}",
                    symbol=symbol,
                    side='BUY' if direction == 'BULL' else 'SELL',
                    order_type=OrderType.LIMIT,
                    quantity=quantity,
                    price=limit_price,
                    time_in_force='GTC'
                )
                orders.append(main_order)
            
            # Generate stop-loss order
            if OrderType.STOP_LOSS in strategy['order_types']:
                stop_price = self.calculate_stop_loss_price(current_price, direction)
                stop_order = Order(
                    order_id=f"stop_{symbol}_{int(time.time())}",
                    symbol=symbol,
                    side='SELL' if direction == 'BULL' else 'BUY',
                    order_type=OrderType.STOP_LOSS,
                    quantity=quantity,
                    stop_price=stop_price,
                    time_in_force='GTC'
                )
                orders.append(stop_order)
            
            # Generate take-profit order
            if OrderType.TAKE_PROFIT in strategy['order_types']:
                take_profit_price = self.calculate_take_profit_price(current_price, direction)
                tp_order = Order(
                    order_id=f"tp_{symbol}_{int(time.time())}",
                    symbol=symbol,
                    side='SELL' if direction == 'BULL' else 'BUY',
                    order_type=OrderType.TAKE_PROFIT,
                    quantity=quantity,
                    price=take_profit_price,
                    time_in_force='GTC'
                )
                orders.append(tp_order)
            
            # Generate OCO order
            if OrderType.OCO in strategy['order_types']:
                oco_order = Order(
                    order_id=f"oco_{symbol}_{int(time.time())}",
                    symbol=symbol,
                    side='SELL' if direction == 'BULL' else 'BUY',
                    order_type=OrderType.OCO,
                    quantity=quantity,
                    stop_price=stop_price,
                    take_profit_price=take_profit_price,
                    time_in_force='GTC'
                )
                orders.append(oco_order)
            
            return orders
            
        except Exception as e:
            logger.error(f"Order generation error: {e}")
            return []
    
    def map_entity_to_symbol(self, entity: str) -> Optional[str]:
        """Map entity to trading symbol"""
        symbol_mapping = {
            'BTC': 'BTCUSDT',
            'ETH': 'ETHUSDT',
            'SOL': 'SOLUSDT',
            'ADA': 'ADAUSDT',
            'DOT': 'DOTUSDT',
            'LINK': 'LINKUSDT',
            'UNI': 'UNIUSDT',
            'AVAX': 'AVAXUSDT',
            'BINANCE': 'BNBUSDT',
            'COINBASE': 'COINUSDT'
        }
        return symbol_mapping.get(entity.upper())
    
    async def get_market_price(self, symbol: str) -> Optional[float]:
        """Get current market price"""
        try:
            # Check cache first
            cache_key = f"market_price:{symbol}"
            cached_price = self.redis_client.get(cache_key)
            
            if cached_price:
                return float(cached_price)
            
            # TODO: Implement real market data fetching
            # For now, use mock prices
            mock_prices = {
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
            
            price = mock_prices.get(symbol, 100.0)
            
            # Cache price for 1 minute
            self.redis_client.setex(cache_key, 60, str(price))
            
            return price
            
        except Exception as e:
            logger.error(f"Market price fetch error: {e}")
            return None
    
    def calculate_limit_price(self, current_price: float, direction: str) -> float:
        """Calculate limit price based on direction"""
        if direction == 'BULL':
            return current_price * 1.001  # Slightly above current price
        elif direction == 'BEAR':
            return current_price * 0.999  # Slightly below current price
        else:
            return current_price
    
    def calculate_stop_loss_price(self, current_price: float, direction: str) -> float:
        """Calculate stop-loss price"""
        if direction == 'BULL':
            return current_price * 0.95  # 5% below current price
        elif direction == 'BEAR':
            return current_price * 1.05  # 5% above current price
        else:
            return current_price * 0.98
    
    def calculate_take_profit_price(self, current_price: float, direction: str) -> float:
        """Calculate take-profit price"""
        if direction == 'BULL':
            return current_price * 1.10  # 10% above current price
        elif direction == 'BEAR':
            return current_price * 0.90  # 10% below current price
        else:
            return current_price * 1.05
    
    async def validate_orders(self, orders: List[Order]) -> List[Order]:
        """Validate orders against risk limits"""
        try:
            validated_orders = []
            
            for order in orders:
                # Check position size limit
                if order.quantity * await self.get_market_price(order.symbol) > self.portfolio.total_value * self.risk_limits['max_position_size']:
                    logger.warning(f"Order {order.order_id} exceeds position size limit")
                    continue
                
                # Check leverage limit
                if order.quantity * await self.get_market_price(order.symbol) > self.portfolio.available_balance * self.risk_limits['max_leverage']:
                    logger.warning(f"Order {order.order_id} exceeds leverage limit")
                    continue
                
                # Check correlation limit
                if not await self.check_correlation_limit(order):
                    logger.warning(f"Order {order.order_id} exceeds correlation limit")
                    continue
                
                validated_orders.append(order)
            
            return validated_orders
            
        except Exception as e:
            logger.error(f"Order validation error: {e}")
            return []
    
    async def check_correlation_limit(self, order: Order) -> bool:
        """Check if order exceeds correlation limit"""
        try:
            # TODO: Implement correlation calculation
            # For now, allow all orders
            return True
        except Exception as e:
            logger.error(f"Correlation check error: {e}")
            return True
    
    async def execute_order(self, order: Order):
        """Execute order"""
        try:
            logger.info(f"Executing order: {order.order_id} - {order.symbol} {order.side} {order.quantity}")
            
            # Add to active orders
            self.active_orders[order.order_id] = order
            
            # Add to order history
            self.order_history.append(order)
            
            # Publish order to Kafka
            await self.publish_order(order)
            
            # Simulate order execution (in production, this would call exchange API)
            await self.simulate_order_execution(order)
            
        except Exception as e:
            logger.error(f"Order execution error: {e}")
    
    async def simulate_order_execution(self, order: Order):
        """Simulate order execution for testing"""
        try:
            # Simulate execution delay
            await asyncio.sleep(2)
            
            # Update order status
            if order.order_type == OrderType.MARKET:
                order.status = OrderStatus.FILLED
                order.filled_quantity = order.quantity
                order.avg_fill_price = await self.get_market_price(order.symbol)
            else:
                order.status = OrderStatus.PENDING
            
            order.updated_at = datetime.now()
            
            # Update active orders
            if order.status == OrderStatus.FILLED:
                del self.active_orders[order.order_id]
                
                # Update portfolio
                await self.update_portfolio_from_order(order)
            
            logger.info(f"Order {order.order_id} status: {order.status}")
            
        except Exception as e:
            logger.error(f"Order execution simulation error: {e}")
    
    async def update_portfolio_from_order(self, order: Order):
        """Update portfolio from executed order"""
        try:
            # Calculate order value
            order_value = order.filled_quantity * order.avg_fill_price
            
            if order.side == 'BUY':
                # Opening long position
                self.portfolio.available_balance -= order_value
                self.portfolio.margin_used += order_value
                
                # Add position
                position = Position(
                    symbol=order.symbol,
                    side='LONG',
                    quantity=order.filled_quantity,
                    avg_entry_price=order.avg_fill_price,
                    current_price=order.avg_fill_price,
                    unrealized_pnl=0.0,
                    realized_pnl=0.0,
                    total_pnl=0.0,
                    margin_used=order_value,
                    leverage=1.0,
                    created_at=datetime.now(),
                    updated_at=datetime.now()
                )
                self.portfolio.positions.append(position)
                
            elif order.side == 'SELL':
                # Closing position or short selling
                # TODO: Implement position closing logic
                pass
            
            # Update portfolio timestamp
            self.portfolio.updated_at = datetime.now()
            
        except Exception as e:
            logger.error(f"Portfolio update error: {e}")
    
    async def update_portfolio(self):
        """Update portfolio metrics"""
        try:
            # Update position P&L
            total_unrealized_pnl = 0.0
            total_margin_used = 0.0
            
            for position in self.portfolio.positions:
                current_price = await self.get_market_price(position.symbol)
                if current_price:
                    position.current_price = current_price
                    if position.side == 'LONG':
                        position.unrealized_pnl = (current_price - position.avg_entry_price) * position.quantity
                    else:
                        position.unrealized_pnl = (position.avg_entry_price - current_price) * position.quantity
                    
                    total_unrealized_pnl += position.unrealized_pnl
                    total_margin_used += position.margin_used
                    position.updated_at = datetime.now()
            
            # Update portfolio metrics
            self.portfolio.unrealized_pnl = total_unrealized_pnl
            self.portfolio.margin_used = total_margin_used
            self.portfolio.total_pnl = self.portfolio.realized_pnl + total_unrealized_pnl
            self.portfolio.total_value = self.portfolio.available_balance + total_margin_used + total_unrealized_pnl
            
            # Calculate daily metrics
            await self.calculate_daily_metrics()
            
            # Calculate risk metrics
            await self.calculate_risk_metrics()
            
            # Update timestamp
            self.portfolio.updated_at = datetime.now()
            
        except Exception as e:
            logger.error(f"Portfolio update error: {e}")
    
    async def calculate_daily_metrics(self):
        """Calculate daily portfolio metrics"""
        try:
            # TODO: Implement daily P&L calculation
            # For now, use mock values
            self.portfolio.daily_pnl = self.portfolio.total_pnl * 0.1
            self.portfolio.daily_return = self.portfolio.daily_pnl / self.portfolio.total_value if self.portfolio.total_value > 0 else 0.0
            
        except Exception as e:
            logger.error(f"Daily metrics calculation error: {e}")
    
    async def calculate_risk_metrics(self):
        """Calculate portfolio risk metrics"""
        try:
            # Calculate max drawdown
            if self.portfolio.total_value > 0:
                drawdown = (self.portfolio.total_value - 10000.0) / 10000.0  # Starting value
                self.portfolio.max_drawdown = min(self.portfolio.max_drawdown, drawdown)
            
            # Calculate Sharpe ratio (simplified)
            if self.portfolio.daily_return != 0:
                self.portfolio.sharpe_ratio = self.portfolio.daily_return / 0.02  # Assuming 2% daily volatility
            
            # Store risk metrics
            self.portfolio.risk_metrics = {
                'var_95': self.calculate_var(0.95),
                'var_99': self.calculate_var(0.99),
                'volatility': 0.02,  # Mock volatility
                'beta': 1.0,  # Mock beta
                'correlation': 0.5  # Mock correlation
            }
            
        except Exception as e:
            logger.error(f"Risk metrics calculation error: {e}")
    
    def calculate_var(self, confidence: float) -> float:
        """Calculate Value at Risk"""
        try:
            # Simplified VaR calculation
            portfolio_value = self.portfolio.total_value
            volatility = 0.02  # 2% daily volatility
            z_score = 1.65 if confidence == 0.95 else 2.33  # 95% or 99% confidence
            
            var = portfolio_value * volatility * z_score
            return var
            
        except Exception as e:
            logger.error(f"VaR calculation error: {e}")
            return 0.0
    
    async def monitor_risk(self):
        """Monitor portfolio risk"""
        try:
            # Check risk limits
            if self.portfolio.daily_pnl < -(self.portfolio.total_value * self.risk_limits['max_daily_loss']):
                logger.warning("Daily loss limit exceeded - stopping trading")
                await self.stop_trading()
                
            if self.portfolio.max_drawdown < -self.risk_limits['max_drawdown']:
                logger.warning("Max drawdown exceeded - stopping trading")
                await self.stop_trading()
                
            # Check position limits
            for position in self.portfolio.positions:
                position_value = position.quantity * position.current_price
                if position_value > self.portfolio.total_value * self.risk_limits['max_position_size']:
                    logger.warning(f"Position {position.symbol} exceeds size limit")
                    await self.close_position(position)
                    
        except Exception as e:
            logger.error(f"Risk monitoring error: {e}")
    
    async def close_position(self, position: Position):
        """Close position"""
        try:
            logger.info(f"Closing position: {position.symbol}")
            
            # Create closing order
            close_order = Order(
                order_id=f"close_{position.symbol}_{int(time.time())}",
                symbol=position.symbol,
                side='SELL' if position.side == 'LONG' else 'BUY',
                order_type=OrderType.MARKET,
                quantity=position.quantity,
                time_in_force='IOC'  # Immediate or Cancel
            )
            
            # Execute closing order
            await self.execute_order(close_order)
            
            # Remove from portfolio
            self.portfolio.positions.remove(position)
            
        except Exception as e:
            logger.error(f"Position closing error: {e}")
    
    async def stop_trading(self):
        """Stop trading due to risk limits"""
        try:
            logger.warning("Stopping trading due to risk limits")
            
            # Close all positions
            for position in self.portfolio.positions.copy():
                await self.close_position(position)
            
            # Cancel all active orders
            for order in self.active_orders.copy():
                order.status = OrderStatus.CANCELLED
                del self.active_orders[order.order_id]
            
            # Publish trading stopped event
            await self.publish_trading_stopped()
            
        except Exception as e:
            logger.error(f"Stop trading error: {e}")
    
    async def publish_order(self, order: Order):
        """Publish order to Kafka"""
        try:
            future = self.kafka_producer.send(
                'trading.orders.v1',
                value=asdict(order),
                key=order.order_id
            )
            await asyncio.get_event_loop().run_in_executor(None, future.get, 10)
            logger.info(f"Published order: {order.order_id}")
        except Exception as e:
            logger.error(f"Error publishing order: {e}")
    
    async def publish_trading_stopped(self):
        """Publish trading stopped event"""
        try:
            event = {
                'event_type': 'TRADING_STOPPED',
                'reason': 'Risk limits exceeded',
                'portfolio_state': asdict(self.portfolio),
                'timestamp': datetime.now().isoformat()
            }
            
            future = self.kafka_producer.send(
                'trading.events.v1',
                value=event,
                key='trading_stopped'.encode()
            )
            await asyncio.get_event_loop().run_in_executor(None, future.get, 10)
            logger.info("Published trading stopped event")
        except Exception as e:
            logger.error(f"Error publishing trading stopped event: {e}")
    
    async def stop(self):
        """Stop advanced trading service"""
        logger.info("Stopping Advanced Trading Service...")
        self.running = False
        
        # Close Kafka connections
        self.kafka_producer.close()
        self.kafka_consumer.close()
        
        # Close Redis connection
        self.redis_client.close()

async def main():
    """Main entry point"""
    service = AdvancedTradingService()
    
    try:
        await service.start_trading()
    except KeyboardInterrupt:
        logger.info("Shutting down Advanced Trading Service")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
    finally:
        await service.stop()

if __name__ == "__main__":
    asyncio.run(main())
