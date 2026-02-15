"""
LatexArchitect V3 — PostgreSQL Edition (The Typesetter)
========================================================

Reads READY_TO_COMPILE applications from Postgres, injects tailored_content
into a Jinja2 LaTeX template, compiles PDF, and updates the application row.

Features:
- LaTeX escaping for all user-generated strings
- Skill clustering (flat list → categorized dict)
- Sandboxed compilation in temp directories
- Status tracking + pipeline_log audit

Status flow: READY_TO_COMPILE → COMPILING → COMPLETED (or FAILED)
"""

import os
import re
import shutil
import subprocess
import tempfile
import uuid
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import jinja2
from jinja2 import Environment, FileSystemLoader
from sqlalchemy import select, update

from config.database import SessionLocal
from core.orm_models import Application, Job, PipelineLog

logger = logging.getLogger("midjab.latex_architect")


class LatexArchitect:
    """PostgreSQL-driven LaTeX compiler (The Typesetter)."""

    def __init__(
        self,
        template_dir: str = "templates",
        output_dir: str = "final_applications",
    ):
        self.template_dir = Path(template_dir)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.jinja_env = self._setup_jinja()

        print(f"✓ LatexArchitect initialized")
        print(f"  - Templates: {self.template_dir}")
        print(f"  - Output: {self.output_dir}")

    # ─────────────── Jinja setup ───────────────

    def _setup_jinja(self) -> Environment:
        loader = FileSystemLoader(str(self.template_dir))
        env = Environment(loader=loader, autoescape=False)
        env.filters["latex_escape"] = self._escape_latex
        return env

    @staticmethod
    def _escape_latex(text: str) -> str:
        """Escape LaTeX special chars to prevent compilation crashes."""
        if text is None:
            return ""
        text = str(text)
        text = text.replace("\\", r"\textbackslash{}")
        text = text.replace("&", r"\&")
        text = text.replace("%", r"\%")
        text = text.replace("$", r"\$")
        text = text.replace("#", r"\#")
        text = text.replace("_", r"\_")
        text = text.replace("{", r"\{")
        text = text.replace("}", r"\}")
        text = text.replace("~", r"\textasciitilde{}")
        text = text.replace("^", r"\textasciicircum{}")
        return text

    # ─────────────── skill clustering ───────────────

    def _cluster_skills(self, flat_skills: list) -> Dict[str, List[str]]:
        """Cluster flat skills list into categories for the template."""
        if not flat_skills:
            return {"General": []}

        # Flatten if it's already a dict
        if isinstance(flat_skills, dict):
            return flat_skills

        category_keywords = {
            "Core Tech": [
                "python", "java", "javascript", "typescript", "c++", "c#", "go", "rust",
                "ruby", "php", "swift", "kotlin", "scala", "r", "matlab",
            ],
            "AI/ML": [
                "machine learning", "deep learning", "ai", "tensorflow", "pytorch",
                "keras", "scikit-learn", "sklearn", "nlp", "computer vision", "opencv",
            ],
            "Data Engineering": [
                "sql", "postgresql", "mysql", "mongodb", "redis", "elasticsearch",
                "spark", "pyspark", "hadoop", "kafka", "airflow", "etl", "bigquery", "snowflake",
            ],
            "Cloud & DevOps": [
                "aws", "azure", "gcp", "docker", "kubernetes", "k8s",
                "terraform", "ansible", "ci/cd", "jenkins", "github actions",
            ],
            "Backend": [
                "django", "flask", "fastapi", "express", "spring", "rest api", "graphql",
            ],
            "Frontend": [
                "react", "vue", "angular", "html", "css", "next.js", "tailwind",
            ],
        }

        clustered: Dict[str, List[str]] = {cat: [] for cat in category_keywords}
        clustered["General"] = []

        for skill in flat_skills:
            skill_lower = str(skill).lower()
            placed = False
            for cat, keywords in category_keywords.items():
                if any(kw in skill_lower for kw in keywords):
                    clustered[cat].append(str(skill))
                    placed = True
                    break
            if not placed:
                clustered["General"].append(str(skill))

        return {k: v for k, v in clustered.items() if v}

    # ─────────────── template data prep ───────────────

    def _prepare_template_data(self, content: Dict[str, Any]) -> Dict[str, Any]:
        """Transform tailored_content JSONB into template-ready dict."""
        esc = self._escape_latex

        data = {
            "full_name": esc(content.get("full_name", "")),
            "contact_info": {
                k: esc(v) if isinstance(v, str) else v
                for k, v in content.get("contact_info", {}).items()
            },
            "summary": esc(content.get("summary")) if content.get("summary") else None,
            "skills": self._cluster_skills(content.get("skills", [])),
            "experience": [],
            "projects": [],
            "education": [],
        }

        for exp in content.get("experience", []):
            data["experience"].append({
                "title": esc(exp.get("title", "")),
                "company": esc(exp.get("company", "")),
                "location": esc(exp.get("location", "")),
                "dates": esc(exp.get("dates", "")),
                "highlights": [esc(b) for b in exp.get("highlights", [])],
            })

        for proj in content.get("projects", []):
            data["projects"].append({
                "name": esc(proj.get("name") or proj.get("title", "")),
                "dates": esc(proj.get("dates") or proj.get("date", "")),
                "highlights": [esc(b) for b in (proj.get("highlights") or proj.get("description_points", []))],
            })

        for edu in content.get("education", []):
            data["education"].append({
                "institution": esc(edu.get("institution") or edu.get("university", "")),
                "degree": esc(edu.get("degree", "")),
                "dates": esc(edu.get("dates", "")),
                "gpa": esc(edu.get("gpa", "")) if edu.get("gpa") else None,
                "details": esc(edu.get("details", "")) if edu.get("details") else None,
            })

        return data

    # ─────────────── PDF compilation ───────────────

    def _compile_pdf(self, tex_content: str, temp_dir: Path, app_id: str) -> Tuple[bool, Optional[str], Optional[str]]:
        tex_file = temp_dir / "resume.tex"
        tex_file.write_text(tex_content, encoding="utf-8")

        try:
            result = subprocess.run(
                ["pdflatex", "-interaction=nonstopmode", f"-output-directory={temp_dir}", str(tex_file)],
                capture_output=True, text=True, timeout=30, cwd=str(temp_dir),
            )
            pdf_file = temp_dir / "resume.pdf"
            if result.returncode == 0 and pdf_file.exists():
                output_name = f"{app_id}.pdf"
                output_path = self.output_dir / output_name
                shutil.copy2(pdf_file, output_path)
                return True, str(output_path), None
            else:
                error_log = f"rc={result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
                return False, None, error_log
        except subprocess.TimeoutExpired:
            return False, None, "pdflatex timed out (30s)"
        except FileNotFoundError:
            return False, None, "pdflatex not found — install TeX Live or MiKTeX"
        except Exception as e:
            return False, None, str(e)

    def _render_template(self, data: Dict[str, Any]) -> str:
        template = self.jinja_env.get_template("master_resume.tex.j2")
        return template.render(**data)

    # ─────────────── pipeline logging ───────────────

    def _log_pipeline(self, app_id: Optional[uuid.UUID], action: str, msg: str, meta: Optional[dict] = None):
        with SessionLocal() as session:
            session.add(PipelineLog(
                application_id=app_id,
                agent_name="compiler",
                action=action[:50],
                message=msg[:5000] if msg else None,
                log_metadata=meta,
            ))
            session.commit()

    # ─────────────── compile one application ───────────────

    def compile_application(self, app_id: uuid.UUID, tailored_content: Dict[str, Any]) -> bool:
        """Compile a single application to PDF."""
        print(f"  Compiling {app_id}...")

        if not tailored_content:
            print("    ✗ No tailored_content")
            return False

        template_data = self._prepare_template_data(tailored_content)

        try:
            tex_content = self._render_template(template_data)
        except Exception as e:
            print(f"    ✗ Template render failed: {e}")
            self._log_pipeline(app_id, "render_error", str(e))
            return False

        with tempfile.TemporaryDirectory() as tmp:
            success, pdf_path, error_log = self._compile_pdf(tex_content, Path(tmp), str(app_id))

            with SessionLocal() as session:
                if success:
                    session.execute(
                        update(Application).where(Application.id == app_id).values(
                            status="COMPLETED",
                            generated_pdf_path=pdf_path,
                            updated_at=datetime.now(timezone.utc),
                        )
                    )
                    session.commit()
                    self._log_pipeline(app_id, "compiled", f"PDF: {pdf_path}")
                    print(f"    ✓ PDF: {pdf_path}")
                    return True
                else:
                    session.execute(
                        update(Application).where(Application.id == app_id).values(
                            status="FAILED",
                            updated_at=datetime.now(timezone.utc),
                        )
                    )
                    session.commit()
                    self._log_pipeline(app_id, "compile_error", error_log or "unknown")
                    print(f"    ✗ Compilation failed")
                    if error_log:
                        print(f"      {error_log[:200]}")
                    return False

    # ─────────────── batch pipeline ───────────────

    def run_compiler_pipeline(self, batch_size: int = 10):
        """
        Compile all READY_TO_COMPILE applications.

        For each:
          1. Mark status → COMPILING
          2. Render template
          3. Compile PDF
          4. Update status → COMPLETED or FAILED
        """
        print("\n" + "=" * 60)
        print("LATEX ARCHITECT V3 — COMPILATION PIPELINE")
        print("=" * 60)

        with SessionLocal() as session:
            apps = session.execute(
                select(Application)
                .where(Application.status == "READY_TO_COMPILE")
                .limit(batch_size)
            ).scalars().all()
            app_data = [
                {"id": a.id, "tailored_content": a.tailored_content}
                for a in apps
            ]

        if not app_data:
            print("✓ No applications ready to compile")
            return

        print(f"Found {len(app_data)} applications to compile")

        successful, failed = 0, 0
        for i, ad in enumerate(app_data, 1):
            app_id = ad["id"]
            print(f"\n[{i}/{len(app_data)}] Application {app_id}")

            # Mark COMPILING
            with SessionLocal() as session:
                session.execute(
                    update(Application).where(Application.id == app_id).values(
                        status="COMPILING",
                        updated_at=datetime.now(timezone.utc),
                    )
                )
                session.commit()

            if self.compile_application(app_id, ad["tailored_content"]):
                successful += 1
            else:
                failed += 1

        print("\n" + "=" * 60)
        print(f"DONE — Successful: {successful}  Failed: {failed}  Total: {len(app_data)}")
        print("=" * 60)


def main():
    print("\n" + "=" * 60)
    print("LATEX ARCHITECT V3 — THE TYPESETTER")
    print("=" * 60)
    try:
        architect = LatexArchitect()
        architect.run_compiler_pipeline(batch_size=10)
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
