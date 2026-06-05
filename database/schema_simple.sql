-- ============================================
-- Complete Database Schema for Session Management
-- ============================================

-- 1. USERS TABLE
CREATE TABLE IF NOT EXISTS users (
    id VARCHAR(255) PRIMARY KEY,
    email VARCHAR(500) UNIQUE NOT NULL,
    name VARCHAR(500),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    last_login TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE,
    metadata JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_is_active ON users(is_active);

-- 2. PROJECTS TABLE
CREATE TABLE IF NOT EXISTS projects (
    id VARCHAR(255) PRIMARY KEY,
    user_id VARCHAR(255) NOT NULL,
    project_name VARCHAR(500) NOT NULL,
    description TEXT,
    jira_project_key VARCHAR(100),
    confluence_space_key VARCHAR(100),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    is_deleted BOOLEAN DEFAULT FALSE,
    metadata JSONB DEFAULT '{}'::jsonb,
    
    CONSTRAINT fk_project_user FOREIGN KEY (user_id) 
        REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_projects_user_id ON projects(user_id);
CREATE INDEX IF NOT EXISTS idx_projects_is_deleted ON projects(is_deleted);
CREATE INDEX IF NOT EXISTS idx_projects_user_active ON projects(user_id, is_deleted);
CREATE INDEX IF NOT EXISTS idx_projects_updated_at ON projects(updated_at DESC);

-- 3. ANALYST SESSIONS TABLE
CREATE TABLE IF NOT EXISTS analyst_sessions (
    id VARCHAR(255) PRIMARY KEY,
    project_id VARCHAR(255) NOT NULL,
    user_id VARCHAR(255) NOT NULL,
    title VARCHAR(500) NOT NULL DEFAULT 'New Chat',
    brd_id VARCHAR(255),
    message_count INTEGER DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    last_updated TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    is_deleted BOOLEAN DEFAULT FALSE,
    metadata JSONB DEFAULT '{}'::jsonb,
    
    CONSTRAINT fk_session_project FOREIGN KEY (project_id) 
        REFERENCES projects(id) ON DELETE CASCADE,
    CONSTRAINT fk_session_user FOREIGN KEY (user_id) 
        REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sessions_project_id ON analyst_sessions(project_id);
CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON analyst_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_created_at ON analyst_sessions(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_last_updated ON analyst_sessions(last_updated DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_is_deleted ON analyst_sessions(is_deleted);
CREATE INDEX IF NOT EXISTS idx_sessions_project_active_updated 
    ON analyst_sessions(project_id, is_deleted, last_updated DESC);

-- 4. AUTO-UPDATE TRIGGERS
CREATE OR REPLACE FUNCTION update_last_updated_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.last_updated = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_sessions_last_updated ON analyst_sessions;
CREATE TRIGGER trigger_sessions_last_updated
    BEFORE UPDATE ON analyst_sessions
    FOR EACH ROW
    EXECUTE FUNCTION update_last_updated_column();

DROP TRIGGER IF EXISTS trigger_projects_updated ON projects;
CREATE TRIGGER trigger_projects_updated
    BEFORE UPDATE ON projects
    FOR EACH ROW
    EXECUTE FUNCTION update_last_updated_column();
