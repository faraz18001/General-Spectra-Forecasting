
-- ICP AI Forecasting Platform - Database Schema
-- Compatible with: MSSQL (SQL Server)


-- 1. Users table (authentication & authorization)
CREATE TABLE users (
    id INT NOT NULL IDENTITY(1,1) PRIMARY KEY,
    email VARCHAR(255) NOT NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(50) DEFAULT 'user',              -- 'user' or 'admin'
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    is_active BIT DEFAULT 1,
    allowed_branches TEXT NULL                     -- Comma-separated branch names, NULL = all access
);

-- 2. Branches table (office locations)
CREATE TABLE branches (
    id INT NOT NULL IDENTITY(1,1) PRIMARY KEY,
    name VARCHAR(255) NOT NULL UNIQUE
);

-- 3. Categories table (service types per branch)
CREATE TABLE categories (
    id INT NOT NULL IDENTITY(1,1) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    branch_id INT NOT NULL,
    [rank] INT NULL,
    FOREIGN KEY (branch_id) REFERENCES branches(id)
);

-- 4. Training Runs table (model training history)
CREATE TABLE training_runs (
    id INT NOT NULL IDENTITY(1,1) PRIMARY KEY,
    branch_id INT NULL,                           -- NULL = all branches
    started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    completed_at DATETIME NULL,
    status VARCHAR(50) DEFAULT 'running',         -- running, success, failed
    years_used VARCHAR(255) NULL,                 -- e.g. "2024,2025,2026"
    prediction_days INT DEFAULT 365,
    confidence FLOAT DEFAULT 0.95,
    mape FLOAT NULL,
    rmse FLOAT NULL,
    FOREIGN KEY (branch_id) REFERENCES branches(id)
);

-- 5. Daily Forecasts table (predicted values per day/branch/category)
CREATE TABLE daily_forecasts (
    training_run_id INT NOT NULL,
    branch_id INT NOT NULL,
    category_id INT NOT NULL DEFAULT 0,           -- 0 = branch-level aggregate
    date DATE NOT NULL,
    day_of_week VARCHAR(10) NOT NULL,
    predicted INT NOT NULL,
    lower_bound INT NOT NULL,
    upper_bound INT NOT NULL,
    PRIMARY KEY (training_run_id, branch_id, category_id, date),
    FOREIGN KEY (training_run_id) REFERENCES training_runs(id),
    FOREIGN KEY (branch_id) REFERENCES branches(id)
);

-- 6. Actual Traffic table (real observed values for validation)
CREATE TABLE actual_traffic (
    branch_id INT NOT NULL,
    category_id INT NOT NULL DEFAULT 0,           -- 0 = branch-level aggregate
    date DATE NOT NULL,
    actual_count INT NOT NULL,
    PRIMARY KEY (branch_id, category_id, date),
    FOREIGN KEY (branch_id) REFERENCES branches(id)
);

-- 7. Events table (holidays, special events affecting traffic)
CREATE TABLE events (
    id INT NOT NULL IDENTITY(1,1) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL
);

-- INDEXES (for performance on large datasets)
CREATE INDEX idx_forecasts_date ON daily_forecasts(date);
CREATE INDEX idx_forecasts_branch ON daily_forecasts(branch_id, category_id);
CREATE INDEX idx_actuals_date ON actual_traffic(date);
CREATE INDEX idx_actuals_branch ON actual_traffic(branch_id, category_id);
CREATE INDEX idx_training_status ON training_runs(status);
