"""
OpportunityScorer V3 — PostgreSQL Edition
==========================================

Hybrid scoring engine that reads NEW jobs from Postgres, scores them
using keyword matching + local LLM (Ollama), and creates Application
rows with match_score and score_reasoning.

Flow per job:
  1. keyword_score  — deterministic skill-frequency match
  2. llm_score      — contextual semantic analysis via Ollama
  3. requirement_score — hard-requirement validation
  4. Weighted combination → final_score (1-10)
  5. INSERT Application row (job_id, match_score, score_reasoning)
  6. UPDATE Job.status → 'SCORED'
  7. INSERT PipelineLog for audit trail
"""

import json
import re
import ast
import uuid
import hashlib
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy import select, update

from config.database import SessionLocal
from config.llm import call_llm, parse_llm_json
from core.orm_models import Application, Job, PipelineLog

logger = logging.getLogger("midjab.opportunity_scorer")


class OpportunityScorerV3:
    """
    PostgreSQL-driven OpportunityScorer with local Ollama integration.

    Scoring strategy:
      final = (keyword * 0.3) + (llm * 0.5) + (requirement * 0.2)
      then normalized to 1-10 scale.
    """

    def __init__(
        self,
        llm_model: str = "gemini-1.5-flash",
        resume_id: Optional[uuid.UUID] = None,
    ):
        self.llm_model = llm_model
        self.resume_id = resume_id  # optional FK to resumes table

        self.user_profile: Optional[dict] = None
        self.user_skills: List[str] = []

        # Hard-requirement phrases
        self.requirement_phrases = [
            "must have", "required", "minimum qualifications",
            "key qualifications", "essential", "mandatory",
            "prerequisite", "minimum requirements", "necessary",
            "required experience", "required skills",
        ]

        self._log("INFO", f"OpportunityScorerV3 initialized — model={llm_model}")
        print(f"✓ OpportunityScorerV3 initialized")
        print(f"  - Model: {llm_model}")

    # ─────────────── logging helpers ───────────────

    def _log(self, level: str, message: str, application_id: Optional[uuid.UUID] = None, metadata: Optional[dict] = None):
        """Write a pipeline_log row."""
        with SessionLocal() as session:
            entry = PipelineLog(
                application_id=application_id,
                agent_name="scorer",
                action=level,
                message=message[:5000] if message else None,
                log_metadata=metadata,
            )
            session.add(entry)
            session.commit()

    # ─────────────── profile loading ───────────────

    def load_user_profile(self, profile_path: str = "outputs/user_profile.json"):
        """Load user profile JSON and extract flat skills list."""
        with open(profile_path, "r") as f:
            self.user_profile = json.load(f)

        self.user_skills = []
        for section in ("technical_skills", "soft_skills", "languages"):
            data = self.user_profile.get(section)
            if isinstance(data, list):
                self.user_skills.extend(s.lower() for s in data)
            elif isinstance(data, dict):
                for _cat, items in data.items():
                    if isinstance(items, list):
                        self.user_skills.extend(s.lower() for s in items)

        # Also try the generic "skills" key (flat list or dict of lists)
        skills_raw = self.user_profile.get("skills")
        if isinstance(skills_raw, list):
            self.user_skills.extend(s.lower() for s in skills_raw if isinstance(s, str))
        elif isinstance(skills_raw, dict):
            for _cat, items in skills_raw.items():
                if isinstance(items, list):
                    self.user_skills.extend(s.lower() for s in items if isinstance(s, str))

        # dedupe preserving order
        seen = set()
        deduped = []
        for s in self.user_skills:
            if s not in seen:
                seen.add(s)
                deduped.append(s)
        self.user_skills = deduped

        self._log("INFO", f"User profile loaded: {len(self.user_skills)} skills")
        print(f"✓ User profile loaded: {len(self.user_skills)} skills")

    # ─────────────── scoring sub-engines ───────────────

    def _keyword_score(self, title: str, description: str) -> float:
        title_l = title.lower()
        desc_l = description.lower()
        score = 0.0
        for skill in self.user_skills:
            if skill in desc_l:
                score += 1.0 + 0.3 * desc_l.count(skill)
                if skill in title_l:
                    score += 1.5
        return score

    def _requirement_score(self, description: str) -> float:
        desc_l = description.lower()
        sentences = re.split(r"[.\n]", desc_l)
        score = 0.0
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            if any(phrase in sentence for phrase in self.requirement_phrases):
                if any(skill in sentence for skill in self.user_skills):
                    score += 1.0
        return score

    def _call_llm(self, prompt: str) -> Optional[Dict]:
        try:
            response = call_llm(
                prompt=prompt,
                model=self.llm_model,
                temperature=0.3,
                format_json=True,
                num_predict=500,
            )
            if response and "message" in response:
                return parse_llm_json(response["message"]["content"])
            return None
        except Exception as e:
            self._log("ERROR", f"LLM call failed: {e}")
            return None

    def _llm_score(self, title: str, description: str) -> Tuple[float, str]:
        user_ctx = {
            "skills": self.user_skills[:20],
            "summary": (self.user_profile or {}).get("summary", "")[:300],
        }
        prompt = f"""You are an expert career advisor. Analyze how well this job matches the candidate.

CANDIDATE PROFILE:
- Skills: {', '.join(user_ctx['skills'])}
- Summary: {user_ctx['summary']}

JOB POSTING:
Title: {title}
Description: {description[:1000]}

Evaluate match on a scale of 0-10 considering:
1. Skill alignment
2. Career trajectory fit
3. Role responsibility match

Respond with ONLY valid JSON:
{{
    "score": 7.5,
    "reasoning": "Brief explanation"
}}"""
        result = self._call_llm(prompt)
        if result and "score" in result:
            return float(result["score"]), result.get("reasoning", "")
        return 5.0, "LLM evaluation failed — default score"

    # ─────────────── score a single job ───────────────

    def score_job(self, job_id: uuid.UUID, title: str, description: str, fingerprint: str) -> Optional[Dict]:
        """
        Score one job. Creates an Application row and updates Job.status.
        """
        try:
            kw = self._keyword_score(title, description or "")
            req = self._requirement_score(description or "")
            llm, reasoning = self._llm_score(title, description or "")

            weighted = (kw * 0.3) + (llm * 0.5) + (req * 0.2)
            final = min(10, max(1, round(1 + (weighted / 15) * 9)))

            score_reasoning = {
                "keyword_score": round(kw, 2),
                "llm_score": round(llm, 2),
                "requirement_score": round(req, 2),
                "llm_reasoning": reasoning,
                "model": self.llm_model,
            }

            with SessionLocal() as session:
                # Create Application row
                app = Application(
                    id=str(uuid.uuid4()),
                    job_id=job_id,
                    resume_id=self.resume_id,
                    status="SCORED",
                    match_score=final,
                    score_reasoning=score_reasoning,
                )
                session.add(app)

                # Update job status
                session.execute(
                    update(Job).where(Job.id == job_id).values(status="SCORED")
                )

                session.commit()
                app_id = app.id

            # Audit log
            self._log(
                "INFO",
                f"Scored {fingerprint}: {final}/10 (kw={kw:.1f} llm={llm:.1f} req={req:.1f})",
                application_id=app_id,
                metadata=score_reasoning,
            )

            return {
                "job_id": str(job_id),
                "fingerprint": fingerprint,
                "title": title,
                "final_score": final,
                "keyword_score": kw,
                "llm_score": llm,
                "requirement_score": req,
                "reasoning": reasoning,
                "application_id": str(app_id),
            }

        except Exception as e:
            logger.error("Failed to score job %s: %s", fingerprint, e)
            self._log("ERROR", f"Scoring failed for {fingerprint}: {e}")
            return None

    # ─────────────── batch run ───────────────

    def run_full_scoring(self, batch_size: int = 50):
        """Score all jobs where status='NEW'."""
        with SessionLocal() as session:
            jobs = session.execute(
                select(Job).where(Job.status == "NEW").limit(batch_size)
            ).scalars().all()
            # Detach from session so we can use them after session closes
            job_data = [
                {"id": j.id, "title": j.title, "description": j.description or "", "fingerprint": j.fingerprint}
                for j in jobs
            ]

        if not job_data:
            print("✓ No NEW jobs to score")
            return

        print(f"\nScoring {len(job_data)} jobs...")
        self._log("INFO", f"Batch scoring started: {len(job_data)} jobs")

        scored, failed = 0, 0
        for i, jd in enumerate(job_data, 1):
            print(f"  [{i}/{len(job_data)}] {jd['title'][:50]}...", end="")
            result = self.score_job(jd["id"], jd["title"], jd["description"], jd["fingerprint"])
            if result:
                print(f" ✓ {result['final_score']}/10")
                scored += 1
            else:
                print(" ✗")
                failed += 1

        self._log("INFO", f"Batch complete: {scored} scored, {failed} failed")
        print(f"\n✓ Scoring done: {scored}/{len(job_data)} succeeded")

    # ─────────────── shortlisted query ───────────────

    def get_shortlisted_jobs(self, threshold: int = 4, limit: int = 50) -> List[Dict]:
        """Return applications with match_score >= threshold, joined with job details."""
        with SessionLocal() as session:
            rows = session.execute(
                select(Application, Job)
                .join(Job, Application.job_id == Job.id)
                .where(Application.match_score >= threshold)
                .order_by(Application.match_score.desc())
                .limit(limit)
            ).all()

            results = []
            for app, job in rows:
                results.append({
                    "application_id": str(app.id),
                    "job_id": str(job.id),
                    "fingerprint": job.fingerprint,
                    "title": job.title,
                    "company": job.company,
                    "location": job.location,
                    "match_score": float(app.match_score) if app.match_score else None,
                    "score_reasoning": app.score_reasoning,
                    "source_url": job.source_url,
                })

        print(f"✓ {len(results)} shortlisted jobs (threshold={threshold})")
        return results


# ─────────────── standalone helpers ───────────────

def test_llm_connection(model: str = "gemini-1.5-flash") -> bool:
    print("\n" + "=" * 60)
    print("TESTING LLM CONNECTION")
    print("=" * 60)
    try:
        response = call_llm(
            prompt=f'Respond with only: {{"status":"ok","model":"{model}"}}',
            model=model,
            format_json=True,
        )
        if response and "message" in response:
            result = json.loads(response["message"]["content"])
            print(f"✓ Connected — model: {result.get('model', model)}")
            return True
        return False
    except Exception as e:
        print(f"✗ Failed: {e}")
        print("  1. Ensure GOOGLE_API_KEY is set in .env")
        print("  2. Or ensure Ollama is running: ollama serve")
        return False


def main():
    print("\n" + "=" * 60)
    print("OPPORTUNITYSCORER V3 — POSTGRESQL EDITION")
    print("=" * 60)

    if not test_llm_connection():
        return

    scorer = OpportunityScorerV3()
    scorer.load_user_profile()
    scorer.run_full_scoring(batch_size=50)

    results = scorer.get_shortlisted_jobs(threshold=4)
    if not results:
        print("\n⚠ No jobs met the threshold.")
        return

    print("\n" + "=" * 60)
    print("TOP 5 RECOMMENDED OPPORTUNITIES")
    print("=" * 60)
    for job in results[:5]:
        reasoning = job.get("score_reasoning") or {}
        print(f"\n  Score: {job['match_score']}/10")
        print(f"  Title: {job['title']}")
        print(f"  Company: {job['company']}")
        print(f"  Location: {job.get('location', 'N/A')}")
        print(f"  Breakdown: kw={reasoning.get('keyword_score', '?')} llm={reasoning.get('llm_score', '?')} req={reasoning.get('requirement_score', '?')}")
        print(f"  Reasoning: {reasoning.get('llm_reasoning', 'N/A')[:100]}")
        print("-" * 60)


if __name__ == "__main__":
    main()
