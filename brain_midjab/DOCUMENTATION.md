# MidJab V2 - Complete Project Documentation

## Table of Contents
1. [Project Overview](#project-overview)
2. [Architecture](#architecture)
3. [System Components](#system-components)
4. [Data Models](#data-models)
5. [Pipeline Flow](#pipeline-flow)
6. [Installation & Setup](#installation--setup)
7. [Usage Guide](#usage-guide)
8. [Technical Details](#technical-details)
9. [Work Completed](#work-completed)
10. [Future Enhancements](#future-enhancements)

---

## Project Overview

**MidJab** is an intelligent job application automation system that:
- Scrapes job postings from multiple job boards (LinkedIn, Indeed, Naukri, Glassdoor, ZipRecruiter, Google, Bayt)
- Scores job opportunities based on resume match using hybrid LLM + keyword matching
- Automatically tailors resumes for high-scoring jobs using local LLM (Ollama)
- Generates professional PDF resumes using LaTeX templates

### Key Features
- **Multi-source job aggregation**: Unified job data model from 7+ job boards
- **Intelligent job scoring**: Hybrid approach combining keyword matching and LLM semantic analysis
- **Automated resume tailoring**: LLM-powered content optimization per job
- **Professional PDF generation**: LaTeX-based resume compilation
- **MongoDB-driven workflow**: All state managed in database, no file dependencies
- **Local LLM support**: Uses Ollama for privacy and cost-effectiveness

---

## Architecture

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    MidJab V2 Pipeline                        │
└─────────────────────────────────────────────────────────────┘
                              │
        ┌──────────────────────┼──────────────────────┐
        │                      │                      │
        ▼                      ▼                      ▼
┌──────────────┐      ┌──────────────┐      ┌──────────────┐
│   Stage 1    │      │   Stage 2    │      │   Stage 3    │
│ Profile      │      │ Data         │      │ Opportunity  │
│ Parser       │      │ Ingestion    │      │ Scorer       │
└──────────────┘      └──────────────┘      └──────────────┘
        │                      │                      │
        ▼                      ▼                      ▼
┌──────────────┐      ┌──────────────┐      ┌──────────────┐
│   Stage 4    │      │   Stage 5    │      │   MongoDB    │
│ Resume       │      │ LaTeX        │      │   Database   │
│ Tailor       │      │ Architect    │      │              │
└──────────────┘      └──────────────┘      └──────────────┘
```

### Component Architecture

```
midjab/
├── core/                    # Core database and models
│   ├── db.py               # MongoDB connection & indexes
│   └── models.py           # Pydantic data models
├── agents/                  # Pipeline agents
│   ├── profile_parser.py   # Stage 1: Parse LaTeX resume
│   ├── data_factory.py      # Stage 2: Job ingestion adapter
│   ├── discovery_engine.py # Stage 2: Multi-source scraper
│   ├── opportunity_scorer.py # Stage 3: Hybrid scoring engine
│   ├── resume_tailor.py    # Stage 4: LLM resume writer
│   └── latex_architect.py  # Stage 5: PDF compiler
├── jobspy/                  # Job scraping library
│   ├── linkedin/           # LinkedIn scraper
│   ├── indeed/             # Indeed scraper
│   ├── naukri/             # Naukri scraper
│   ├── glassdoor/          # Glassdoor scraper
│   ├── ziprecruiter/       # ZipRecruiter scraper
│   ├── google/             # Google Jobs scraper
│   └── bayt/               # Bayt scraper
├── templates/               # LaTeX templates
│   └── master_resume.tex.j2 # Jinja2 resume template
├── scripts/                 # Utility scripts
│   └── setup_db.py         # Database initialization
└── main.py                  # Pipeline orchestrator
```

---

## System Components

### Stage 1: Profile Parser (`agents/profile_parser.py`)

**Purpose**: Extract structured data from LaTeX resume

**Functionality**:
- Reads `resume.tex` from project root
- Uses Ollama LLM to parse resume into structured JSON
- Extracts: name, contact info, skills, experience, education, projects
- Outputs: `outputs/user_profile.json`

**Key Features**:
- Supports both "mock" and "live" LLM modes (configurable)
- Robust error handling for file I/O and LLM parsing
- Validates JSON structure before saving

**Configuration**:
- Config file: `config/config.ini`
- LLM mode: `mock` (uses local file) or `live` (uses Ollama)

---

### Stage 2: Data Ingestion (`agents/data_factory.py` + `agents/discovery_engine.py`)

#### Discovery Engine
**Purpose**: Multi-source job scraping

**Functionality**:
- Scrapes from 7+ job boards simultaneously
- Configurable search parameters (location, keywords, date filters)
- Deduplicates jobs based on description
- Outputs: `outputs/raw_jobs.csv`

**Supported Platforms**:
- LinkedIn
- Indeed
- Naukri
- Glassdoor
- ZipRecruiter
- Google Jobs
- Bayt

#### Data Factory
**Purpose**: Transform and persist jobs to MongoDB

**Functionality**:
- Adapts jobspy `JobPost` objects to `UnifiedJob` schema
- Generates deterministic fingerprints for deduplication
- Intelligent merge logic for duplicate jobs
- Atomic upsert operations with retry logic

**Key Features**:
- Normalizes data from different sources to unified schema
- Handles missing/optional fields gracefully
- Preserves source-specific metadata
- Exponential backoff retry for transient DB errors

---

### Stage 3: Opportunity Scorer (`agents/opportunity_scorer.py`)

**Purpose**: Score job opportunities against user resume

**Functionality**:
- Hybrid scoring engine with 3 components:
  1. **Keyword Match Score**: Fast skill-based matching
  2. **Semantic Context Score**: LLM-powered deep analysis
  3. **Requirement Fit Score**: Hard requirement validation
- Final score: Weighted average (0-10 scale)
- Updates job status to "scored" in MongoDB

**Scoring Algorithm**:
```
final_score = (
    skill_relevance_score * 0.3 +
    semantic_context_score * 0.5 +
    requirement_fit_score * 0.2
) * 10
```

**Key Features**:
- Uses local Ollama LLM (phi3.5 default)
- Comprehensive logging to `scoring_logs` collection
- Batch processing with configurable batch size
- Robust JSON parsing for small LLM models

**Output**:
- Updates `jobs` collection with `match_score` field
- Creates `JobScore` documents in `job_scores` collection
- Logs all LLM calls to `scoring_logs` collection

---

### Stage 4: Resume Tailor (`agents/resume_tailor.py`)

**Purpose**: Tailor resume content for specific jobs using LLM

**Functionality**:
- Reads high-scoring jobs (match_score >= threshold)
- Creates base content from user profile (static guarantee)
- Uses LLM to tailor:
  - Professional summary (3 sentences)
  - Experience bullet points
  - Skills ordering
- Preserves static fields (name, education, projects, company names, dates)

**Static vs Dynamic Fields**:
- **Static (never modified)**: full_name, contact_info, education, projects, company names, job titles, dates
- **Dynamic (LLM optimized)**: summary, experience highlights, skills order

**Key Features**:
- State machine: `pending_draft` → `drafting` → `ready_to_compile`
- Comprehensive logging to `tailoring_logs` collection
- Batch processing with configurable batch size
- Fallback to original content if LLM fails

**Output**:
- Creates `TailoredApplication` documents in `tailored_applications` collection
- Status: `ready_to_compile` when complete

---

### Stage 5: LaTeX Architect (`agents/latex_architect.py`)

**Purpose**: Compile tailored resumes to PDF

**Functionality**:
- Reads `TailoredApplication` documents with status `ready_to_compile`
- Renders Jinja2 template with tailored content
- Escapes LaTeX special characters (critical safety)
- Clusters skills into categories for template
- Compiles PDF using `pdflatex`
- Updates status: `compiling` → `completed` or `failed`

**Key Features**:
- LaTeX escaping for all user input (prevents compilation crashes)
- Skill clustering (flat list → categorized dictionary)
- Sandboxed compilation in temporary directories
- Error logging with full pdflatex output

**Output**:
- PDF files in `final_applications/` directory
- Updates `tailored_applications` with `final_pdf_path`
- Compilation logs stored in `compile_log` field

---

## Data Models

### Core Models (`core/models.py`)

#### UnifiedJob
Unified job posting schema that normalizes data from all sources.

**Key Fields**:
- `title`, `description`, `date_posted`
- `company`: UnifiedCompany (name, website, id)
- `location`: UnifiedLocation (city, state, country, geo)
- `compensation`: UnifiedCompensation (min/max, currency, interval)
- `source`, `source_id`, `source_url`
- `fingerprint`: SHA256 hash for deduplication
- `status`: "pending_review" | "scored" | "failed"
- `match_score`: float (0-10)

#### JobScore
Scoring results for a job-resume pair.

**Key Fields**:
- `job_fingerprint`, `resume_fingerprint`
- `skill_relevance_score`, `semantic_context_score`, `requirement_fit_score`
- `final_score`: Weighted average (0-10)
- `scoring_breakdown`: Detailed analysis
- `model_version`, `last_calculated`

#### TailoredApplication
State machine for resume tailoring lifecycle.

**Key Fields**:
- `job_fingerprint`, `resume_fingerprint`, `version`
- `status`: "pending_draft" | "drafting" | "ready_to_compile" | "compiling" | "completed" | "failed"
- `structured_content`: TailoredContent (the tailored resume data)
- `generated_tex_path`, `final_pdf_path`
- `compile_log`: pdflatex output for debugging

#### TailoredContent
Data contract between Writer (Agent 4) and Typesetter (Agent 5).

**Static Fields** (never modified by LLM):
- `full_name`, `contact_info`, `education`, `projects`

**Dynamic Fields** (LLM optimized):
- `summary`: Tailored professional summary
- `skills`: Reordered skills list
- `experience`: Experience entries with tailored highlights

---

## Pipeline Flow

### Complete Pipeline Execution

```python
python midjab/main.py
```

**Pipeline Stages**:

1. **Profile Parsing** (`run_profile_step`)
   - Parse `resume.tex` → `outputs/user_profile.json`
   - Skip if profile already exists

2. **Data Ingestion** (`run_ingest_step`) [Optional: `--skip-ingest`]
   - Scrape jobs from multiple sources
   - Transform to UnifiedJob schema
   - Save to MongoDB with deduplication

3. **Opportunity Scoring** (`run_scoring_step`)
   - Load user profile
   - Score all jobs in database
   - Update jobs with match_score

4. **Resume Tailoring** (`run_tailoring_step`)
   - Query high-scoring jobs (match_score >= threshold)
   - Tailor resume content per job
   - Save to `tailored_applications` collection

5. **PDF Compilation** (`run_compiler_step`)
   - Read `ready_to_compile` applications
   - Render LaTeX template
   - Compile PDFs
   - Update status to `completed`

### Command-Line Options

```bash
# Full pipeline
python midjab/main.py

# Skip ingestion (use existing jobs in DB)
python midjab/main.py --skip-ingest

# Run only scoring
python midjab/main.py --only-score

# Run only tailoring
python midjab/main.py --only-tailor

# Run only compilation
python midjab/main.py --only-compile
```

---

## Installation & Setup

### Prerequisites

1. **Python 3.8+**
2. **MongoDB** (local or remote)
3. **Ollama** (for local LLM)
4. **LaTeX** (TeX Live or MiKTeX for PDF compilation)

### Step 1: Clone Repository

```bash
git clone <repository-url>
cd midjab
```

### Step 2: Install Dependencies

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install Python packages
pip install -r midjab/requirements.txt
```

**Required Packages** (from code analysis):
- `pymongo` - MongoDB driver
- `pydantic` - Data validation
- `ollama` - Local LLM client
- `jinja2` - Template rendering
- `pandas` - Data processing
- `python-dotenv` - Environment variables

### Step 3: Setup MongoDB

```bash
# Start MongoDB (if local)
mongod

# Initialize indexes
python midjab/scripts/setup_db.py
```

**Environment Variables** (`.env` file):
```env
MONGO_URI=mongodb://localhost:27017
MONGO_DB=midjab_v2
USE_MOCK=0  # Set to 1 for testing with mongomock
```

### Step 4: Setup Ollama

```bash
# Install Ollama (if not installed)
# macOS: brew install ollama
# Linux: curl -fsSL https://ollama.ai/install.sh | sh

# Pull required model
ollama pull phi3.5

# Verify Ollama is running
ollama list
```

### Step 5: Prepare Resume

Place your LaTeX resume at project root:
```
midjab/
└── resume.tex  # Your base resume
```

### Step 6: Configure LLM

Create `config/config.ini`:
```ini
[LLM]
mode = live  # or "mock" for testing
remote_host = http://localhost:11434
model_name = phi3.5
```

---

## Usage Guide

### Basic Workflow

1. **Prepare your resume**: Place `resume.tex` in project root

2. **Run full pipeline**:
   ```bash
   python midjab/main.py
   ```

3. **Check results**:
   - Scored jobs: MongoDB `jobs` collection (sorted by `match_score`)
   - Tailored resumes: MongoDB `tailored_applications` collection
   - PDF files: `final_applications/` directory

### Advanced Usage

#### Custom Search Parameters

Edit `agents/discovery_engine.py`:
```python
self.search_parameters = {
    "search_term": "Your Job Title",
    "location": "Your City, Country",
    "results_wanted": 50,
    "hours_old": 168  # 1 week
}
```

#### Adjust Scoring Threshold

Edit `main.py` or call directly:
```python
from agents.resume_tailor import ResumeTailorV2

tailor = ResumeTailorV2(min_match_score=7.0)  # Only tailor jobs with score >= 7.0
tailor.run_tailoring_pipeline(batch_size=20)
```

#### Use Different LLM Model

```python
from agents.opportunity_scorer import OpportunityScorerV2

scorer = OpportunityScorerV2(llm_model="llama3.2")
scorer.run_full_scoring(batch_size=50)
```

---

## Technical Details

### Database Collections

1. **jobs**: Unified job postings
   - Indexes: `fingerprint` (unique), `status`, `match_score`

2. **job_scores**: Scoring results
   - Links: `job_fingerprint` → `resume_fingerprint`

3. **scoring_logs**: LLM call audit trail
   - Tracks: prompts, responses, latency, costs

4. **tailored_applications**: Tailored resume state
   - Status machine: `pending_draft` → `drafting` → `ready_to_compile` → `compiling` → `completed`

5. **tailoring_logs**: Tailoring operation audit trail
   - Tracks: LLM calls for summary, bullets, skills

### Fingerprinting Strategy

Jobs are deduplicated using SHA256 fingerprints:
```
fingerprint = SHA256(
    normalized(company.name) + "::" +
    normalized(title) + "::" +
    normalized(city) + "::" +
    last_5_tokens(description)
)
```

### LaTeX Safety

All user input is escaped before template rendering:
- Special characters: `&`, `%`, `$`, `#`, `_`, `{`, `}`, `~`, `^`, `\`
- Prevents compilation crashes from user data

### Error Handling

- **Retry Logic**: Exponential backoff for transient DB errors (3 attempts)
- **Fallback Fingerprints**: If primary fingerprint generation fails, uses fallback SHA256
- **Graceful Degradation**: LLM failures fall back to original content
- **Comprehensive Logging**: All operations logged to MongoDB for debugging

---

## Work Completed

### ✅ Phase 1: Core Infrastructure
- [x] MongoDB database setup with proper indexes
- [x] Unified data models (UnifiedJob, JobScore, TailoredApplication)
- [x] Database connection layer with mock support
- [x] Index initialization scripts

### ✅ Phase 2: Job Scraping & Ingestion
- [x] Multi-source job scraper (7+ platforms)
- [x] JobSpy integration for LinkedIn, Indeed, Naukri, etc.
- [x] Data factory adapter (JobPost → UnifiedJob)
- [x] Intelligent deduplication with fingerprints
- [x] Merge logic for duplicate jobs

### ✅ Phase 3: Profile Parsing
- [x] LaTeX resume parser
- [x] LLM-powered structured extraction
- [x] Mock mode for testing
- [x] User profile JSON generation

### ✅ Phase 4: Opportunity Scoring
- [x] Hybrid scoring engine (keyword + LLM + requirements)
- [x] Local Ollama LLM integration
- [x] Comprehensive scoring logs
- [x] Batch processing support
- [x] Robust JSON parsing for small models

### ✅ Phase 5: Resume Tailoring
- [x] LLM-powered resume writer
- [x] Static field guarantee (personal info never modified)
- [x] Dynamic field optimization (summary, bullets, skills)
- [x] State machine for tailoring lifecycle
- [x] Comprehensive operation logging

### ✅ Phase 6: PDF Generation
- [x] LaTeX template system (Jinja2)
- [x] LaTeX escaping for safety
- [x] Skill clustering for template
- [x] PDF compilation with error handling
- [x] Status tracking and error logging

### ✅ Phase 7: Pipeline Orchestration
- [x] Main orchestrator (`main.py`)
- [x] Command-line interface
- [x] Stage skipping options
- [x] Error handling and logging

### ✅ Phase 8: Documentation
- [x] Complete project documentation
- [x] Architecture diagrams
- [x] Usage guides
- [x] Technical specifications

---

## Future Enhancements

### Short-term Improvements
1. **Requirements.txt**: Create proper dependency file
2. **Configuration Management**: Centralize config (search params, thresholds)
3. **Error Recovery**: Resume failed operations from last checkpoint
4. **Progress Tracking**: Real-time pipeline progress indicators
5. **Testing Suite**: Unit tests for all agents

### Medium-term Enhancements
1. **Web Interface**: Flask/FastAPI dashboard for job browsing
2. **Email Notifications**: Alert on high-scoring jobs
3. **Multi-resume Support**: Handle multiple resume versions
4. **A/B Testing**: Compare different tailoring strategies
5. **Cost Tracking**: Monitor LLM usage and costs

### Long-term Vision
1. **Application Tracking**: Track application status and responses
2. **Interview Prep**: Generate interview questions from job descriptions
3. **Cover Letter Generation**: Auto-generate tailored cover letters
4. **Multi-language Support**: Support non-English job boards
5. **ML Model Training**: Train custom models on successful applications

---

## File Structure Summary

```
midjab/
├── agents/                    # Pipeline agents (5 stages)
│   ├── profile_parser.py     # Stage 1: Parse resume
│   ├── data_factory.py        # Stage 2: Job ingestion
│   ├── discovery_engine.py   # Stage 2: Multi-source scraper
│   ├── opportunity_scorer.py # Stage 3: Job scoring
│   ├── resume_tailor.py      # Stage 4: Resume tailoring
│   └── latex_architect.py    # Stage 5: PDF compilation
├── core/                      # Core infrastructure
│   ├── db.py                 # MongoDB connection
│   └── models.py             # Pydantic data models
├── jobspy/                    # Job scraping library
│   ├── linkedin/             # LinkedIn scraper
│   ├── indeed/               # Indeed scraper
│   ├── naukri/               # Naukri scraper
│   ├── glassdoor/            # Glassdoor scraper
│   ├── ziprecruiter/         # ZipRecruiter scraper
│   ├── google/                # Google Jobs scraper
│   └── bayt/                 # Bayt scraper
├── templates/                 # LaTeX templates
│   └── master_resume.tex.j2  # Resume template
├── scripts/                   # Utility scripts
│   └── setup_db.py           # DB initialization
├── final_applications/        # Generated PDFs
├── outputs/                   # Intermediate outputs
│   └── user_profile.json     # Parsed resume data
├── config/                    # Configuration
│   └── config.ini            # LLM settings
├── main.py                    # Pipeline orchestrator
└── resume.tex                 # Base resume (user-provided)
```

---

## Conclusion

MidJab V2 is a production-ready job application automation system with:
- **Complete pipeline**: From job scraping to PDF generation
- **Intelligent matching**: Hybrid LLM + keyword scoring
- **Automated tailoring**: LLM-powered resume optimization
- **Professional output**: LaTeX-generated PDFs
- **Robust architecture**: MongoDB-driven, error-handled, logged

The system is ready for use and can be extended with additional features as needed.

---

**Last Updated**: 2025-01-XX
**Version**: 2.0
**Status**: Production Ready
