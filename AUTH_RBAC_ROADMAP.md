# SAFEWARE-CAMEO: Phase 3 (Enterprise Auth, RBAC & Multi-Tenancy Roadmap)

## 🎯 1. Primary Objective & Agent Persona
You are "Anti-Gravity", an Elite Principal Software Architect and Cybersecurity Expert. Your objective is to transform the SAFEWARE-CAMEO single-tenant platform into a highly secure, offline-capable, Multi-Tenant Enterprise SaaS. 

**⚠️ CRITICAL PROACTIVE ENGINEERING DIRECTIVE:** This roadmap is your baseline. You are explicitly instructed to **RESEARCH, ANALYZE, AND INNOVATE** beyond these bullet points. Benchmark against top-tier Enterprise SaaS (like SAP, Oracle, or AWS IAM standards). If you identify missing security features (e.g., Rate Limiting, Brute-force protection, Password Complexity policies, JWT Refresh Token rotation, or MFA/2FA foundations) that are necessary for a world-class system, **you have the absolute authority to propose and implement them proactively.** Do not wait for permission to make the system more secure or scalable.

---

## 🏗️ 2. Deep Database Architecture (The Isolation Strategy)
To ensure strict data privacy for petrochemical clients, implement the following Database Split:

* **`chemicals.db` (Global/Immutable):** The NOAA master database. Read-only.
* **`global_auth.db` (NEW):** Manages all central authentication and tenant routing.
    * **Table `companies`:** `id`, `name`, `tenant_db_path`, `license_status`, `max_users`, `created_at`
    * **Table `users`:** `id`, `email` (UNIQUE), `password_hash` (Bcrypt/Argon2), `company_id` (FK), `role`, `status` (PENDING, ACTIVE, SUSPENDED), `last_login`, `failed_attempts` (for brute-force lockouts).
    * **Table `sessions`:** Session ID, `user_id`, `ip_address`, `user_agent`, `expires_at` (for active session invalidation).
* **`{tenant_id}_user.db` (Dynamic):** Generated per company (e.g., `101_user.db`). Each contains its own `inventory_batches`, `user_inventories`, `audit_trail`, etc.

---

## 🔐 3. Advanced RBAC Matrix (Role-Based Access Control)
Enforce these 4 strict roles using Flask decorators (e.g., `@role_required('admin')`). Design the middleware to be impenetrable.

1.  **Super Admin (SAFEWARE Team):**
    * *Permissions:* Manage platform, approve new companies, monitor server health.
    * *Blind Spot Constraint:* CANNOT query or view any `{tenant_id}_user.db`. Zero access to client inventory.
2.  **Company Admin (HSE Manager):**
    * *Permissions:* Full access to their tenant DB. Must approve/reject `PENDING` users. Can view global tenant audit logs.
3.  **Operator (Warehouse Staff):**
    * *Permissions:* Upload files (ETL), resolve Review Queue, edit/delete chemicals.
    * *Constraints:* Cannot approve users, cannot change company-wide settings.
4.  **Viewer (Safety Inspector):**
    * *Permissions:* **Strictly Read-Only.** View inventory, view reactivity matrix, export PDF/Excel.
    * *Constraints:* All mutating endpoints (POST/PUT/DELETE) must return `403 Forbidden`. UI must hide all action buttons.

---

## 🔄 4. Secure Authentication Flow (Two-Step Verification)
1.  **Sign-Up:** User submits details. Account is created in `global_auth.db` with status `PENDING`. 
2.  **Approval Workflow:** Company Admin logs in, receives a notification (UI badge), assigns a Role, and changes status to `ACTIVE`.
3.  **Sign-In & Routing:** User authenticates. Flask reads `company_id`, maps to `tenant_db_path`, and binds the user's session securely.
4.  *(Proactive Task):* Implement Session Timeouts and secure JWT/Cookie handling (HttpOnly, Secure flags).

---

## 🎨 5. UI/UX Design & Nano Banana Pro Directives
The UI must scream "Premium Industrial SaaS". Use **"Industrial Glassmorphism"** (Tailwind CSS: `backdrop-blur-lg bg-white/5 border border-white/10 shadow-2xl`).

### 🍌 Nano Banana Pro - Image Generation Prompts
Generate 4 distinct, hyper-realistic, 8k resolution background images to rotate on the Login/Signup screens:

* **Background 1 (The Reaction):** "Hyper-realistic macro photography of two luminescent industrial chemicals mixing in a heavy-duty reinforced glass reactor, glowing neon blue and amber, sharp focus, cinematic lighting, dark cinematic background, 8k resolution, photorealistic."
* **Background 2 (The Modern Warehouse):** "Hyper-realistic wide shot of a futuristic, ultra-clean petrochemical storage warehouse, massive steel drums and IBC totes perfectly aligned, glowing LED safety indicators on the floor, dramatic cinematic lighting, industrial cyberpunk aesthetic, 8k resolution."
* **Background 3 (The Matrix Data):** "Abstract hyper-realistic visualization of a chemical safety reactivity matrix, floating holographic hexagonal molecular structures in dark space, glowing crimson and emerald green lines, shallow depth of field, high-tech corporate aesthetic, 8k."
* **Background 4 (The Offshore Rig/Refinery):** "Cinematic night shot of a massive oil refinery facility, illuminated by warm orange industrial lights and cool blue moonlight, complex pipes and storage tanks, mist in the air, hyper-detailed, photorealistic, 8k."

*UI Implementation Note:* Implement smooth Alpine.js transitions for form states (Loading, Success, Invalid Password, Pending Approval).

---

## 🛠️ 6. Granular Execution Plan

* **Phase 1: DB Engineering & Auth Core:** Create `global_auth.db` schema. Implement password hashing and Auth blueprint.
* **Phase 2: The Tenant Router (Critical):** Implement the `before_request` hook in Flask to dynamically connect to the correct `{tenant_id}_user.db` based on the active session. Ensure thread safety for SQLite.
* **Phase 3: RBAC Middleware:** Write the `@login_required` and `@role_required` decorators. Apply them globally.
* **Phase 4: UI/UX & Image Generation:** Use Nano Banana to generate the 4 backgrounds. Build the Glassmorphic login, registration, and Admin Approval views.
* **Phase 5: Self-Audit & Enhancement:** Review the implementation. Did you add Rate Limiting? Are SQL Injections prevented in the dynamic routing? Are CSRF tokens implemented? Apply these enterprise standards automatically.