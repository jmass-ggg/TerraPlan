# 🌱 TerraPlan

<p align="center">
  <img src="photos/logo.png" alt="TerraPlan logo" width="220" />
</p>

<p align="center">
  <strong>Climate intelligence for better farming decisions in Kenya.</strong>
</p>

**TerraPlan** is a climate-smart agricultural decision-support platform designed around the needs of **Kenyan farmers**.

It combines **JKUAT Conduit weather observations, weather forecasts, satellite imagery, soil data, terrain, and historical climate information** to answer one practical question:

> **What should I grow on this land, and what climate risks should I prepare for?**

---

## 🇰🇪 The Problem in Kenya

Agriculture in Kenya is highly exposed to changing rainfall, drought, heat, and flooding.

More than **80% of Kenya's landmass is arid or semi-arid**, while other regions experience intense seasonal rainfall and flooding.

In **2024, Kenya recorded its hottest year on record**, alongside major rainfall variability and destructive floods.

For farmers, this creates difficult decisions:

* What should I plant?
* Will there be enough rainfall?
* Is this crop suitable for my soil?
* Is drought or heat becoming dangerous?
* Should I plant now or wait?

The information needed to answer these questions already exists, but it is scattered across different systems.

```text
Weather
Satellite
Soil
Terrain
Historical Climate
Ground Sensors
      ↓
   TerraPlan
      ↓
Farmer Decision
```

**TerraPlan brings these signals together and converts them into practical agricultural intelligence.**

---

# 💡 From Data to Farmer Action

A farmer selects or draws their farm boundary on the map.

TerraPlan then builds a **Farm Digital Twin** using environmental evidence for that specific location.

```text
Farm Boundary
      ↓
Conduit + Weather + Satellite
Soil + Terrain + Climate
      ↓
Farm Digital Twin
      ↓
Crop Suitability + Climate Risks
      ↓
Seasonal Recommendation
      ↓
Farmer Action
```

<p align="center">
  <img src="photos/workflow.png" alt="TerraPlan environmental intelligence workflow" width="900" />
</p>

Instead of only saying:

> *“Rain is expected.”*

TerraPlan helps answer:

> **“Is that rainfall enough for maize on my farm?”**

> **“Is drought stress increasing?”**

> **“Would sorghum be safer under these conditions?”**

TerraPlan follows a simple idea:

**Data → Insight → Decision → Action**

---

# 🗺️ Farm Digital Twin

Each farm receives an environmental profile containing information such as:

* Temperature and rainfall
* Humidity and wind
* Soil pH and texture
* NDVI / NDMI vegetation indicators
* Elevation and slope
* Historical climate
* Vapor Pressure Deficit
* Ground weather-station observations

TerraPlan also tracks the **source, timestamp, quality, availability, and data mode** of environmental evidence.

This makes recommendations easier to understand and trace back to their source.

---

# 🌾 Crop Simulator

The Crop Simulator answers:

> **“What if I grow this crop here?”**

TerraPlan compares crop requirements with the farm environment and calculates a **0–100 suitability score**.

| Factor                  | Weight |
| ----------------------- | -----: |
| Water / Rainfall        |    30% |
| Temperature             |    25% |
| Soil Compatibility      |    20% |
| Heat Safety             |    10% |
| Drought / Flood Safety  |    10% |
| Environmental Condition |     5% |

TerraPlan supports **12 crops relevant to Kenyan agriculture**:

**Maize · Beans · Sorghum · Cowpea · Kale · Tomatoes · Cabbage · Carrots · Onions · Potatoes · Spinach · Sweet Potatoes**

Example:

```text
🌽 MAIZE — 88/100
Good Match

Temperature       94
Water             88
Soil              82
Heat Safety      100
Drought Safety    85
Environment       72

Limiting factor:
Environmental condition
```

Rather than only giving farmers a score, TerraPlan explains **why the crop received that score and which environmental factor may limit it**.

---

# 🌦️ Climate Risk Intelligence

TerraPlan evaluates five major agricultural climate risks:

* 🌵 Drought
* 🔥 Heat Stress
* 🌧️ Heavy Rainfall
* 🌊 Flood Exposure
* 💨 Wind

Example:

```text
DROUGHT RISK: HIGH
Severity: 86 / 100

Rainfall:         12 mm
Climate baseline: 85 mm
Rainfall deficit: 86%
VPD:              2.9 kPa

Suggested actions:
• Conserve soil moisture with mulch
• Consider supplemental irrigation
• Delay moisture-sensitive planting
```

The goal is to move from:

**“What is happening?”**

to:

**“What should the farmer do about it?”**

---

# 📅 Seasonal Crop Planning

TerraPlan turns crop suitability into a simple seasonal farming plan.

```text
MAR ───── JUN
🌽 Maize

JUL ───── SEP
🥬 Kale

OCT ───── DEC
🫘 Beans
```

The planner considers:

* Crop suitability
* Seasonal climate
* Crop duration
* Planting windows
* Harvest timing
* Crop rotation
* Environmental changes

This helps farmers plan beyond a single planting decision.

---

# 🔬 What-If Climate Simulation

TerraPlan also allows farmers to explore changing climate conditions.

For example:

> **What happens to maize suitability if rainfall decreases by 30%?**

> **Would sorghum perform better under hotter and drier conditions?**

> **How does crop suitability change if temperatures rise?**

The crop engine recalculates suitability using the changed environmental scenario.

This helps farmers explore possible conditions **before planting**.

---

# 🛰️ Environmental Data Sources

TerraPlan combines six environmental data sources.

| Source             | Role in TerraPlan                                       |
| ------------------ | ------------------------------------------------------- |
| **JKUAT Conduit**  | Farm-local temperature, humidity, wind and VPD evidence |
| **Open-Meteo**     | Weather forecasts, rainfall, temperature and wind       |
| **Sentinel-2**     | NDVI, NDMI and vegetation condition                     |
| **SoilGrids**      | Soil pH, texture and crop compatibility                 |
| **Copernicus DEM** | Elevation, slope and flood exposure                     |
| **ERA5-Land**      | Historical rainfall and temperature baselines           |

The value is not simply showing multiple datasets.

The value is **combining them into one agricultural decision**.

---

# 🇰🇪 JKUAT Conduit → Agricultural Intelligence

A key part of TerraPlan is its integration with the **JKUAT Conduit Data Platform**.

```text
JKUAT Conduit
      ↓
Parsing & Validation
      ↓
Normalization & Quality Control
      ↓
Deduplication
      ↓
Hourly / Daily Aggregation
      ↓
Farm Relevance Check
      ↓
Farm Digital Twin
      ↓
Crop & Risk Analysis
```

The pipeline works with signals such as:

* Temperature
* Humidity
* Wind speed
* Wind gusts
* Sensor quality
* Geographic relevance
* Vapor Pressure Deficit

TerraPlan uses temperature and humidity to calculate **Vapor Pressure Deficit (VPD)**, which helps identify atmospheric moisture stress.

```text
Temperature + Humidity
        ↓
       VPD
        ↓
Moisture Stress
        ↓
Drought Safety
        ↓
Farmer Recommendation
```

Instead of showing only:

```text
VPD = 2.9 kPa
```

TerraPlan can translate it into:

> **High evaporative stress is present. Moisture conservation or supplemental irrigation may be needed.**

TerraPlan currently processes **191 real historical Conduit observations from June 2025** through its historical replay pipeline.

---

# 🗣️ Designed for Kenyan Farmers

TerraPlan makes climate intelligence more accessible to local users.

### English ↔ Kiswahili

The interface includes more than **680 translated UI strings**.

```text
Crop Simulator
→ Kiigaji cha Mazao

Drought Risk
→ Hatari ya Ukame

Farm Digital Twin
→ Pacha Dijitali wa Shamba

Heavy Rainfall
→ Mvua Kubwa
```

### Kenya-Relevant Crops

The system focuses on crops commonly relevant to Kenyan farming, including:

**Maize, Beans, Sorghum, Cowpea, Sukuma Wiki, Tomatoes and Potatoes.**

TerraPlan also uses familiar measurements such as:

**hectares · °C · millimetres of rainfall · m/s**

---

# 🏗️ Technology

**Frontend:** React 19 · TypeScript · Tailwind CSS · MapLibre GL JS · Turf.js

**Backend:** FastAPI · Python 3.12 · SQLAlchemy · Pydantic

**Database:** PostgreSQL · PostGIS · Redis

**Infrastructure:** Docker · Docker Compose

**Testing:** Pytest · Hypothesis · Vitest

The backend includes **420+ automated tests** covering environmental processing, database behaviour, APIs and decision-engine logic.

---

# 🌍 Why It Matters

TerraPlan is built around a simple idea:

**Kenyan farmers should not need to understand satellite platforms, climate models, soil databases and weather-station measurements just to make one farming decision.**

TerraPlan translates environmental signals into questions farmers actually care about:

```text
Is this crop suitable?
        ↓
Is there enough water?
        ↓
What climate risk exists?
        ↓
What should I do?
```

The platform can grow further into:

* County-level agricultural intelligence
* Long Rains and Short Rains planning
* Additional Conduit weather stations
* Mobile and SMS farmer alerts
* Cooperative and extension-officer dashboards
* More Kenya-specific crops
* Wider East African climate-smart agriculture support

---

# 🚀 Run Locally

```bash
git clone <repository-url>
cd farmtwin

cp backend/.env.example backend/.env

docker compose build
docker compose up -d
```

```text
Frontend:  http://localhost:3000
API:       http://localhost:8000
API Docs:  http://localhost:8000/docs
```

---

<p align="center">
  <img src="photos/logo.png" alt="TerraPlan logo" width="120" />
</p>

<p align="center">
  <strong>Understand the farm. Understand the climate. Make a better decision before planting.</strong>
</p>

<p align="center">
  JKUAT Conduit + Environmental Data → Farm Intelligence → Farmer Action
</p>
