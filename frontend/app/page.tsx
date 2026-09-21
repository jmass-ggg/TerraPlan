'use client';

import Image from 'next/image';
import Link from 'next/link';
import { useState, useEffect } from 'react';
import {
  ArrowRight,
  CalendarDays,
  CloudRain,
  CloudSun,
  Database,
  Droplet,
  FlaskConical,
  Layers3,
  Leaf,
  MapPinned,
  Menu,
  Mountain,
  ShieldAlert,
  ShieldCheck,
  Sprout,
  Thermometer,
  TriangleAlert,
  TrendingUp,
  X,
} from 'lucide-react';

import { Brand } from '@/components/brand';
import { Button } from '@/components/ui/button';

const challenges = [
  { 
    icon: CloudRain, 
    title: 'Changing climate patterns', 
    copy: 'Rainfall and temperature are becoming harder to plan around.',
    accent: 'blue'
  },
  { 
    icon: Sprout, 
    title: 'Costly crop decisions', 
    copy: 'A poor crop or planting-window choice can affect an entire growing season.',
    accent: 'amber'
  },
  { 
    icon: TriangleAlert, 
    title: 'Fast-moving hazards', 
    copy: 'Drought, floods, heat, and heavy rainfall can damage farms quickly.',
    accent: 'red'
  },
];

const workflowSteps = [
  { 
    icon: MapPinned, 
    title: 'Select your farm', 
    copy: 'Draw and validate the exact boundary.'
  },
  { 
    icon: Database, 
    title: 'Connect evidence', 
    copy: 'Check coverage, time, quality, and source.'
  },
  { 
    icon: Layers3, 
    title: 'Build the twin', 
    copy: 'Create a versioned environmental snapshot.'
  },
  { 
    icon: TrendingUp, 
    title: 'Make a decision', 
    copy: 'Compare explained results and useful actions.'
  },
];

const features = [
  { 
    icon: Layers3, 
    title: 'Farm Digital Twin', 
    copy: 'Bring farm shape, terrain, vegetation, climate, water, and soil evidence into one source-aware view.',
    accent: 'green'
  },
  { 
    icon: FlaskConical, 
    title: 'Crop Simulator', 
    copy: 'Compare crops and planting choices using environmental conditions and transparent suitability rules.',
    accent: 'amber'
  },
  { 
    icon: CalendarDays, 
    title: 'Annual Crop Plan', 
    copy: 'Turn supported seasonal options into a farmer-controlled twelve-month crop plan.',
    accent: 'blue'
  },
  { 
    icon: ShieldAlert, 
    title: 'Disaster Center', 
    copy: 'Understand drought, flood, heat, rainfall, and wind risks with practical actions.',
    accent: 'green'
  },
  { 
    icon: CloudSun, 
    title: 'Climate Overview', 
    copy: 'Keep current observations, forecasts, seasonal outlooks, and scenarios clearly separated.',
    accent: 'blue'
  },
];

const dataSources = [
  { icon: Database, name: 'Conduit', desc: 'Local stations' },
  { icon: Layers3, name: 'Satellite', desc: 'Sentinel-2' },
  { icon: Sprout, name: 'Soil', desc: 'SoilGrids' },
  { icon: Mountain, name: 'Terrain', desc: 'Elevation' },
  { icon: CloudSun, name: 'Weather', desc: 'Open-Meteo' },
];

export default function Home() {
  const [isScrolled, setIsScrolled] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  useEffect(() => {
    const handleScroll = () => {
      setIsScrolled(window.scrollY > 20);
    };
    window.addEventListener('scroll', handleScroll);
    return () => window.removeEventListener('scroll', handleScroll);
  }, []);

  return (
    <main className="redesigned-landing">
      {/* Modern Navbar */}
      <header className={`modern-nav ${isScrolled ? 'scrolled' : ''}`}>
        <div className="modern-nav-container">
          <Brand />
          
          <nav className="modern-nav-center" aria-label="Main navigation">
            <Link href="/">Home</Link>
            <Link href="#how-it-works">How It Works</Link>
            <Link href="#features">Features</Link>
            <Link href="/app/data-sources">About Data</Link>
          </nav>
          
          <div className="modern-nav-actions">
            <Button size="lg" render={<Link href="/app" />} className="modern-nav-cta">
              Start Farm Analysis
            </Button>
            
            <button 
              aria-label="Open navigation menu" 
              className="mobile-menu-button"
              onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
            >
              {mobileMenuOpen ? <X /> : <Menu />}
            </button>
          </div>
        </div>

        {/* Mobile Menu */}
        {mobileMenuOpen && (
          <div className="mobile-menu">
            <nav className="mobile-nav-links">
              <Link href="/" onClick={() => setMobileMenuOpen(false)}>Home</Link>
              <Link href="#how-it-works" onClick={() => setMobileMenuOpen(false)}>How It Works</Link>
              <Link href="#features" onClick={() => setMobileMenuOpen(false)}>Features</Link>
              <Link href="/app/data-sources" onClick={() => setMobileMenuOpen(false)}>About Data</Link>
            </nav>
            <Button size="lg" render={<Link href="/app" />} className="modern-nav-cta mobile-cta">
              Start Farm Analysis
            </Button>
          </div>
        )}
      </header>

      {/* Modern Hero Section */}
      <section className="modern-hero" aria-labelledby="hero-title">
        <div className="modern-hero-container">
          <div className="modern-hero-left">
            <div className="modern-hero-badge">
              <span>Climate-Smart Agriculture</span>
            </div>
            
            <h1 id="hero-title" className="modern-hero-heading">
              Understand Your Land<br />Before You Plant
            </h1>
            
            <p className="modern-hero-description">
              TerraPlan creates a digital representation of your farm using climate, satellite, soil, terrain, and environmental data. It helps you understand your land, test crops, plan your farming year, and prepare for climate risks.
            </p>
            
            <div className="modern-hero-actions">
              <Button size="lg" render={<Link href="/app" />} className="modern-primary-button">
                Start Farm Analysis
              </Button>
              <Button size="lg" variant="outline" render={<Link href="/#how-it-works" />} className="modern-secondary-button">
                See How It Works
              </Button>
            </div>
            
            <p className="modern-trust-line">
              <ShieldCheck aria-hidden="true" />
              <span>Unknown data stays unknown. No invented farm readings.</span>
            </p>
          </div>

          <div className="modern-hero-right">
            <div className="modern-visual-card">
              <div className="modern-image-wrapper">
                <Image
                  src="/farmtwin-kenya-aerial.png"
                  alt="Aerial view of agricultural fields showing farm boundaries"
                  fill
                  priority
                  sizes="(max-width: 768px) 100vw, 52vw"
                  className="modern-image"
                />
              </div>
              
              <div className="modern-boundary-overlay" aria-hidden="true">
                <svg viewBox="0 0 520 360" preserveAspectRatio="none">
                  <polygon points="115,112 352,78 430,237 276,306 82,245" />
                </svg>
              </div>
              
              <div className="modern-data-pill modern-pill-temp">
                <Thermometer className="modern-pill-icon" />
                <span>Temperature 24°C</span>
              </div>
              
              <div className="modern-data-pill modern-pill-veg">
                <Leaf className="modern-pill-icon" />
                <span>Vegetation Good</span>
              </div>
              
              <div className="modern-data-pill modern-pill-water">
                <Droplet className="modern-pill-icon" />
                <span>Water Moderate</span>
              </div>
              
              <div className="modern-data-pill modern-pill-drought">
                <TriangleAlert className="modern-pill-icon" />
                <span>Drought Risk High</span>
              </div>
              
              <div className="modern-visual-caption">
                Sample preview · not live readings
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Why TerraPlan Section */}
      <section className="redesigned-why-section" aria-labelledby="why-title">
        <div className="redesigned-section-container">
          <div className="redesigned-section-header">
            <p className="redesigned-eyebrow">WHY TERRAPLAN</p>
            <h2 id="why-title" className="redesigned-section-heading">
              Farming decisions<br />are becoming harder
            </h2>
            <p className="redesigned-section-description">
              TerraPlan is designed to show the evidence behind a decision before the growing season is at risk.
            </p>
          </div>
          
          <div className="redesigned-problem-cards">
            {challenges.map(({ icon: Icon, title, copy, accent }) => (
              <article className="redesigned-problem-card" key={title} data-accent={accent}>
                <div className="redesigned-card-icon">
                  <Icon />
                </div>
                <h3 className="redesigned-card-title">{title}</h3>
                <p className="redesigned-card-description">{copy}</p>
              </article>
            ))}
          </div>
        </div>
      </section>

      {/* How It Works Section */}
      <section className="redesigned-how-section" id="how-it-works" aria-labelledby="workflow-title">
        <div className="redesigned-section-container">
          <div className="redesigned-section-header">
            <p className="redesigned-eyebrow">HOW IT WORKS</p>
            <h2 id="workflow-title" className="redesigned-section-heading">
              One clear path from land to action
            </h2>
          </div>
          
          <div className="redesigned-workflow-steps">
            {workflowSteps.map(({ icon: Icon, title, copy }, index) => (
              <div className="redesigned-workflow-step" key={title}>
                <div className="redesigned-step-badge">{index + 1}</div>
                <div className="redesigned-step-icon">
                  <Icon />
                </div>
                <h3 className="redesigned-step-title">{title}</h3>
                <p className="redesigned-step-description">{copy}</p>
                {index < workflowSteps.length - 1 && (
                  <ArrowRight className="redesigned-step-connector" aria-hidden="true" />
                )}
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Features Section - Bento Grid */}
      <section className="redesigned-features-section" id="features" aria-labelledby="features-title">
        <div className="redesigned-section-container">
          <div className="redesigned-section-header">
            <p className="redesigned-eyebrow">TERRAPLAN CAPABILITIES</p>
            <h2 id="features-title" className="redesigned-section-heading">
              Everything you need<br />to understand your farm
            </h2>
            <p className="redesigned-section-description">
              Environmental evidence becomes useful when it can be turned into understandable decisions.
            </p>
          </div>
          
          <div className="redesigned-bento-grid">
            {features.map(({ icon: Icon, title, copy, accent }, index) => (
              <article 
                className="redesigned-bento-card" 
                key={title} 
                data-accent={accent}
                data-size={index >= 3 ? 'large' : 'regular'}
              >
                <div className="redesigned-bento-icon">
                  <Icon />
                </div>
                <h3 className="redesigned-bento-title">{title}</h3>
                <p className="redesigned-bento-description">{copy}</p>
              </article>
            ))}
          </div>
        </div>
      </section>

      {/* Environmental Evidence Section */}
      <section className="redesigned-evidence-section" aria-labelledby="evidence-title">
        <div className="redesigned-section-container">
          <div className="redesigned-section-header">
            <p className="redesigned-eyebrow">ENVIRONMENTAL EVIDENCE</p>
            <h2 id="evidence-title" className="redesigned-section-heading">
              Built to preserve provenance
            </h2>
            <p className="redesigned-section-description">
              Every result should retain its provider, timestamp, quality, data mode, and geographic relevance.
            </p>
          </div>
          
          <div className="redesigned-source-chips">
            {dataSources.map(({ icon: Icon, name, desc }) => (
              <div className="redesigned-source-chip" key={name}>
                <Icon className="redesigned-source-chip-icon" />
                <div className="redesigned-source-chip-info">
                  <span className="redesigned-source-chip-name">{name}</span>
                  <span className="redesigned-source-chip-desc">{desc}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Final CTA Section */}
      <section className="redesigned-final-cta">
        <div className="redesigned-cta-container">
          <div className="redesigned-cta-content">
            <p className="redesigned-cta-eyebrow">START WITH WHAT IS REAL</p>
            <h2 className="redesigned-cta-heading">
              Understand your land<br />before risking a season.
            </h2>
          </div>
          <Button size="lg" render={<Link href="/app" />} className="redesigned-cta-button">
            Start Farm Analysis
            <ArrowRight className="redesigned-cta-arrow" />
          </Button>
        </div>
      </section>

      {/* Footer */}
      <footer className="redesigned-footer">
        <div className="redesigned-footer-container">
          <div className="redesigned-footer-brand">
            <Brand />
            <p className="redesigned-footer-tagline">Understand your land before you plant.</p>
          </div>
          
          <nav className="redesigned-footer-nav" aria-label="Footer navigation">
            <a href="#how-it-works">How it works</a>
            <a href="#features">Capabilities</a>
            <Link href="/app/data-sources">Data sources</Link>
            <Link href="/app/project">Project</Link>
          </nav>
          
          <p className="redesigned-footer-copyright">
            TerraPlan · Climate-smart agriculture
          </p>
        </div>
      </footer>
    </main>
  );
}
