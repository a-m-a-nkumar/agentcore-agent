# Jira Epic & User Story Generation from Confluence

## Overview
This feature allows users to automatically generate Jira Epics and User Stories from Confluence BRD pages using AI (Bedrock/Claude). Users can review and select which items to create in Jira.

## Flow

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. User clicks "Generate Jira Items" button on Confluence page │
└────────────────────────────┬────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ 2. Frontend shows loading state                                 │
└────────────────────────────┬────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ 3. Backend fetches Confluence page content                      │
└────────────────────────────┬────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ 4. Send to Bedrock/Claude for analysis                          │
│    - Extract major features → Epics                             │
│    - Break down into User Stories                               │
│    - Map to BRD requirements                                    │
└────────────────────────────┬────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ 5. Frontend displays results in table                           │
│    - Epics with expandable User Stories                         │
│    - Checkboxes for selection                                   │
│    - BRD requirement mappings                                   │
│    - Story points and priorities                                │
└────────────────────────────┬────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ 6. User selects desired items                                   │
└────────────────────────────┬────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ 7. User clicks "Create in Jira"                                 │
└────────────────────────────┬────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ 8. Backend creates Epics and selected User Stories in Jira      │
│    - Creates Epics first                                        │
│    - Creates User Stories linked to Epics                       │
│    - Returns created Jira keys                                  │
└─────────────────────────────────────────────────────────────────┘
```

## Backend Implementation

### Files Modified/Created

#### 1. **services/confluence_service.py**
**Added**: `get_page_content(page_id)` method
- Fetches full Confluence page content including HTML body
- Returns page title, content, and version

#### 2. **services/jira_service.py**
**Added**: `create_issue(issue_data)` method
- Creates Epics and User Stories in Jira
- Handles validation errors
- Returns created issue key and ID

#### 3. **routers/jira_generation.py** (NEW)
Main router for Jira generation functionality

**Endpoints**:

##### POST `/api/jira/generate-from-confluence`
Generates Epics and User Stories from Confluence page

**Request**:
```json
{
  "confluence_page_id": "263880708",
  "project_id": "uuid-of-project"
}
```

**Response**:
```json
{
  "epics": [
    {
      "epic_id": "temp_epic_1",
      "title": "User Authentication System",
      "description": "Implement complete authentication...",
      "mapped_to_brd_section": "Functional Requirements - Authentication",
      "user_stories": [
        {
          "story_id": "temp_story_1",
          "title": "As a user, I want to login with email",
          "description": "User should be able to...",
          "acceptance_criteria": [
            "Email validation works",
            "Password is encrypted"
          ],
          "story_points": 5,
          "priority": "High",
          "mapped_to_requirement": "FR-001: User Login",
          "selected": false
        }
      ]
    }
  ],
  "total_epics": 3,
  "total_stories": 15
}
```

##### POST `/api/jira/create-from-generated`
Creates selected Epics and User Stories in Jira

**Request**:
```json
{
  "project_id": "uuid-of-project",
  "jira_project_key": "PROJ",
  "epics": [
    {
      "epic_id": "temp_epic_1",
      "title": "User Authentication System",
      "description": "...",
      "create_epic": true,
      "user_stories": [
        {
          "story_id": "temp_story_1",
          "title": "As a user, I want to login with email",
          "description": "...",
          "acceptance_criteria": ["..."],
          "story_points": 5,
          "priority": "High",
          "selected": true
        }
      ]
    }
  ]
}
```

**Response**:
```json
{
  "status": "success",
  "created_epics": [
    {
      "temp_id": "temp_epic_1",
      "jira_key": "PROJ-123",
      "title": "User Authentication System"
    }
  ],
  "created_stories": [
    {
      "temp_id": "temp_story_1",
      "jira_key": "PROJ-124",
      "title": "As a user, I want to login with email",
      "epic": "PROJ-123"
    }
  ],
  "failed": [],
  "summary": {
    "total_epics_created": 1,
    "total_stories_created": 1,
    "total_failed": 0
  }
}
```

#### 4. **app.py**
**Modified**: Added router registration
```python
from routers.jira_generation import router as jira_generation_router
app.include_router(jira_generation_router)
```

### AI Prompt Strategy

The system uses a structured prompt to guide Bedrock/Claude:

```
You are a Jira expert analyzing a Business Requirements Document.

Task:
1. Identify major features/modules → Create Epics
2. For each Epic, break down into User Stories
3. Map each item back to specific BRD requirements

Output Format (JSON):
{
  "epics": [
    {
      "epic_id": "temp_epic_1",
      "title": "...",
      "description": "...",
      "mapped_to_brd_section": "...",
      "user_stories": [...]
    }
  ]
}

Guidelines:
- Each Epic represents a major feature
- User Stories follow "As a [role], I want [goal]" format
- Include acceptance criteria
- Estimate story points (1, 2, 3, 5, 8, 13, 21)
- Map to specific BRD requirements
```

## Frontend Implementation (To Be Done)

### New Page: Jira Generation Review

**Route**: `/jira-generation/:confluencePageId`

**Components Needed**:

#### 1. **JiraGenerationPage.tsx**
Main page component
- Fetches generated items on load
- Shows loading state
- Displays results table
- Handles creation

#### 2. **EpicCard.tsx**
Expandable card for each Epic
- Epic title and description
- BRD section mapping
- Expand/collapse user stories
- Select/deselect all stories

#### 3. **UserStoryTable.tsx**
Table of user stories
- Checkbox for selection
- Story title
- BRD requirement mapping
- Story points
- Priority
- Acceptance criteria (expandable)

### UI Mockup

```
┌─────────────────────────────────────────────────────────────────┐
│ Generate Jira Items from BRD                                    │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│ ┌──────────────────────────────────────────────────────────┐   │
│ │ 📋 Epic: User Authentication System            [✓ All]   │   │
│ │ Mapped to: Functional Requirements - Authentication      │   │
│ ├──────────────────────────────────────────────────────────┤   │
│ │ ┌──┬─────────────────────┬──────────┬────────┬─────────┐ │   │
│ │ │☑ │ User Story          │ BRD Req  │ Points │ Priority│ │   │
│ │ ├──┼─────────────────────┼──────────┼────────┼─────────┤ │   │
│ │ │☑ │ Login with email    │ FR-001   │ 5      │ High    │ │   │
│ │ │☐ │ Login with Google   │ FR-001   │ 8      │ Medium  │ │   │
│ │ │☑ │ Password reset      │ FR-002   │ 3      │ High    │ │   │
│ │ └──┴─────────────────────┴──────────┴────────┴─────────┘ │   │
│ └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│ ┌──────────────────────────────────────────────────────────┐   │
│ │ 📋 Epic: Payment Processing                    [✓ All]   │   │
│ │ Mapped to: Functional Requirements - Payments            │   │
│ ├──────────────────────────────────────────────────────────┤   │
│ │ ┌──┬─────────────────────┬──────────┬────────┬─────────┐ │   │
│ │ │☑ │ Stripe integration  │ FR-010   │ 13     │ High    │ │   │
│ │ │☐ │ PayPal integration  │ FR-010   │ 13     │ Medium  │ │   │
│ │ └──┴─────────────────────┴──────────┴────────┴─────────┘ │   │
│ └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│ Summary: 2 Epics, 3 User Stories selected                       │
│                                                                  │
│ [Cancel]                            [Create Selected in Jira]   │
└─────────────────────────────────────────────────────────────────┘
```

### Frontend API Service

**File**: `src/services/jiraGenerationApi.ts`

```typescript
export const jiraGenerationApi = {
  generateFromConfluence: async (
    confluencePageId: string,
    projectId: string,
    token: string
  ) => {
    const response = await axios.post(
      `${API_BASE_URL}/api/jira/generate-from-confluence`,
      { confluence_page_id: confluencePageId, project_id: projectId },
      { headers: { Authorization: `Bearer ${token}` } }
    );
    return response.data;
  },

  createInJira: async (
    projectId: string,
    jiraProjectKey: string,
    epics: any[],
    token: string
  ) => {
    const response = await axios.post(
      `${API_BASE_URL}/api/jira/create-from-generated`,
      { project_id: projectId, jira_project_key: jiraProjectKey, epics },
      { headers: { Authorization: `Bearer ${token}` } }
    );
    return response.data;
  }
};
```

## Testing

### Backend Testing

1. **Generate from Confluence**:
```bash
POST http://localhost:8000/api/jira/generate-from-confluence
Authorization: Bearer <token>
{
  "confluence_page_id": "263880708",
  "project_id": "c3c739dd-1009-4770-9c4b-1b004e2f1b22"
}
```

2. **Create in Jira**:
```bash
POST http://localhost:8000/api/jira/create-from-generated
Authorization: Bearer <token>
{
  "project_id": "c3c739dd-1009-4770-9c4b-1b004e2f1b22",
  "jira_project_key": "PROJ",
  "epics": [...]
}
```

### Frontend Testing

1. Navigate to Confluence page
2. Click "Generate Jira Items" button
3. Wait for AI generation (loading state)
4. Review generated Epics and User Stories
5. Select desired items
6. Click "Create in Jira"
7. Verify items created in Jira

## Configuration

### Required Environment Variables

Already configured in `.env`:
- `BEDROCK_MODEL_ID` - Claude model ID
- `AWS_REGION` - AWS region for Bedrock
- Atlassian credentials (stored per user in database)

### Jira Custom Fields

The system uses standard Jira fields:
- `customfield_10016` - Story Points (common field ID)
- May need adjustment based on your Jira configuration

## Error Handling

The system handles:
- ✅ Confluence page not found
- ✅ Atlassian credentials not linked
- ✅ Bedrock API errors
- ✅ Jira creation failures
- ✅ Invalid JSON from AI
- ✅ Validation errors

## Future Enhancements

1. **Edit Before Creation**: Allow editing titles/descriptions
2. **Bulk Operations**: Select/deselect all, filter by priority
3. **Conflict Detection**: Check for duplicate Epics/Stories
4. **Save Drafts**: Save generated items for later
5. **Version Tracking**: Track what's been created from each BRD version
6. **Custom Fields**: Support custom Jira fields
7. **Templates**: Save common Epic/Story patterns

## Next Steps

1. ✅ Backend implementation complete
2. ⏳ Create frontend components
3. ⏳ Add button to Confluence page view
4. ⏳ Test end-to-end flow
5. ⏳ Refine AI prompt based on results
