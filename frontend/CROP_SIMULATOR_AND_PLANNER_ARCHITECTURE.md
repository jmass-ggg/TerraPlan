# Crop Simulator, Disaster Center & Annual Planner - Frontend Architecture Guide

This document provides a concise explanation of how the Crop Simulator, Disaster Center (Risk Center), and Annual Crop Planner pages work. Use this to understand the structure without reading all the code.

---

## 1. Crop Simulator Page (`/app/farms/[farmId]/crops`)

**Purpose**: Allow users to simulate crop suitability based on planting dates, cultivation modes, and environmental conditions.

### Key Components

#### Main Page Component
- **Location**: `frontend/app/app/farms/[farmId]/crops/page.tsx`
- **State Management**:
  - Controls: `plantingDate`, `cultivationMode`, `irrigationMm`, `categoryFilter`, `searchQuery`
  - Results: `rankedResults` (list of all crops), `selectedResult` (detailed single crop)
  - Scenario: `draftRainfall`, `draftTemperature`, `draftIrrigationMm`, `scenarioResult`
  - Loading: `loading`, `error`, `validationMessage`, `selectedLoading`

#### Data Flow
```
User adjusts controls (date/mode/irrigation)
  ↓
useEffect triggers → loadRanked()
  ↓
API: simulateCrop(farmId, params) → /api/v1/farms/{id}/simulate-crop
  ↓
Returns: CropRankingResponse { ranked: SimulationResult[] }
  ↓
setRankedResults(ranking.ranked)
  ↓
Display filtered grid of CropCard components
```

#### Selection Flow
```
User clicks CropCard
  ↓
handleSelectCrop(cropName)
  ↓
API: simulateCrop with crop_name parameter
  ↓
Returns: SimulationResponse { selected: SimulationResult }
  ↓
setSelectedResult(sim.selected)
  ↓
Display CropDetailPanel in sidebar
```

#### Scenario Flow
```
User adjusts climate controls (rainfall/temperature)
  ↓
handleScenarioApply()
  ↓
API: computeScenario(farmId, name, delta) → /api/v1/farms/{id}/scenario
  ↓
Returns: { crops: CropScenarioResult[], hazards: HazardScenarioResult[] }
  ↓
setScenarioResult(result)
  ↓
Display comparison table (baseline vs scenario)
```

### UI Components

#### CropCard (`frontend/features/crops/CropCard.tsx`)
- Displays: crop name, category, suitability score, reason
- Score badge colors: green (82+), amber (68-81), red (<68)
- Handles: click to select/deselect

#### CropDetailPanel (`frontend/features/crops/CropDetailPanel.tsx`)
- **Tabs**:
  - Overview: Score dial, component bars, limiting factor
  - Requirements: Input completeness table, engine version
  - Risks: Hard exclusions, heat/drought/water warnings
- **Component Scores**: temperature, water, soil, heat_safety, drought_flood_safety, environmental_condition

### Key Features
1. **Real-time filtering**: Category chips + search input
2. **Dual mode**: Demonstration (no snapshot) vs Snapshot-backed (real data)
3. **Climate What-If**: Only shown when snapshot exists
4. **Add to plan**: Button appears when crop selected (links to annual planner)

---

## 2. Disaster Center Page (`/app/farms/[farmId]/risks`)

**Purpose**: Display hazard assessments for five climate-related risks and provide actionable recommendations.

### Key Components

#### Main Page Component
- **Location**: `frontend/app/app/farms/[farmId]/risks/page.tsx`
- **State Management**:
  - Data: `risks` (RiskResponse with all assessments)
  - Selection: `selectedHazard` (string | null)
  - Loading: `loading`, `error`

#### Data Flow
```
Page loads
  ↓
loadRisks()
  ↓
API: getFarmRisks(farmId) → /api/v1/farms/{id}/risks
  ↓
Returns: RiskResponse {
  farm_id,
  assessments: HazardAssessment[],  // 5 hazards
  engine_version,
  snapshot_id,
  data_mode
}
  ↓
setRisks(data)
  ↓
Display 5 hazard cards in grid
```

#### Selection Flow
```
User clicks hazard card
  ↓
setSelectedHazard(hazard)
  ↓
Find assessment from risks.assessments
  ↓
Display HazardDetailPanel in sidebar
```

#### Action Completion Flow
```
User clicks action checkbox
  ↓
completeAction(farmId, actionId)
  ↓
API: PATCH /api/v1/farms/{id}/actions/{actionId}
  ↓
Returns: { action_id, completed: true, completed_at }
  ↓
Optimistically update local state (setRisks)
  ↓
Checkbox shows as completed
```

### UI Components

#### HazardCard (inline component)
- Displays: hazard name, level badge, explanation, driver, action completion count
- Level colors: Low (green), Medium (amber), High (red), Unknown (gray)
- Click to select/deselect

#### HazardDetailPanel (inline component)
- **Header**: Hazard name, horizon (current/7-day/seasonal), level badge
- **Content**:
  - Explanation text
  - Hazard index (0-100)
  - At-risk crops list
  - Evidence inputs (shows data completeness)
  - Recommended actions list
  - Driver and threshold note

#### ActionRow (inline component)
- Displays: priority (P1, P2, P3), action text, source, review date
- Checkbox: Circle icon (uncompleted) → CheckCircle (completed)
- Click to mark as complete (only if not already completed)
- Shows completion date when done

### Hazard Types
The system assesses 5 hazards:
1. **Drought** - Water scarcity risk
2. **Heat Stress** - High temperature impact
3. **Heavy Rainfall** - Excessive precipitation
4. **Flood Exposure** - Inundation risk
5. **Wind** - Wind damage potential

### Hazard Assessment Structure
```typescript
HazardAssessment {
  hazard: string,                    // e.g., "drought", "heat"
  index: number,                     // 0-100 intensity
  level: 'Low' | 'Medium' | 'High' | 'Unknown',
  driver: string,                    // e.g., "rainfall_deficit"
  explanation: string,               // Human-readable assessment
  horizon: 'current' | 'short_term' | 'seasonal',
  at_risk_crops: string[],          // Crops vulnerable to this hazard
  actions: ActionRule[],            // Recommended actions
  evidence_used: Record<string, string>,  // Data completeness map
  engine_version: string,
  snapshot_id: string | null,
  data_mode: string
}
```

### Key Features
1. **Evidence-based assessment**: Shows what data was used (not probability)
2. **Unknown handling**: Missing data = "Unknown" (not assumed safe)
3. **Actionable recommendations**: Priority-based action list
4. **Action tracking**: Mark actions as complete, persisted to backend
5. **Horizon labeling**: Clear indication of timeframe (current/7-day/seasonal)
6. **At-risk crop identification**: Which crops are vulnerable

---

## 3. Annual Planner Page (`/app/farms/[farmId]/annual-plan`)

**Purpose**: Help users plan their growing year by comparing 12 monthly planting periods and saving entries.

### Key Components

#### Main Page Component
- **Location**: `frontend/app/app/farms/[farmId]/annual-plan/page.tsx`
- **State Management**:
  - Selection: `month` (1-12)
  - Scenario: `draftRainfall`, `draftTemperature`, `draftIrrigationMm`, `scenario`
  - Results: `scenarioResult` (real scenario data)
  - Dialogs: `addToPlanOpen`, `addToPlanCrop`

#### Data Flow
```
User selects month + scenario params
  ↓
useQuery → calculateDecisionSupport()
  ↓
API: POST /api/v1/farms/{id}/decision-support
  ↓
Returns: {
  months: [...],       // 12 month summaries
  selected_month: {    // Detailed month data
    recommendations: [...],  // Top crops for this month
    expected_temperature_c,
    expected_rainfall_mm,
    main_risk,
    comparison: [...]  // Baseline vs scenario
  }
}
  ↓
Display month grid + selected month detail
```

#### Saved Plan Flow
```
useQuery → getAnnualPlan(farmId, year)
  ↓
API: GET /api/v1/farms/{id}/crop-plan?year=2026
  ↓
Returns: {
  entries: PlanEntryResponse[],  // Saved entries
  proposals: ChangeProposalResponse[],  // Change suggestions
  months: MonthRecommendationResponse[]  // Month-by-month data
}
  ↓
Display "My Schedule" panel with saved entries
```

#### Add to Plan Flow
```
User clicks "Add to plan" on crop recommendation
  ↓
Dialog opens with AddToPlanForm
  ↓
User fills: planting_date, cultivation_mode, irrigation_mm, area_ha
  ↓
createPlanEntry(farmId, data)
  ↓
API: POST /api/v1/farms/{id}/crop-plan/entries
  ↓
Returns: PlanEntryResponse
  ↓
invalidatePlan() → re-fetch getAnnualPlan
  ↓
"Saved" badge appears on month grid
```

#### Delete Entry Flow
```
User clicks trash icon → DeleteEntryDialog opens
  ↓
User confirms
  ↓
deletePlanEntry(farmId, entryId)
  ↓
API: DELETE /api/v1/farms/{id}/crop-plan/entries/{entryId}
  ↓
invalidatePlan()
```

#### Change Proposals Flow
```
When environmental data updates, backend creates proposals
  ↓
getAnnualPlan includes proposals: [{ 
  id,
  entry_id, 
  old_suitability_index,
  new_suitability_index,
  changed_inputs
}]
  ↓
ProposalDialog shows pending proposals count
  ↓
User reviews and accepts or dismisses
  ↓
acceptChangeProposal() or dismissChangeProposal()
  ↓
API: POST /api/v1/farms/{id}/crop-plan/proposals/{id}/accept|dismiss
```

### UI Components

#### AddToPlanForm (inline component)
- Fields: crop_name (readonly), planting_date, cultivation_mode, irrigation_mm, area_ha
- Mutation: `createPlanEntry`

#### DeleteEntryDialog (inline component)
- Confirmation dialog for removing saved entries
- Mutation: `deletePlanEntry`

#### ProposalDialog (inline component)
- Lists pending change proposals
- Shows: old/new suitability scores, changed inputs
- Actions: Accept or Reject

#### ScenarioControls (`frontend/features/decision/ScenarioControls.tsx`)
- Shared between both pages
- Controls: rainfall slider, temperature slider, irrigation input
- Actions: Apply (triggers scenario), Reset (clears)

### Key Features
1. **Month-by-month comparison**: 12 cards showing top 2 crops per month
2. **Detailed recommendations**: Selected month shows top crops with scores
3. **Saved entries tracking**: "Saved" badges on months with entries
4. **Change proposals**: Notifications when environmental data updates
5. **Climate What-If**: Real scenario mode when snapshot exists, demo mode otherwise
6. **My Schedule panel**: List of all saved entries with delete option

---

## 4. API Integration Patterns

### Crop Simulator APIs
```typescript
// Get ranked list of all crops
simulateCrop(farmId, { planting_date, cultivation_mode, irrigation_mm? })
→ CropRankingResponse { ranked: SimulationResult[] }

// Get single crop detail
simulateCrop(farmId, { crop_name, planting_date, cultivation_mode, irrigation_mm? })
→ SimulationResponse { selected: SimulationResult }

// Compute climate scenario
computeScenario(farmId, name, { rainfall_change_pct, temperature_change_c, irrigation_mm_override? })
→ ScenarioResponse { crops: CropScenarioResult[], hazards: HazardScenarioResult[] }
```

### Disaster Center APIs
```typescript
// Get all hazard assessments
getFarmRisks(farmId)
→ RiskResponse { 
  assessments: HazardAssessment[],  // Always 5 hazards
  engine_version,
  snapshot_id,
  data_mode
}

// Mark action as complete
completeAction(farmId, actionId)
→ ActionCompletionResponse { 
  action_id, 
  completed: true, 
  completed_at: "2026-09-15T10:30:00Z" 
}
```

### Annual Planner APIs
```typescript
// Get decision support (month-by-month data)
calculateDecisionSupport(farmId, { selected_month, rainfall_change_pct, temperature_change_c })
→ DecisionSupportResponse { months: [...], selected_month: {...}, comparison: [...] }

// Get annual plan (saved entries + proposals)
getAnnualPlan(farmId, year)
→ AnnualPlanResponse { entries: [...], proposals: [...], months: [...] }

// CRUD operations
createPlanEntry(farmId, data) → PlanEntryResponse
deletePlanEntry(farmId, entryId) → void
acceptChangeProposal(farmId, proposalId) → PlanEntryResponse
dismissChangeProposal(farmId, proposalId) → void
```

---

## 5. Shared Patterns

### Data Mode System
All three pages support two modes:
- **Demonstration**: Uses sample data when no farm snapshot exists
- **Snapshot-backed**: Uses real farm environmental data from completed analysis

Check: `data_mode` field in API responses
- `demonstration` → Show "Demonstration index" pill
- `snapshot` → Show "Snapshot-backed" pill + snapshot_id

### State Management
All pages use:
- **React hooks** for local state (useState, useEffect, useCallback)
- **React Query** (useQuery, useMutation) for Crop Simulator and Annual Planner
- **Manual fetch** for Disaster Center (simpler data flow)

### Error Handling
Custom error classes:
- `CropApiError` / `CropValidationError`
- `PlannerApiError` / `PlannerConflictError`
- `ScenarioApiError`
- `RiskApiError`

All mapped from base `FarmTwinApiError`

### Loading States
- Initial load: Show skeleton screens
- Selected item load: Show skeleton in sidebar/panel
- Action completion: Disable button, show busy state

### Validation
- Client-side: Form validation (required fields, number ranges)
- Server-side: API returns 422 with error details
- Display: Toast notifications for errors (planner) or inline messages (simulator)

---

## 6. Quick Reference for Editing

### To modify Crop Simulator controls:
1. Edit state in `page.tsx` (lines 35-45)
2. Update `loadRanked()` API call (lines 100-130)
3. Modify controls JSX (lines 250-330)

### To modify Crop detail panel:
1. Edit `CropDetailPanel.tsx` component
2. Tabs are hardcoded: "overview", "requirements", "risks"
3. Component scores mapped via `COMPONENT_LABELS` object

### To modify Disaster Center:
1. Edit `frontend/app/app/farms/[farmId]/risks/page.tsx`
2. Hazard cards in grid: `HazardCard` component (inline)
3. Detail panel: `HazardDetailPanel` component (inline)
4. Action completion: `ActionRow` component (inline)
5. Hazard labels: `hazardLabel()` helper function

### To add new hazard types:
1. Update `hazardLabel()` function to include new hazard name
2. Backend will automatically include it in assessments array
3. No other frontend changes needed (renders dynamically)

### To modify action tracking:
1. Edit `ActionRow` component for UI changes
2. Update `completeAction()` API client if endpoint changes
3. Modify `handleActionComplete()` for state update logic

### To add new planner features:
1. Add state to `AnnualPlanPage` component
2. Update `calculateDecisionSupport` API types
3. Modify month grid or detail section JSX

### To modify saved schedule display:
1. Edit "My Schedule" section (lines 870-920 in annual-plan/page.tsx)
2. Update `PlanEntryResponse` type if adding fields
3. Modify `createPlanEntry` mutation for new functionality

---

## 7. TypeScript Types Summary

### Core Types
```typescript
// Crop simulation
SimulationResult {
  crop_name, suitability_index, label, components, limiting_factor,
  reason, hard_exclusion, hard_exclusion_reason, engine_version,
  snapshot_id, data_mode, input_completeness
}

// Hazard assessment
HazardAssessment {
  hazard, index, level, driver, explanation, horizon,
  at_risk_crops, actions, evidence_used, engine_version,
  snapshot_id, data_mode
}

ActionRule {
  id, priority, text, source, review_date,
  completed, completed_at
}

// Plan entry
PlanEntryResponse {
  id, farm_id, crop_name, planting_date, harvest_date,
  cultivation_mode, irrigation_mm, area_ha, snapshot_id,
  data_mode, suitability_index, engine_version, created_at, updated_at
}

// Change proposal
ChangeProposalResponse {
  id, farm_id, entry_id, old_suitability_index, new_suitability_index,
  changed_inputs, new_snapshot_id, issue_date, status, created_at
}

// Scenario
CropScenarioResult {
  crop_name, baseline_index, scenario_index, baseline_label, scenario_label
}
```

---

## 8. Component Comparison

| Feature | Crop Simulator | Disaster Center | Annual Planner |
|---------|---------------|-----------------|----------------|
| **Main view** | Grid of crop cards | Grid of hazard cards | Month grid + detail |
| **Selection** | Click card → sidebar | Click card → sidebar | Click month → detail |
| **Data refresh** | On control change | On page load | On month/scenario change |
| **State management** | Local state + useEffect | Local state + useEffect | React Query |
| **Filtering** | Category + search | None | None |
| **Actions** | View details, add to plan | Mark actions complete | Add/delete entries, proposals |
| **Scenario support** | Yes (climate what-if) | No | Yes (climate what-if) |
| **Persistence** | None | Action completion | Plan entries |

---

## Summary

All three pages follow similar patterns:
1. **User controls/selection** trigger API calls or state updates
2. **API responses** update local state
3. **Components render** from state with loading/error handling
4. **Mutations** (add/delete/complete) update backend and local state
5. **Data mode** switches between demo and real data based on snapshot availability

Key files to understand:
- `frontend/app/app/farms/[farmId]/crops/page.tsx` - Crop simulator page
- `frontend/app/app/farms/[farmId]/risks/page.tsx` - Disaster center page
- `frontend/app/app/farms/[farmId]/annual-plan/page.tsx` - Annual planner page
- `frontend/features/crops/CropCard.tsx` - Crop card component
- `frontend/features/crops/CropDetailPanel.tsx` - Crop detail panel
- `frontend/lib/api/crops.ts` - Crop API client
- `frontend/lib/api/risks.ts` - Risk API client
- `frontend/lib/api/planner.ts` - Planner API client
- `frontend/lib/api/scenarios.ts` - Scenario API client
