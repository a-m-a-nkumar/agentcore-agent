# Confluence BRD Upload Implementation

## Overview
This implementation allows users to upload BRD (Business Requirements Document) content from S3 to Confluence with a single button click in the frontend.

## How It Works

### Backend Flow
1. **User clicks "Upload to Confluence" button** in the frontend
2. **Frontend sends request** to `/api/integrations/confluence/upload-brd` with:
   - `brd_id`: The BRD identifier
   - `project_id`: The project ID
   - `page_title` (optional): Custom title for the Confluence page

3. **Backend processes the request**:
   - Validates user has linked Atlassian account
   - Fetches project details to get the linked Confluence space key
   - Downloads BRD from S3 (tries JSON format first, falls back to text)
   - Converts BRD structure to Confluence storage format (HTML)
   - Creates a new Confluence page in the linked space
   - Returns page details including web URL

### Key Features

✅ **Automatic Format Conversion**: Converts BRD JSON structure to Confluence-compatible HTML
- Sections → H2 headings
- Paragraphs → `<p>` tags with proper line breaks
- Bullet lists → `<ul>` and `<li>` tags
- Tables → Confluence table format with headers

✅ **Smart Fallback**: If JSON structure is not available, falls back to text format

✅ **Auto-Generated Titles**: If no custom title provided, generates: `BRD - {ProjectName} - {Timestamp}`

✅ **New Page Every Time**: Each upload creates a fresh Confluence page (as requested)

## API Endpoint

### POST `/api/integrations/confluence/upload-brd`

**Request Body:**
```json
{
  "brd_id": "uuid-of-brd",
  "project_id": "uuid-of-project",
  "page_title": "Optional Custom Title"
}
```

**Success Response (200):**
```json
{
  "status": "success",
  "message": "BRD uploaded to Confluence successfully",
  "confluence_page": {
    "id": "123456",
    "title": "BRD - My Project - 2026-02-12 13:45",
    "web_url": "https://yourcompany.atlassian.net/wiki/spaces/SPACE/pages/123456/...",
    "space_key": "SPACE"
  }
}
```

**Error Responses:**
- `400`: Atlassian account not linked
- `400`: No Confluence space linked to project
- `404`: Project not found
- `500`: Failed to fetch BRD from S3 or create Confluence page

## Prerequisites

For this to work, users must:
1. ✅ Link their Atlassian account (domain, email, API token)
2. ✅ Link a Confluence space to the project
3. ✅ Have a BRD generated and stored in S3

## S3 Structure

The system looks for BRD files in S3 with this structure:
```
s3://bucket-name/
  └── brds/
      └── {brd_id}/
          ├── brd_structure.json  (preferred)
          └── BRD_{brd_id}.txt    (fallback)
```

## Files Modified

### 1. `services/confluence_service.py`
**Added:**
- `convert_brd_to_confluence_storage()`: Converts BRD JSON to Confluence HTML format
- `create_page()`: Creates a new Confluence page via REST API

### 2. `routers/integrations.py`
**Added:**
- `UploadBRDToConfluenceRequest`: Request model
- `upload_brd_to_confluence()`: Main endpoint handler

**Imports Added:**
- `boto3`: For S3 access
- `json`: For JSON parsing
- `os`: For environment variables
- `datetime`: For timestamp generation
- `get_project`: From db_helper

## Frontend Integration

The frontend should call this endpoint when the user clicks "Upload to Confluence":

```typescript
const uploadToConfluence = async (brdId: string, projectId: string) => {
  try {
    const response = await fetch('/api/integrations/confluence/upload-brd', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${accessToken}`
      },
      body: JSON.stringify({
        brd_id: brdId,
        project_id: projectId,
        page_title: null  // or custom title
      })
    });
    
    const result = await response.json();
    
    if (result.status === 'success') {
      // Show success message with link
      console.log('Confluence page created:', result.confluence_page.web_url);
      // Optionally open the page in a new tab
      window.open(result.confluence_page.web_url, '_blank');
    }
  } catch (error) {
    console.error('Failed to upload to Confluence:', error);
  }
};
```

## Testing

To test this implementation:

1. **Link Atlassian Account**:
   ```bash
   POST /api/integrations/atlassian/link
   {
     "domain": "yourcompany.atlassian.net",
     "email": "your@email.com",
     "api_token": "your-api-token"
   }
   ```

2. **Create/Update Project with Confluence Space**:
   ```bash
   PUT /api/projects/{project_id}
   {
     "confluence_space_key": "YOURSPACE"
   }
   ```

3. **Upload BRD to Confluence**:
   ```bash
   POST /api/integrations/confluence/upload-brd
   {
     "brd_id": "your-brd-id",
     "project_id": "your-project-id"
   }
   ```

## Future Enhancements (Not Implemented Yet)

- ❌ Update same page instead of creating new one
- ❌ Store Confluence page ID in database
- ❌ Version tracking
- ❌ Diff highlighting between versions

## Notes

- Each upload creates a **new** Confluence page (as per requirements)
- The page title includes a timestamp to avoid duplicates
- HTML content is properly escaped to prevent XSS
- Supports both JSON and text BRD formats from S3
