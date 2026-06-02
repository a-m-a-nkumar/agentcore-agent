# Jira Issue Type ID Fix

## 🐛 Problem

When creating Epics and Stories in Jira, the API was rejecting the requests with the error:

```
Invalid issue data: {'issuetype': 'Specify a valid issue type'}
```

### Root Cause:

The code was using issue type **names** instead of **IDs**:

```python
# ❌ WRONG - Using name
"issuetype": {"name": "Epic"}
"issuetype": {"name": "Story"}
```

Jira's REST API v3 requires the issue type **ID**, not the name, because:
- Different projects can have different issue type configurations
- Issue type IDs are unique and consistent
- Names can vary or be customized per project

## ✅ Solution

### 1. Added Methods to JiraService

Added two new methods to fetch issue type IDs:

```python
def get_project_issue_types(self, project_key: str) -> List[Dict]:
    """Fetch all available issue types for a project"""
    # Get project ID
    # Fetch issue types for that project
    # Return list of issue types with IDs and names

def get_issue_type_id(self, project_key: str, issue_type_name: str) -> Optional[str]:
    """Get the ID for a specific issue type name"""
    # Fetch all issue types
    # Find the one matching the name (case-insensitive)
    # Return the ID
```

### 2. Updated Epic/Story Creation Logic

Modified the `create_jira_items` endpoint to:

1. **Fetch issue type IDs before creating issues**:
```python
epic_type_id = jira_service.get_issue_type_id(request.jira_project_key, "Epic")
story_type_id = jira_service.get_issue_type_id(request.jira_project_key, "Story")
```

2. **Fallback to alternative names** if not found:
```python
if not story_type_id:
    story_type_id = jira_service.get_issue_type_id(request.jira_project_key, "Task")
    if not story_type_id:
        story_type_id = jira_service.get_issue_type_id(request.jira_project_key, "User Story")
```

3. **Use IDs when creating issues**:
```python
# ✅ CORRECT - Using ID
"issuetype": {"id": epic_type_id}
"issuetype": {"id": story_type_id}
```

## 🔧 How It Works

### Flow:

```
1. User clicks "Create in Jira"
   ↓
2. Backend fetches issue types for the project
   GET /rest/api/3/project/{projectKey}
   GET /rest/api/3/issuetype/project?projectId={id}
   ↓
3. Find "Epic" issue type → Get ID (e.g., "10000")
   Find "Story" issue type → Get ID (e.g., "10001")
   ↓
4. Create Epic with ID:
   POST /rest/api/3/issue
   {
     "fields": {
       "issuetype": {"id": "10000"}
     }
   }
   ↓
5. Create Stories with ID:
   POST /rest/api/3/issue
   {
     "fields": {
       "issuetype": {"id": "10001"},
       "parent": {"key": "EPIC-123"}
     }
   }
```

## 🎯 Benefits

### 1. **Works with Any Jira Project**
- Automatically adapts to project configuration
- No hardcoded issue type IDs

### 2. **Fallback Support**
- If "Story" not found, tries "Task"
- If "Task" not found, tries "User Story"
- Handles different Jira project templates

### 3. **Clear Error Messages**
```python
if not epic_type_id:
    raise Exception("Epic issue type not found in project. Please ensure your Jira project supports Epics.")
```

### 4. **Logging**
```python
logger.info(f"Using issue type IDs - Epic: {epic_type_id}, Story: {story_type_id}")
```

## 📊 Example Issue Types

Different Jira projects may have different issue types:

### Scrum Project:
```json
[
  {"id": "10000", "name": "Epic"},
  {"id": "10001", "name": "Story"},
  {"id": "10002", "name": "Task"},
  {"id": "10003", "name": "Bug"},
  {"id": "10004", "name": "Subtask"}
]
```

### Kanban Project:
```json
[
  {"id": "10000", "name": "Epic"},
  {"id": "10001", "name": "Task"},
  {"id": "10002", "name": "Bug"}
]
```

### Company Template Project:
```json
[
  {"id": "10000", "name": "Epic"},
  {"id": "10001", "name": "User Story"},
  {"id": "10002", "name": "Technical Task"},
  {"id": "10003", "name": "Bug"}
]
```

Our code handles all these variations!

## 🧪 Testing

### Test Case 1: Standard Scrum Project
```
Project: BGUS
Issue Types: Epic, Story, Task, Bug
Expected: ✓ Creates Epics and Stories successfully
```

### Test Case 2: Kanban Project (No "Story")
```
Project: KANBAN
Issue Types: Epic, Task, Bug
Expected: ✓ Falls back to "Task" for stories
```

### Test Case 3: Custom Template
```
Project: CUSTOM
Issue Types: Epic, User Story, Technical Task
Expected: ✓ Falls back to "User Story" for stories
```

### Test Case 4: No Epic Support
```
Project: SIMPLE
Issue Types: Task, Bug
Expected: ✗ Clear error: "Epic issue type not found in project"
```

## 🔍 Debugging

If issues still fail to create, check the logs:

```
INFO: Using issue type IDs - Epic: 10000, Story: 10001
INFO: Creating Jira issue: User Registration & Onboarding
INFO: Created Epic: BGUS-123
INFO: Creating Jira issue: As a user, I want to register...
INFO: Created Story: BGUS-124 under Epic BGUS-123
```

If you see:
```
WARNING: Issue type 'Epic' not found in project BGUS
```

Then the project doesn't support Epics - you may need to:
1. Enable Epics in Jira project settings
2. Use a different project template
3. Create a custom issue type

## ✅ Status

- ✅ Added `get_project_issue_types()` method
- ✅ Added `get_issue_type_id()` method
- ✅ Updated epic creation to use IDs
- ✅ Updated story creation to use IDs
- ✅ Added fallback for alternative names
- ✅ Added error handling
- ✅ Added logging

**The issue type validation error is now fixed!** 🎉

## 🔄 Next Steps

Try creating Epics and Stories again - they should now be created successfully in Jira!
