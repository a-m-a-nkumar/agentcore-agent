-- ============================================
-- Complete Database Schema for Session Management
-- ============================================
-- This schema supports:
-- - Multi-user system with Azure AD authentication
-- - Multiple projects per user
-- - Multiple chat sessions per project
-- - Soft deletes for data recovery
-- - Auto-updating timestamps
-- ============================================

-- ============================================
-- 1. USERS TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS users (
    id VARCHAR(255) PRIMARY KEY,           -- Azure AD oid
    email VARCHAR(500) UNIQUE NOT NULL,
    name VARCHAR(500),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    last_login TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE,
    metadata JSONB DEFAULT '{}'::jsonb
);

-- Indexes for users
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_is_active ON users(is_active);

COMMENT ON TABLE users IS 'Stores authenticated users from Azure AD';
COMMENT ON COLUMN users.id IS 'Azure AD oid (object ID)';
COMMENT ON COLUMN users.metadata IS 'Additional user preferences and settings';

-- ============================================
-- 2. PROJECTS TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS projects (
    id VARCHAR(255) PRIMARY KEY,           -- UUID from frontend
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

-- Indexes for projects
CREATE INDEX IF NOT EXISTS idx_projects_user_id ON projects(user_id);
CREATE INDEX IF NOT EXISTS idx_projects_is_deleted ON projects(is_deleted);
CREATE INDEX IF NOT EXISTS idx_projects_user_active ON projects(user_id, is_deleted);
CREATE INDEX IF NOT EXISTS idx_projects_updated_at ON projects(updated_at DESC);

COMMENT ON TABLE projects IS 'Stores user projects (replaces localStorage local_brd_projects)';
COMMENT ON COLUMN projects.is_deleted IS 'Soft delete flag - allows data recovery';

-- ============================================
-- 3. ANALYST SESSIONS TABLE
-- ============================================
CREATE TABLE IF NOT EXISTS analyst_sessions (
    id VARCHAR(255) PRIMARY KEY,           -- Session ID (min 33 chars for AgentCore)
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

-- Indexes for analyst_sessions
CREATE INDEX IF NOT EXISTS idx_sessions_project_id ON analyst_sessions(project_id);
CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON analyst_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_created_at ON analyst_sessions(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_last_updated ON analyst_sessions(last_updated DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_is_deleted ON analyst_sessions(is_deleted);
-- Composite index for efficient filtering (most common query pattern)
CREATE INDEX IF NOT EXISTS idx_sessions_project_active_updated 
    ON analyst_sessions(project_id, is_deleted, last_updated DESC);

COMMENT ON TABLE analyst_sessions IS 'Stores chat sessions for Analyst Agent (replaces localStorage analyst_sessions)';
COMMENT ON COLUMN analyst_sessions.id IS 'Session ID - must be 33+ characters for AgentCore compatibility';
COMMENT ON COLUMN analyst_sessions.brd_id IS 'Associated BRD ID if generated';
COMMENT ON COLUMN analyst_sessions.message_count IS 'Number of messages in this session';

-- ============================================
-- 4. AUTO-UPDATE TRIGGERS
-- ============================================

-- Function to update last_updated timestamp
CREATE OR REPLACE FUNCTION update_last_updated_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.last_updated = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger for analyst_sessions
DROP TRIGGER IF EXISTS trigger_sessions_last_updated ON analyst_sessions;
CREATE TRIGGER trigger_sessions_last_updated
    BEFORE UPDATE ON analyst_sessions
    FOR EACH ROW
    EXECUTE FUNCTION update_last_updated_column();

-- Trigger for projects
DROP TRIGGER IF EXISTS trigger_projects_updated ON projects;
CREATE TRIGGER trigger_projects_updated
    BEFORE UPDATE ON projects
    FOR EACH ROW
    EXECUTE FUNCTION update_last_updated_column();

-- ============================================
-- 5. USEFUL QUERIES (For Reference)
-- ============================================

-- Get all sessions for a project (NO JOIN NEEDED)
-- SELECT id, project_id, user_id, title, brd_id, message_count, 
--        created_at, last_updated
-- FROM analyst_sessions
-- WHERE project_id = 'project-abc-123'
--   AND is_deleted = FALSE
-- ORDER BY last_updated DESC;

-- Get all projects for a user
-- SELECT id, user_id, project_name, description, created_at, updated_at
-- FROM projects
-- WHERE user_id = 'user-xyz-789'
--   AND is_deleted = FALSE
-- ORDER BY updated_at DESC;

-- Create new session
-- INSERT INTO analyst_sessions (id, project_id, user_id, title, message_count)
-- VALUES ('session-1738478400000-abc123-def456', 'project-abc-123', 'user-xyz-789', 'Payment Gateway Discussion', 0);

-- Update session title
-- UPDATE analyst_sessions
-- SET title = 'Updated Chat Title'
-- WHERE id = 'session-1738478400000-abc123-def456';

-- Soft delete session
-- UPDATE analyst_sessions
-- SET is_deleted = TRUE
-- WHERE id = 'session-1738478400000-abc123-def456';

-- Hard delete old soft-deleted sessions (cleanup)
-- DELETE FROM analyst_sessions
-- WHERE is_deleted = TRUE
--   AND last_updated < CURRENT_TIMESTAMP - INTERVAL '30 days';

-- ============================================
-- 6. VERIFICATION QUERIES
-- ============================================

-- Check if tables exist
-- SELECT table_name 
-- FROM information_schema.tables 
-- WHERE table_schema = 'public' 
-- AND table_name IN ('users', 'projects', 'analyst_sessions')
-- ORDER BY table_name;

-- Check indexes
-- SELECT indexname, indexdef
-- FROM pg_indexes
-- WHERE tablename IN ('users', 'projects', 'analyst_sessions')
-- ORDER BY tablename, indexname;

-- Count records
-- SELECT 
--     (SELECT COUNT(*) FROM users) as users_count,
--     (SELECT COUNT(*) FROM projects) as projects_count,
--     (SELECT COUNT(*) FROM analyst_sessions) as sessions_count;
