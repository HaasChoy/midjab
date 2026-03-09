"""
Resume Tailor V3 — PostgreSQL Edition (The Writer)
====================================================

Reads SCORED applications from Postgres, uses local LLM (Ollama) to tailor
resume content (summary, experience bullets, skills order), and stores the
structured result in Application.tailored_content (JSONB).

Static guarantee: personal info, education, projects are NEVER modified by LLM.
Dynamic tailoring: summary, experience highlights, skills order.

Status flow: SCORED → TAILORING → READY_TO_COMPILE (or FAILED)
"""

import json
import hashlib
import re
import time
import uuid
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select, update

from config.database import SessionLocal
from config.llm import call_llm, parse_llm_json
from core.orm_models import Application, Job, PipelineLog

logger = logging.getLogger("midjab.resume_tailor")


# ─────────────────────────────────────────────
# TailoredContent — data contract (kept as plain dict for JSONB)
# ─────────────────────────────────────────────

def _make_base_content(profile: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build the base tailored content from user_profile.json.

    STATIC fields (never touched by LLM):
      full_name, contact_info, education, projects

    DYNAMIC fields (LLM will overwrite):
      summary, skills, experience[].highlights
    """
    contact_info = {}
    for key in ("email", "phone", "github_url", "portfolio_url", "linkedin"):
        val = profile.get(key, "")
        if val:
            contact_info[key] = val

    experience = []
    for exp in profile.get("experience", []):
        experience.append({
            "company": exp.get("company", ""),
            "title": exp.get("title", ""),
            "location": exp.get("location", ""),
            "dates": exp.get("duration", ""),
            "highlights": exp.get("description_points", []),
        })

    return {
        "full_name": profile.get("full_name", ""),
        "contact_info": contact_info,
        "education": profile.get("education", []),
        "projects": profile.get("projects", []),
        "summary": profile.get("summary", ""),
        "skills": profile.get("skills", []),
        "experience": experience,
    }


class ResumeTailorV3:
    """PostgreSQL-driven Resume Tailor (The Writer)."""

    def __init__(
        self,
        llm_model: str = "gemini-1.5-flash",
        profile_path: str = "outputs/user_profile.json",
        min_match_score: float = 6.0,
    ):
        self.llm_model = llm_model
        self.profile_path = profile_path
        self.min_match_score = min_match_score

        # Load profile
        self.user_profile = self._load_profile()
        self.resume_fingerprint = self._profile_fingerprint()

        print(f"✓ ResumeTailorV3 initialized")
        print(f"  - Model: {llm_model}")
        print(f"  - Min Score: {min_match_score}")

    def _load_profile(self) -> Dict[str, Any]:
        with open(self.profile_path, "r", encoding="utf-8") as f:
            profile = json.load(f)
        print(f"✓ Loaded profile: {profile.get('full_name', 'Unknown')}")
        return profile

    def _profile_fingerprint(self) -> str:
        raw = json.dumps(self.user_profile, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()

    # ─────────────── LLM helpers ───────────────

    def _call_llm(self, prompt: str, action: str, app_id: uuid.UUID) -> Optional[Dict]:
        start = time.time()
        raw_resp = None
        try:
            response = call_llm(
                prompt=prompt,
                model=self.llm_model,
                temperature=0.3,
                format_json=True,
                num_predict=1000,
            )
            if response and "message" in response:
                raw_resp = response["message"]["content"]
                parsed = parse_llm_json(raw_resp)
                latency = int((time.time() - start) * 1000)

                self._log_pipeline(
                    app_id, "tailor", action,
                    f"LLM call {'ok' if parsed else 'parse_fail'} ({latency}ms)",
                    {"latency_ms": latency, "success": parsed is not None},
                )
                return parsed
            return None
        except Exception as e:
            latency = int((time.time() - start) * 1000)
            self._log_pipeline(app_id, "tailor", action, f"LLM error: {e}", {"latency_ms": latency})
            return None

    def _log_pipeline(self, app_id: Optional[uuid.UUID], agent: str, action: str, msg: str, meta: Optional[dict] = None):
        with SessionLocal() as session:
            session.add(PipelineLog(
                application_id=app_id,
                agent_name=agent[:50],
                action=action[:50],
                message=msg[:5000] if msg else None,
                log_metadata=meta,
            ))
            session.commit()

    # ─────────────── tailoring sub-steps ───────────────

    def _tailor_summary(self, job_title: str, job_desc: str, original: str, app_id: uuid.UUID) -> str:
        prompt = f"""You are an expert resume writer. Write a professional summary for this job.

JOB: {job_title}
Description: {job_desc[:1500]}

CANDIDATE SUMMARY: {original}

Write a NEW 3-sentence summary that:
1. Highlights relevant skills for THIS job
2. Uses keywords from the job description
3. Is authentic (only mention real experience)

Respond ONLY with JSON: {{"summary": "..."}}"""
        result = self._call_llm(prompt, "draft_summary", app_id)
        return result["summary"] if result and "summary" in result else original

    def _tailor_bullets(self, job_title: str, job_desc: str, experience: List[Dict], app_id: uuid.UUID) -> List[Dict]:
        tailored = []
        for i, exp in enumerate(experience):
            highlights = exp.get("highlights", [])
            if not highlights:
                tailored.append(exp)
                continue

            bullets_text = "\n".join(f"- {h}" for h in highlights)
            prompt = f"""You are an expert resume writer. Rewrite bullet points for this job.

JOB: {job_title}
Description: {job_desc[:1500]}

EXPERIENCE at {exp.get('company', '?')} as {exp.get('title', '?')}:
{bullets_text}

Rewrite to:
1. Match keywords from job description
2. Keep same number of bullets
3. Keep factual accuracy
4. Make impactful and quantifiable

Respond ONLY with JSON: {{"highlights": ["bullet1", "bullet2", ...]}}"""
            result = self._call_llm(prompt, f"optimize_bullets_{i}", app_id)
            entry = exp.copy()
            if result and "highlights" in result and isinstance(result["highlights"], list):
                entry["highlights"] = result["highlights"]
            tailored.append(entry)
        return tailored

    def _tailor_skills(self, job_title: str, job_desc: str, skills: list, app_id: uuid.UUID) -> list:
        if not skills:
            return []

        # Flatten if skills is dict-of-lists
        flat = []
        if isinstance(skills, dict):
            for _cat, items in skills.items():
                if isinstance(items, list):
                    flat.extend(items)
        elif isinstance(skills, list):
            flat = skills
        else:
            return skills

        prompt = f"""You are an expert resume writer. Reorder skills for this job.

JOB: {job_title}
Description: {job_desc[:1500]}

SKILLS: {', '.join(str(s) for s in flat)}

Reorder to put most relevant first. Remove completely irrelevant ones. Keep original names.

Respond ONLY with JSON: {{"skills": ["skill1", "skill2", ...]}}"""
        result = self._call_llm(prompt, "reorder_skills", app_id)
        return result["skills"] if result and "skills" in result and isinstance(result["skills"], list) else flat

    # ─────────────── main pipeline ───────────────

    def run_tailoring_pipeline(self, batch_size: int = 10):
        """
        Tailor all SCORED applications with match_score >= min_match_score.

        For each application:
          1. Load the joined Job data
          2. Build base content from user profile
          3. LLM-tailor summary, bullets, skills
          4. Store in Application.tailored_content
          5. Set status → READY_TO_COMPILE
        """
        print("\n" + "=" * 60)
        print("RESUME TAILOR V3 — TAILORING PIPELINE")
        print("=" * 60)

        with SessionLocal() as session:
            rows = session.execute(
                select(Application, Job)
                .join(Job, Application.job_id == Job.id)
                .where(Application.status == "SCORED")
                .where(Application.match_score >= self.min_match_score)
                .order_by(Application.match_score.desc())
                .limit(batch_size)
            ).all()
            app_jobs = [
                {
                    "app_id": a.id,
                    "job_title": j.title,
                    "job_desc": j.description or "",
                    "job_company": j.company,
                    "job_fingerprint": j.fingerprint,
                }
                for a, j in rows
            ]

        if not app_jobs:
            print(f"✓ No SCORED applications with score >= {self.min_match_score}")
            return

        print(f"Found {len(app_jobs)} applications to tailor")

        base_content = _make_base_content(self.user_profile)
        print("✓ Base content prepared (static fields preserved)")

        processed, failed = 0, 0

        for i, aj in enumerate(app_jobs, 1):
            app_id = aj["app_id"]
            print(f"\n[{i}/{len(app_jobs)}] {aj['job_title']} @ {aj['job_company']}")

            try:
                # Mark as TAILORING
                with SessionLocal() as session:
                    session.execute(
                        update(Application).where(Application.id == app_id).values(
                            status="TAILORING",
                            updated_at=datetime.now(timezone.utc),
                        )
                    )
                    session.commit()

                # Tailor content
                print("  → Summary...")
                summary = self._tailor_summary(aj["job_title"], aj["job_desc"], base_content.get("summary", ""), app_id)
                print("  → Bullets...")
                experience = self._tailor_bullets(aj["job_title"], aj["job_desc"], base_content.get("experience", []), app_id)
                print("  → Skills...")
                skills = self._tailor_skills(aj["job_title"], aj["job_desc"], base_content.get("skills", []), app_id)

                tailored = {
                    **base_content,
                    "summary": summary,
                    "experience": experience,
                    "skills": skills,
                }

                # Save
                with SessionLocal() as session:
                    session.execute(
                        update(Application).where(Application.id == app_id).values(
                            tailored_content=tailored,
                            status="READY_TO_COMPILE",
                            updated_at=datetime.now(timezone.utc),
                        )
                    )
                    session.commit()

                self._log_pipeline(app_id, "tailor", "complete", f"Tailored for {aj['job_fingerprint']}")
                print("  ✓ Saved (READY_TO_COMPILE)")
                processed += 1

            except Exception as e:
                logger.error("Tailoring failed for app %s: %s", app_id, e)
                with SessionLocal() as session:
                    session.execute(
                        update(Application).where(Application.id == app_id).values(
                            status="FAILED",
                            updated_at=datetime.now(timezone.utc),
                        )
                    )
                    session.commit()
                self._log_pipeline(app_id, "tailor", "error", str(e))
                print(f"  ✗ Failed: {e}")
                failed += 1

        print("\n" + "=" * 60)
        print(f"DONE — Processed: {processed}  Failed: {failed}  Total: {len(app_jobs)}")
        print("=" * 60)


def main():
    print("\n" + "=" * 60)
    print("RESUME TAILOR V3 — THE WRITER")
    print("=" * 60)
    try:
        tailor = ResumeTailorV3(llm_model="phi3.5", min_match_score=6.0)
        tailor.run_tailoring_pipeline(batch_size=10)
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
