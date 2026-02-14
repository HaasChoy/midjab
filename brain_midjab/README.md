# MidJab V2 - Intelligent Job Application Automation

**MidJab** is an automated job application system that scrapes jobs from multiple platforms, scores them against your resume, and generates tailored PDF resumes for high-scoring opportunities.

## 🚀 Features

- **Multi-Source Job Scraping**: Aggregates jobs from LinkedIn, Indeed, Naukri, Glassdoor, ZipRecruiter, Google Jobs, and Bayt
- **Intelligent Job Scoring**: Hybrid LLM + keyword matching to rank opportunities (0-10 scale)
- **Automated Resume Tailoring**: Uses local LLM (Ollama) to optimize resume content per job
- **Professional PDF Generation**: LaTeX-based resume compilation with proper formatting
- **MongoDB-Driven Workflow**: All state managed in database for reliability and scalability

## 📋 Quick Start

1. **Install Dependencies**:
   ```bash
   pip install -r midjab/requirements.txt
   ```

2. **Setup MongoDB**:
   ```bash
   python midjab/scripts/setup_db.py
   ```

3. **Setup Ollama** (for local LLM):
   ```bash
   ollama pull phi3.5
   ```

4. **Place your resume** at project root: `resume.tex`

5. **Run the pipeline**:
   ```bash
   python midjab/main.py
   ```

## 📖 Documentation

For complete documentation, see [DOCUMENTATION.md](DOCUMENTATION.md)

The documentation includes:
- Complete architecture overview
- Detailed component descriptions
- Installation & setup guide
- Usage examples
- Technical specifications
- Work completed summary

## 🏗️ Architecture

The system consists of 5 pipeline stages:

1. **Profile Parser**: Extracts structured data from LaTeX resume
2. **Data Ingestion**: Scrapes and normalizes jobs from multiple sources
3. **Opportunity Scorer**: Scores jobs using hybrid LLM + keyword matching
4. **Resume Tailor**: Optimizes resume content per job using LLM
5. **LaTeX Architect**: Compiles tailored resumes to PDF

## 📁 Project Structure

```
midjab/
├── agents/          # Pipeline agents (5 stages)
├── core/            # Database & data models
├── jobspy/          # Job scraping library
├── templates/       # LaTeX resume templates
├── scripts/         # Utility scripts
└── main.py         # Pipeline orchestrator
```

## 🔧 Configuration

Create `config/config.ini`:
```ini
[LLM]
mode = live
remote_host = http://localhost:11434
model_name = phi3.5
```

Set environment variables (`.env`):
```env
MONGO_URI=mongodb://localhost:27017
MONGO_DB=midjab_v2
```

## 📊 Output

- **Scored Jobs**: MongoDB `jobs` collection (sorted by `match_score`)
- **Tailored Resumes**: MongoDB `tailored_applications` collection
- **PDF Files**: `final_applications/` directory

## 🛠️ Command-Line Options

```bash
# Full pipeline
python midjab/main.py

# Skip ingestion (use existing jobs)
python midjab/main.py --skip-ingest

# Run only specific stage
python midjab/main.py --only-score
python midjab/main.py --only-tailor
python midjab/main.py --only-compile
```

## 📝 Requirements

- Python 3.8+
- MongoDB (local or remote)
- Ollama (for local LLM)
- LaTeX (TeX Live or MiKTeX)

## 🎯 Status

**Version**: 2.0  
**Status**: Production Ready

All core features implemented and tested. See [DOCUMENTATION.md](DOCUMENTATION.md) for complete work summary.

## 📄 License

See LICENSE file for details.
