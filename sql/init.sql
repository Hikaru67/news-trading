-- News Trading System - Phase 1 Database Schema

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Users table
CREATE TABLE users (
    user_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    username VARCHAR(50) UNIQUE NOT NULL,
    api_key_hash VARCHAR(255) UNIQUE,
    daily_loss_limit DECIMAL(10,2) DEFAULT 100.00,
    max_position_size DECIMAL(10,2) DEFAULT 1000.00,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Orders table
CREATE TABLE orders (
    order_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES users(user_id),
    symbol VARCHAR(20) NOT NULL,
    side VARCHAR(10) CHECK (side IN ('BUY', 'SELL')),
    order_type VARCHAR(10) NOT NULL CHECK (order_type IN ('MARKET', 'LIMIT', 'IOC', 'FOK')),
    quantity DECIMAL(20,8) NOT NULL,
    price DECIMAL(20,8),
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'FILLED', 'PARTIALLY_FILLED', 'CANCELLED', 'REJECTED')),
    exchange VARCHAR(50) NOT NULL,
    exchange_order_id VARCHAR(100),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Trades table
CREATE TABLE trades (
    trade_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    order_id UUID REFERENCES orders(order_id),
    symbol VARCHAR(20) NOT NULL,
    side VARCHAR(10) CHECK (side IN ('BUY', 'SELL')),
    quantity DECIMAL(20,8) NOT NULL,
    price DECIMAL(20,8) NOT NULL,
    fees DECIMAL(20,8) DEFAULT 0,
    exchange VARCHAR(50) NOT NULL,
    exchange_trade_id VARCHAR(100),
    executed_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Signals table (for tracking processed signals)
CREATE TABLE signals (
    signal_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    event_id VARCHAR(100) UNIQUE NOT NULL,
    source VARCHAR(100) NOT NULL,
    event_type VARCHAR(50) NOT NULL,
    headline TEXT,
    url TEXT,
    severity DECIMAL(3,2) CHECK (severity >= 0 AND severity <= 1),
    direction VARCHAR(10) CHECK (direction IN ('BULL', 'BEAR', 'UNKNOWN')),
    confidence DECIMAL(3,2) CHECK (confidence >= 0 AND confidence <= 1),
    raw_text TEXT,
    processed_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Audit logs table
CREATE TABLE audit_logs (
    log_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES users(user_id),
    action VARCHAR(50) NOT NULL,
    table_name VARCHAR(50),
    record_id UUID,
    old_values JSONB,
    new_values JSONB,
    ip_address INET,
    user_agent TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Risk limits table
CREATE TABLE risk_limits (
    limit_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES users(user_id),
    limit_type VARCHAR(50) NOT NULL,
    limit_value DECIMAL(20,8) NOT NULL,
    current_value DECIMAL(20,8) DEFAULT 0,
    reset_period VARCHAR(20) DEFAULT 'DAILY',
    last_reset TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Create indexes for performance
CREATE INDEX idx_orders_user_id ON orders(user_id);
CREATE INDEX idx_orders_symbol ON orders(symbol);
CREATE INDEX idx_orders_status ON orders(status);
CREATE INDEX idx_orders_created_at ON orders(created_at);

CREATE INDEX idx_trades_order_id ON trades(order_id);
CREATE INDEX idx_trades_symbol ON trades(symbol);
CREATE INDEX idx_trades_executed_at ON trades(executed_at);

CREATE INDEX idx_signals_event_id ON signals(event_id);
CREATE INDEX idx_signals_event_type ON signals(event_type);
CREATE INDEX idx_signals_processed_at ON signals(processed_at);

CREATE INDEX idx_audit_logs_user_id ON audit_logs(user_id);
CREATE INDEX idx_audit_logs_created_at ON audit_logs(created_at);

CREATE INDEX idx_risk_limits_user_id ON risk_limits(user_id);
CREATE INDEX idx_risk_limits_limit_type ON risk_limits(limit_type);

-- Create function to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Create triggers for updated_at
CREATE TRIGGER update_users_updated_at BEFORE UPDATE ON users FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER update_orders_updated_at BEFORE UPDATE ON orders FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE TRIGGER update_risk_limits_updated_at BEFORE UPDATE ON risk_limits FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Insert default user for testing
INSERT INTO users (username, api_key_hash, daily_loss_limit, max_position_size) 
VALUES ('test_user', 'test_hash', 100.00, 1000.00);

-- Insert default risk limits
INSERT INTO risk_limits (user_id, limit_type, limit_value, reset_period)
SELECT user_id, 'DAILY_LOSS', 100.00, 'DAILY' FROM users WHERE username = 'test_user';

INSERT INTO risk_limits (user_id, limit_type, limit_value, reset_period)
SELECT user_id, 'MAX_POSITION', 1000.00, 'DAILY' FROM users WHERE username = 'test_user';
