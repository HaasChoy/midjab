from __future__ import annotations
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, validator
from datetime import datetime
import hashlib
import re


def _normalize_text(s: Optional[str]) -> Optional[str]:
    if s is None:
        return None
    s = s.strip()
    s = re.sub(r"\s+", " ", s)
    return s


class UnifiedLocation(BaseModel):
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    postal_code: Optional[str] = None
    geo: Optional[Dict[str, float]] = None 

    @validator("city", "state", "country", pre=True, always=True)
    def _strip_strings(cls, v):
        return _normalize_text(v)


class UnifiedCompensation(BaseModel):
    min_amount: Optional[float] = None
    max_amount: Optional[float] = None
    currency: Optional[str] = None  
    interval: Optional[str] = None  

    @validator("currency", pre=True, always=True)
    def _norm_currency(cls, v):
        if v is None:
            return None
        return _normalize_text(v).upper()


class UnifiedCompany(BaseModel):
    name: Optional[str] = None
    website: Optional[str] = None
    id: Optional[str] = None 

    @validator("name", pre=True, always=True)
    def _strip_name(cls, v):
        return _normalize_text(v)


class UnifiedJob(BaseModel):
    # Core fields
    title: Optional[str] = None
    description: Optional[str] = None
    date_posted: Optional[datetime] = None
    date_scraped: datetime = Field(default_factory=datetime.utcnow)

    # Nested structured fields
    company: Optional[UnifiedCompany] = None
    location: Optional[UnifiedLocation] = None
    compensation: Optional[UnifiedCompensation] = None

    # Source & provenance
    source: Optional[str] = None 
    source_id: Optional[str] = None  
    source_url: Optional[str] = None
    source_metadata: Dict[str, Any] = Field(default_factory=dict)

    # System fields
    fingerprint: Optional[str] = None
    status: str = Field(default="pending_review")
    match_score: Optional[float] = None
    schema_version: int = Field(default=1)

    # Soft fields / enrichment
    skills: Optional[List[str]] = None
    job_level: Optional[str] = None
    employment_type: Optional[str] = None  
    remote: Optional[bool] = None

    # Raw payload (keeps the original scraped object minimally)
    raw: Optional[Dict[str, Any]] = None

    # ---- Helpers ----
    def generate_fingerprint(self, extra_tokens: int = 5) -> str:
        """Generate a deterministic fingerprint for deduplication.

        Default strategy:
          normalized(company.name) + '::' + normalized(title) + '::' + normalized(city) + '::' + last_n_tokens(description, extra_tokens)

        Returns a sha256 hex digest.
        """
        parts = []
        if self.company and self.company.name:
            parts.append(_normalize_text(self.company.name).lower())
        else:
            parts.append("")

        parts.append(_normalize_text(self.title or "").lower())

        city = None
        if self.location and self.location.city:
            city = _normalize_text(self.location.city).lower()
        parts.append(city or "")

        desc_tail = ""
        if self.description:
            tokens = re.findall(r"\w+", self.description.lower())
            if tokens:
                desc_tail = " ".join(tokens[-extra_tokens:])
        parts.append(desc_tail)

        raw = "::".join(parts)
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        self.fingerprint = digest
        return digest

    def to_mongo(self) -> Dict[str, Any]:
        """Prepare a Mongo-friendly dict. Use by storage layer.
        Ensures datetime -> ISO format and drops None values when sensible.
        """
        d = self.dict(by_alias=True, exclude_none=True)
        # Pydantic keeps datetime objects; leave them as-is for pymongo to handle.
        return d

    @validator("title", "description", "source_url", pre=True, always=True)
    def _normalize_text_fields(cls, v):
        return _normalize_text(v)


# -----------------------------
# Scoring models (single source of truth)
# -----------------------------


class JobScore(BaseModel):
    """Model for documents stored in the `job_scores` collection.

    This is the canonical / single-source definition for job scoring records.
    """
    job_fingerprint: str
    resume_fingerprint: str
    skill_relevance_score: float = Field(..., ge=0.0, le=1.0)
    semantic_context_score: float = Field(..., ge=0.0, le=1.0)
    requirement_fit_score: float = Field(..., ge=0.0, le=1.0)
    final_score: float = Field(..., ge=0.0, le=1.0)
    scoring_breakdown: Dict[str, Any] = Field(default_factory=dict)
    model_version: str = Field(default="scoring_v1.0")
    llm_prompt_hash: Optional[str] = None
    last_calculated: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        schema_extra = {
            "example": {
                "job_fingerprint": "abc123",
                "resume_fingerprint": "resumehash01",
                "skill_relevance_score": 0.85,
                "semantic_context_score": 0.72,
                "requirement_fit_score": 0.9,
                "final_score": 0.82,
                "scoring_breakdown": {"missing_skills": ["react"], "matched_requirements": ["aws"]},
                "model_version": "scoring_v1.2",
                "llm_prompt_hash": "sha256-...",
                "last_calculated": "2025-11-15T12:00:00Z"
            }
        }


class ScoringLog(BaseModel):
    """Model for documents stored in the `scoring_logs` collection.

    Stores auditable LLM call details used by the scoring pipeline.
    """
    job_fingerprint: str
    resume_fingerprint: str
    score_type_requested: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    llm_used: Optional[str] = None
    llm_prompt_hash: Optional[str] = None
    raw_prompt: Optional[str] = None
    raw_llm_response: Optional[str] = None
    parsed_result: Optional[Any] = None
    cost: Optional[float] = None
    latency_ms: Optional[int] = None

    class Config:
        schema_extra = {
            "example": {
                "job_fingerprint": "abc123",
                "resume_fingerprint": "resumehash01",
                "score_type_requested": "semantic_context_score",
                "llm_used": "gpt-4o-mini",
                "llm_prompt_hash": "sha256-...",
                "raw_prompt": "...",
                "raw_llm_response": "...",
                "parsed_result": 0.72,
                "cost": 0.0023,
                "latency_ms": 234
            }
        }


# -----------------------------
# Tailoring models (Phase 3: Resume Tailoring)
# -----------------------------


class TailoredContent(BaseModel):
    """Data contract between Writer (Agent 4) and Typesetter (Agent 5).
    
    This model enforces strict separation between:
    - Static Data: Fields that LLMs CANNOT modify (personal info, dates, company names)
    - Dynamic Data: Fields that LLMs optimize for job fit (summaries, bullets, skills order)
    
    Used in the `tailored_applications` collection as the `structured_content` field.
    """
    # --- STATIC FIELDS (LLM cannot modify) ---
    full_name: str
    contact_info: Dict[str, Any] = Field(
        default_factory=dict,
        description="Contact details: email, phone, linkedin, github, etc."
    )
    education: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Educational background. Structure: {degree, institution, dates, gpa, etc.}"
    )
    projects: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Side projects. Structure: {name, description, technologies, url, etc.}"
    )
    
    # --- DYNAMIC FIELDS (LLM optimizes) ---
    summary: Optional[str] = Field(
        None,
        description="Tailored professional summary targeting the specific job"
    )
    skills: List[str] = Field(
        default_factory=list,
        description="Reordered and filtered skills list optimized for job relevance"
    )
    experience: List[Dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Work experience entries. Structure must preserve static fields "
            "(company, title, dates) while allowing dynamic fields (highlights/bullets) "
            "to be tailored. Example: "
            "{company: 'Acme Inc', title: 'Engineer', dates: '2020-2023', "
            "highlights: ['Optimized...', 'Built...']}"
        )
    )
    
    class Config:
        schema_extra = {
            "example": {
                "full_name": "Jane Doe",
                "contact_info": {
                    "email": "jane@example.com",
                    "phone": "+91-9876543210",
                    "linkedin": "linkedin.com/in/janedoe"
                },
                "education": [
                    {
                        "degree": "B.Tech Computer Science",
                        "institution": "IIT Bombay",
                        "dates": "2016-2020",
                        "gpa": "9.2/10"
                    }
                ],
                "summary": "Full-stack engineer with 4 years building scalable systems...",
                "skills": ["Python", "Django", "PostgreSQL", "AWS", "Docker"],
                "experience": [
                    {
                        "company": "Tech Corp",
                        "title": "Senior Engineer",
                        "dates": "2021-2024",
                        "highlights": [
                            "Reduced API latency by 40% through caching optimization",
                            "Led migration of monolith to microservices architecture"
                        ]
                    }
                ],
                "projects": [
                    {
                        "name": "OpenSource Contributor",
                        "description": "Core contributor to Django REST framework",
                        "url": "github.com/janedoe/contributions"
                    }
                ]
            }
        }


class TailoredApplication(BaseModel):
    """State machine for resume tailoring lifecycle.
    
    Represents a specific resume version tailored for a specific job.
    Maps to the `tailored_applications` MongoDB collection.
    
    Lifecycle states:
    - pending_draft: Initial state, waiting for Writer to start
    - drafting: Writer (Agent 4) is generating content
    - ready_to_compile: Writer finished, content ready for Typesetter
    - compiling: Typesetter (Agent 5) is generating LaTeX/PDF
    - completed: PDF successfully generated
    - failed: Error occurred during drafting or compilation
    """
    # --- Identity ---
    job_fingerprint: str = Field(..., description="Links to the target job")
    resume_fingerprint: str = Field(..., description="Links to the source resume")
    version: int = Field(default=1, description="Version number for iterative improvements")
    
    # --- Status ---
    status: str = Field(
        default="pending_draft",
        description="Current state in the tailoring pipeline"
    )
    
    # --- Content ---
    structured_content: Optional[TailoredContent] = Field(
        None,
        description="The tailored resume content (Writer output)"
    )
    
    # --- Artifacts ---
    latex_template_used: str = Field(
        default="modern_cv_v1.tex",
        description="LaTeX template identifier used by Typesetter"
    )
    generated_tex_path: Optional[str] = Field(
        None,
        description="File path to generated .tex file"
    )
    final_pdf_path: Optional[str] = Field(
        None,
        description="File path to compiled PDF resume"
    )
    compile_log: Optional[str] = Field(
        None,
        description="pdflatex stdout/stderr for debugging compilation issues"
    )
    
    # --- Timestamps ---
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_updated: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        schema_extra = {
            "example": {
                "job_fingerprint": "abc123def456",
                "resume_fingerprint": "resume789xyz",
                "version": 1,
                "status": "completed",
                "structured_content": {
                    "full_name": "Jane Doe",
                    "summary": "Experienced backend engineer...",
                    "skills": ["Python", "AWS", "PostgreSQL"]
                },
                "latex_template_used": "modern_cv_v1.tex",
                "generated_tex_path": "/tmp/resumes/jane_doe_abc123_v1.tex",
                "final_pdf_path": "/tmp/resumes/jane_doe_abc123_v1.pdf",
                "compile_log": "This is pdfTeX, Version 3.14159...",
                "created_at": "2025-11-20T10:30:00Z",
                "last_updated": "2025-11-20T10:45:00Z"
            }
        }


class TailoringLog(BaseModel):
    """Audit trail for LLM-based tailoring operations.
    
    Stores detailed logs of Writer and Typesetter actions for debugging,
    cost tracking, and quality analysis. Maps to the `tailoring_logs` collection.
    """
    # --- Identity ---
    job_fingerprint: str
    resume_fingerprint: str = Field(
        ...,
        description="Links to the source resume being tailored"
    )
    application_version: int = Field(
        ...,
        description="Version of the TailoredApplication this log entry relates to"
    )
    
    # --- Action Details ---
    action: str = Field(
        ...,
        description=(
            "Type of tailoring action performed. Examples: "
            "'draft_summary', 'optimize_bullets', 'reorder_skills', 'compile_latex'"
        )
    )
    
    # --- LLM Call Details ---
    raw_prompt: Optional[str] = Field(
        None,
        description="Complete prompt sent to the LLM"
    )
    llm_response: Optional[str] = Field(
        None,
        description="Raw response from the LLM"
    )
    llm_model: Optional[str] = Field(
        None,
        description="Model used (e.g., 'gpt-4o', 'claude-sonnet-4')"
    )
    
    # --- Metrics ---
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    latency_ms: Optional[int] = Field(
        None,
        description="Time taken for the operation in milliseconds"
    )
    token_count: Optional[int] = Field(
        None,
        description="Total tokens used (input + output)"
    )
    cost_usd: Optional[float] = Field(
        None,
        description="Estimated cost in USD"
    )
    
    # --- Outcome ---
    success: bool = Field(default=True, description="Whether the action succeeded")
    error_message: Optional[str] = Field(
        None,
        description="Error details if action failed"
    )
    
    class Config:
        schema_extra = {
            "example": {
                "job_fingerprint": "abc123def456",
                "resume_fingerprint": "resume789xyz",
                "application_version": 1,
                "action": "draft_summary",
                "raw_prompt": "Given job requirements... write a professional summary...",
                "llm_response": "Experienced software engineer with 5 years...",
                "llm_model": "gpt-4o-mini",
                "timestamp": "2025-11-20T10:30:15Z",
                "latency_ms": 1250,
                "token_count": 450,
                "cost_usd": 0.0023,
                "success": True,
                "error_message": None
            }
        }


# Example testing for tailoring models
if __name__ == "__main__":
    # Test TailoredContent creation
    content = TailoredContent(
        full_name="John Smith",
        contact_info={"email": "john@example.com"},
        summary="Skilled developer with expertise in Python and cloud technologies",
        skills=["Python", "AWS", "Docker"],
        experience=[
            {
                "company": "Tech Startup",
                "title": "Software Engineer",
                "dates": "2020-2023",
                "highlights": ["Built scalable APIs", "Improved deployment pipeline"]
            }
        ]
    )
    
    # Test TailoredApplication creation
    application = TailoredApplication(
        job_fingerprint="job123",
        resume_fingerprint="resume456",
        structured_content=content,
        status="ready_to_compile"
    )
    
    # Test TailoringLog creation
    log = TailoringLog(
        job_fingerprint="job123",
        resume_fingerprint="resume456",
        application_version=1,
        action="draft_summary",
        llm_model="gpt-4o-mini",
        latency_ms=1200,
        token_count=400
    )
    
    print("TailoredContent:", content.dict())
    print("\nTailoredApplication:", application.dict())
    print("\nTailoringLog:", log.dict())