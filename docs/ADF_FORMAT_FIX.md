# Atlassian Document Format (ADF) Fix

## 🐛 Problem

After fixing the issue type ID problem, Jira was still rejecting Epic and Story creation with:

```
'description': 'Operation value must be an Atlassian Document (see the Atlassian Document Format)'
```

### Root Cause:

Jira Cloud API v3 requires descriptions to be in **Atlassian Document Format (ADF)**, not plain text strings.

**Before** (❌ Rejected):
```python
"description": "This is a plain text description"
```

**After** (✅ Accepted):
```python
"description": {
    "type": "doc",
    "version": 1,
    "content": [
        {
            "type": "paragraph",
            "content": [
                {
                    "type": "text",
                    "text": "This is a plain text description"
                }
            ]
        }
    ]
}
```

## ✅ Solution

### 1. Created ADF Conversion Function

Added `convert_to_adf()` function that converts plain text to ADF format:

```python
def convert_to_adf(text: str) -> Dict:
    """
    Convert plain text to Atlassian Document Format (ADF)
    
    Supports:
    - Paragraphs (separated by \n\n)
    - Bullet lists (lines starting with - or *)
    - Empty descriptions
    """
```

### 2. Updated Epic Creation

```python
epic_payload = {
    "fields": {
        "summary": epic_data['title'],
        "description": convert_to_adf(epic_data.get('description', '')),  # ✅ ADF format
        "issuetype": {"id": epic_type_id}
    }
}
```

### 3. Updated Story Creation

```python
# Build description with acceptance criteria
description_text = story.get('description', '')
if story.get('acceptance_criteria'):
    description_text += "\n\nAcceptance Criteria:\n"
    for criterion in story['acceptance_criteria']:
        description_text += f"- {criterion}\n"

story_payload = {
    "fields": {
        "summary": story['title'],
        "description": convert_to_adf(description_text),  # ✅ ADF format
        "issuetype": {"id": story_type_id},
        "parent": {"key": jira_epic_key}
    }
}
```

## 🎯 ADF Format Examples

### Example 1: Simple Paragraph

**Input (Plain Text)**:
```
"Implement user registration with email and password"
```

**Output (ADF)**:
```json
{
  "type": "doc",
  "version": 1,
  "content": [
    {
      "type": "paragraph",
      "content": [
        {
          "type": "text",
          "text": "Implement user registration with email and password"
        }
      ]
    }
  ]
}
```

### Example 2: Multiple Paragraphs

**Input (Plain Text)**:
```
"This is the first paragraph.

This is the second paragraph."
```

**Output (ADF)**:
```json
{
  "type": "doc",
  "version": 1,
  "content": [
    {
      "type": "paragraph",
      "content": [{"type": "text", "text": "This is the first paragraph."}]
    },
    {
      "type": "paragraph",
      "content": [{"type": "text", "text": "This is the second paragraph."}]
    }
  ]
}
```

### Example 3: Bullet List (Acceptance Criteria)

**Input (Plain Text)**:
```
"User registration functionality

Acceptance Criteria:
- Email validation works correctly
- Password is encrypted using bcrypt
- User is redirected after successful registration"
```

**Output (ADF)**:
```json
{
  "type": "doc",
  "version": 1,
  "content": [
    {
      "type": "paragraph",
      "content": [{"type": "text", "text": "User registration functionality"}]
    },
    {
      "type": "paragraph",
      "content": [{"type": "text", "text": "Acceptance Criteria:"}]
    },
    {
      "type": "bulletList",
      "content": [
        {
          "type": "listItem",
          "content": [{
            "type": "paragraph",
            "content": [{"type": "text", "text": "Email validation works correctly"}]
          }]
        },
        {
          "type": "listItem",
          "content": [{
            "type": "paragraph",
            "content": [{"type": "text", "text": "Password is encrypted using bcrypt"}]
          }]
        },
        {
          "type": "listItem",
          "content": [{
            "type": "paragraph",
            "content": [{"type": "text", "text": "User is redirected after successful registration"}]
          }]
        }
      ]
    }
  ]
}
```

## 📊 How It Works

### Flow:

```
1. AI generates plain text description
   "Implement user authentication with email/password login"
   ↓
2. convert_to_adf() processes the text
   - Splits by \n\n for paragraphs
   - Detects lines starting with - or * for bullets
   - Builds ADF JSON structure
   ↓
3. Jira API receives ADF format
   {
     "type": "doc",
     "version": 1,
     "content": [...]
   }
   ↓
4. ✅ Jira accepts and creates the issue
```

## 🎯 Features

### ✅ **Paragraph Support**
- Automatically splits text by double newlines
- Each paragraph becomes a separate ADF paragraph node

### ✅ **Bullet List Support**
- Detects lines starting with `-` or `*`
- Converts to ADF bulletList structure
- Perfect for acceptance criteria!

### ✅ **Empty Description Handling**
- Returns valid empty ADF document
- No errors for missing descriptions

### ✅ **Acceptance Criteria Integration**
- Automatically appends acceptance criteria to story description
- Formats as bullet list in ADF
- Shows up nicely in Jira UI

## 📝 What Shows Up in Jira

### Epic Description:
```
Implement complete user authentication system with email/password login, 
OAuth integration, and password reset functionality
```

### Story Description:
```
Users should be able to authenticate using their registered email address 
and password. The system should validate credentials and provide appropriate 
error messages.

Acceptance Criteria:
• Email validation works correctly
• Password is encrypted using bcrypt
• Failed login attempts are logged
• User is redirected to dashboard after successful login
```

## 🔍 ADF Resources

- [Atlassian Document Format Spec](https://developer.atlassian.com/cloud/jira/platform/apis/document/structure/)
- [ADF Builder](https://developer.atlassian.com/cloud/jira/platform/apis/document/playground/)

## ✅ Status

- ✅ Created `convert_to_adf()` function
- ✅ Handles paragraphs
- ✅ Handles bullet lists
- ✅ Handles empty descriptions
- ✅ Updated epic creation to use ADF
- ✅ Updated story creation to use ADF
- ✅ Integrated acceptance criteria into story descriptions

**Descriptions are now in the correct format for Jira Cloud API v3!** 🎉

## 🧪 Test It Now

Try creating Epics and Stories again - they should now be created successfully with properly formatted descriptions!
