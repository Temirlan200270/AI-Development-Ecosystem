-- Schema for Temir CLI cache system
-- This database stores successful plans for reuse

-- Table for storing execution plans
CREATE TABLE IF NOT EXISTS execution_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_hash TEXT UNIQUE NOT NULL,
    task_description TEXT NOT NULL,
    plan_content TEXT NOT NULL,
    role TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    used_count INTEGER DEFAULT 0,
    last_used TIMESTAMP,
    success_rate REAL DEFAULT 0.0,
    is_successful BOOLEAN DEFAULT FALSE
);

-- Table for storing plan execution results
CREATE TABLE IF NOT EXISTS plan_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER NOT NULL,
    execution_result TEXT,
    exit_code INTEGER,
    execution_time REAL,
    executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    was_successful BOOLEAN DEFAULT FALSE,
    FOREIGN KEY (plan_id) REFERENCES execution_plans (id)
);

-- Table for storing task patterns
CREATE TABLE IF NOT EXISTS task_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_hash TEXT UNIQUE NOT NULL,
    pattern_type TEXT NOT NULL,
    pattern_content TEXT NOT NULL,
    frequency INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen TIMESTAMP
);

-- Indexes for better performance
CREATE INDEX IF NOT EXISTS idx_execution_plans_task_hash ON execution_plans(task_hash);
CREATE INDEX IF NOT EXISTS idx_execution_plans_role ON execution_plans(role);
CREATE INDEX IF NOT EXISTS idx_execution_plans_successful ON execution_plans(is_successful);
CREATE INDEX IF NOT EXISTS idx_plan_results_plan_id ON plan_results(plan_id);
CREATE INDEX IF NOT EXISTS idx_task_patterns_pattern_hash ON task_patterns(pattern_hash);
CREATE INDEX IF NOT EXISTS idx_task_patterns_type ON task_patterns(pattern_type);

-- View for successful plans with statistics
CREATE VIEW IF NOT EXISTS successful_plans_view AS
SELECT 
    ep.id,
    ep.task_hash,
    ep.task_description,
    ep.plan_content,
    ep.role,
    ep.created_at,
    ep.used_count,
    ep.last_used,
    ep.success_rate,
    COUNT(pr.id) as total_executions,
    SUM(CASE WHEN pr.was_successful THEN 1 ELSE 0 END) as successful_executions
FROM execution_plans ep
LEFT JOIN plan_results pr ON ep.id = pr.plan_id
WHERE ep.is_successful = TRUE
GROUP BY ep.id;

-- Trigger to update last_used timestamp
CREATE TRIGGER IF NOT EXISTS update_last_used
AFTER UPDATE ON execution_plans
FOR EACH ROW
WHEN NEW.used_count != OLD.used_count
BEGIN
    UPDATE execution_plans SET last_used = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;