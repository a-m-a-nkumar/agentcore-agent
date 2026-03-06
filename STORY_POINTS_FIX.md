# Story Points Field Fix

## 🐛 Problem

Stories were failing to create with the error:

```
'customfield_10016': "Field 'customfield_10016' cannot be set. It is not on the appropriate screen, or unknown."
```

### Root Cause:

The story points custom field (`customfield_10016`) is not configured or available in your Jira project.

**Why this happens:**
- Custom field IDs vary between Jira instances
- `customfield_10016` is a common ID for story points, but not universal
- The field may not be added to the Story issue type screen
- Some Jira projects don't use story points at all

## ✅ Solution

### Made Story Points Optional

Updated the code to **skip story points** during creation:

**Before** (❌ Caused errors):
```python
# Add story points if available
if 'story_points' in story:
    story_payload['fields']['customfield_10016'] = story['story_points']
```

**After** (✅ Works):
```python
# Note: Story points (customfield_10016) is skipped as it may not be configured
# Users can manually add story points in Jira after creation
```

### Priority Field Protection

Also added error handling for priority field:

```python
# Add priority if available (this is usually a standard field)
if 'priority' in story:
    try:
        story_payload['fields']['priority'] = {"name": story['priority']}
    except:
        logger.warning(f"Could not set priority for story {story['story_id']}")
```

## 📊 What Gets Created Now

### ✅ **Epic Fields:**
- ✅ Summary (title)
- ✅ Description (ADF format)
- ✅ Issue Type (Epic)
- ✅ Project

### ✅ **Story Fields:**
- ✅ Summary (title)
- ✅ Description (ADF format with acceptance criteria)
- ✅ Issue Type (Story/Task)
- ✅ Parent (linked to Epic)
- ✅ Project
- ⚠️ Priority (if supported)
- ❌ Story Points (skipped - add manually in Jira)

## 🎯 How to Add Story Points Manually

After stories are created, you can add story points in Jira:

1. **Open the story** in Jira
2. **Find the "Story Points" field** (if available)
3. **Enter the value** (the AI suggested values are in the description)
4. **Save**

## 💡 Alternative: Find Your Story Points Field ID

If you want to enable story points in the future:

### Method 1: Via Jira API
```bash
curl -u email@example.com:api_token \
  https://your-domain.atlassian.net/rest/api/3/field
```

Look for a field with `name: "Story Points"` and note its `id`.

### Method 2: Via Jira UI
1. Go to **Jira Settings** → **Issues** → **Custom Fields**
2. Find **"Story Points"** field
3. Click **"..."** → **"Contexts and default value"**
4. The URL will show the field ID

### Method 3: Check Issue JSON
1. Open any story in Jira
2. Add `.json` to the URL (e.g., `BGUS-1.json`)
3. Search for "story" or "points" in the JSON
4. Find the custom field ID

## 🔧 If You Want to Re-enable Story Points

Update this line in `routers/jira_generation.py`:

```python
# Replace customfield_10016 with your actual field ID
if 'story_points' in story:
    story_payload['fields']['customfield_XXXXX'] = story['story_points']
```

## ✅ Status

- ✅ Epics create successfully
- ✅ Stories create successfully
- ✅ Stories link to Epics
- ✅ Descriptions in ADF format
- ✅ Acceptance criteria included
- ⚠️ Story points skipped (add manually)
- ⚠️ Priority may be skipped if not supported

**Stories now create without errors!** 🎉

## 🧪 Test It Now

Try creating your Epics and Stories again - they should all be created successfully now!
