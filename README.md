# 🌱 TerraPlan

<p align="center">
  <img src="photos/logo.png" alt="TerraPlan logo" width="220" />
</p>

<p align="center">
  <strong>Understand your land before you plant.</strong>
</p>

## Why I Built TerraPlan

Kenyan farmers make high-risk decisions every season: **what to plant, when to plant, whether rainfall will be enough, and what climate risks to prepare for**.

Those decisions are becoming harder because Kenya faces both drought and extreme rainfall. Kenya's **2026–2030 Disaster Risk Financing Strategy** states that more than **80% of the country's landmass is arid or semi-arid**, making drought a major national risk.

The **Kenya Meteorological Department** also reported that **2024 was Kenya's hottest year on record**, while rainfall varied sharply between regions and flooding caused crop, livestock, infrastructure, and livelihood losses.

At the same time, farmers often have to interpret information from separate systems: weather forecasts, soil databases, satellite imagery, terrain models, and historical climate records.

FAO Kenya highlighted this gap in 2026, explaining that farmers need **timely, reliable, and actionable climate information** for decisions such as planting, water management, input application, and harvesting.

**TerraPlan was built to turn scattered environmental data into farm-specific agricultural decisions.**

---

## 🌍 What Is TerraPlan?

**TerraPlan** is a climate-smart agricultural decision-support platform.

A user selects their farm on a map, and TerraPlan combines available:

* Weather data
* Satellite observations
* Soil information
* Terrain and elevation
* Historical climate
* Ground observations

to create a **digital environmental profile of that farm**.

Instead of only showing raw environmental data, TerraPlan helps answer:

> **What crops are suitable for this land?**
> **Is rainfall likely to be enough?**
> **What climate risks should I prepare for?**
> **What should I plant during different seasons?**

---

## ⚙️ How TerraPlan Works

```text
Select Farm Boundary
        ↓
Collect Environmental Evidence
        ↓
Weather + Satellite + Soil + Terrain + Climate
        ↓
Build Farm Digital Twin
        ↓
Crop + Water + Climate Risk Analysis
        ↓
Generate Agricultural Decisions
        ↓
Create Seasonal Crop Plan
```

![TerraPlan environmental intelligence workflow](photos/workflow.png)

---

## 🌾 Core Features

### 1. Farm Digital Twin

The user draws a farm boundary using an interactive map.

TerraPlan builds an environmental snapshot using available information such as temperature, rainfall, soil properties, vegetation condition, moisture indicators, elevation, slope, historical climate, and eligible ground observations.

### 2. Crop Simulator — “What If I Grow This?”

TerraPlan compares crop requirements with farm conditions using a deterministic scoring engine.

| Factor                         | Weight |
| ------------------------------ | -----: |
| Temperature                    |    25% |
| Rainfall / Water               |    30% |
| Soil compatibility             |    20% |
| Heat safety                    |    10% |
| Drought / Flood safety         |    10% |
| NDVI / Environmental condition |     5% |

Instead of only returning a percentage, TerraPlan also shows the **limiting factor and explanation** behind the result.

### 3. Climate & Water Risk

TerraPlan evaluates indicators related to:

* Rainfall deficit
* Drought
* Heat stress
* Heavy rainfall
* Terrain-related flood exposure
* Wind exposure
* Crop rainfall adequacy

This moves beyond a generic forecast such as:

> *“Rain is expected.”*

and toward the more useful question:

> **“What does this weather mean for this farm and this crop?”**

### 4. Seasonal Crop Plan

TerraPlan combines crop suitability, seasonal climate context, crop duration, planting periods, farm allocation, and rotation rules to create a farm-specific seasonal plan.

```text
MAR ───── JUN   🌽 Maize
JUL ───── SEP   🥬 Kale
OCT ───── DEC   🫘 Beans
```

---

## 🛰️ Environmental Data Sources

| Data                | Source                   | Purpose                                    |
| ------------------- | ------------------------ | ------------------------------------------ |
| Weather             | Open-Meteo               | Temperature, precipitation, wind, forecast |
| Satellite           | Sentinel-2 / Copernicus  | NDVI, NDMI, vegetation condition           |
| Soil                | SoilGrids                | Modeled soil properties and texture        |
| Terrain             | Copernicus DEM GLO-30    | Elevation and slope                        |
| Historical Climate  | Open-Meteo / ERA5-family | Seasonal rainfall and temperature context  |
| Ground Observations | JKUAT Conduit            | Temperature, humidity, wind, VPD           |

The current JKUAT Conduit integration uses an **imported historical dataset**, not a live real-time feed.

---

## 🔎 Trustworthy by Design

TerraPlan does not silently replace missing environmental evidence with fake “live” values.

Environmental snapshots can preserve information such as:

* Provider/source
* Acquisition and retrieval time
* Availability status
* Data mode
* Quality information
* Geographic relevance

Crop suitability and risk calculations are **deterministic**.

AI is used only to explain calculated results in simpler language. It does **not** generate or override the numerical crop suitability or risk scores.

---

## 🏗️ Architecture

```text
React + TypeScript + MapLibre
            ↓
         FastAPI
            ↓
 Environmental Provider Layer
            ↓
Weather + Satellite + Soil + Terrain
      + Climate + Conduit
            ↓
 Versioned Environmental Snapshot
            ↓
      Farm Digital Twin
            ↓
Crop + Risk + Water + Planner Engines
            ↓
     Optional AI Explanation
```

### Tech Stack

**Frontend:** React 19, TypeScript, Vinext/Vite, Tailwind CSS, MapLibre GL JS, Turf.js
**Backend:** FastAPI, Python 3.12, SQLAlchemy, Pydantic
**Database:** PostgreSQL, PostGIS, Redis
**Optimization:** OR-Tools
**Infrastructure:** Docker, Docker Compose
**Testing:** Pytest, Hypothesis, Vitest

---

## 📚 Research & Evidence I Studied

TerraPlan's problem framing and solution design were informed by research into **Kenyan rain-fed agriculture, climate variability, drought, farmer adaptation, and climate-smart agriculture**.

### Government & Climate Sources

**1. Kenya Disaster Risk Financing Strategy 2026–2030 — Government of Kenya**

Used to understand Kenya's exposure to drought, floods, heat stress, and the scale of its arid and semi-arid regions.

**2. State of the Climate Report, Kenya 2024 — Kenya Meteorological Department**

Used to study temperature extremes, rainfall variability, flooding, and agricultural impacts.

**3. FAO Agrometeorological Advisory Services in Kenya — 2026**

Helped validate the need to translate climate forecasts into practical recommendations for planting, water management, inputs, and harvesting.

### Research Papers

**4. “Response to Climate Change in a Rain-Fed Crop Production System: Insights from Maize Farmers of Western Kenya” — Kogo et al., 2022**

The study found Kenyan maize farmers responding to climate change through changes in planting dates, crop diversification, early-maturing cultivars, and drought-tolerant varieties.

**5. “Climate Change Impacts and Relevance of Smallholder Farmers' Response in Arid and Semi-Arid Lands in Kenya” — 2021**

This research studied farmers in Kenya's ASAL regions and reported drought, flooding, food shortages, water scarcity, and the importance of crop and water-management adaptation.

**6. “Effects of Climate Variability and Change on Agricultural Production: The Case of Small-Scale Farmers in Kenya” — 2016**

This study analyzed how changes in temperature and rainfall affect agricultural production and revenues of Kenyan smallholder farmers.

**7. “Economy-wide Impacts of Climate-Induced Agricultural Yield Changes in Kenya” — 2026**

This study examines how climate-driven agricultural yield changes can affect Kenya beyond individual farms and influence the wider economy.

> Direct links to all of these papers and reports are included in the downloadable `README.md`.

---

## 🚀 Run Locally

```bash
git clone <repository-url>
cd farmtwin

cp backend/.env.example backend/.env

docker compose build
docker compose up -d
```

Open:

```text
Frontend:  http://localhost:3000
API:       http://localhost:8000
API Docs:  http://localhost:8000/docs
```

---

## ⚠️ Limitations

TerraPlan is a **decision-support system**, not a replacement for agronomists, laboratory soil testing, or local agricultural expertise.

* SoilGrids provides modeled soil estimates.
* Satellite observations can be affected by clouds and acquisition timing.
* Weather forecasts change as new observations become available.
* Environmental datasets have different spatial and temporal resolutions.
* JKUAT Conduit currently uses imported historical observations.
* The water model is a planning estimate, not a full irrigation-engineering model.

---

## 🌱 Vision

TerraPlan's goal is simple:

> **Give farmers one place where environmental data becomes an understandable agricultural decision.**

Instead of asking farmers to interpret several separate environmental systems, TerraPlan aims to answer the question that actually matters:

> **“What does all of this mean for my land, my crop, and my next decision?”**

<p align="center">
  <strong>TerraPlan — Understand your land before you plant.</strong>
</p>
