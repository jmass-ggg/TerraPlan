# Annual Planner Optimization - File Structure Guide

## Purpose
This guide shows ONLY the files needed to add OR-Tools/CP-SAT optimization, diversity penalties, calendar-aware logic, and AI explanations to the Annual Planner.

---

## Files to Modify/Create

### 1. Core Optimization Logic

#### **NEW: `farmtwin/backend/app/services/planner_optimizer.py`**
**Purpose:** OR-Tools/CP-SAT constraint solver for annual crop planning
**Create this file with:**
```python
"""
Annual crop plan optimizer using Google OR-Tools CP-SAT.

Constraints:
- Crop rotation (avoid same crop/family in consecutive months)
- Land capacity (total area ≤ farm size)
- Growing period occupancy (track when crops occupy land)
- Diversity (maximize different crop categories)

Objective:
- Maximize total suitability score across 12 months
- Bonus for category diversity
- Penalty for repetition
"""
from ortools.sat.python import cp_model
from app.domain.crop_register import CROP_REGISTER
from app.domain.crop_engine import score

class CropPlanOptimizer:
    """Optimizes annual crop recommendations using constraint programming."""
    
    def optimize(
        self, 
        farm_hectares: float,
        year: int,
        scores: dict[str, dict[int, float]],  # {crop_name: {month: score}}
        existing_entries: list[PlanEntry]
    ) -> list[MonthRecommendation]:
        """
        Returns optimized top-3 crops per month.
        
        Uses CP-SAT to solve:
        - Which crops to recommend each month
        - Respect calendar (don't recommend if already growing)
        - Maximize diversity + suitability
        """
        pass
```

**Dependencies to add:**
```python
# farmtwin/backend/requirements.txt
ortools>=9.8.3296  # ADD THIS LINE
```

---

#### **MODIFY: `farmtwin/backend/app/services/planner_service.py`**
**Purpose:** Replace simple sorting with optimizer
**Lines to change:** 188-231

**Current code (simple sort):**
```python
# Line 188-231
months = []
for month in range(1, 13):
    ranked = []
    for crop in CROP_REGISTER:
        context = _context(farm, snapshot, date(year, month, 1), crop)
        result = score(crop, context)
        ranked.append(result)
    ranked.sort(key=lambda r: (-r.suitability_index, r.crop_name))
    top3 = ranked[:3]
    months.append(MonthRecommendation(...))
```

**New code (with optimizer):**
```python
from app.services.planner_optimizer import CropPlanOptimizer

# Line 188-231 - REPLACE WITH:
# Step 1: Score all crops for all months
scores = {}  # {crop_name: {month: suitability_index}}
for crop in CROP_REGISTER:
    scores[crop.name] = {}
    for month in range(1, 13):
        context = _context(farm, snapshot, date(year, month, 1), crop)
        result = score(crop, context)
        scores[crop.name][month] = result.suitability_index

# Step 2: Run optimizer
optimizer = CropPlanOptimizer()
months = optimizer.optimize(
    farm_hectares=farm.hectares,
    year=year,
    scores=scores,
    existing_entries=entries  # Already fetched earlier
)
```

---

### 2. AI Explanation Integration

#### **MODIFY: `farmtwin/backend/app/api/v1/planner_schemas.py`**
**Purpose:** Add AI explanation to monthly crop recommendations
**Add new field:**

```python
from app.api.v1.crop_schemas import CropExplanationResponse  # Import

@dataclass
class CropRecommendation:
    """Single crop recommendation for a month."""
    crop_name: str
    category: str
    suitability_index: int
    label: str
    explanation: CropExplanationResponse | None = None  # ADD THIS
```

---

#### **MODIFY: `farmtwin/backend/app/services/planner_service.py`**
**Purpose:** Generate AI explanations for top-3 monthly crops
**Add after line 231:**

```python
from app.services.ai import get_crop_explanation_provider

# After optimizer returns months, add AI explanations
provider = get_crop_explanation_provider()

for month_rec in months:
    for crop_rec in month_rec.crops:
        # Get crop requirements
        crop_req = next(c for c in CROP_REGISTER if c.name == crop_rec.crop_name)
        
        # Re-build context for this month
        context = _context(farm, snapshot, date(year, month_rec.month, 1), crop_req)
        result = score(crop_req, context)
        
        # Generate AI explanation
        explanation = await provider.generate(
            result=result,
            crop_requirements=crop_req,
            farm_id=str(farm_id)
        )
        
        crop_rec.explanation = explanation
```

---

### 3. Calendar-Aware Logic

#### **MODIFY: `farmtwin/backend/app/services/planner_optimizer.py`**
**Purpose:** Track which crops are growing each month
**Add to optimizer:**

```python
def _build_occupancy_map(
    self, 
    existing_entries: list[PlanEntry]
) -> dict[int, set[str]]:
    """
    Returns which crops are growing in which months.
    
    Returns:
        {month: {crop_name1, crop_name2, ...}}
    """
    occupancy = {month: set() for month in range(1, 13)}
    
    for entry in existing_entries:
        # Calculate which months this crop occupies
        start_month = entry.planting_date.month
        end_month = entry.harvest_date.month
        
        # Handle year wraparound
        if end_month >= start_month:
            months = range(start_month, end_month + 1)
        else:
            months = list(range(start_month, 13)) + list(range(1, end_month + 1))
        
        for month in months:
            occupancy[month].add(entry.crop_name)
    
    return occupancy

def optimize(self, ...):
    # Use occupancy map in constraints
    occupancy = self._build_occupancy_map(existing_entries)
    
    # In CP-SAT model:
    # Don't recommend crops that are already growing
    for month in range(1, 13):
        for crop in occupancy[month]:
            # Add constraint: crop cannot be recommended this month
            pass
```

---

### 4. Diversity Penalties

#### **MODIFY: `farmtwin/backend/app/services/planner_optimizer.py`**
**Purpose:** Penalize repetitive recommendations
**Add to optimizer objective:**

```python
def optimize(self, ...):
    model = cp_model.CpModel()
    
    # Variables: selected[crop][month] = 1 if crop recommended in month
    selected = {}
    for crop in CROP_REGISTER:
        selected[crop.name] = {}
        for month in range(1, 13):
            selected[crop.name][month] = model.NewBoolVar(f'{crop.name}_m{month}')
    
    # Objective: maximize suitability + diversity
    objective_terms = []
    
    # 1. Base suitability scores
    for crop_name, months_scores in scores.items():
        for month, score in months_scores.items():
            objective_terms.append(score * selected[crop_name][month])
    
    # 2. Diversity bonus: different categories across months
    category_vars = {}
    for category in ['cereal', 'legume', 'vegetable', 'root', 'fruit']:
        category_vars[category] = model.NewBoolVar(f'category_{category}')
        # category_var = 1 if ANY crop in this category is selected
        crops_in_category = [c for c in CROP_REGISTER if c.category == category]
        model.Add(
            sum(selected[c.name][m] for c in crops_in_category for m in range(1,13)) >= 1
        ).OnlyEnforceIf(category_vars[category])
    
    # Bonus for each unique category
    diversity_bonus = 20  # Tune this
    for cat_var in category_vars.values():
        objective_terms.append(diversity_bonus * cat_var)
    
    # 3. Repetition penalty: same crop in consecutive months
    for crop_name in scores.keys():
        for month in range(1, 12):
            # If crop selected in both month and month+1, apply penalty
            both_selected = model.NewBoolVar(f'{crop_name}_repeat_{month}')
            model.AddMultiplicationEquality(
                both_selected, 
                [selected[crop_name][month], selected[crop_name][month+1]]
            )
            repetition_penalty = -15  # Tune this
            objective_terms.append(repetition_penalty * both_selected)
    
    model.Maximize(sum(objective_terms))
```

---

### 5. Frontend Type Definitions

#### **MODIFY: `farmtwin/frontend/lib/api/planner.ts`**
**Purpose:** Add AI explanation type to MonthRecommendation
**Lines to change:** 1-40 (type definitions)

**Add import:**
```typescript
import type { CropExplanation } from './crops';  // ADD THIS
```

**Update MonthRecommendation interface (line ~25):**
```typescript
export interface MonthRecommendation {
  crop_name: string;
  suitability_index: number;
  label: string;
  limiting_factor: string;
  explanation?: CropExplanation;  // ADD THIS LINE
}
```

---

### 6. Frontend API Client

#### **MODIFY: `farmtwin/frontend/lib/api/planner.ts`**
**Purpose:** Pass explanation data from API to frontend
**Lines to change:** ~144-165 (getAnnualPlan function)

**Current code:**
```typescript
export async function getAnnualPlan(
  farmId: string,
  year: number,
  scenario?: { rainfall_change_pct: number; temperature_change_c: number; irrigation_mm: number | null },
  signal?: AbortSignal,
): Promise<AnnualPlanResponse> {
  // ... existing implementation
}
```

**No changes needed** - The function already returns `AnnualPlanResponse` which includes `MonthRecommendationResponse.recommendations` (typed as `MonthRecommendation[]`), so explanations will flow through automatically once the type is updated.

---

### 7. Frontend UI Components

#### **MODIFY: `farmtwin/frontend/app/app/farms/[farmId]/annual-plan/page.tsx`**
**Purpose:** Display AI explanations in the annual plan UI
**Total lines:** 853 lines

**Component structure:**
```
AnnualPlanPage (main component)
├── AddToPlanForm (dialog form for adding crops)
├── DeleteEntryDialog (confirmation for deleting entries)
├── ProposalDialog (review change proposals)
└── Main render:
    ├── ScenarioControls (climate what-if controls)
    ├── Planner sequence summary (stats)
    ├── Annual months grid (timeline visualization)
    ├── Month detail panel (selected month details)
    ├── Perennial opportunities section
    ├── Scenario comparison table
    └── My Schedule panel (saved entries)
```

---

#### **A. Display explanations in timeline grid**
**Lines to change:** ~598-645 (timeline.map() section)

**Current code (line ~598):**
```tsx
{timeline.map((item) => (
  <button
    key={item.month}
    type="button"
    className="planner-month-card workspace-card"
    data-active={item.month === month || undefined}
    data-stage={item.stage}
    // ... other props
  >
    <div className="planner-month-header">
      <span className="planner-month-name">{item.month_name.slice(0, 3)}</span>
      {savedMonths.has(item.month) && <span className="planner-saved-indicator" aria-label="Saved entry"><CheckCircle /></span>}
    </div>
    <div className="planner-month-visual"><CropVisual cropName={item.crop_name ?? ''} /></div>
    <div className="planner-month-info">
      <strong className="planner-crop-name">{item.crop_name ?? 'Field recovery'}</strong>
      <span className="planner-stage-badge" data-stage={item.stage}>{item.stage}</span>
      {item.stage === 'planting' && item.suitability_index !== null && <small>{item.suitability_index}% suitability</small>}
    </div>
  </button>
))}
```

**Add after `<small>{item.suitability_index}% suitability</small>`:**
```tsx
{item.stage === 'planting' && item.suitability_index !== null && (
  <small>{item.suitability_index}% suitability</small>
)}
{/* ADD THIS: Show AI explanation preview in timeline card */}
{item.explanation && (
  <div className="crop-explanation-preview">
    <p className="text-xs text-muted-foreground line-clamp-2">
      {item.explanation.headline}
    </p>
  </div>
)}
```

---

#### **B. Display detailed explanations in month detail panel**
**Lines to change:** ~647-690 (planner-month-detail aside)

**Find this section (line ~674):**
```tsx
{selectedActivity?.reason && (
  <div className="planner-tip-card">
    <Info className="planner-tip-icon" />
    <div className="planner-tip-content">
      <strong>Why this crop now?</strong>
      <p>{selectedActivity.reason}</p>
    </div>
  </div>
)}
```

**Replace with AI explanation if available:**
```tsx
{/* Option 1: Show AI explanation if available, fallback to reason */}
{selectedActivity?.explanation ? (
  <div className="ai-explanation-section">
    <h3>Why this crop?</h3>
    <p className="explanation-headline">{selectedActivity.explanation.headline}</p>
    <p className="explanation-summary">{selectedActivity.explanation.summary}</p>
    
    {selectedActivity.explanation.strengths.length > 0 && (
      <div className="explanation-factors">
        <strong>✓ What looks good</strong>
        {selectedActivity.explanation.strengths.map((strength, idx) => (
          <div key={idx} className="explanation-factor">
            <span className="factor-label">{strength.factor}</span>
            <span className="factor-message">{strength.message}</span>
          </div>
        ))}
      </div>
    )}
    
    {selectedActivity.explanation.concerns.length > 0 && (
      <div className="explanation-factors">
        <strong>⚠ What to watch</strong>
        {selectedActivity.explanation.concerns.map((concern, idx) => (
          <div key={idx} className="explanation-factor">
            <span className="factor-label">{concern.factor}</span>
            <span className="factor-message">{concern.message}</span>
          </div>
        ))}
      </div>
    )}
    
    {selectedActivity.explanation.data_note && (
      <p className="explanation-data-note">{selectedActivity.explanation.data_note}</p>
    )}
  </div>
) : selectedActivity?.reason ? (
  <div className="planner-tip-card">
    <Info className="planner-tip-icon" />
    <div className="planner-tip-content">
      <strong>Why this crop now?</strong>
      <p>{selectedActivity.reason}</p>
    </div>
  </div>
) : null}
```

**Note:** The `PlanTimelineItem` type needs to be extended to include `explanation`:

---

#### **C. Update PlanTimelineItem type**
**File:** `farmtwin/frontend/lib/api/planner.ts`
**Lines to change:** ~58-78 (PlanTimelineItem interface)

**Add to PlanTimelineItem interface:**
```typescript
export interface PlanTimelineItem {
  month: number;
  month_name: string;
  crop_name: string | null;
  stage: 'planting' | 'growing' | 'flowering' | 'maturing' | 'harvest' | 'recovery';
  action: 'plant' | 'continue' | 'harvest' | 'recover';
  season_id: string | null;
  suitability_index: number | null;
  planning_score: number | null;
  plant_month: number | null;
  harvest_month: number | null;
  duration_months: number | null;
  previous_crop: string | null;
  rotation_effect: string | null;
  reason: string | null;
  limiting_factor: string | null;
  continues_next_year: boolean;
  data_mode: string;
  explanation?: CropExplanation;  // ADD THIS LINE
}
```

---

### 8. Frontend Styling (Optional)

#### **MODIFY: `farmtwin/frontend/app/globals.css`**
**Purpose:** Add styles for AI explanation preview in timeline cards
**Add to existing planner styles:**

```css
/* AI explanation preview in timeline cards */
.crop-explanation-preview {
  margin-top: 0.5rem;
  padding: 0.5rem;
  background: var(--muted);
  border-radius: 0.375rem;
  font-size: 0.75rem;
  line-height: 1.25rem;
  color: var(--muted-foreground);
}

/* Detailed explanation in month detail panel */
.ai-explanation-section {
  padding: 1rem;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 0.5rem;
  margin-top: 1rem;
}

.ai-explanation-section h3 {
  font-size: 1rem;
  font-weight: 600;
  margin-bottom: 0.5rem;
}

.explanation-headline {
  font-size: 0.875rem;
  font-weight: 500;
  margin-bottom: 0.25rem;
}

.explanation-summary {
  font-size: 0.875rem;
  color: var(--muted-foreground);
  margin-bottom: 1rem;
}

.explanation-factors {
  margin-top: 0.75rem;
}

.explanation-factors strong {
  display: block;
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin-bottom: 0.5rem;
  color: var(--muted-foreground);
}

.explanation-factor {
  display: flex;
  gap: 0.5rem;
  padding: 0.5rem;
  background: var(--muted);
  border-radius: 0.375rem;
  margin-bottom: 0.25rem;
}

.factor-label {
  font-weight: 500;
  font-size: 0.75rem;
  text-transform: capitalize;
}

.factor-message {
  font-size: 0.75rem;
  color: var(--muted-foreground);
}

.explanation-data-note {
  font-size: 0.75rem;
  color: var(--muted-foreground);
  margin-top: 0.75rem;
  font-style: italic;
}

/* Line clamp utility */
.line-clamp-2 {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
```

---

## File Summary Table

### Backend Files

| File | Action | Lines | Purpose |
|------|--------|-------|---------|
| `farmtwin/backend/app/services/planner_optimizer.py` | **CREATE** | new file | OR-Tools CP-SAT constraint solver |
| `farmtwin/backend/app/services/planner_service.py` | **MODIFY** | 188-231, add after 231 | Replace sort with optimizer, add AI explanations |
| `farmtwin/backend/app/api/v1/planner_schemas.py` | **MODIFY** | add field | Add `explanation` field to CropRecommendation |
| `farmtwin/backend/requirements.txt` | **MODIFY** | add line | Add `ortools>=9.8.3296` dependency |

### Frontend Files

| File | Action | Lines | Purpose |
|------|--------|-------|---------|
| `farmtwin/frontend/lib/api/planner.ts` | **MODIFY** | ~25, ~78 | Add `explanation` field to MonthRecommendation and PlanTimelineItem types |
| `farmtwin/frontend/lib/api/crops.ts` | **REFERENCE ONLY** | - | Import CropExplanation type (already exists) |
| `farmtwin/frontend/app/app/farms/[farmId]/annual-plan/page.tsx` | **MODIFY** | ~598-645, ~674-690 | Display AI explanations in timeline cards and detail panel |
| `farmtwin/frontend/app/globals.css` | **MODIFY** (optional) | add styles | Styles for explanation preview and detail display |

---

## Installation Steps

1. **Add dependency:**
   ```bash
   cd farmtwin/backend
   echo "ortools>=9.8.3296" >> requirements.txt
   pip install ortools
   ```

2. **Create optimizer file:**
   ```bash
   touch farmtwin/backend/app/services/planner_optimizer.py
   # Then implement the CropPlanOptimizer class
   ```

3. **Modify existing files:**
   - `planner_service.py` (replace simple sort with optimizer)
   - `planner_schemas.py` (add explanation field)
   - `page.tsx` (display explanations)

4. **Test:**
   ```bash
   # Backend tests
   pytest farmtwin/backend/tests/test_planner_service.py -v
   
   # Verify optimization
   curl http://localhost:8000/api/v1/farms/{farm_id}/crop-plan?year=2027
   ```

---

## Key Concepts

### OR-Tools CP-SAT
- **Constraint Programming**: Define variables, constraints, and objective
- **Variables**: `selected[crop][month]` = 1 if crop recommended
- **Constraints**: Rotation rules, land capacity, no duplicates
- **Objective**: Maximize (suitability + diversity - repetition)

### Calendar Awareness
- **Occupancy Map**: Track which crops are growing each month
- **Constraint**: Don't recommend crops that are already planted
- **Example**: If Maize planted in March (4-month duration), don't recommend it in Mar-Jun

### Diversity Penalties
- **Category Bonus**: +20 points for each unique category (cereal, legume, etc.)
- **Repetition Penalty**: -15 points if same crop in consecutive months
- **Result**: More varied recommendations across the year

### AI Explanations
- **Integration**: Call `get_crop_explanation_provider()` after optimization
- **Per-crop**: Generate explanation for each top-3 crop per month
- **Display**: Show headline + strengths in monthly cards

---

## Testing Checklist

- [ ] Optimizer returns 12 months with 3 crops each
- [ ] No crop appears in consecutive months (unless score >> others)
- [ ] Different categories appear across the year
- [ ] Crops already growing (saved entries) are not recommended again
- [ ] AI explanations are generated for each recommended crop
- [ ] Frontend displays explanations in monthly cards
- [ ] Performance: API responds in < 2 seconds

---

## Related Files (for reference only, don't modify)

- `farmtwin/backend/app/domain/crop_register.py` - Crop database (read-only)
- `farmtwin/backend/app/domain/crop_engine.py` - Scoring logic (read-only)
- `farmtwin/backend/data/crops/requirements_v1.yaml` - Crop data (read-only)
- `farmtwin/backend/app/services/ai/crop_explanation_service.py` - AI service (use, don't modify)
- `farmtwin/backend/app/models/plan.py` - Database models (read-only)

