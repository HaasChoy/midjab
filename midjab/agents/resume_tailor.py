"""
Resume Tailor V2 (The Writer)
==============================

MongoDB-driven resume tailoring agent that reads high-scoring jobs from MongoDB,
uses a local LLM (Ollama) to rewrite resume content (Summary and Experience bullets),
and saves the structured result back to MongoDB.

This agent acts as the "Writer" - it does NOT generate PDFs (handled by separate agent).

Key Features:
- Database-driven workflow (no file I/O)
- Static guarantee: personal info, education, projects are NEVER modified by LLM
- Dynamic tailoring: Summary, Experience bullets, Skills order optimized per job
- Comprehensive logging of all LLM operations
- Robust JSON parsing for Llama 3.2 output
"""

import json
import hashlib
import re
import time
from datetime import datetime
from typing import Dict, List, Optional, Any
import ollama

from core.db import get_db
from core.models import TailoredApplication, TailoredContent, TailoringLog


class ResumeTailorV2:
    """
    MongoDB-Driven Resume Tailor V2 (The Writer).
    
    Reads scored jobs from MongoDB, tailors resume content using local LLM,
    and saves structured results back to MongoDB.
    """
    
    def __init__(self, 
                 llm_model: str = "phi3.5",
                 ollama_host: str = "http://localhost:11434",
                 profile_path: str = "outputs/user_profile.json",
                 min_match_score: float = 6.0):
        """
        Initialize ResumeTailorV2.
        
        Args:
            llm_model: Ollama model name (default: phi3.5)
            ollama_host: Ollama server URL
            profile_path: Path to user_profile.json
            min_match_score: Minimum match_score threshold for jobs to process
        """
        self.db = get_db()
        self.llm_model = llm_model
        self.ollama_host = ollama_host
        self.profile_path = profile_path
        self.min_match_score = min_match_score
        
        # Load user profile
        self.user_profile = self._load_user_profile()
        
        # Calculate resume fingerprint (SHA256 of profile JSON)
        self.resume_fingerprint = self._calculate_resume_fingerprint()
        
        # Initialize Ollama client
        try:
            self.ollama_client = ollama.Client(host=ollama_host)
        except Exception as e:
            print(f"Warning: Could not initialize Ollama client: {e}")
            self.ollama_client = None
        
        print(f"✓ ResumeTailorV2 initialized")
        print(f"  - Model: {llm_model}")
        print(f"  - Resume Fingerprint: {self.resume_fingerprint}")
        print(f"  - Min Match Score: {min_match_score}")
    
    def _load_user_profile(self) -> Dict[str, Any]:
        """Load user profile from JSON file."""
        try:
            with open(self.profile_path, 'r', encoding='utf-8') as f:
                profile = json.load(f)
            print(f"✓ Loaded user profile: {profile.get('full_name', 'Unknown')}")
            return profile
        except FileNotFoundError:
            raise FileNotFoundError(f"User profile not found: {self.profile_path}")
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in user profile: {e}")
    
    def _calculate_resume_fingerprint(self) -> str:
        """
        Calculate SHA256 fingerprint of user profile JSON.
        
        This links applications to the correct resume version.
        """
        profile_json = json.dumps(self.user_profile, sort_keys=True)
        fingerprint = hashlib.sha256(profile_json.encode('utf-8')).hexdigest()
        return fingerprint
    
    def _parse_llm_json(self, content: str) -> Optional[Dict]:
        """
        Robust JSON parser for LLM outputs (reused from Scorer V2).
        
        Handles:
        - Markdown code blocks (```json ... ```)
        - Extra text before/after JSON
        - Malformed JSON attempts
        
        Args:
            content: Raw LLM output
            
        Returns:
            dict: Parsed JSON object or None
        """
        try:
            # Try direct parsing first
            return json.loads(content)
        except json.JSONDecodeError:
            pass
        
        # Remove markdown code blocks
        content = re.sub(r'```json\s*|\s*```', '', content)
        
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass
        
        # Last resort: Extract content between first { and last }
        try:
            start = content.find('{')
            end = content.rfind('}')
            if start != -1 and end != -1 and end > start:
                json_str = content[start:end+1]
                return json.loads(json_str)
        except (json.JSONDecodeError, ValueError):
            pass
        
        print(f"Warning: Failed to parse JSON from LLM output: {content[:200]}")
        return None
    
    def _call_llm(self, prompt: str, action: str, job_fingerprint: str, 
                  application_version: int = 1) -> Optional[Dict]:
        """
        Call local Ollama LLM with structured JSON output and log the call.
        
        Args:
            prompt: The prompt to send to the LLM
            action: Action type for logging (e.g., 'draft_summary', 'optimize_bullets')
            job_fingerprint: Job fingerprint for logging
            application_version: Application version for logging
            
        Returns:
            dict: Parsed JSON response from LLM or None if failed
        """
        if not self.ollama_client:
            print("Error: Ollama client not initialized")
            return None
        
        start_time = time.time()
        raw_response = None
        success = False
        error_message = None
        
        try:
            response = self.ollama_client.chat(
                model=self.llm_model,
                messages=[{'role': 'user', 'content': prompt}],
                format='json',  # Force JSON output
                options={
                    'temperature': 0.3,  # Lower temperature for more consistent output
                    'num_predict': 1000   # Limit response length
                }
            )
            
            # Extract content
            raw_response = response['message']['content']
            
            # Parse JSON with robust error handling
            parsed = self._parse_llm_json(raw_response)
            
            if parsed:
                success = True
            else:
                error_message = "Failed to parse JSON from LLM response"
            
            latency_ms = int((time.time() - start_time) * 1000)
            
            # Log the LLM call
            self._log_llm_call(
                job_fingerprint=job_fingerprint,
                application_version=application_version,
                action=action,
                prompt=prompt,
                response=raw_response,
                latency_ms=latency_ms,
                success=success,
                error_message=error_message
            )
            
            return parsed
            
        except Exception as e:
            error_message = str(e)
            latency_ms = int((time.time() - start_time) * 1000)
            
            # Log failed call
            self._log_llm_call(
                job_fingerprint=job_fingerprint,
                application_version=application_version,
                action=action,
                prompt=prompt,
                response=None,
                latency_ms=latency_ms,
                success=False,
                error_message=error_message
            )
            
            print(f"Error in LLM call ({action}): {e}")
            return None
    
    def _log_llm_call(self, job_fingerprint: str, application_version: int,
                     action: str, prompt: str, response: Optional[str],
                     latency_ms: int, success: bool, error_message: Optional[str]):
        """
        Log LLM call to MongoDB tailoring_logs collection.
        
        Args:
            job_fingerprint: Job fingerprint
            application_version: Application version
            action: Action type (e.g., 'draft_summary')
            prompt: Raw prompt sent to LLM
            response: Raw response from LLM
            latency_ms: Latency in milliseconds
            success: Whether the call succeeded
            error_message: Error message if failed
        """
        log_entry = TailoringLog(
            job_fingerprint=job_fingerprint,
            resume_fingerprint=self.resume_fingerprint,
            application_version=application_version,
            action=action,
            raw_prompt=prompt,
            llm_response=response,
            llm_model=self.llm_model,
            timestamp=datetime.utcnow(),
            latency_ms=latency_ms,
            success=success,
            error_message=error_message
        )
        
        self.db.tailoring_logs.insert_one(log_entry.dict(by_alias=True))
    
    def _create_base_content(self) -> TailoredContent:
        """
        Create base TailoredContent by copying static fields from user profile.
        
        CRITICAL: This method ensures the "Static Guarantee" - these fields
        are NEVER modified by the LLM. They are copied directly from user_profile.json.
        
        Returns:
            TailoredContent with static fields populated
        """
        # Extract contact info
        contact_info = {
            "email": self.user_profile.get("email", ""),
            "phone": self.user_profile.get("phone", ""),
            "github": self.user_profile.get("github_url", ""),
            "portfolio": self.user_profile.get("portfolio_url", ""),
        }
        # Remove empty values
        contact_info = {k: v for k, v in contact_info.items() if v}
        
        # Copy education directly (static)
        education = self.user_profile.get("education", [])
        
        # Copy projects directly (static)
        projects = self.user_profile.get("projects", [])
        
        # Extract experience structure (preserve static fields, prepare for highlights)
        experience = []
        for exp in self.user_profile.get("experience", []):
            exp_entry = {
                "company": exp.get("company", ""),
                "title": exp.get("title", ""),
                "location": exp.get("location", ""),
                "dates": exp.get("duration", ""),
                # Highlights will be populated by LLM (dynamic)
                "highlights": exp.get("description_points", [])
            }
            experience.append(exp_entry)
        
        # Get original summary (will be tailored by LLM)
        original_summary = self.user_profile.get("summary", "")
        
        # Get original skills (will be reordered by LLM)
        original_skills = self.user_profile.get("skills", [])
        
        return TailoredContent(
            full_name=self.user_profile.get("full_name", ""),
            contact_info=contact_info,
            education=education,
            projects=projects,
            summary=original_summary,  # Will be tailored
            skills=original_skills,    # Will be reordered
            experience=experience      # Highlights will be tailored
        )
    
    def _tailor_summary(self, job: Dict, base_content: TailoredContent, 
                       job_fingerprint: str, application_version: int) -> str:
        """
        Use LLM to tailor the professional summary for a specific job.
        
        Args:
            job: Job document from MongoDB
            base_content: Base TailoredContent with original summary
            job_fingerprint: Job fingerprint for logging
            application_version: Application version for logging
            
        Returns:
            str: Tailored summary (3 sentences)
        """
        job_title = job.get("title", "")
        job_description = job.get("description", "")[:1500]  # Limit length
        original_summary = base_content.summary or "No summary provided"
        
        prompt = f"""You are an expert resume writer. Write a professional summary optimized for this specific job.

JOB POSTING:
Title: {job_title}
Description: {job_description[:1500]}

CANDIDATE'S ORIGINAL SUMMARY:
{original_summary}

TASK:
Write a NEW 3-sentence professional summary that:
1. Highlights the candidate's most relevant skills/experience for THIS job
2. Uses keywords and phrases from the job description
3. Maintains authenticity (only mention skills/experience the candidate actually has)
4. Is concise and impactful (exactly 3 sentences)

Respond with ONLY valid JSON in this exact format:
{{
    "summary": "First sentence. Second sentence. Third sentence."
}}"""
        
        result = self._call_llm(
            prompt=prompt,
            action="draft_summary",
            job_fingerprint=job_fingerprint,
            application_version=application_version
        )
        
        if result and "summary" in result:
            return result["summary"]
        else:
            # Fallback to original summary
            print(f"Warning: LLM failed to tailor summary, using original")
            return original_summary
    
    def _tailor_experience_bullets(self, job: Dict, base_content: TailoredContent,
                                   job_fingerprint: str, application_version: int) -> List[Dict]:
        """
        Use LLM to tailor experience bullet points for a specific job.
        
        Args:
            job: Job document from MongoDB
            base_content: Base TailoredContent with original experience
            job_fingerprint: Job fingerprint for logging
            application_version: Application version for logging
            
        Returns:
            List[Dict]: Experience entries with tailored highlights
        """
        job_title = job.get("title", "")
        job_description = job.get("description", "")[:1500]
        
        tailored_experience = []
        
        # Process each experience entry
        for i, exp_entry in enumerate(base_content.experience):
            company = exp_entry.get("company", "")
            title = exp_entry.get("title", "")
            original_highlights = exp_entry.get("highlights", [])
            
            if not original_highlights:
                # No highlights to tailor, keep as-is
                tailored_experience.append(exp_entry)
                continue
            
            # Create prompt for this experience entry
            highlights_text = "\n".join([f"- {h}" for h in original_highlights])
            
            prompt = f"""You are an expert resume writer. Rewrite experience bullet points to better match a job description.

JOB POSTING:
Title: {job_title}
Description: {job_description[:1500]}

CANDIDATE'S EXPERIENCE:
Company: {company}
Title: {title}
Original Bullet Points:
{highlights_text}

TASK:
Rewrite the bullet points to:
1. Highlight skills/achievements most relevant to THIS job
2. Use keywords and phrases from the job description
3. Maintain the same number of bullet points
4. Keep the same factual accuracy (don't add experience they don't have)
5. Make each bullet impactful and quantifiable where possible

Respond with ONLY valid JSON in this exact format:
{{
    "highlights": [
        "Rewritten bullet point 1",
        "Rewritten bullet point 2",
        "Rewritten bullet point 3"
    ]
}}"""
            
            result = self._call_llm(
                prompt=prompt,
                action=f"optimize_bullets_{i}",
                job_fingerprint=job_fingerprint,
                application_version=application_version
            )
            
            if result and "highlights" in result and isinstance(result["highlights"], list):
                # Update highlights while preserving static fields
                tailored_entry = exp_entry.copy()
                tailored_entry["highlights"] = result["highlights"]
                tailored_experience.append(tailored_entry)
            else:
                # Fallback to original highlights
                print(f"Warning: LLM failed to tailor bullets for {company}, using original")
                tailored_experience.append(exp_entry)
        
        return tailored_experience
    
    def _tailor_skills(self, job: Dict, base_content: TailoredContent,
                      job_fingerprint: str, application_version: int) -> List[str]:
        """
        Use LLM to reorder and filter skills based on job description.
        
        Args:
            job: Job document from MongoDB
            base_content: Base TailoredContent with original skills
            job_fingerprint: Job fingerprint for logging
            application_version: Application version for logging
            
        Returns:
            List[str]: Reordered skills list (most relevant first)
        """
        job_title = job.get("title", "")
        job_description = job.get("description", "")[:1500]
        original_skills = base_content.skills or []
        
        if not original_skills:
            return []
        
        skills_text = ", ".join(original_skills)
        
        prompt = f"""You are an expert resume writer. Reorder and filter skills to match a job description.

JOB POSTING:
Title: {job_title}
Description: {job_description[:1500]}

CANDIDATE'S SKILLS:
{skills_text}

TASK:
Reorder the skills list to:
1. Put the most relevant skills for THIS job first
2. Remove skills that are completely irrelevant to this job (if any)
3. Keep all skills that could be relevant (even if not explicitly mentioned)
4. Maintain the original skill names (don't rename them)

Respond with ONLY valid JSON in this exact format:
{{
    "skills": [
        "Most relevant skill 1",
        "Most relevant skill 2",
        "Less relevant skill 3",
        ...
    ]
}}"""
        
        result = self._call_llm(
            prompt=prompt,
            action="reorder_skills",
            job_fingerprint=job_fingerprint,
            application_version=application_version
        )
        
        if result and "skills" in result and isinstance(result["skills"], list):
            return result["skills"]
        else:
            # Fallback to original skills
            print(f"Warning: LLM failed to reorder skills, using original")
            return original_skills
    
    def _tailor_content(self, job: Dict, base_content: TailoredContent) -> TailoredContent:
        """
        Tailor resume content for a specific job using LLM.
        
        This method orchestrates the dynamic tailoring process:
        - Step A: Tailor summary
        - Step B: Tailor experience bullets
        - Step C: Reorder skills
        
        Args:
            job: Job document from MongoDB
            base_content: Base TailoredContent with static fields populated
            
        Returns:
            TailoredContent: Fully tailored content
        """
        job_fingerprint = job.get("fingerprint", "unknown")
        application_version = 1  # Default version
        
        print(f"  Tailoring content for: {job.get('title', 'Unknown')} at {job.get('company', 'Unknown')}")
        
        # Step A: Tailor Summary
        print(f"    → Tailoring summary...")
        tailored_summary = self._tailor_summary(job, base_content, job_fingerprint, application_version)
        
        # Step B: Tailor Experience Bullets
        print(f"    → Tailoring experience bullets...")
        tailored_experience = self._tailor_experience_bullets(job, base_content, job_fingerprint, application_version)
        
        # Step C: Reorder Skills
        print(f"    → Reordering skills...")
        tailored_skills = self._tailor_skills(job, base_content, job_fingerprint, application_version)
        
        # Create final tailored content
        tailored_content = TailoredContent(
            full_name=base_content.full_name,  # Static - never modified
            contact_info=base_content.contact_info,  # Static - never modified
            education=base_content.education,  # Static - never modified
            projects=base_content.projects,  # Static - never modified
            summary=tailored_summary,  # Dynamic - tailored
            skills=tailored_skills,  # Dynamic - reordered
            experience=tailored_experience  # Dynamic - highlights tailored
        )
        
        print(f"    ✓ Content tailored successfully")
        return tailored_content
    
    def run_tailoring_pipeline(self, batch_size: int = 10):
        """
        Run the complete tailoring pipeline.
        
        Pipeline logic:
        1. Query jobs where status="scored" AND match_score >= min_match_score
        2. For each job:
           - Check for existing TailoredApplication record
           - Skip if status is "ready_to_compile" or "completed"
           - Process if status is "pending_draft" or record doesn't exist
        3. Create base content (static guarantee)
        4. Tailor content using LLM
        5. Save to MongoDB with status "ready_to_compile"
        
        Args:
            batch_size: Maximum number of jobs to process in one run
        """
        print("\n" + "="*60)
        print("RESUME TAILOR V2 - TAILORING PIPELINE")
        print("="*60)
        
        # Query for high-scoring jobs
        query = {
            "status": "scored",
            "match_score": {"$gte": self.min_match_score}
        }
        
        jobs = list(self.db.jobs.find(query).sort("match_score", -1).limit(batch_size))
        
        if not jobs:
            print(f"✓ No jobs found with status='scored' and match_score >= {self.min_match_score}")
            return
        
        print(f"Found {len(jobs)} jobs to process (match_score >= {self.min_match_score})")
        
        # Create base content once (static guarantee)
        base_content = self._create_base_content()
        print(f"✓ Created base content (static fields preserved)")
        
        processed = 0
        skipped = 0
        failed = 0
        
        for i, job in enumerate(jobs, 1):
            job_fingerprint = job.get("fingerprint")
            if not job_fingerprint:
                print(f"  [{i}/{len(jobs)}] Skipping job without fingerprint")
                skipped += 1
                continue
            
            job_title = job.get("title", "Unknown")
            company = job.get("company", "Unknown")
            
            print(f"\n[{i}/{len(jobs)}] Processing: {job_title} at {company}")
            
            # Check for existing TailoredApplication
            existing_app = self.db.tailored_applications.find_one({
                "job_fingerprint": job_fingerprint,
                "resume_fingerprint": self.resume_fingerprint
            })
            
            if existing_app:
                status = existing_app.get("status", "pending_draft")
                if status in ["ready_to_compile", "completed"]:
                    print(f"  → Skipping (status: {status})")
                    skipped += 1
                    continue
                elif status == "pending_draft":
                    print(f"  → Resuming from pending_draft")
                    application_version = existing_app.get("version", 1)
                else:
                    print(f"  → Processing (status: {status})")
                    application_version = existing_app.get("version", 1)
            else:
                application_version = 1
                print(f"  → New application")
            
            try:
                # Update status to "drafting"
                self.db.tailored_applications.update_one(
                    {
                        "job_fingerprint": job_fingerprint,
                        "resume_fingerprint": self.resume_fingerprint
                    },
                    {
                        "$set": {
                            "status": "drafting",
                            "last_updated": datetime.utcnow()
                        },
                        "$setOnInsert": {
                            "job_fingerprint": job_fingerprint,
                            "resume_fingerprint": self.resume_fingerprint,
                            "version": application_version,
                            "created_at": datetime.utcnow()
                        }
                    },
                    upsert=True
                )
                
                # Tailor content
                tailored_content = self._tailor_content(job, base_content)
                
                # Save to MongoDB with status "ready_to_compile"
                tailored_app = TailoredApplication(
                    job_fingerprint=job_fingerprint,
                    resume_fingerprint=self.resume_fingerprint,
                    version=application_version,
                    status="ready_to_compile",
                    structured_content=tailored_content,
                    last_updated=datetime.utcnow()
                )
                
                self.db.tailored_applications.update_one(
                    {
                        "job_fingerprint": job_fingerprint,
                        "resume_fingerprint": self.resume_fingerprint
                    },
                    {
                        "$set": tailored_app.dict(by_alias=True)
                    },
                    upsert=True
                )
                
                print(f"  ✓ Saved to MongoDB (status: ready_to_compile)")
                processed += 1
                
            except Exception as e:
                print(f"  ✗ Failed: {str(e)}")
                
                # Update status to "failed"
                self.db.tailored_applications.update_one(
                    {
                        "job_fingerprint": job_fingerprint,
                        "resume_fingerprint": self.resume_fingerprint
                    },
                    {
                        "$set": {
                            "status": "failed",
                            "last_updated": datetime.utcnow()
                        }
                    },
                    upsert=True
                )
                
                failed += 1
        
        # Summary
        print("\n" + "="*60)
        print("PIPELINE SUMMARY")
        print("="*60)
        print(f"Processed: {processed}")
        print(f"Skipped: {skipped}")
        print(f"Failed: {failed}")
        print(f"Total: {len(jobs)}")
        print(f"\n✓ Pipeline complete! Check MongoDB 'tailored_applications' collection.")


def main():
    """Main execution function."""
    print("\n" + "="*60)
    print("RESUME TAILOR V2 - THE WRITER")
    print("="*60)
    
    try:
        # Initialize tailor
        tailor = ResumeTailorV2(
            llm_model="phi3.5",
            min_match_score=6.0
        )
        
        # Run tailoring pipeline
        tailor.run_tailoring_pipeline(batch_size=10)
        
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
