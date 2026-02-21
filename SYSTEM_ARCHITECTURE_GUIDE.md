# مستندات جامع فنی و تحلیلی سیستم CAMEO
## راهنمای کامل ۰ تا ۱۰۰ — خط به خط، فایل به فایل، لایه به لایه

---

# فهرست مطالب

1. [نمای کلی معماری](#1-نمای-کلی-معماری)
2. [تکنولوژی‌های استفاده شده](#2-تکنولوژیهای-استفاده-شده)
3. [ساختار پوشه‌بندی پروژه](#3-ساختار-پوشهبندی-پروژه)
4. [پایگاه‌های داده](#4-پایگاههای-داده)
5. [بک‌اند: فایل اصلی app.py](#5-بکاند-فایل-اصلی-apppy)
6. [موتور واکنش‌پذیری (Reactivity Engine)](#6-موتور-واکنشپذیری)
7. [ثابت‌ها و تنظیمات (constants.py)](#7-ثابتها-و-تنظیمات)
8. [سیستم ETL — ورود و پردازش داده](#8-سیستم-etl)
9. [مسیرهای API (Routes / Blueprints)](#9-مسیرهای-api)
10. [قالب‌های HTML (Templates)](#10-قالبهای-html)
11. [فرانت‌اند React (مستقل)](#11-فرانتاند-react)
12. [جریان کامل داده: از ورود کاربر تا خروجی](#12-جریان-کامل-داده)

---

# 1. نمای کلی معماری

سیستم CAMEO یک پلتفرم مدیریت ایمنی مواد شیمیایی است. معماری آن **Client-Server** بوده و از دو بخش اصلی تشکیل شده:

- **بک‌اند (Python Flask):** سرور اصلی که API ها، موتور تحلیل واکنش‌پذیری، پایپ‌لاین ETL، و رندر صفحات HTML را مدیریت می‌کند. روی پورت `5000` اجرا می‌شود.
- **فرانت‌اند (دوگانه):**
  - صفحات HTML رندر شده توسط Jinja2 سمت سرور (بخش اصلی و فعال سیستم)
  - اپلیکیشن React+TypeScript مستقل در پوشه `src/` (فرم جستجوی آفلاین، روی پورت `5173`)

ارتباط بین فرانت و بک از طریق **REST API** با فرمت **JSON** انجام می‌شود.

---

# 2. تکنولوژی‌های استفاده شده

| لایه | تکنولوژی | نسخه/جزئیات |
|------|----------|-------------|
| سرور وب | Python Flask | با CORS فعال (`flask_cors`) |
| پایگاه داده | SQLite | دو فایل: `chemicals.db` و `user.db` |
| قالب HTML | Jinja2 | رندر سمت سرور |
| استایل‌دهی | TailwindCSS | نسخه 3.4.17 |
| فرانت مستقل | React 18 + TypeScript 5.7 | با Vite 5.4 |
| روتینگ فرانت | react-router-dom | نسخه 7.0.2 |
| آیکون‌ها | lucide-react | نسخه 0.460.0 |
| State Management | Zustand | نسخه 5.0.0 |
| تطبیق فازی | rapidfuzz | در ETL Layer 4 (match.py) |
| اعتبارسنجی داده | Pydantic | در ETL (models.py) Anti-Hallucination |
| پردازش فایل | pandas + openpyxl | خواندن Excel/CSV |
| خروجی PDF | reportlab | (اختیاری) اکسپورت گزارش |
| لاگینگ | logging (استاندارد Python) | در تمام ماژول‌ها |

---

# 3. ساختار پوشه‌بندی پروژه

```
CAMEO-new/
├── backend/                    ← بک‌اند اصلی Flask
│   ├── app.py                  ← نقطه ورود اصلی سرور (861 خط)
│   ├── data/                   ← فایل‌های دیتابیس
│   │   ├── chemicals.db        ← دیتابیس مواد شیمیایی NOAA
│   │   └── user.db             ← دیتابیس کاربر (موجودی، لاگ‌ها)
│   ├── logic/                  ← هسته منطقی سیستم
│   │   ├── __init__.py
│   │   ├── constants.py        ← ثابت‌ها: Enum سازگاری، رنگ‌ها، اولویت‌ها (95 خط)
│   │   └── reactivity_engine.py← موتور واکنش‌پذیری شیمیایی (605 خط)
│   ├── etl/                    ← پایپ‌لاین ورود داده (12 فایل)
│   │   ├── __init__.py
│   │   ├── pipeline.py         ← ارکستراتور اصلی ETL (620 خط)
│   │   ├── ingest.py           ← لایه 1: خواندن فایل (982 خط)
│   │   ├── schema.py           ← لایه 2: نگاشت ستون‌ها (1446 خط)
│   │   ├── clean.py            ← لایه 3: پاکسازی داده (854 خط)
│   │   ├── match.py            ← لایه 4: تطبیق هیبریدی (1093 خط)
│   │   ├── match_cascade.py    ← لایه 4 جایگزین: تطبیق آبشاری (258 خط)
│   │   ├── semantics.py        ← تحلیل معنایی نام‌ها (727 خط)
│   │   ├── header_guard.py     ← حذف هدرهای تکراری (88 خط)
│   │   ├── last_ditch_recovery.py ← بازیابی آخرین تلاش (223 خط)
│   │   ├── models.py           ← مدل‌های Pydantic (78 خط)
│   │   └── report.py           ← تولید گزارش (140 خط)
│   ├── routes/                 ← مسیرهای Blueprint
│   │   ├── __init__.py
│   │   ├── inventory.py        ← API‌های مدیریت موجودی و ETL (464 خط)
│   │   ├── inventory_actions.py← اکشن‌های CRUD روی سطرها (417 خط)
│   │   └── inventory_analysis.py← تحلیل سازگاری موجودی (476 خط)
│   ├── templates/              ← صفحات HTML (Jinja2)
│   │   ├── base.html           ← قالب پایه (منو، هدر، ساختار)
│   │   ├── dashboard.html      ← داشبورد اصلی
│   │   ├── inventory.html      ← مدیریت موجودی
│   │   ├── mixer.html          ← میکسر واکنش‌ها (ماتریس سازگاری)
│   │   ├── warehouse.html      ← نمای انبار فیزیکی
│   │   ├── logs.html           ← لاگ فعالیت‌ها
│   │   ├── chemical_detail.html← جزئیات یک ماده شیمیایی
│   │   ├── admin_import.html   ← صفحه آپلود فایل ETL
│   │   └── inventory_analysis.html ← نتایج تحلیل سازگاری
│   ├── scripts/                ← اسکریپت‌های SQL
│   ├── uploads/                ← فایل‌های آپلود شده
│   └── tests/                  ← تست‌ها
├── src/                        ← فرانت‌اند React (مستقل)
│   ├── main.tsx                ← نقطه ورود React
│   ├── App.tsx                 ← کامپوننت اصلی (220 خط)
│   ├── index.css               ← استایل پایه
│   ├── services/
│   │   └── ChemicalSearchService.ts ← سرویس ارتباط با API (74 خط)
│   └── types/
│       └── index.ts            ← تایپ‌های TypeScript (125 خط)
├── index.html                  ← صفحه HTML پایه Vite
├── vite.config.ts              ← پیکربندی Vite
├── package.json                ← وابستگی‌های Node.js
├── tailwind.config.js          ← تنظیمات Tailwind
└── tsconfig.json               ← تنظیمات TypeScript
```

---

# 4. پایگاه‌های داده

## 4.1. `chemicals.db` — دیتابیس مرجع NOAA

این دیتابیس دارای جداول زیر است:

| جدول | شرح |
|------|-----|
| `chemicals` | جدول اصلی مواد شیمیایی با ۹۰+ ستون (شامل خواص فیزیکی، NFPA، خطرات) |
| `chemical_cas` | شماره‌های CAS هر ماده (رابطه: `chem_id → chemicals.id`) |
| `chemical_unna` | شماره‌های UN/NA هر ماده |
| `chemical_icsc` | کدهای ICSC |
| `reacts` | لیست گروه‌های واکنش‌پذیر (Reactive Groups) |
| `mm_chemical_react` | جدول ارتباط Many-to-Many بین مواد و گروه‌های واکنش‌پذیر |
| `reactivity` | قوانین سازگاری بین جفت گروه‌ها (`react1`, `react2`, `pair_compatibility`) |

## 4.2. `user.db` — دیتابیس کاربری

ساخته شده توسط `init_inventory_tables()` در `pipeline.py`:

| جدول | شرح |
|------|-----|
| `favorites` | مواد شیمیایی مورد علاقه کاربر |
| `inventory_batches` | متادیتای هر Batch آپلود شده (status, filename, column_mapping) |
| `inventory_staging` | سطرهای پردازش شده هر Batch (raw_data, cleaned_data, match_status, chemical_id, confidence, quality_score) |
| `review_queue` | صف بررسی انسانی (priority, status, candidates) |
| `audit_trail` | لاگ ممیزی تمام عملیات (action, input_data, output_data, timestamp) |
| `learning_data` | داده‌های یادگیری برای بهبود آینده (input_pattern, correct_chemical_id) |
| `user_inventories` | اسنپ‌شات نهایی موجودی تأیید شده |
| `analysis_results` | نتایج ذخیره شده تحلیل سازگاری |
| `audit_log` | لاگ تحلیل‌های ReactivityEngine |

---

# 5. بک‌اند: فایل اصلی `app.py`

**مسیر:** `backend/app.py` — **861 خط**

## 5.1. بخش راه‌اندازی (خطوط 1-90)

```python
# Imports
import os, re, sqlite3, logging, difflib
from flask import Flask, jsonify, request, render_template
from flask_cors import CORS
from logic.reactivity_engine import ReactivityEngine
from logic.constants import Compatibility, COMPATIBILITY_MAP
```

- اپلیکیشن Flask ساخته شده و CORS فعال می‌شود.
- مسیر دیتابیس‌ها تنظیم شده: `chemicals.db` و `user.db` در فولدر `backend/data/`.
- شیء `ReactivityEngine` با مسیر `chemicals.db` ساخته می‌شود.
- سه Blueprint ثبت می‌شوند: `inventory_bp`, `inventory_actions_bp`, `inventory_analysis_bp`.

### توابع اتصال به دیتابیس:
- **`get_chemicals_db_connection()`**: اتصال به `chemicals.db` با `row_factory = sqlite3.Row`.
- **`get_user_db_connection()`**: اتصال به `user.db` (اگر وجود نداشته باشد ابتدا `init_user_db()` صدا زده می‌شود).
- **`init_user_db()`**: جدول `favorites` را می‌سازد.
- **`init_inventory_tables()`**: فایل SQL از `scripts/create_inventory_tables.sql` را اجرا می‌کند.

## 5.2. API جستجو — `GET /api/search` (خطوط 92-269)

**مهم‌ترین Endpoint عمومی سیستم.** وقتی کاربر عبارتی را در هر جای سیستم جستجو می‌کند، این تابع فراخوانی می‌شود.

### مراحل اجرا:

**مرحله 1 — کوئری SQL (خطوط 110-132):**
- یک `SELECT DISTINCT` با `LEFT JOIN` بر `chemical_cas` و `chemical_unna` اجرا می‌شود.
- فیلدهای جستجو: `name`, `synonyms`, `formulas`, `cas_id`, `unna_id`.
- اگر ورودی با `UN` شروع شود، پیشوند حذف می‌شود.
- حداکثر 200 نتیجه برگردانده می‌شود.

**مرحله 2 — جمع‌آوری CAS و UN (خطوط 134-152):**
- برای هر نتیجه، شماره‌های CAS و UN از جداول مربوطه خوانده می‌شوند.

**مرحله 3 — امتیازدهی پایتونی (خطوط 156-257):**
- هر نتیجه بر اساس نوع مطابقت امتیاز می‌گیرد:
  - **1000:** مطابقت دقیق نام
  - **950:** مطابقت دقیق CAS
  - **900:** نام با کوئری شروع شود
  - **850:** CAS حاوی کوئری باشد
  - **800:** فرمول دقیقاً مطابق
  - **750:** فرمول با کوئری شروع شود
  - **700:** فرمول حاوی کوئری
  - **650:** شماره UN
  - **600:** نام شامل کوئری
  - **550/500/450:** مطابقت با Synonym (دقیق/پیشوند/شامل)
- **Tiebreak:** نام‌های کوتاه‌تر اولویت بالاتر دارند (`score -= len(name) * 0.01`).

**مرحله 4 — مرتب‌سازی و خروجی (خطوط 259-265):**
- نتایج بر اساس امتیاز نزولی مرتب و 20 نتیجه اول برگردانده می‌شوند.
- هر نتیجه شامل: `id`, `name`, `formula`, `cas[]`, `un[]`, `match_type`, `matched_text`, `nfpa{}`.

## 5.3. API جزئیات ماده — `GET /api/chemical/<id>` (خطوط 271-288)

- تمام اطلاعات یک ماده از جدول `chemicals` خوانده و به صورت JSON برگردانده می‌شود.

## 5.4. صفحه جزئیات ماده — `GET /chemical/<id>` (خطوط 291-343)

- رندر صفحه `chemical_detail.html` با داده‌های کامل شامل:
  - CAS Numbers (از `chemical_cas`)
  - UN/NA Numbers (از `chemical_unna`)
  - ICSC Codes (از `chemical_icsc`)
  - Reactive Groups (از `reacts` با JOIN بر `mm_chemical_react`)
  - ERG Guide Number (استخراج با regex از فیلد `isolation`)

## 5.5. سیستم علاقه‌مندی‌ها (خطوط 345-402)

- **`GET /api/favorites`**: لیست علاقه‌مندی‌ها از `user.db`.
- **`POST /api/favorites`**: افزودن علاقه‌مندی (پشتیبانی از `snake_case` و `camelCase`).
- **`DELETE /api/favorites/<id>`**: حذف علاقه‌مندی.

## 5.6. مسیرهای رندر صفحات (خطوط 404-432)

| Route | تابع | Template |
|-------|------|----------|
| `/` | `index()` | `dashboard.html` |
| `/dashboard` | `dashboard_page()` | `dashboard.html` |
| `/inventory` | `inventory_page()` | `inventory.html` |
| `/mixer` | `mixer_page()` | `mixer.html` |
| `/warehouse` | `warehouse_page()` | `warehouse.html` |
| `/logs` | `logs_page()` | `logs.html` |

## 5.7. API لیست Batchها — `GET /api/inventory/batches` (خطوط 435-455)

- از `inventory_batches` در `user.db` آخرین 50 Batch را می‌خواند.

## 5.8. API ماتریس سازگاری — `GET /api/matrix/data` (خطوط 458-538)

- پارامتر `?ids=1,2,3` یا `?limit=N` (حداکثر 500).
- مواد از دیتابیس خوانده شده و به `reactivity_engine.analyze()` فرستاده می‌شوند.
- ماتریس N×N ساخته شده و هر سلول وضعیت سازگاری دارد:
  - `SELF` (قطر اصلی)، `UPPER` (مثلث بالا — خالی)
  - سلول‌های واقعی: `I` (ناسازگار)، `C!` (احتیاط)، `C` (سازگار)، `?` (نامشخص)

## 5.9. API آمار داشبورد — `GET /api/dashboard/stats` (خطوط 541-572)

- تعداد کل مواد، تعداد گروه‌های واکنش‌پذیر، تعداد جفت‌های ممکن.
- آمار شبیه‌سازی شده: 60% ایمن، 25% احتیاط، 15% بحرانی.

## 5.10. API انبار — `GET /api/warehouse` (خطوط 575-631)

- مواد را بر اساس `location` (از `json_extract` در `cleaned_data`) گروه‌بندی می‌کند.
- درصد ایمنی هر لوکیشن: `matched / total × 100`.
- وضعیت: `safe` (>80%)، `warning` (>50%)، `danger` (<50%).
- اگر داده واقعی موجود نباشد، داده دمو برگردانده می‌شود.

## 5.11. API لاگ‌ها — `GET /api/logs` (خطوط 634-709)

- از `audit_trail` و `inventory_batches` در `user.db` خوانده می‌شود.
- هر لاگ شامل: `type`, `title`, `detail`, `timestamp`, `user`, `category`.
- اگر دیتایی نباشد، 6 رکورد دمو تولید می‌شود.

## 5.12. API تحلیل سازگاری — `POST /api/analyze` (خطوط 728-828)

**Endpoint ایمنی-بحرانی (Safety-Critical)**

- ورودی: `{"chemical_ids": [1, 5, 23], "options": {"include_water_check": true}}`
- اعتبارسنجی: حداقل 2 ماده.
- `reactivity_engine.analyze()` صدا زده می‌شود.
- خروجی: شامل `overall` (سازگاری کلی + رنگ + اقدام)، `matrix` (ماتریس N×N)، `critical_pairs`, `warnings`.

## 5.13. API آمار واکنش‌پذیری و گروه‌ها (خطوط 831-856)

- **`GET /api/reactivity/stats`**: آمار کلی دیتابیس.
- **`GET /api/reactive-groups`**: لیست تمام گروه‌های واکنش‌پذیر.

---

# 6. موتور واکنش‌پذیری

**مسیر:** `backend/logic/reactivity_engine.py` — **605 خط**

## 6.1. مدل‌های داده

### `PairResult` (خطوط 25-36):
نتیجه تحلیل یک جفت ماده شیمیایی:
- `chem_a_id`, `chem_b_id`: آیدی دو ماده
- `compatibility`: نوع `Compatibility` (از Enum)
- `hazards`: لیست خطرات (`FIRE`, `EXPLOSION`, `TOXIC_GAS`, ...)
- `gas_products`: محصولات گازی واکنش
- `interaction_details`: جزئیات تعاملات غیرسازگار

### `MatrixResult` (خطوط 39-49):
نتیجه کامل تحلیل ماتریسی:
- `matrix`: ماتریس دوبعدی N×N از `PairResult`
- `overall_compatibility`: بدترین حالت کلی
- `critical_pairs`: لیست جفت‌های بحرانی
- `warnings`: هشدارها (مثل واکنش‌پذیر با آب)

## 6.2. کلاس `ReactivityEngine` (خطوط 52-604)

### متد `__init__`:
- مسیر دیتابیس ذخیره شده.
- دو کش: `_rule_cache` (قوانین سازگاری) و `_group_cache` (گروه‌های واکنش‌پذیر هر ماده).

### متد `_get_chemical_groups(chemical_id)` (خطوط 83-107):
- از جدول `mm_chemical_react` تمام `react_id` های مربوط به ماده را می‌خواند.
- نتیجه در `_group_cache` ذخیره می‌شود.

### ⚠️ متد بحرانی `_get_rule(group1_id, group2_id)` (خطوط 109-225):
**این حساس‌ترین تابع سیستم است.**

1. جفت گروه‌ها نرمال‌سازی می‌شود (کوچکتر اول).
2. اگر دو گروه یکسان باشند → `COMPATIBLE`.
3. از جدول `reactivity` کوئری زده می‌شود.
4. **اگر هیچ قانونی وجود نداشته باشد →** `NO_DATA` **(رفتار Fail-Safe — هرگز Compatible فرض نمی‌شود!)**
5. اگر قانون وجود داشته باشد:
   - `pair_compatibility` از DB به Enum ما نگاشت می‌شود (با `DB_COMPATIBILITY_MAP`).
   - `gas_products` با `|` جداسازی می‌شوند.
   - خطرات از `hazards_documentation` با کلمات کلیدی استخراج می‌شوند:
     - `fire` → `FIRE`, `explosion` → `EXPLOSION`, `toxic` → `TOXIC_GAS`, ...
   - گازهای سمی و قابل اشتعال از لیست محصولات گازی شناسایی می‌شوند.

### متد `_get_special_hazards(chemical_id)` (خطوط 227-263):
- فیلد `special_hazards` از جدول `chemicals` خوانده می‌شود.
- خطرات خاص شناسایی: `PEROXIDE_FORMER`, `PYROPHORIC`, `WATER_REACTIVE`, `EXPLOSIVE`, ...

### متد `_analyze_pair(...)` (خطوط 265-348):
**منطق ضربدری (Cartesian Product):**
- تمام ترکیبات گروه‌های ماده A با گروه‌های ماده B بررسی می‌شوند.
- برای هر جفت گروه، `_get_rule()` صدا زده می‌شود.
- **بدترین حالت** (worst case) به عنوان نتیجه نهایی انتخاب می‌شود.
- تمام خطرات و گازهای تولیدی جمع‌آوری می‌شوند.

### متد اصلی `analyze(chemical_ids, ...)` (خطوط 401-572):
**PUBLIC API** — تابع اصلی تحلیل.

1. اطلاعات هر ماده از DB خوانده می‌شود.
2. گروه‌های واکنش‌پذیر هر ماده بارگذاری می‌شود.
3. **حلقه ساخت ماتریس:**
   - قطر اصلی (`i == j`): بررسی خطرات خاص (self-hazards).
   - مثلث بالا (`i < j`): `_analyze_pair()` اجرا و نتیجه در هر دو `[i][j]` و `[j][i]` ذخیره.
   - بدترین حالت کلی ثبت می‌شود.
4. **بررسی واکنش‌پذیری با آب:** برای هر ماده، گروه‌هایش با گروه آب (ID=104) بررسی می‌شوند.
5. **ذخیره لاگ ممیزی** در جدول `audit_log`.

---

# 7. ثابت‌ها و تنظیمات

**مسیر:** `backend/logic/constants.py` — **95 خط**

### `Compatibility` (Enum):
| مقدار | کد | شرح |
|-------|-----|------|
| `COMPATIBLE` | `C` | سازگار |
| `CAUTION` | `I-C` | احتیاط |
| `INCOMPATIBLE` | `I` | ناسازگار |
| `NO_DATA` | `N` | بدون داده |

### `HazardCode` (Enum):
11 نوع خطر: `HEAT`, `FIRE`, `EXPLOSION`, `INERT_GAS`, `FLAMMABLE_GAS`, `TOXIC_GAS`, `CORROSIVE_GAS`, `TOXIC_SOLUTION`, `POLYMERIZATION`, `VIOLENT_REACTION`, `SPONTANEOUS_IGNITION`.

### `COMPATIBILITY_MAP`:
هر سطح سازگاری دارای:
- `priority`: برای مقایسه worst-case (1=سازگار، 2=احتیاط/نامشخص، 3=ناسازگار)
- `color_hex`: رنگ UI (سبز، زرد، قرمز، نارنجی)
- `label_fa`/`label_en`: برچسب دوزبانه
- `action_required`: اقدام لازم

### `DB_COMPATIBILITY_MAP`:
نگاشت مقادیر دیتابیس (`Compatible`, `Caution`, `Incompatible`, `C`, `I-C`, `I`, `N`) به Enum.

### ثابت‌های ویژه:
- `WATER_GROUP_ID = 104` (آب و محلول‌های آبی)
- `AIR_GROUP_ID = 101`

---

# 8. سیستم ETL

## 8.1. ارکستراتور — `pipeline.py` (620 خط)

**نقش:** هماهنگ‌کننده اصلی. فایل آپلود شده را از 5 لایه عبور می‌دهد.

### تابع `run_async()`:
- پایپ‌لاین را در **Thread جداگانه** شروع می‌کند (`daemon=True`).
- هدف: سایت هنگ نکند.

### تابع `_run_pipeline()` (خطوط 296-601):
**هیچ‌وقت Crash نمی‌کند. هر خطا گرفته شده و در Batch ذخیره می‌شود.**

#### لایه 1 — فراخوانی `read_file()` از `ingest.py`:
- فایل خوانده شده و DataFrame ساخته می‌شود.
- متادیتا (encoding, confidence, warnings) ذخیره.
- اگر DataFrame خالی باشد → `status = 'error'`.

#### لایه 2 — فراخوانی `map_columns()` از `schema.py`:
- ستون‌ها به نام‌های استاندارد نگاشت می‌شوند.
- نتیجه نگاشت در Batch ذخیره.
- `df.rename(columns=canonical_rename)` اعمال.

#### پیش‌پردازش — `remove_repeated_headers()`:
- هدرهای تکراری در میان داده حذف می‌شوند.

#### لایه 3+4 — پردازش سطر به سطر:
برای هر سطر:
1. **Layer 3:** `validate_row()` از `clean.py` → داده پاکسازی + issues + quality_score.
2. **Layer 4:** `matcher.match()` از `match.py` → تطبیق با دیتابیس.
3. **اگر UNIDENTIFIED شد:** `attempt_last_ditch_recovery()` → تلاش نهایی.
4. **اعتبارسنجی Pydantic** با `MatchResult`.
5. **Auto-fill:** اگر نام خالی بود ولی CAS/UN مچ شد → نام از DB پر شود.
6. ذخیره در `inventory_staging` + `audit_trail`.
7. اگر `REVIEW_REQUIRED/UNIDENTIFIED` → اضافه به `review_queue`.
8. اگر هر سطر خطا بزند → ذخیره `ERROR` بدون Crash.

#### لایه 5 — `generate_summary()`:
- گزارش نهایی تولید و در Batch ذخیره.

---

## 8.2. لایه 1: `ingest.py` (982 خط)

**وظیفه:** خواندن هر نوع فایل بدون Crash.

### تابع اصلی `read_file(filepath)`:
- فراخوانی `smart_ingest()`.
- اگر موفق: متادیتا در `df.attrs['ingestion_metadata']` ذخیره.
- اگر ناموفق: DataFrame خالی برگردانده.

### تابع `smart_ingest(filepath)` (خطوط 131-304):
1. **شناسایی نوع فایل:** `_detect_file_type()` — از extension و magic bytes.
2. **خواندن بر اساس نوع:**
   - Excel: `_read_excel_smart()` با انتخاب هوشمند شیت.
   - CSV: `_read_csv_smart()` با تست چندین encoding.
   - TXT/TSV: `_read_text_smart()` با تشخیص delimiter.
   - JSON: `_read_json_safe()`.
3. **حذف سطرهای خالی:** `_remove_empty_rows()`.
4. **تشخیص هدر:** اگر هدر تشخیص داده نشد، سطر اول به عنوان هدر استفاده شود.

### ویژگی‌های خاص:
- **Encoding detection:** 6 encoding تست: `utf-8-sig`, `utf-8`, `cp1256`, `cp1252`, `latin-1`, `iso-8859-1`.
- **پشتیبانی فارسی:** کلمات کلیدی هدر فارسی (`نام`, `ماده`, `کالا`, `مقدار`, `انبار`).
- **انتخاب شیت اکسل:** الگوریتم امتیازدهی بر اساس نام شیت و تعداد سطرها.

---

## 8.3. لایه 2: `schema.py` (1446 خط)

**وظیفه:** تشخیص معنایی هر ستون و نگاشت به نام استاندارد.

### 4 استراتژی تشخیص:

1. **Keyword Matching (دوزبانه):** دیکشنری جامع فارسی و انگلیسی برای هر نوع ستون:
   - `name`: نام، ماده، chemical, product, material
   - `cas`: cas, cas number, شماره cas
   - `quantity`: مقدار، تعداد، amount, qty
   - `location`: انبار، محل، warehouse, storage
   - و 13 نوع دیگر...

2. **Definitive Rules:** قوانین قطعی مثل regex CAS (`\d{2,7}-\d{2}-\d`)، تشخیص ارز، فرمول شیمیایی.

3. **Content Analysis:** بررسی محتوای نمونه از 100 سطر اول:
   - آیا اعداد فقط حاوی ارقام هستند؟ (quantity)
   - آیا الگوی CAS دارند؟
   - آیا نام‌های شیمیایی شناخته شده در آن‌ها هست؟

4. **Cross-validation با CAMEO:** بررسی در ایندکس نام‌ها/CASهای واقعی دیتابیس.

### خروجی:
```python
{
    'canonical_rename': {'ستون اصلی': 'name', ...},
    'critical_fields_found': ['name', 'cas'],
    'missing_fields': ['location'],
    'warnings': [...]
}
```

---

## 8.4. لایه 3: `clean.py` (854 خط)

**وظیفه:** پاکسازی و اعتبارسنجی هر سطر.

### توابع پاکسازی رشته:
- **`sanitize_string()`:** حذف کاراکترهای نامرئی (ZWSP, NBSP)، تبدیل ارقام فارسی/عربی (۰-۹ → 0-9)، نرمال‌سازی NaN.
- **`convert_persian_digits()`:** `str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')`.

### اعتبارسنجی CAS:
- **`validate_cas()`:** الگوریتم Checksum رسمی CAS. فرمت: `XXXXXXX-YY-Z`.
- کدهای 4 رقمی عمومی رد می‌شوند (مثل 1080, 1115) — اینها کد گروه هستند نه CAS.
- **`scan_cas_from_all_columns()`:** اسکن regex تمام ستون‌ها برای یافتن CAS.
- **`reconstruct_cas_from_digits()`:** تلاش ساخت CAS از رشته عددی خالص (مثلاً `7664939` → `7664-93-9`).

### نرمال‌سازی:
- **`normalize_concentration()`:** استخراج غلظت از نام (مثلاً `Hydrogen Peroxide 30%` → `Hydrogen Peroxide` + `30%`).
- **`normalize_formula()`:** تبدیل `H₂SO₄` → `H2SO4`, رفع اشتباه OCR `H202` → `H2O2`.

### تابع اصلی `validate_row()` (خطوط 400-571):
1. هر فیلد سطر sanitize می‌شود.
2. بررسی وجود نام (اگر نباشد error).
3. CAS اعتبارسنجی (checksum).
4. Quantity و Unit پارس می‌شوند.
5. امتیاز کیفیت (0-100) محاسبه.
6. لیست issues برگردانده.

---

## 8.5. لایه 4: `match.py` (1093 خط)

**وظیفه:** تطبیق هوشمند نام/CAS/فرمول با دیتابیس CAMEO.

### معماری: **Hybrid Multi-Signal Matching**
هر فیلد مستقلاً "سیگنال" تولید می‌کند و سپس Fusion بهترین را انتخاب می‌کند.

### کلاس `Signal`:
- `chemical_id`, `chemical_name`, `source` (منبع سیگنال), `raw_score`, `weight`.

### وزن سیگنال‌ها:
```
exact_cas: 1.00        ← CAS دقیق = قطعی
cas_nodash: 0.95       ← CAS بدون خط‌تیره
exact_name: 0.98       ← نام دقیق
synonym_exact: 0.90    ← مترادف دقیق
formula_name: 0.88     ← فرمول + نام
un_exact: 0.85         ← UN دقیق
semantic: 0.80         ← امتیاز معنایی
name_fuzzy: 0.75       ← تطبیق فازی نام
synonym_fuzzy: 0.65    ← تطبیق فازی مترادف
```

### آستانه‌ها:
- `MATCHED`: confidence ≥ 0.80
- `REVIEW_REQUIRED`: confidence 0.50-0.80
- `UNIDENTIFIED`: confidence < 0.50

### کلاس `HybridMatcher`:
- در `__init__` کش‌ها ساخته می‌شوند (نام‌ها، CASها، فرمول‌ها از DB).
- تابع `match(cleaned)` تمام سیگنال‌ها را تولید و Fusion می‌کند.

### دیکشنری `INDUSTRIAL_SYNONYMS` (120+ مدخل):
- نگاشت اسامی تجاری/عامیانه → نام استاندارد CAMEO.
- مثال: `alcohol → ethanol`, `ipa → isopropanol`, `muriatic acid → hydrochloric acid`.

---

## 8.6. `semantics.py` (727 خط)

**وظیفه:** تحلیل معنایی عمیق نام مواد شیمیایی.

### نقش‌های توکن (`TokenRole`):
- `BASE`: ماده اصلی (مثل Sodium, Acetone)
- `SALT`: نمک (مثل Sulfate, Chloride, Phosphate)
- `FORM`: فرم فیزیکی (Solution, Anhydrous, Powder)
- `GRADE`: درجه کیفیت (AR, Reagent, Technical)
- `CONC`: غلظت (30%, w/v)
- `SAFETY`: سافتی (Flavor, Wax)
- `HAZARD`: خطرناک (Phosphorus, Cyanide)
- `NOISE`: نویز (the, a, 123)

### تابع `semantic_score(input, candidate)`:
- توکن‌های هر نام طبقه‌بندی می‌شوند.
- امتیاز بر اساس overlap توکن‌های BASE و SALT.
- **Safety Veto:** اگر ورودی `SAFETY` context داشته باشد ولی کاندید `HAZARD` باشد → match رد.

### تابع `classify_material(name)`:
- پیش‌فیلتر: شناسایی موادی که اصلاً شیمیایی نیستند (موبل، پلاستیک...).

---

## 8.7. `header_guard.py` (88 خط)

- اگر ≥50% سلول‌های غیرخالی یک سطر با نام ستون‌ها مطابقت داشته باشند → آن سطر هدر تکراری است و حذف می‌شود.

## 8.8. `last_ditch_recovery.py` (223 خط)

آخرین تلاش بازیابی برای سطرهای `UNIDENTIFIED`:
- **Strategy A:** اسکن regex تمام سلول‌ها برای CAS و UN.
- **Strategy B:** اگر ستون CAS حاوی نام است → جستجو به عنوان نام؛ اگر ستون نام حاوی CAS است → جستجو به عنوان CAS.
- همیشه `REVIEW_REQUIRED` برگردانده (هرگز مستقیم `MATCHED` نیست).

## 8.9. `match_cascade.py` (258 خط)

**ماچر جایگزین** (Phase 1). اولویت‌بندی سلسله‌مراتبی:
1. CAS exact → CONFIRMED (1.0)
2. UN exact → CONFIRMED (0.98)
3. Formula + Name fuzzy > 85% → CONFIRMED (0.90)
4. Synonym exact → CONFIRMED (0.95)
5. Name fuzzy ≥ 90% → REVIEW (0.75)
6. Name fuzzy ≥ 70% → REVIEW (0.60)
7. None → UNIDENTIFIED

## 8.10. `models.py` (78 خط) — مدل‌های Pydantic

**پروتکل Anti-Hallucination:** هر خروجی match باید از این اعتبارسنج عبور کند.

- `MatchResult`: `chemical_id` باید None یا ID واقعی باشد.
- `match_status` فقط `MATCHED`, `REVIEW_REQUIRED`, `UNIDENTIFIED`.
- `confidence` بین 0.0 و 1.0.

## 8.11. `report.py` (140 خط)

- تمام سطرهای یک Batch خوانده و گزارش تولید:
  - تعداد `matched`, `review_required`, `unidentified`.
  - `match_rate`, `avg_quality_score`, `avg_confidence`.
  - `method_breakdown` (شمارش هر روش match).
  - `top_issues` (مرتب بر اساس فراوانی).

---

# 9. مسیرهای API (Routes / Blueprints)

## 9.1. `inventory.py` (464 خط) — Blueprint: `inventory_bp`

| Route | Method | شرح |
|-------|--------|------|
| `/admin/import` | GET | صفحه آپلود فایل |
| `/api/inventory/upload` | POST | آپلود فایل → ساخت Batch → شروع ETL |
| `/api/inventory/status/<batch_id>` | GET | وضعیت پردازش (Polling) |
| `/api/inventory/rows/<batch_id>` | GET | تمام سطرهای Staging |
| `/api/inventory/review/<batch_id>` | GET | سطرهای نیازمند بررسی انسانی |
| `/api/inventory/confirm` | POST | تأیید دستی یک سطر (Human-in-the-loop) |
| `/api/inventory/search_chemicals` | GET | جستجو برای لینک دستی |
| `/api/inventory/column_mapping/<batch_id>` | GET | نتیجه نگاشت ستون‌ها |
| `/api/inventory/review_queue/<batch_id>` | GET | صف بررسی اولویت‌بندی شده |
| `/api/inventory/resolve_review` | POST | حل یک آیتم review + ذخیره در learning_data |
| `/api/inventory/audit/<batch_id>` | GET | مسیر ممیزی |

**ویژگی ایمنی:** `confirm_match()` قبل از تأیید، `chemical_id` واقعاً در `chemicals.db` وجود دارد بررسی می‌کند (Anti-Hallucination).

## 9.2. `inventory_actions.py` (417 خط) — Blueprint: `inventory_actions_bp`

| Route | Method | شرح |
|-------|--------|------|
| `/api/inventory/edit` | POST | ویرایش سطر Staging (با Optimistic Concurrency) |
| `/api/inventory/delete/<staging_id>` | DELETE | حذف سطر |
| `/api/inventory/add` | POST | افزودن سطر جدید با انتخاب chemical_id |

**ویژگی:** `_row_version_hash()` — هش SHA-256 برای جلوگیری از ویرایش همزمان.

## 9.3. `inventory_analysis.py` (476 خط) — Blueprint: `inventory_analysis_bp`

| Route | Method | شرح |
|-------|--------|------|
| `/api/inventory/analyze` | POST | تحلیل سازگاری تمام مواد Batch |
| `/inventory/analysis/<batch_id>` | GET | صفحه نتایج تحلیل |
| `/api/inventory/analysis/<batch_id>` | GET | دریافت نتایج JSON |
| `/api/inventory/analysis/<batch_id>/export/excel` | GET | اکسپورت به XLSX |
| `/api/inventory/analysis/<batch_id>/export/pdf` | GET | اکسپورت به PDF |

### تابع `analyze_inventory()`:
1. سطرهای MATCHED خوانده شده.
2. **اگر سطر unresolved وجود داشته باشد → خطا (باید اول بررسی شوند).**
3. `ReactivityEngine.analyze()` اجرا.
4. **Storage Proximity Analysis:** بررسی اینکه آیا مواد خطرناک در یک لوکیشن ذخیره شده‌اند.
5. نتایج در `analysis_results` ذخیره.

---

# 10. قالب‌های HTML (Templates)

| فایل | صفحه | شرح |
|------|------|------|
| `base.html` | — | قالب پایه: منوی کناری، هدر، ساختار صفحه |
| `dashboard.html` | داشبورد | KPIها، نمودارها، فعالیت اخیر |
| `inventory.html` | موجودی | لیست Batchها، آپلود فایل، مدیریت سطرها |
| `mixer.html` | میکسر | انتخاب مواد + ماتریس سازگاری N×N |
| `warehouse.html` | انبار | نقشه لوکیشن‌ها و درصد ایمنی |
| `logs.html` | لاگ‌ها | تاریخچه فعالیت‌ها |
| `chemical_detail.html` | جزئیات | اطلاعات کامل یک ماده |
| `admin_import.html` | آپلود | فرم آپلود + نمایش نتایج ETL |
| `inventory_analysis.html` | تحلیل | ماتریس ریسک + هشدارهای ذخیره‌سازی |

---

# 11. فرانت‌اند React (مستقل)

**پوشه:** `src/` — اپلیکیشن Vite + React + TypeScript

## 11.1. `main.tsx` (11 خط)
- نقطه ورود React. کامپوننت `App` در `StrictMode` رندر می‌شود.

## 11.2. `App.tsx` (220 خط)
دو صفحه:

### `SearchPage`:
- فرم جستجو با state: `query`, `results`, `selectedChemical`, `loading`.
- `handleSearch()`: `ChemicalSearchService.search(query)` → نتایج نمایش.
- `handleSelectChemical()`: `ChemicalSearchService.getChemical(id)` → جزئیات نمایش.
- لیست نتایج با دکمه Details (باز شدن تب جدید `/chemical/:id`).
- نمایش جزئیات: description, health_haz, fire_haz, NFPA ratings, molecular weight.

### `ChemicalDetailPage`:
- صفحه placeholder برای جزئیات بیشتر (در حال توسعه).

### `App`:
- `BrowserRouter` با دو مسیر: `/` (SearchPage) و `/chemical/:chemicalId` (DetailPage).

## 11.3. `ChemicalSearchService.ts` (74 خط)
- **`API_BASE_URL`:** `http://localhost:5000/api`
- متدها: `search()`, `getChemical()`, `getFavorites()`, `addFavorite()`, `removeFavorite()`.
- همه با `fetch` و error handling مناسب.

## 11.4. `types/index.ts` (125 خط)
- اینترفیس `Chemical`: 90+ فیلد (تمام خواص فیزیکی و ایمنی).
- `ChemicalSearchMeta`: اطلاعات regulatory.
- `ChemicalSummary`: خلاصه برای نتایج جستجو.
- `SearchResult`: `{ items: ChemicalSummary[], total: number }`.

## 11.5. `vite.config.ts` (39 خط)
- `react()` plugin + `sql.js` WASM copy.
- Proxy: `/api` → `http://localhost:5000`.
- سرور: `127.0.0.1:5173`.

---

# 12. جریان کامل داده: از ورود کاربر تا خروجی

## سناریو 1: جستجوی ماده شیمیایی
```
کاربر تایپ می‌کند "acetone"
  → JS: fetch('/api/search?q=acetone')
  → Flask: search()
    → SQL: SELECT DISTINCT chemicals + LEFT JOIN cas/unna WHERE name LIKE '%acetone%'
    → Python scoring: exact match = 1000, prefix = 900, ...
    → Sort by score → top 20
  → JSON: {items: [{id, name, cas, nfpa, match_type}], total: 20}
  → JS: render results list
```

## سناریو 2: آپلود فایل موجودی
```
کاربر فایل Excel آپلود می‌کند
  → POST /api/inventory/upload
    → save file → create batch → run_async()
      → Thread:
        L1 (ingest): read Excel → detect encoding → select sheet → DataFrame
        L2 (schema): detect column types → rename to canonical
        Header Guard: remove repeated headers
        L3+L4 (per row):
          clean.validate_row() → sanitize, validate CAS, parse quantity
          match.HybridMatcher.match() → signals from CAS/name/formula/UN → fusion
          if UNIDENTIFIED → last_ditch_recovery()
          validate with Pydantic MatchResult
          INSERT INTO inventory_staging + audit_trail
          if REVIEW → INSERT INTO review_queue
        L5 (report): generate_summary() → UPDATE batch status
  → Polling: GET /api/inventory/status/<batch_id>
```

## سناریو 3: تحلیل سازگاری (Mixer)
```
کاربر 3 ماده انتخاب می‌کند
  → POST /api/analyze {chemical_ids: [1, 5, 23]}
  → Flask: analyze_chemicals()
    → ReactivityEngine.analyze([1, 5, 23])
      → برای هر ماده: get groups از mm_chemical_react
      → ماتریس 3×3:
        [1,1]: self-hazards
        [1,2]: _analyze_pair(1, 5) → Cartesian groups_1 × groups_5
          → _get_rule(g_a, g_b) → reactivity table → hazards
        [1,3]: _analyze_pair(1, 23) → ...
        [2,3]: _analyze_pair(5, 23) → ...
      → water check: groups vs WATER_GROUP_ID=104
      → overall = worst case
      → save audit_log
    → JSON: {overall, matrix, critical_pairs, warnings}
  → JS: render matrix with colors (green/yellow/red)
```

## سناریو 4: بررسی انسانی (Human-in-the-loop)
```
ETL یک سطر را REVIEW_REQUIRED مارک می‌کند
  → کاربر صفحه review queue را می‌بیند
  → جستجو در chemicals: GET /api/inventory/search_chemicals?q=...
  → انتخاب ماده صحیح
  → POST /api/inventory/resolve_review {queue_id, chemical_id}
    → verify chemical exists (Anti-Hallucination)
    → UPDATE inventory_staging SET matched
    → UPDATE review_queue SET resolved
    → INSERT INTO learning_data (for future improvement)
    → INSERT INTO audit_trail
```
