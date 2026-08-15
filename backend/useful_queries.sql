-- =====================================================================
-- WAVECAST FORECASTING ENGINE — USEFUL SQL AUDIT & INSPECTION QUERIES
-- Compatible with: Beekeeper Studio, SSMS, DBeaver, Azure Data Studio
-- Database: forecast_app
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. OVERVIEW: ROW COUNTS FOR ALL DATABASE TABLES
-- ---------------------------------------------------------------------
SELECT 'branches' AS table_name, COUNT(*) AS total_rows FROM branches
UNION ALL
SELECT 'categories', COUNT(*) FROM categories
UNION ALL
SELECT 'ingested_files', COUNT(*) FROM ingested_files
UNION ALL
SELECT 'training_runs', COUNT(*) FROM training_runs
UNION ALL
SELECT 'actual_traffic', COUNT(*) FROM actual_traffic
UNION ALL
SELECT 'daily_forecasts', COUNT(*) FROM daily_forecasts
UNION ALL
SELECT 'events', COUNT(*) FROM events;


-- ---------------------------------------------------------------------
-- 2. INGESTED FILES AUDIT (Tracking Appended Files & mtime)
-- ---------------------------------------------------------------------
SELECT 
    id,
    filename,
    row_count,
    file_mtime,
    ingested_at
FROM ingested_files
ORDER BY ingested_at DESC;


-- ---------------------------------------------------------------------
-- 3. TRAINING RUNS AUDIT (Model Retraining History)
-- ---------------------------------------------------------------------
SELECT 
    tr.id AS run_id,
    tr.started_at,
    tr.status,
    tr.prediction_days,
    MIN(df.date) AS forecast_start_date,
    MAX(df.date) AS forecast_end_date,
    COUNT(df.id) AS total_forecast_rows
FROM training_runs tr
LEFT JOIN daily_forecasts df ON df.training_run_id = tr.id
GROUP BY tr.id, tr.started_at, tr.status, tr.prediction_days
ORDER BY tr.id DESC;


-- ---------------------------------------------------------------------
-- 4. ACTUAL TRAFFIC SUMMARY (Ground-Truth Ticket Actuals)
-- ---------------------------------------------------------------------
-- A) Summary of Actual Traffic Date Range & Totals by Branch
SELECT 
    b.name AS branch_name,
    COUNT(DISTINCT a.date) AS total_days_ingested,
    MIN(a.date) AS min_actual_date,
    MAX(a.date) AS max_actual_date,
    SUM(a.actual_count) AS total_tickets
FROM actual_traffic a
JOIN branches b ON b.id = a.branch_id
WHERE a.category_id = 0  -- 0 = Branch Aggregate
GROUP BY b.name;

-- B) Monthly Aggregated Actual Traffic Volumes
SELECT 
    b.name AS branch_name,
    YEAR(a.date) AS year_val,
    MONTH(a.date) AS month_val,
    COUNT(DISTINCT a.date) AS active_days,
    SUM(a.actual_count) AS total_monthly_actuals
FROM actual_traffic a
JOIN branches b ON b.id = a.branch_id
WHERE a.category_id = 0
GROUP BY b.name, YEAR(a.date), MONTH(a.date)
ORDER BY year_val DESC, month_val DESC;


-- ---------------------------------------------------------------------
-- 5. LATEST MODEL FORECAST PREDICTIONS (By Month & Branch)
-- ---------------------------------------------------------------------
SELECT 
    df.training_run_id,
    b.name AS branch_name,
    c.name AS category_name,
    df.date,
    df.day_of_week,
    df.predicted,
    df.lower_bound,
    df.upper_bound
FROM daily_forecasts df
JOIN branches b ON b.id = df.branch_id
LEFT JOIN categories c ON c.id = df.category_id
WHERE df.training_run_id = (SELECT MAX(id) FROM training_runs WHERE status = 'success')
  AND b.name = 'Brampton'
  AND df.category_id = 0
ORDER BY df.date ASC;


-- ---------------------------------------------------------------------
-- 6. MODEL VALIDATION: ACTUAL VS PREDICTED COMPARISON (July & August)
-- ---------------------------------------------------------------------
-- Pairs ground-truth actuals against predictions generated in Training Run #1
SELECT 
    a.date,
    b.name AS branch_name,
    a.actual_count AS actual_tickets,
    df.predicted AS predicted_tickets,
    (df.predicted - a.actual_count) AS forecast_error,
    ABS(df.predicted - a.actual_count) AS absolute_error
FROM actual_traffic a
JOIN branches b ON b.id = a.branch_id
LEFT JOIN daily_forecasts df 
    ON df.branch_id = a.branch_id 
   AND df.category_id = a.category_id 
   AND df.date = a.date
   AND df.training_run_id = 1  -- Compare against Training Run #1
WHERE a.category_id = 0
  AND b.name = 'Brampton'
  AND a.date >= '2026-08-01' AND a.date <= '2026-08-31'
ORDER BY a.date ASC;


-- ---------------------------------------------------------------------
-- 7. BRANCH & CATEGORY REGISTRY LOOKUP
-- ---------------------------------------------------------------------
SELECT 
    b.id AS branch_id,
    b.name AS branch_name,
    c.id AS category_id,
    c.name AS category_name,
    c.priority_rank
FROM branches b
LEFT JOIN categories c ON c.branch_id = b.id
ORDER BY b.name, c.priority_rank;


-- ---------------------------------------------------------------------
-- 8. SPECIAL CALENDAR EVENTS (Eid & National Holidays)
-- ---------------------------------------------------------------------
SELECT 
    id,
    name,
    start_date,
    end_date,
    impact_factor
FROM events
ORDER BY start_date ASC;
