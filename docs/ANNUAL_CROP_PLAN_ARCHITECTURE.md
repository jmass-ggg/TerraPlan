# Annual Crop Plan Feature - Architecture Documentation

## Overview

The Annual Crop Plan feature provides:
1. **12-month recommendation grid** - Independent monthly crop suitability scoring
2. **Saved Schedule** - Persistent planting calendar with overlap validation and harvest tracking

**Current Status:** The monthly grid runs independent suitability scoring (doesn't model crop calendars). The Saved Schedule properly models planting → growth → harvest cycles.

---

## File Structure

### Backend Files

#### Core Logic
```
farmtwin/backend/
├── data/crops/
│   └── requirements_v1.yaml              # Crop database (18 crops: cereals, legumes, vegetables, roots, fruits)
├── app/domain/
│   ├── crop_register.py                  # Loads crops from YAML into immutable CROP_REGISTER tuple
│   ├── crop_engine.py                    # Scores crops against environmental conditions (deterministic)
│   └── snapshot_context.py               # Environmental context builder (not shown here)
├── app/services/
│   └── planner_service.py                # Annual plan business logic (12-month grid + saved entries)
├── app/api/v1/
│   ├── planner.py                        # REST endpoints for annual plan
│   └── planner_schemas.py                # Pydantic request/response schemas
└── app/models/
    └── plan.py                           # SQLAlchemy models (PlanEntry, ChangeProposal)
```

#### Database
```sql
-- Tables
plan_entries          # Saved planting schedule
change_proposals      # Notifications when environmental data changes affect saved entries
```

### Frontend Files

```
farmtwin/frontend/
└── app/app/farms/[farmId]/annual-plan/
    └── page.tsx                          # Annual Planner UI (monthly grid + saved schedule panel)
```

---

## Backend Architecture

### 1. Crop Database (`data/crops/requirements_v1.yaml`)

**Purpose:** Defines agronomic requirements for all crops

**Structure:**
```yaml
version: "1.1"
last_updated: "2026-09-18"
crops:
  - name: "Maize"
    category: "cereal"                    # cereal | legume | vegetable | root | fruit
    temperature:
      min_c: 10
      optimum_c: 25
      max_c: 35
    rainfall_per_duration_mm:
      min: 400
      max: 760
    drought_tolerance: 0.45               # 0.0 (none) to 1.0 (very high)
    heat_tolerance_ceiling_c: 35          # Lethal temperature threshold
    duration_months: 4                    # Growing period length
    soil_ph: { min: 5.5, max: 7.5 }
    soil_texture_preference: [loam, clay_loam, silt_loam, sandy_loam]
```

**Current Crops (18 total):**
- 4 cereals: Maize, Wheat, Rice, Sorghum
- 2 legumes: Beans, Soybean
- 3 vegetables: Tomato, Onion, Cabbage
- 3 roots: Potato, Carrot, Sweet Potato
- 6 fruits: Mango, Banana, Orange, Papaya, Avocado, Pineapple

**To Add Crops:** Add new entry to YAML, restart backend to reload

---

### 2. Crop Register (`app/domain/crop_register.py`)

**Purpose:** Loads YAML into immutable runtime data structure

**Key Export:**
```python
CROP_REGISTER: tuple[CropRequirements, ...]  # Loaded at import time
```

**CropRequirements dataclass:**
```python
@dataclass(frozen=True)
class CropRequirements:
    name: str
    category: str  # cereal | legume | vegetable | root | fruit
    min_temp_c: float
    optimum_temp_c: float
    max_temp_c: float
    min_rainfall_mm: float
    max_rainfall_mm: float
    drought_tolerance: float  # 0.0 to 1.0
    heat_tolerance_ceiling_c: float
    duration_months: int
    # ... soil fields
```

---

### 3. Crop Scoring Engine (`app/domain/crop_engine.py`)

**Purpose:** Deterministic suitability scoring (0-100) for crop + environment

**Key Function:**
```python
def score(crop: CropRequirements, context: SnapshotContext) -> SimulationResult
```

**SimulationResult:**
```python
@dataclass(frozen=True)
class SimulationResult:
    crop_name: str
    suitability_index: int | None      # 0-100 weighted score
    label: str                         # "Good match" | "Possible match" | "Higher caution"
    limiting_factor: str               # Weakest component (temp, water, soil, heat, drought, env)
    reason: str                        # Plain-language explanation
    components: ComponentScores        # Individual scores (temp, water, soil, etc.)
```

**Scoring Components (weighted):**
- Temperature (25%): Distance from optimum range
- Water/Rainfall (30%): Rainfall adequacy vs crop needs
- Soil (20%): pH + texture compatibility
- Heat Safety (10%): Distance from lethal ceiling
- Drought Safety (10%): Rainfall + VPD vs drought tolerance
- Environmental Condition (5%): NDVI (vegetation health)

**Hard Exclusion:** If temperature exceeds `heat_tolerance_ceiling_c + 8°C`, suitability_index = 0 regardless of other scores

---

### 4. Planner Service (`app/services/planner_service.py`)

**Purpose:** Business logic for annual plan generation and saved schedule management

#### Key Function: `get_annual_plan()`

**What it does:**
```python
async def get_annual_plan(
    session: AsyncSession,
    principal: Principal,
    farm_id: UUID,
    year: int,
) -> AnnualPlanResponse
```

**Logic:**
```python
# For each of 12 months:
for month in range(1, 13):
    # 1. Build environmental context for that month
    context = _context(farm, snapshot, date(year, month, 1), crop)
    
    # 2. Score ALL crops independently
    ranked = []
    for crop in CROP_REGISTER:
        ranked.append(score(crop, context))
    
    # 3. Sort by suitability_index descending
    ranked.sort(key=lambda r: (-r.suitability_index, r.crop_name))
    
    # 4. Take top 3
    top3 = ranked[:3]
    months.append(MonthRecommendation(month, top3))

# Load saved entries and proposals
entries = load_saved_plan_entries(farm_id, year)
proposals = load_pending_proposals(farm_id)

return AnnualPlanResponse(months, entries, proposals)
```

**Important:** Each month runs **independent scoring** - it doesn't know what was recommended in previous months or what's already growing. This is why you see repetition (Sorghum appears multiple times because it scores high in hot/dry conditions across many months).

#### Saved Schedule Functions

**Create Entry:**
```python
async def create_entry(
    session: AsyncSession,
    principal: Principal,
    farm_id: UUID,
    data: PlanEntryCreate,
) -> PlanEntry
```

**Logic:**
1. Derive `harvest_date = planting_date + crop.duration_months`
2. Validate no overlap with existing entries (same field or farm capacity exceeded)
3. Score the crop for that planting date
4. Persist to `plan_entries` table

**Update/Delete Entry:**
- `update_entry()`: Re-validates overlap, re-scores crop
- `delete_entry()`: Removes from database

**Proposal Management:**
- `accept_proposal()`: Applies new recommendation to saved entry
- `dismiss_proposal()`: Marks proposal as dismissed

---

### 5. API Endpoints (`app/api/v1/planner.py`)

```python
# Get 12-month grid + saved schedule
GET /api/v1/farms/{farm_id}/crop-plan?year=2027

# Saved schedule management
POST   /api/v1/farms/{farm_id}/plan-entries
PATCH  /api/v1/farms/{farm_id}/plan-entries/{entry_id}
DELETE /api/v1/farms/{farm_id}/plan-entries/{entry_id}

# Proposal management
POST   /api/v1/farms/{farm_id}/change-proposals/{proposal_id}/accept
POST   /api/v1/farms/{farm_id}/change-proposals/{proposal_id}/dismiss
```

---

### 6. Database Models (`app/models/plan.py`)

**PlanEntry:**
```python
class PlanEntry(Base):
    __tablename__ = "plan_entries"
    
    id: UUID
    farm_id: UUID                     # FK to farms
    crop_name: str                    # e.g., "Maize"
    field_name: str | None            # Optional field identifier
    planting_date: date
    harvest_date: date                # Auto-calculated: planting_date + duration_months
    cultivation_mode: str             # "rain_fed" | "irrigated"
    irrigation_mm: float | None       # Monthly irrigation if irrigated
    area_ha: float                    # Hectares allocated
    snapshot_id: UUID | None          # Environmental data snapshot at save time
    data_mode: str                    # "real" | "demonstration"
    suitability_index: int | None     # Score at save time
    engine_version: str               # "farmtwin-crop-v2"
    revision: int                     # Optimistic locking
```

**ChangeProposal:**
```python
class ChangeProposal(Base):
    __tablename__ = "change_proposals"
    
    id: UUID
    farm_id: UUID                     # FK to farms
    entry_id: UUID                    # FK to plan_entries
    old_suitability_index: int | None
    new_suitability_index: int | None
    new_snapshot_id: UUID             # New environmental data
    changed_inputs: dict              # JSONB: which inputs changed
    issue_date: datetime
    status: ProposalStatus            # PENDING | ACCEPTED | DISMISSED
```

---

## Frontend Architecture

### Annual Plan Page (`app/app/farms/[farmId]/annual-plan/page.tsx`)

**Component Structure:**
```tsx
AnnualPlanPage
├── 12-Month Grid Section
│   ├── MonthCard (x12)
│   │   ├── Month name
│   │   ├── Top 3 crop cards
│   │   │   ├── Crop name
│   │   │   ├── Suitability score
│   │   │   ├── "Add to plan" button
│   │   └── "Saved" badge (if entry exists)
│   
└── My Schedule Panel
    ├── Saved entries list
    │   ├── Entry card
    │   │   ├── Crop name + dates
    │   │   ├── Status/score
    │   │   └── Delete button
    └── Change proposals notification
```

**Data Flow:**
```tsx
// 1. Fetch annual plan on mount
useEffect(() => {
  const data = await fetch(`/api/v1/farms/${farmId}/crop-plan?year=2027`)
  setMonths(data.months)          // 12 months with top-3 crops each
  setEntries(data.entries)        // Saved schedule
  setProposals(data.proposals)    // Pending proposals
}, [farmId])

// 2. Add to plan action
async function handleAddToPlan(crop: string, month: number) {
  await fetch(`/api/v1/farms/${farmId}/plan-entries`, {
    method: 'POST',
    body: JSON.stringify({
      crop_name: crop,
      planting_date: `2027-${month}-01`,
      cultivation_mode: 'rain_fed',
      area_ha: 1.0
    })
  })
  // Reload annual plan
}
```

---

## How to Edit the Annual Crop Plan

### Problem: Repetitive Monthly Recommendations

**Root Cause:** `planner_service.py` line 216 loops through 12 months and scores all crops **independently** for each month. It doesn't consider:
- What was recommended in previous months
- What's already growing
- Crop rotation principles

**Current Logic:**
```python
# Lines 188-231 in planner_service.py
months = []
for month in range(1, 13):
    ranked = []
    for crop in CROP_REGISTER:
        context = _context(farm, snapshot, date(year, month, 1), crop)
        ranked.append(score(crop, context))  # Independent scoring
    ranked.sort(key=lambda r: (-(r.suitability_index ...), r.crop_name))
    top3 = ranked[:3]
    months.append(MonthRecommendation(month, calendar.month_name[month], ..., top3))
```

### Solution Options

#### Option 1: Increase Crop Diversity (Already Done)
- ✅ Added 6 fruit crops with different temperature/rainfall preferences
- Result: More variety, but still shows repetition if one crop dominates climatically

#### Option 2: Add Category Diversity Bonus
**Modify:** `app/domain/crop_engine.py` `rank_all()` function

Add post-processing to boost category diversity:
```python
def rank_all_with_diversity(context: SnapshotContext, previous_picks: list[str] = None) -> list[SimulationResult]:
    """Score all crops with diversity bonus."""
    results = [score(crop, context) for crop in CROP_REGISTER]
    
    # Apply diversity penalty to recently picked crops
    if previous_picks:
        for r in results:
            if r.crop_name in previous_picks:
                r.suitability_index = max(0, r.suitability_index - 10)  # Diversity penalty
    
    results.sort(key=lambda r: (-r.suitability_index, r.crop_name))
    return results
```

Then modify `planner_service.py`:
```python
previous_picks = []
for month in range(1, 13):
    ranked = rank_all_with_diversity(context, previous_picks)
    top3 = ranked[:3]
    previous_picks.extend([r.crop_name for r in top3])
    months.append(...)
```

#### Option 3: Calendar-Aware Recommendations (Major Change)
**Modify:** `planner_service.py` `get_annual_plan()` to track land occupation

```python
# Track which crops are growing in which months
occupied_months = {}  # {crop_name: [list of months it occupies]}

for month in range(1, 13):
    # Check if any saved entries are growing this month
    growing_now = [e.crop_name for e in entries 
                   if e.planting_date.month <= month <= e.harvest_date.month]
    
    # Score only crops that aren't already growing
    available_crops = [c for c in CROP_REGISTER if c.name not in growing_now]
    ranked = [score(crop, context) for crop in available_crops]
    ...
```

#### Option 4: Showcase Saved Schedule Instead
**Recommended for Presentation:**

The Saved Schedule already properly models crop calendars with:
- Planting → Harvest timeline
- Overlap validation
- Rotation support

Create a demo saved schedule that shows proper rotation:
```python
# Example realistic schedule
Mar 2027: Plant Maize (4 months) → Harvest Jul 2027
Aug 2027: Plant Beans (3 months) → Harvest Nov 2027  # Nitrogen fixation
Dec 2027: Plant Tomato (4 months) → Harvest Apr 2028
```

---

## Quick Edit Guide

### To Add More Crop Variety
1. Edit `farmtwin/backend/data/crops/requirements_v1.yaml`
2. Add new crop entries with diverse requirements
3. Restart backend: `docker compose restart backend`

### To Reduce Repetition (Simple)
1. Edit `farmtwin/backend/app/services/planner_service.py`
2. Modify lines 188-231 to add diversity penalty
3. Test with `pytest tests/` 

### To Change Scoring Weights
1. Edit `farmtwin/backend/app/domain/crop_engine.py`
2. Modify lines 32-37 (weight constants)
3. Adjust to emphasize different factors

### To Change UI
1. Edit `farmtwin/frontend/app/app/farms/[farmId]/annual-plan/page.tsx`
2. Modify MonthCard or SavedSchedulePanel components
3. Test with `npm run dev`

---

## Testing

```bash
# Backend tests
cd farmtwin/backend
pytest tests/test_planner_service.py -v

# Frontend tests
cd farmtwin/frontend
npm test annual-plan

# Full integration
docker compose up
# Visit http://localhost:3000/app/farms/{farmId}/annual-plan
```

---

## Key Insights for AI Codex

1. **Monthly grid = independent scoring** - Each month is scored in isolation
2. **Saved schedule = proper calendar** - Tracks planting → harvest correctly
3. **Sorghum dominates because** - High drought tolerance (0.80) + heat tolerance (40°C)
4. **Fruits added** - 6 new crops with different preferences should increase variety
5. **To fix repetition** - Need to add diversity logic or showcase saved schedule instead

**Files to Edit for Variety:**
- `farmtwin/backend/app/services/planner_service.py` (lines 188-231)
- `farmtwin/backend/app/domain/crop_engine.py` (optional: diversity scoring)
- `farmtwin/backend/data/crops/requirements_v1.yaml` (add more crops)
