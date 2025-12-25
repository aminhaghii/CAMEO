CameoChemical Offline App – Architecture & Specification
Document Version: 1.1 (Revised)
Last Updated: December 2025
Status: Canonical Project Specification
Purpose: Long-term AI project memory and development guide
1. Quick Summary
What: A fully offline desktop application for hazardous chemical information lookup and reactivity prediction
Who: Emergency responders, hazmat teams, safety officers, chemical facility planners, students, and researchers
Core Modules:
Chemical Search & Browse Engine
Chemical Detail Viewer
Reactivity/Compatibility Prediction Engine
Local Database Manager (Static Data + User Data)
Data Export & Reporting
Tech Stack (Recommended): Electron + React + TypeScript + SQLite + better-sqlite3
Key Constraints:
100% offline-first operation (no internet required after installation)
Bundled local SQLite database (chemicals.db) with thousands of chemical records
Separate local SQLite database (user.db) for user data reliability
Fast full-text search (<100ms for typical queries)
Rule-based reactivity prediction using 68+ reactive groups
Extensible architecture for future localization and data updates
2. Background & Inspiration
2.1 About NOAA CAMEO Chemicals
CAMEO Chemicals is a hazardous chemical database developed by NOAA Office of Response and Restoration in partnership with U.S. EPA Office of Emergency Management.
Key characteristics:
Target Audience: First responders, firefighters, emergency planners, hazmat teams
Primary Purpose: Provide critical response information during chemical emergencies
Data Sources: Emergency Response Guidebook (ERG), NIOSH Pocket Guide, U.S. Coast Guard CHRIS manual, International Chemical Safety Cards, Hazardous Materials Table (49 CFR 172.101), EPA regulatory data
Search Capabilities: By chemical name/synonym, CAS number, UN/NA identification number
Chemical Datasheets Include: Physical properties, health hazards, air/water hazards, firefighting recommendations, first aid procedures, spill response guidance, regulatory information
Reactivity Prediction: 68 reactive groups, predicts hazards when chemicals mix
MyChemicals Feature: Create collections and run batch reactivity predictions
Offline Capability: Desktop and mobile app versions work completely offline
2.2 Why This Project?
Create an independent implementation inspired by CAMEO Chemicals to:
Build similar tool using open/public chemical data sources
Learn best practices for offline-first desktop applications
Create extensible platform for regional customization
Support Persian (Farsi) localization in future versions
3. Product Vision & Goals
3.1 Vision Statement
Enable anyone handling hazardous materials to quickly access critical safety information and predict chemical interaction hazards, anytime, anywhere, without internet connectivity.
3.2 Primary Goals
Instant Access: Sub-second search across thousands of chemical records
Comprehensive Information: All critical safety, handling, and emergency response data
Reactivity Intelligence: Accurately predict hazards when chemicals are combined
Zero Dependencies: Function completely offline after installation
Professional Quality: Meet standards expected by emergency response professionals
Data Integrity: Robust handling of chemical data and user preferences via transactional databases
Extensibility: Design for future enhancements without architectural rewrites
3.3 Non-Goals (v1.0)
Non-Goal	Rationale
Cloud sync	Keep v1 simple; add in v1.1+
Multi-user collaboration	Single-user desktop focus
Mobile app versions	Desktop-first; mobile later
Real-time incident mapping	Focus on core features
Persian (Farsi) UI	English-first; add localization in v1.1+
Custom chemical entry	Complex validation needed; defer to v1.2+
4. Target Users & Use Cases
4.1 User Personas
Persona 1: Emergency Responder (Primary)
Firefighter/Hazmat Technician needing immediate guidance at chemical spill
Needs: Fast search, clear hazard warnings, response procedures
Persona 2: Safety Officer (Primary)
Industrial Safety Manager planning chemical storage layouts
Needs: Batch compatibility checking, detailed property data, regulatory limits
Persona 3: Emergency Planner (Secondary)
Local Emergency Planning Committee Member
Needs: Chemical inventory lookups, exportable reports
Persona 4: Student/Researcher (Secondary)
Chemistry Graduate Student researching safety data
Needs: Comprehensive data access, browsing by categories
4.2 Use Cases
ID	Use Case	Priority
UC-01	Quick Chemical Lookup (name, CAS, UN)	Must
UC-02	Browse by Category	Should
UC-03	View Chemical Details	Must
UC-04	Two-Chemical Reactivity Check	Must
UC-05	Multi-Chemical Compatibility	Must
UC-06	Save to Favorites	Should
UC-07	Print/Export Datasheet	Should
UC-08	View ERG Guide	Should
UC-09	Search History	Could
UC-10	Bulk Data Export	Could
5. Functional Requirements
5.1 Search & Navigation (FR-1xx)
ID	Requirement	Priority
FR-101	Full-text search across chemical names and synonyms	Must
FR-102	Search by exact CAS number (format: XXXXXXX-XX-X)	Must
FR-103	Search by UN/NA identification number (format: UN####)	Must
FR-104	Return results in <100ms for typical queries	Must
FR-105	Display results with: name, CAS, primary hazard icon, relevance	Must
FR-106	Filter results by: physical state, hazard class, reactive group	Should
FR-107	Auto-complete suggestions as user types	Should
FR-108	Maintain search history (last 50 searches)	Could
5.2 Chemical Detail Pages (FR-2xx)
ID	Requirement	Priority
FR-201	Display chemical identification: name, synonyms, CAS, UN/NA, formula	Must
FR-202	Display physical properties: MW, BP, MP, vapor pressure, SG, solubility	Must
FR-203	Display health hazards: toxicity routes, symptoms, exposure effects	Must
FR-204	Display fire hazards: flash point, flammability, extinguishing agents	Must
FR-205	Display reactive hazards: reactive groups, incompatibilities summary	Must
FR-206	Display emergency response: firefighting, spill response, first aid	Must
FR-207	Display protective equipment recommendations	Must
FR-208	Display regulatory information: exposure limits (PEL, TLV, IDLH)	Should
FR-209	Display environmental hazards: water reactivity, air hazards	Should
FR-210	Section-based navigation within datasheet	Should
FR-211	Show ERG guide number and link to ERG details	Should
5.3 Reactivity / Compatibility Checker (FR-3xx)
ID	Requirement	Priority
FR-301	Allow user to select 2+ chemicals for reactivity analysis	Must
FR-302	Map each chemical to its assigned reactive group(s)	Must
FR-303	Apply reactivity rules to all pairwise combinations	Must
FR-304	Display results with hazard categories (Explosive, Toxic Gas, Heat, Fire, None)	Must
FR-305	Color-code results: Red (Dangerous), Orange (Caution), Yellow (Possible), Green (Compatible)	Must
FR-306	Display detailed explanation for each predicted reaction	Should
FR-307	Allow adding chemicals by reactive group if not in database	Should
FR-308	Support saving chemical collections for future checks	Should
FR-309	Generate printable compatibility matrix	Could
FR-310	Support up to 20 chemicals in single analysis	Should
5.4 Data Management (FR-4xx)
ID	Requirement	Priority
FR-401	Include bundled SQLite database (chemicals.db) with read-only chemical data	Must
FR-402	Include separate SQLite database (user.db) for user data	Must
FR-403	Support database integrity verification on startup	Should
FR-404	Mechanism for database updates via update packages	Could
FR-405	Backup user database before updates	Could
5.5 Auxiliary Features (FR-5xx)
ID	Requirement	Priority
FR-501	Allow marking chemicals as favorites	Should
FR-502	Quick access to favorites from home screen	Should
FR-503	Support printing chemical datasheets	Should
FR-504	Support exporting datasheets as PDF	Should
FR-505	Application settings: font size, theme (light/dark)	Should
FR-506	Offline help documentation	Should
FR-507	Show application version and credits	Must
6. Non-Functional Requirements
6.1 Performance (NFR-1xx)
ID	Requirement	Target
NFR-101	Application cold start time	< 3 seconds
NFR-102	Search query response time	< 100ms (95th percentile)
NFR-103	Chemical detail page load time	< 200ms
NFR-104	Reactivity analysis (10 chemicals)	< 500ms
NFR-105	Memory usage (idle)	< 200MB
NFR-106	Database size on disk	< 500MB
NFR-107	Database Query Execution (Detail View)	< 50ms aggregate time
6.2 Reliability & Data Integrity (NFR-2xx)
ID	Requirement
NFR-201	No crash on malformed search input
NFR-202	Validate database integrity on startup
NFR-203	Gracefully handle corrupted database with user notification
NFR-204	All chemical data read-only (no user modifications to source)
NFR-205	Favorites and settings persist across restarts via transactional DB
6.3 Offline Behavior (NFR-3xx)
ID	Requirement
NFR-301	All features function without internet
NFR-302	No network requests during normal operation
NFR-303	All required data bundled with installation
6.4 UX & Maintainability (NFR-4xx, NFR-5xx)
ID	Requirement
NFR-401	Consistent color scheme for hazard levels
NFR-402	Consistent navigation across all screens
NFR-403	User-friendly error messages
NFR-404	Keyboard navigation support
NFR-501	TypeScript for type safety
NFR-502	Separate UI, business logic, and data layers
NFR-503	Externalized UI text for future localization
7. High-Level Architecture
7.1 Architecture Overview
code
Code
┌─────────────────────────────────────────────────────────────┐
│                      ELECTRON SHELL                          │
├─────────────────────────────────────────────────────────────┤
│  RENDERER PROCESS (UI Layer)                                 │
│  ┌─────────┐ ┌─────────┐ ┌───────────┐ ┌─────────┐         │
│  │ Search  │ │Chemical │ │Reactivity │ │Settings │         │
│  │ Screen  │ │ Detail  │ │  Checker  │ │ Screen  │         │
│  └─────────┘ └─────────┘ └───────────┘ └─────────┘         │
├─────────────────────────────────────────────────────────────┤
│  APPLICATION LAYER (Services)                                │
│  ┌────────────┐ ┌────────────┐ ┌──────────────────┐        │
│  │SearchSvc   │ │ChemicalSvc │ │ReactivityEngine  │        │
│  ├────────────┤ ├────────────┤ ├──────────────────┤        │
│  │FavoritesSvc│ │ExportSvc   │ │SettingsSvc       │        │
│  └────────────┘ └────────────┘ └──────────────────┘        │
├─────────────────────────────────────────────────────────────┤
│  DATA ACCESS LAYER (Main Process via IPC)                    │
│  ┌──────────────────┐  ┌─────────────────────┐             │
│  │ ReadOnly DB Svc  │  │ UserWrite DB Svc    │             │
│  │ (chemicals.db)   │  │ (user.db)           │             │
│  └──────────────────┘  └─────────────────────┘             │
├─────────────────────────────────────────────────────────────┤
│  STORAGE LAYER                                               │
│  ┌──────────────────┐  ┌─────────────────────┐             │
│  │  chemicals.db    │  │     user.db         │             │
│  │  (Read Only)     │  │   (Read/Write)      │             │
│  └──────────────────┘  └─────────────────────┘             │
└─────────────────────────────────────────────────────────────┘
7.2 Key Architectural Decisions
Decision	Rationale
Electron	Cross-platform, proven for similar apps, good offline support
React	Component-based, large ecosystem, good TypeScript support
SQLite (better-sqlite3)	Single-file, excellent read performance, FTS5 support
Separate DBs	Isolate static reference data (chemicals.db) from user data (user.db) for safer updates
Flat Tables	Use wide, flat tables for properties instead of EAV for query performance
IPC for main/renderer	Standard Electron pattern, security best practice
8. Data Model & Storage Design
8.1 Storage Technology
Static Database: chemicals.db (SQLite)
Read-only for the application.
Contains all chemical data, reactivity rules, and texts.
Uses FTS5 for search.
User Database: user.db (SQLite)
Read-write.
Stores favorites, history, settings, and collections.
Ensures data integrity via transactions.
8.2 Core Tables (chemicals.db) - Improved Flat Design
Table: chemicals
Column	Type	Description
id	INTEGER	Primary key
name	TEXT	Primary chemical name (Indexed)
cas_number	TEXT	CAS Registry Number (Indexed)
un_number	TEXT	UN identification number (Indexed)
molecular_formula	TEXT	Chemical formula
molecular_weight	REAL	MW in g/mol
description	TEXT	Brief description
Table: synonyms
Column	Type	Description
id	INTEGER	Primary key
chemical_id	INTEGER	FK to chemicals
synonym	TEXT	Alternative name (Indexed for FTS)
synonym_type	TEXT	"common", "trade", "systematic"
Table: physical_properties (Flattened)
Note: Using a flat structure instead of EAV for faster queries.
| Column | Type | Description |
|--------|------|-------------|
| chemical_id | INTEGER | PK/FK to chemicals |
| boiling_point_text | TEXT | Display text (e.g., "100 °C (212 °F)") |
| boiling_point_c | REAL | Numeric value for sorting/filtering |
| melting_point_text | TEXT | Display text |
| melting_point_c | REAL | Numeric value |
| vapor_pressure_mmhg | REAL | Vapor pressure |
| specific_gravity | REAL | Specific gravity |
| water_solubility_text| TEXT | Text description of solubility |
| flash_point_c | REAL | Flash point in Celsius |
| density | TEXT | Density description |
Table: health_hazards (Flattened)
Column	Type	Description
chemical_id	INTEGER	PK/FK to chemicals
inhalation_symptoms	TEXT	Symptoms if inhaled
skin_symptoms	TEXT	Symptoms on skin contact
eye_symptoms	TEXT	Symptoms on eye contact
ingestion_symptoms	TEXT	Symptoms if swallowed
toxicity_summary	TEXT	General toxicity overview
exposure_limits_summary	TEXT	Summary of PEL/TLV
Table: fire_hazards (Flattened)
Column	Type	Description
chemical_id	INTEGER	PK/FK to chemicals
flammability_class	TEXT	NFPA class
extinguishing_agents	TEXT	Recommended agents
fire_fighting_procedures	TEXT	How to fight fire
explosion_hazards	TEXT	Specific explosion risks
Table: response_guidelines
Column	Type	Description
chemical_id	INTEGER	PK/FK to chemicals
spill_leak_procedures	TEXT	Cleanup instructions
first_aid_procedures	TEXT	Medical response
isolation_distance_m	INTEGER	Initial isolation distance
Table: reactive_groups
Column	Type	Description
id	INTEGER	Primary key
name	TEXT	Group name
description	TEXT	Description
Table: chemical_reactive_groups
Column	Type	Description
chemical_id	INTEGER	FK to chemicals
reactive_group_id	INTEGER	FK to reactive_groups
Table: reactivity_rules
Column	Type	Description
id	INTEGER	Primary key
group_id_1	INTEGER	FK to reactive_groups
group_id_2	INTEGER	FK to reactive_groups
hazard_level	TEXT	"none", "caution", "warning", "dangerous"
hazard_types_json	TEXT	JSON array: ["heat","toxic_gas"]
description	TEXT	Explanation of reaction
8.3 User Database Schema (user.db) - New Structure
Table: favorites
Column	Type	Description
id	INTEGER	Primary Key
chemical_id	INTEGER	FK to chemicals (logical)
added_at	DATETIME	Timestamp
note	TEXT	User note (optional)
Table: collections
Column	Type	Description
id	INTEGER	Primary Key
name	TEXT	Name of collection (e.g., "Warehouse B")
created_at	DATETIME	Timestamp
Table: collection_items
Column	Type	Description
collection_id	INTEGER	FK to collections
chemical_id	INTEGER	Logical FK to chemicals
Table: search_history
Column	Type	Description
id	INTEGER	Primary Key
query	TEXT	Search text
timestamp	DATETIME	When searched
Table: app_settings
Column	Type	Description
key	TEXT	Primary Key (e.g., "theme")
value	TEXT	Value (e.g., "dark")
9. Application Flows (Input → Processing → Output)
9.1 Flow 1 – Chemical Search
Inputs:
User: search_query, search_type, filters, page, limit
System: chemicals.db (FTS5 tables)
Processing:
Validate/sanitize input
Detect query type (CAS/UN/text)
Execute SQL query against chemicals.db
Apply filters (using fast numerical columns in physical_properties)
Paginate and format results
Outputs:
code
TypeScript
interface SearchResult {
  items: ChemicalSummary[];
  pagination: { page, limit, total_count, total_pages };
  query_info: { original_query, detected_type, execution_time_ms };
}
9.2 Flow 2 – View Chemical Detail
Inputs:
User: chemical_id
System: chemicals.db (Read data), user.db (Check favorites)
Processing:
Validate ID
Execute optimized SQL JOIN query to fetch data from chemicals, physical_properties, health_hazards, etc. (Single query or parallel queries)
Check user.db to see if chemical_id exists in favorites table
Aggregate into structured object
Outputs:
code
TypeScript
interface ChemicalDetail {
  id, name, synonyms, cas_number, un_number, molecular_formula;
  physical_properties: PhysicalPropertiesFlat;
  health_hazards: HealthHazardsFlat;
  // ... other sections
  is_favorite: boolean;
}
9.3 Flow 3 – Reactivity Check
Inputs:
User: chemical_ids[], reactive_group_ids[], include_water
System: chemicals.db
Processing:
Validate input (2-20 items)
Resolve chemicals to reactive groups
Build unique group set
Generate all pairwise combinations
Lookup reactivity rules for each pair
Calculate summary statistics
Outputs:
code
TypeScript
interface ReactivityResult {
  input_summary: {...};
  pairwise_results: PairwiseReaction[];
  summary: {
    compatible_pairs, caution_pairs, warning_pairs, dangerous_pairs;
    overall_assessment: "COMPATIBLE" | "CAUTION" | "WARNING" | "DANGEROUS";
  };
}
10. Internal APIs / Service Layer
10.1 ChemicalSearchService
code
TypeScript
interface ChemicalSearchService {
  search(query: string, options?: SearchOptions): Promise<SearchResult>;
  getSuggestions(prefix: string, limit?: number): Promise<string[]>;
  detectQueryType(query: string): "name" | "cas" | "un" | "na";
}
10.2 ChemicalDetailsService
code
TypeScript
interface ChemicalDetailsService {
  getById(id: number): Promise<ChemicalDetail>;
  getByCAS(casNumber: string): Promise<ChemicalDetail | null>;
  getByUN(unNumber: string): Promise<ChemicalDetail | null>;
  getMultiple(ids: number[]): Promise<ChemicalDetail[]>;
}
10.3 ReactivityEngine
code
TypeScript
interface ReactivityEngine {
  checkReactivity(params: {
    chemicalIds?: number[];
    reactiveGroupIds?: number[];
    includeWater?: boolean;
  }): Promise<ReactivityResult>;
  getReactiveGroups(chemicalId: number): Promise<ReactiveGroupInfo[]>;
  getAllReactiveGroups(): Promise<ReactiveGroupInfo[]>;
  getRule(groupId1: number, groupId2: number): Promise<ReactivityRule | null>;
}
10.4 FavoritesService (Writes to user.db)
code
TypeScript
interface FavoritesService {
  getAll(): Promise<FavoriteItem[]>;
  add(chemicalId: number, note?: string): Promise<FavoriteToggleResult>;
  remove(chemicalId: number): Promise<FavoriteToggleResult>;
  isFavorite(chemicalId: number): Promise<boolean>;
}
10.5 ExportService
code
TypeScript
interface ExportService {
  exportToPDF(chemicalId: number, options?: ExportOptions): Promise<ExportResult>;
  exportReactivityReport(result: ReactivityResult): Promise<ExportResult>;
  print(chemicalId: number): Promise<void>;
}
10.6 SettingsService (Writes to user.db)
code
TypeScript
interface SettingsService {
  getAll(): Promise<AppSettings>;
  get<K extends keyof AppSettings>(key: K): Promise<AppSettings[K]>;
  set<K extends keyof AppSettings>(key: K, value: AppSettings[K]): Promise<void>;
  resetToDefaults(): Promise<void>;
}
11. UI / UX Structure
11.1 Screen Map
code
Code
┌─────────────────────────────────────────────────────┐
│                    HOME SCREEN                       │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐ │
│  │Quick Search │  │ Favorites   │  │ Collections │ │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘ │
└─────────┼────────────────┼────────────────┼────────┘
          │                │                │
          ▼                ▼                ▼
┌─────────────────┐  ┌─────────────┐  ┌─────────────────┐
│  SEARCH RESULTS │  │  FAVORITES  │  │   COLLECTION    │
│     SCREEN      │  │    LIST     │  │      VIEW       │
└────────┬────────┘  └──────┬──────┘  └────────┬────────┘
         │                  │                   │
         └──────────────────┼───────────────────┘
                            ▼
                 ┌─────────────────────┐
                 │   CHEMICAL DETAIL   │
                 │       SCREEN        │
                 └─────────────────────┘

┌─────────────────────────────────────────────────────┐
│               REACTIVITY CHECKER                     │
│  Select chemicals → Run Analysis → View Results     │
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│                   SETTINGS                           │
│  Theme │ Font Size │ Database Info │ About          │
└─────────────────────────────────────────────────────┘
11.2 Screen Details
Home Screen
Purpose: Entry point, quick access to main features
Sections:
Search bar (prominent, centered)
Recent searches (from user.db)
Favorites quick access (from user.db)
Quick links: Reactivity Checker, Browse Categories
Search Results Screen
Purpose: Display matching chemicals for a query
Sections:
Search bar (editable)
Filter panel (collapsible sidebar)
Results list (scrollable)
Pagination controls
Data Displayed: ChemicalSummary objects
Interactions: Click result → Chemical Detail; Add to favorites; Add to collection
Chemical Detail Screen
Purpose: Comprehensive chemical information
Sections (tabs or scrollable):
Overview: Name, identifiers, description, hazard summary icons
Properties: Physical/chemical properties table (Flat data)
Health Hazards: By exposure route with severity badges
Fire Hazards: Flammability data, extinguishing agents
Response: Firefighting, spill, first aid procedures
Regulatory: Exposure limits table (PEL, TLV, IDLH)
Reactivity: Assigned groups, incompatibilities
ERG Guide: (if UN number exists) Full guide content
Actions: Toggle favorite, Add to collection, Print, Export PDF
Reactivity Checker Screen
Purpose: Check compatibility between chemicals
Sections:
Chemical selector (search + add)
Selected chemicals list (with remove option)
"Include Water" toggle
"Check Reactivity" button
Results: Compatibility matrix + detailed warnings
Data Displayed: ReactivityResult
Settings Screen
Purpose: Application configuration
Sections:
Appearance: Theme (light/dark), Font size
Search: Default search type, Results per page
Database: Version, Record count, Last updated, Verify integrity
About: App version, Credits, License info
12. Technology Stack Recommendation
12.1 Recommended Stack
Layer	Technology	Version	Justification
Runtime	Electron	28.x	Cross-platform, proven for offline apps, used by CAMEO
Frontend	React	18.x	Component-based, large ecosystem, excellent TypeScript support
Language	TypeScript	5.x	Type safety, better IDE support, maintainability
Styling	TailwindCSS	3.x	Utility-first, rapid development, small bundle
Components	shadcn/ui	Latest	Accessible, customizable, React-based
Icons	Lucide React	Latest	Consistent, comprehensive icon set
Database	SQLite	3.x	Single-file, excellent read performance, FTS5
DB Driver	better-sqlite3	9.x	Synchronous API, better performance than sqlite3
State Mgmt	Zustand	4.x	Simple, TypeScript-friendly, minimal boilerplate
Routing	React Router	6.x	Standard React routing solution
PDF Export	electron-pdf	Latest	Native PDF generation in Electron
Build Tool	Vite	5.x	Fast builds, excellent Electron integration
Packaging	electron-builder	Latest	Cross-platform packaging, auto-updates
12.2 Why This Stack?
Electron: CAMEO Chemicals itself uses Electron. It provides:
True offline capability with bundled resources
Cross-platform (Windows, macOS, Linux)
Access to native file system for database
Mature ecosystem for desktop apps
React + TypeScript: Best combination for:
Complex UI with many interactive components
Type-safe development reducing runtime errors
Large community and extensive libraries
Easy to find developers familiar with stack
SQLite + better-sqlite3:
SQLite is proven for offline apps with large datasets
better-sqlite3 offers synchronous API (simpler code) and better performance
FTS5 provides fast full-text search without external dependencies
Single file database easy to bundle and distribute
12.3 Project Structure
code
Code
cameochemical-app/
├── electron/
│   ├── main.ts              # Electron main process
│   ├── preload.ts           # IPC bridge
│   └── database/
│       ├── chemicals.ts     # Connection to read-only DB
│       └── user.ts          # Connection to read-write DB
├── src/
│   ├── components/          # React components
│   │   ├── ui/              # Base UI components
│   │   ├── search/          # Search-related
│   │   ├── chemical/        # Chemical detail
│   │   └── reactivity/      # Reactivity checker
│   ├── services/            # Business logic
│   ├── stores/              # Zustand stores
│   ├── hooks/               # Custom React hooks
│   ├── types/               # TypeScript types
│   ├── utils/               # Utility functions
│   └── App.tsx              # Root component
├── resources/
│   ├── chemicals.db         # Pre-built Static DB
│   └── user.db              # Template for new user DB
├── scripts/                 # ETL and Build scripts
├── package.json
└── vite.config.ts
13. Roadmap
Phase 1: Foundation & Data Pipeline (4-6 weeks)
Goal: Build the data engine and core search.
Week	Tasks
1-2	ETL Pipeline Development: Scrapers, data cleaning, SQLite generation script
3	Project setup (Electron + React), DB connection setup (better-sqlite3)
4	Search service + FTS implementation
5	Chemical detail page (connected to new flat tables)
6	Basic UI polish, packaging test
Deliverables:
chemicals.db generator script
Working search & detail view
DB Query performance verification (<50ms)
Phase 2: Reactivity Engine & User DB (3-4 weeks)
Goal: Full reactivity prediction and user persistence.
Week	Tasks
7	Reactive groups data logic, rules engine implementation
8	User DB Implementation: favorites & collections tables
9	Reactivity UI (selector, matrix, details)
10	Collections feature linked to User DB
Deliverables:
Functioning Reactivity Matrix
Persistent Favorites/Collections (saved in user.db)
Phase 3: Polish & Export (2-3 weeks)
Goal: Production-ready features
Week	Tasks
11	Print/Export PDF functionality
12	Settings screen, theme support
13	Help documentation, final polish
Deliverables:
PDF Export
Complete Settings module
Phase 4: v1.0 Release (1-2 weeks)
Week	Tasks
14	Final testing, performance optimization
15	Installer creation, documentation
14. Testing & Validation
14.1 Testing Strategy
Test Type	Tool	Coverage
Unit Tests	Vitest	Services, utilities, data transformations
Component Tests	React Testing Library	UI components
Integration Tests	Vitest + better-sqlite3	Database operations (Both DBs)
E2E Tests	Playwright	Critical user flows
14.2 Database Integrity Tests
code
TypeScript
describe('UserDatabase', () => {
  test('saves and retrieves favorites reliably', async () => {
    await favoritesService.add(101, 'Test Note');
    const favs = await favoritesService.getAll();
    expect(favs).toHaveLength(1);
    expect(favs[0].note).toBe('Test Note');
  });
  
  test('prevents duplicates in favorites', async () => {
    await favoritesService.add(101);
    await expect(favoritesService.add(101)).rejects.toThrow(); 
    // Or handle gracefully depending on implementation
  });
});
14.3 Search & Reactivity Tests
(Same as previous version, verifying search logic and reaction predictions)
15. Future Extensions
15.1 v1.1 - Localization & Updates
Persian (Farsi) UI: Externalized strings, RTL layout support
Database Updates: Download and apply update packages
ERG Integration: Full ERG 2024 guide content with isolation distances
15.2 v1.2 - Enhanced Features
Custom Chemicals: User-defined chemicals with validation
User Notes: Add personal notes to chemical records
Advanced Search: Boolean operators, range queries
15.3 v2.0 - Advanced Capabilities
Cloud Sync: Optional sync of favorites and collections
Reactivity Visualization: Graphical representation of reaction pathways
Mobile Apps: iOS and Android versions sharing database
16. Glossary & Notes for Future Agents
Term	Definition
ETL	Extract, Transform, Load - The process of scraping web/PDF data and putting it into chemicals.db
Flat Table	A database table design where attributes are columns, not rows (Contrast with EAV)
CAS Number	Chemical Abstracts Service registry number
Reactive Group	Category of chemicals with similar reactivity
16.2 Notes for Future AI Agents
IMPORTANT NOTES:
Data Model: Strictly follow the Flat Table design for properties. Do not use EAV tables.
User Data: Always use user.db for storing user-generated data. Do not write to JSON files for critical user data.
Performance: Prioritize query optimization. JOINs on indexed columns are preferred over processing data in JavaScript.
ETL Pipeline: The chemicals.db is an artifact produced before the app build. It is not created at runtime.
17. Metadata
code
Yaml
document:
  title: "CameoChemical Offline App - Architecture & Specification"
  version: "1.1"
  created: "2024-12-25"
  revision: "Revised with Flat DB Schema and ETL Pipeline"
  status: "Canonical Specification"
  
project:
  name: "CameoChemical Offline App"
  type: "Desktop Application"
  
tech_stack:
  runtime: "Electron 28.x"
  database: "SQLite 3.x (Dual DB Architecture)"
18. Data Ingestion Pipeline (ETL) - NEW SECTION
This section defines how the chemicals.db is constructed before the application runs.
18.1 Pipeline Overview
The application relies on a pre-populated SQLite database. This database is generated via a Python-based ETL pipeline.
Steps:
Extract (Scraping/Parsing):
Source: CAMEO Chemicals website (HTML) or PDF datasheets.
Tool: Python + Playwright / BeautifulSoup / PyPDF2.
Output: Raw JSON files per chemical.
Transform (Cleaning & Normalization):
Processing:
Standardize CAS numbers (remove spaces).
Parse physical properties (extract numbers from strings like "100 °C" -> 100).
Map Reactive Groups to IDs.
Flatten nested JSON into table structures.
Validation: Ensure every chemical has a Name and at least one ID (CAS/UN).
Load (Database Generation):
Tool: Python sqlite3 library.
Action:
Create Schema (as defined in Section 8).
Insert data into chemicals, physical_properties, etc.
Build FTS5 indexes.
VACUUM the database to minimize size.
Outcome: A single, optimized chemicals.db file placed in the resources/ directory of the Electron project.
END OF SPECIFICATION DOCUMENT