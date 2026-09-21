# FarmTwin/TerraPlan Rebranding - Files to Modify

This document lists ALL files you need to modify to rebrand the application. Read and modify ONLY these files.

---

## VISUAL BRANDING (Logos & Images)

### Logo Files (Replace these images)
```
frontend/public/logo.png                    ← Main logo (used everywhere)
frontend/public/favicon.svg                 ← Browser tab icon
photos/logo.png                            ← Documentation logo
```

**Action**: Replace these 3 image files with your new brand images.
- `logo.png` should be ~82x82px (or larger, will be scaled)
- `favicon.svg` should be a simple icon version

---

## APPLICATION NAME CHANGES

### 1. Main README (Root level)
**File**: `README.md`
**Lines to change**:
- Line 1: `# TerraPlan` → `# YourBrandName`
- Line 4: `<img src="photos/logo.png" alt="TerraPlan logo"` → Update alt text
- Line 9: All mentions of "TerraPlan" → "YourBrandName"
- Line 235: Keep "FarmTwin" in technical references (API routes, Docker names)

### 2. Frontend Landing Page
**File**: `frontend/app/page.tsx` (400 lines)
**Search for**: `TerraPlan` (8 occurrences)
**Lines to change**:
- Line 186: Hero description
- Line 255: "WHY TERRAPLAN" section
- Line 260: Description text
- Line 310: "TERRAPLAN CAPABILITIES" section
- Line 397: Footer copyright

### 3. Frontend App Welcome Page
**File**: `frontend/app/app/page.tsx` (50 lines)
**Search for**: `TerraPlan` (1 occurrence)
**Lines to change**:
- Line 39: `Welcome to TerraPlan` → `Welcome to YourBrandName`

### 4. Frontend Layout (Page Titles)
**File**: `frontend/app/layout.tsx`
**Lines to change**:
- Line 21: `default: 'TerraPlan — Understand your land before you plant'`
- Line 22: `template: '%s · TerraPlan'`
- Line 25: `description:` (update meta description)

### 5. Frontend Brand Component
**File**: `frontend/components/brand.tsx` (20 lines)
**Lines to change**:
- Line 6: `aria-label="TerraPlan home"`
- Line 9: `alt="TerraPlan"`

### 6. Frontend README
**File**: `frontend/README.md`
**Lines to change**:
- Line 1: `# TerraPlan frontend`
- Line 3: `Responsive TerraPlan landing page`

### 7. Frontend Settings Page
**File**: `frontend/app/app/settings/page.tsx`
**Lines to change**:
- Line 209: `'This build is connected to the configured TerraPlan backend.'`

### 8. Frontend Annual Plan Page
**File**: `frontend/app/app/farms/[farmId]/annual-plan/page.tsx`
**Lines to change**:
- Line 946: `'TerraPlan balanced crop conditions...'` (explanation text)

### 9. Frontend Tools Page
**File**: `frontend/app/app/tools/[tool]/page.tsx`
**Lines to change**:
- Line 117: `<strong>Why it is locked</strong> TerraPlan does not generate`

### 10. Frontend Server
**File**: `frontend/server.js`
**Lines to change**:
- Line 7: `console.log('Starting TerraPlan frontend server...')`

### 11. Frontend API Error Messages
**File**: `frontend/components/api-state.tsx`
**Lines to change**:
- Line 18: `'TerraPlan is temporarily unavailable'`

---

## TECHNICAL CONFIGURATION (Backend)

### Backend Configuration Files

**File**: `backend/.env.example`
**Lines to change**:
- Line 1: `# FarmTwin Backend Configuration` (optional - keep for compatibility)

**File**: `backend/README.md`
**Lines to change**:
- Line 1: `# FarmTwin Backend — Phase 10`
- Line 7: Description text mentioning "FarmTwin"
- Line 97: Header section mentions

**File**: `backend/pyproject.toml`
**Lines to change**:
- Line 3: `name = "farmtwin-backend"` (keep for package name)
- Line 4: `description = "FarmTwin Phase 1 Backend Foundation"`

**File**: `backend/Dockerfile`
**Lines to change**:
- Line 1: `# FarmTwin Backend - Phase 1 Foundation`

**File**: `backend/requirements.txt`
**Lines to change**:
- Line 1: `# FarmTwin Backend Phase 1 - Locked Dependencies`

**File**: `backend/alembic.ini`
**Lines to change**:
- Line 1: `# Alembic configuration for FarmTwin Backend`

**File**: `compose.yaml`
**Lines to change**:
- Line 1: `# FarmTwin Application - Phase 1 Local Development`

**File**: `start_services.sh`
**Lines to change**:
- Line 2: `# Start all FarmTwin Docker services`
- Line 7: `echo "Starting FarmTwin Docker Services"`

**File**: `backend/app/db/migrations/README.md`
**Lines to change**:
- Line 3: `This directory contains Alembic database migrations for FarmTwin Backend.`

---

## API CLIENT CONFIGURATION

### Environment Variable Name (Frontend)
**File**: `frontend/lib/api/client.ts`
**Lines to change**:
- Line 34: `const configured = process.env.NEXT_PUBLIC_FARMTWIN_API_URL?.trim();`
- Line 38: Error message mentioning `NEXT_PUBLIC_FARMTWIN_API_URL`

**Note**: If you change the env var name, also update:
- `.env.example` files
- Any deployment documentation
- Docker compose files

---

## INTERNAL API REFERENCES (Keep as-is for compatibility)

### These files contain "FarmTwin" in technical contexts - DON'T change unless you want to:

**Class Names** (Breaking change if modified):
- `frontend/lib/api/client.ts` - `FarmTwinApiError` class (line 19)
- All error handling references to `FarmTwinApiError`

**Function Names** (Breaking change if modified):
- `frontend/lib/api/farms.ts`:
  - `FarmTwinStatus` type (line 135)
  - `FarmTwinResult` interface (line 137)
  - `normaliseFarmTwin()` function (line 323)
  - `getFarmTwin()` function (line 528)
  - `farmTwinApi` object (line 109)

**HTTP Headers** (Breaking change if modified):
- `X-FarmTwin-Data-Mode` header
- `X-FarmTwin-Auth-Mode` header

**Backend API Routes** (Keep for backward compatibility):
- All routes stay as `/api/v1/...`
- Internal service names stay the same

---

## QUICK REBRANDING CHECKLIST

### Step 1: Replace Images (3 files)
- [ ] `frontend/public/logo.png`
- [ ] `frontend/public/favicon.svg`
- [ ] `photos/logo.png`

### Step 2: Update Application Name (12 files)
- [ ] `README.md` - Main description
- [ ] `frontend/app/page.tsx` - Landing page
- [ ] `frontend/app/app/page.tsx` - Welcome
- [ ] `frontend/app/layout.tsx` - Page titles
- [ ] `frontend/components/brand.tsx` - Brand component
- [ ] `frontend/README.md` - Frontend docs
- [ ] `frontend/app/app/settings/page.tsx` - Settings
- [ ] `frontend/app/app/farms/[farmId]/annual-plan/page.tsx` - Plan explanations
- [ ] `frontend/app/app/tools/[tool]/page.tsx` - Tool descriptions
- [ ] `frontend/server.js` - Server logs
- [ ] `frontend/components/api-state.tsx` - Error messages
- [ ] `backend/README.md` - Backend docs

### Step 3: Update Backend Documentation (8 files) - OPTIONAL
- [ ] `backend/.env.example`
- [ ] `backend/pyproject.toml`
- [ ] `backend/Dockerfile`
- [ ] `backend/requirements.txt`
- [ ] `backend/alembic.ini`
- [ ] `compose.yaml`
- [ ] `start_services.sh`
- [ ] `backend/app/db/migrations/README.md`

### Step 4: Environment Variables (if renaming) - OPTIONAL
- [ ] `frontend/lib/api/client.ts` - Env var name
- [ ] `.env.example` - Variable name
- [ ] Deployment configs

---

## SEARCH & REPLACE STRATEGY

### Safe Search & Replace:
```bash
# In frontend files:
find frontend -type f \( -name "*.tsx" -o -name "*.ts" -o -name "*.md" \) \
  -exec sed -i 's/TerraPlan/YourBrandName/g' {} +

# In backend docs:
find backend -type f -name "*.md" \
  -exec sed -i 's/FarmTwin/YourBrandName/g' {} +
```

### DO NOT Search & Replace in:
- `node_modules/` - External packages
- `.git/` - Git history
- `dist/` - Build outputs
- Backend Python code (`.py` files) - Only update comments/docstrings manually
- TypeScript class/function names (breaking change)

---

## TESTING AFTER REBRANDING

### Visual Tests:
1. Check logo appears correctly on:
   - Landing page (/)
   - App header (/app)
   - Browser tab (favicon)
2. Check all page titles in browser tabs
3. Check footer text

### Functional Tests:
1. API connection should still work (env var unchanged)
2. All navigation links should work
3. Error messages should show new brand name

---

## MINIMAL REBRANDING (Just the visible parts)

If you want the FASTEST rebranding with minimal risk:

**Change ONLY these 5 files:**
1. `frontend/public/logo.png` - Replace image
2. `frontend/public/favicon.svg` - Replace icon
3. `frontend/app/layout.tsx` - Page title (line 21-22)
4. `frontend/components/brand.tsx` - Logo alt text (line 9)
5. `README.md` - Documentation (line 1, 9)

This covers 95% of what users see without touching any code logic.

---

## WHAT NOT TO CHANGE

❌ **DO NOT** change these (will break the app):
- Class names: `FarmTwinApiError`
- Function names: `getFarmTwin()`, `normaliseFarmTwin()`
- Type names: `FarmTwinResult`, `FarmTwinStatus`
- HTTP headers: `X-FarmTwin-Data-Mode`
- API routes: `/api/v1/...`
- Database table names
- Docker service names (unless you update compose.yaml)

These are internal technical identifiers that should remain for backward compatibility.

---

## ESTIMATED TIME

- **Quick visual rebrand**: 30 minutes (5 files)
- **Full UI rebrand**: 2 hours (20 files)
- **Complete rebrand**: 4 hours (all files + testing)

---

## File Count Summary

**Must Change** (User-facing): 15 files
**Should Change** (Documentation): 10 files  
**Could Change** (Technical): 5 files
**Don't Change** (Breaking): Keep as-is

**Total Impact**: ~30 files for complete rebranding
**Minimal Impact**: ~5 files for visual rebranding
