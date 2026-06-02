# Backend & Database Golden Rules 🌟

Here are the **memorable patterns** you need to know when building secure and robust backends. Think of these as "Mental Checklists" for every API endpoint.

---

## 1. The "VOE" Pattern (Update or Delete)
*Use this when changing existing data.*
> **"Does it exist? Is it yours? Do it."**

1.  **Verify Existence**: check if the `id` exists in the DB.
    *   *If not found → Return 404 Not Found.*
2.  **Verify Ownership**: check if `record.user_id == current_user.id`.
    *   *If mismatch → Return 403 Forbidden.*
3.  **Execute**: Perform the Update or Delete.

```python
# MENTAL MODEL
maybe_item = db.get(id)        # 1. Exist?
if not maybe_item: error(404)

if maybe_item.owner != me:     # 2. Yours?
    error(403)

db.delete(id)                  # 3. Do it.
```

---

## 2. The "Parent-Trap" Pattern (Create Child)
*Use this when creating something inside something else (e.g., A Session inside a Project).*
> **"Check the Parent first!"**

1.  **Validate Input**: Is the data valid? (Pydantic does this).
2.  **Check Parent Existence**: Does the `project_id` exist?
    *   *If not → Return 404.*
3.  **Check Parent Ownership**: Do I own the Project I'm adding this Session to?
    *   *If not → Return 403.*
4.  **Execute**: Create the child record.

```python
# MENTAL MODEL
project = db.get(project_id)   # 2. Parent Exist?
if not project: error(404)

if project.owner != me:        # 3. Parent Yours?
    error(403)

db.create_session(...)         # 4. Create Child
```

---

## 3. The "Tunnel Vision" Pattern (List/Search)
*Use this when getting multiple records.*
> **"Wear Blinders: Only see what is yours."**

**NEVER** fetch all data and then filter in code.
**ALWAYS** filter in the SQL query itself.

1.  **Filter by Owner**: Always include `WHERE user_id = %s`.
2.  **Filter by Status**: Often exclude deleted items (`is_deleted = FALSE`).
3.  **Sort**: Always define an order (e.g., `ORDER BY date DESC`) so the UI doesn't jump around.

```python
# ✅ CORRECT (Tunnel Vision)
SELECT * FROM projects WHERE user_id = me;

# ❌ WRONG (Lazy Filtering)
rows = SELECT * FROM projects; # GETS EVERYONE'S DATA!
return [r for r in rows if r.owner == me] # Too late, memory wasted, security risk.
```

---

## 4. The "Boomerang" Pattern (Insert/Update)
*Use this when saving data.*
> **"Throw data in, catch the truth back."**

When you write to the DB, don't trust the data you sent. Trust the data the DB created.

1.  **Insert**: Send data.
2.  **Return**: Use `RETURNING *` to get ID, Created_At, and Defaults.
3.  **Reply**: Send THAT exact data back to the frontend.

---

## 5. The "Fail-Safe" Pattern (Transactions)
*Use this when doing 2+ things.*
> **"All for one, and one for all."**

If you have to update a Balance AND insert a Log...

1.  **Start Transaction**.
2.  **Do Step A**.
3.  **Do Step B**.
4.  **Commit** (Save).
    *   *If ANY error happens → ROLLBACK (Undo everything).*

---

## Summary Checklist 📝

| Action | Pattern Name | The Mantra |
| :--- | :--- | :--- |
| **GET (List)** | Tunnel Vision | *"Only select WHERE user_id is me."* |
| **POST (Create)** | Parent-Trap | *"Check the Parent exists and is mine."* |
| **PATCH (Update)** | VOE | *"Exist? Yours? Update."* |
| **DELETE** | VOE | *"Exist? Yours? Delete."* |
| **DB Write** | Boomerang | *"Use RETURNING to get the generated truth."* |
