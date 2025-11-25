"""
LatexArchitect (The Typesetter)
================================

Agent 5 of the midjab V2 pipeline. Takes structured JSON content from MongoDB,
injects it safely into a Jinja2 LaTeX template, and compiles a PDF without errors.

Key Features:
- LaTeX escaping for all user input (critical safety)
- Skill clustering (flat list → categorized dictionary)
- Sandboxed compilation in temporary directories
- Status tracking and error logging
"""

import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

import jinja2
from jinja2 import Environment, FileSystemLoader

from core.db import get_db
from core.models import TailoredApplication, TailoredContent


class LatexArchitect:
    """
    MongoDB-Driven LaTeX Compiler (The Typesetter).
    
    Reads TailoredApplication documents from MongoDB, renders them into LaTeX,
    and compiles PDFs with proper error handling and status tracking.
    """
    
    def __init__(self, 
                 template_dir: str = "templates",
                 output_dir: str = "final_applications"):
        """
        Initialize LatexArchitect.
        
        Args:
            template_dir: Directory containing LaTeX templates
            output_dir: Directory for compiled PDFs
        """
        self.db = get_db()
        self.template_dir = Path(template_dir)
        self.output_dir = Path(output_dir)
        
        # Ensure output directory exists
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize Jinja2 environment with LaTeX escaping
        self.jinja_env = self._setup_jinja_environment()
        
        print(f"✓ LatexArchitect initialized")
        print(f"  - Template dir: {self.template_dir}")
        print(f"  - Output dir: {self.output_dir}")
    
    def _setup_jinja_environment(self) -> Environment:
        """
        Set up Jinja2 environment with LaTeX escaping filter.
        
        Returns:
            Configured Jinja2 Environment
        """
        loader = FileSystemLoader(str(self.template_dir))
        env = Environment(loader=loader, autoescape=False)
        
        # Register the critical LaTeX escaping filter
        env.filters['latex_escape'] = self._escape_latex
        
        return env
    
    @staticmethod
    def _escape_latex(text: str) -> str:
        """
        Escape LaTeX special characters to prevent compilation crashes.
        
        CRITICAL: This must be applied to EVERY string injected into the template.
        
        Escapes the following characters:
        - & → \&
        - % → \%
        - $ → \$
        - # → \#
        - _ → \_
        - { → \{
        - } → \}
        - ~ → \textasciitilde
        - ^ → \textasciicircum
        - \ → \textbackslash
        
        Args:
            text: Input string (may be None)
            
        Returns:
            Escaped string safe for LaTeX
        """
        if text is None:
            return ""
        
        # Convert to string if not already
        text = str(text)
        
        # Escape in order (backslash must be first!)
        text = text.replace('\\', r'\textbackslash{}')
        text = text.replace('&', r'\&')
        text = text.replace('%', r'\%')
        text = text.replace('$', r'\$')
        text = text.replace('#', r'\#')
        text = text.replace('_', r'\_')
        text = text.replace('{', r'\{')
        text = text.replace('}', r'\}')
        text = text.replace('~', r'\textasciitilde{}')
        text = text.replace('^', r'\textasciicircum{}')
        
        return text
    
    def _cluster_skills(self, flat_skills: List[str]) -> Dict[str, List[str]]:
        """
        Cluster flat skills list into categorized dictionary for template.
        
        The Writer agent outputs a flat list (e.g., ["Python", "AWS", "Docker"]).
        The LaTeX template requires a Dictionary: {'Core Tech': [...], 'AI/ML': [...], 'Cloud': [...]}.
        
        Logic:
        - Use predefined keyword map to categorize skills
        - Any skill that doesn't match goes into "General" category
        
        Args:
            flat_skills: List of skill strings
            
        Returns:
            Dictionary mapping category names to skill lists
        """
        if not flat_skills:
            return {"General": []}
        
        # Predefined keyword mapping
        # Format: {category: [keywords that indicate this category]}
        category_keywords = {
            "Core Tech": [
                "python", "java", "javascript", "typescript", "c++", "c#", "go", "rust",
                "ruby", "php", "swift", "kotlin", "scala", "r", "matlab"
            ],
            "AI/ML": [
                "machine learning", "deep learning", "ai", "artificial intelligence",
                "tensorflow", "pytorch", "keras", "scikit-learn", "sklearn",
                "neural networks", "nlp", "natural language processing",
                "computer vision", "reinforcement learning", "opencv", "nltk", "spacy"
            ],
            "Data Engineering": [
                "sql", "postgresql", "mysql", "mongodb", "redis", "elasticsearch",
                "spark", "apache spark", "pyspark", "hadoop", "kafka", "airflow",
                "etl", "data pipeline", "data warehouse", "bigquery", "snowflake"
            ],
            "Cloud": [
                "aws", "amazon web services", "azure", "gcp", "google cloud",
                "ec2", "s3", "lambda", "docker", "kubernetes", "k8s",
                "terraform", "ansible", "ci/cd", "jenkins", "github actions",
                "cloudformation", "serverless", "microservices"
            ],
            "Frontend": [
                "react", "vue", "angular", "html", "css", "javascript", "typescript",
                "next.js", "node.js", "webpack", "babel", "sass", "less", "tailwind"
            ],
            "Backend": [
                "django", "flask", "fastapi", "express", "spring", "spring boot",
                "rest api", "graphql", "microservices", "nginx", "apache"
            ],
            "DevOps": [
                "docker", "kubernetes", "jenkins", "gitlab ci", "github actions",
                "terraform", "ansible", "prometheus", "grafana", "elk stack"
            ]
        }
        
        # Initialize result dictionary
        clustered = {category: [] for category in category_keywords.keys()}
        clustered["General"] = []
        
        # Categorize each skill
        for skill in flat_skills:
            skill_lower = skill.lower()
            categorized = False
            
            # Check each category
            for category, keywords in category_keywords.items():
                for keyword in keywords:
                    if keyword in skill_lower:
                        clustered[category].append(skill)
                        categorized = True
                        break
                
                if categorized:
                    break
            
            # If no category matched, add to General
            if not categorized:
                clustered["General"].append(skill)
        
        # Remove empty categories
        clustered = {k: v for k, v in clustered.items() if v}
        
        # If only General has items, return as-is
        if len(clustered) == 1 and "General" in clustered:
            return clustered
        
        # If General is empty and we have other categories, remove it
        if "General" in clustered and not clustered["General"]:
            del clustered["General"]
        
        return clustered
    
    def _prepare_template_data(self, content: TailoredContent) -> Dict[str, Any]:
        """
        Prepare data structure for Jinja2 template rendering.
        
        Transforms TailoredContent into the format expected by the template,
        including skill clustering and data sanitization.
        
        Args:
            content: TailoredContent from MongoDB
            
        Returns:
            Dictionary ready for template rendering
        """
        # Escape all string fields
        template_data = {
            "full_name": self._escape_latex(content.full_name),
            "contact_info": {
                k: self._escape_latex(v) if isinstance(v, str) else v
                for k, v in content.contact_info.items()
            },
            "summary": self._escape_latex(content.summary) if content.summary else None,
            "skills": self._cluster_skills(content.skills),
            "experience": [],
            "projects": [],
            "education": []
        }
        
        # Process experience entries
        for exp in content.experience:
            exp_entry = {
                "title": self._escape_latex(exp.get("title", "")),
                "company": self._escape_latex(exp.get("company", "")),
                "location": self._escape_latex(exp.get("location", "")),
                "dates": self._escape_latex(exp.get("dates", "")),
                "highlights": [
                    self._escape_latex(bullet) 
                    for bullet in exp.get("highlights", [])
                ]
            }
            template_data["experience"].append(exp_entry)
        
        # Process projects
        # Note: Template expects "name" and "dates", but user_profile has "title" and "date"
        for proj in content.projects:
            proj_entry = {
                "name": self._escape_latex(proj.get("name") or proj.get("title", "")),
                "dates": self._escape_latex(proj.get("dates") or proj.get("date", "")),
                "highlights": [
                    self._escape_latex(bullet)
                    for bullet in proj.get("highlights") or proj.get("description_points", [])
                ]
            }
            template_data["projects"].append(proj_entry)
        
        # Process education
        for edu in content.education:
            edu_entry = {
                "institution": self._escape_latex(edu.get("institution") or edu.get("university", "")),
                "degree": self._escape_latex(edu.get("degree", "")),
                "dates": self._escape_latex(edu.get("dates", "")),
                "gpa": self._escape_latex(edu.get("gpa", "")) if edu.get("gpa") else None,
                "details": self._escape_latex(edu.get("details", "")) if edu.get("details") else None
            }
            template_data["education"].append(edu_entry)
        
        return template_data
    
    def _compile_pdf(self, tex_content: str, temp_dir: Path, 
                     job_fingerprint: str, resume_fingerprint: str) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Compile LaTeX content to PDF using pdflatex.
        
        Args:
            tex_content: LaTeX source code
            temp_dir: Temporary directory for compilation
            job_fingerprint: Job fingerprint for naming
            resume_fingerprint: Resume fingerprint for naming
            
        Returns:
            Tuple of (success: bool, pdf_path: Optional[str], error_log: Optional[str])
        """
        # Write LaTeX file to temp directory
        tex_file = temp_dir / "resume.tex"
        with open(tex_file, 'w', encoding='utf-8') as f:
            f.write(tex_content)
        
        # Run pdflatex
        try:
            result = subprocess.run(
                [
                    'pdflatex',
                    '-interaction=nonstopmode',
                    f'-output-directory={temp_dir}',
                    str(tex_file)
                ],
                capture_output=True,
                text=True,
                timeout=30,  # 30 second timeout
                cwd=str(temp_dir)
            )
            
            # Check if PDF was created
            pdf_file = temp_dir / "resume.pdf"
            
            if result.returncode == 0 and pdf_file.exists():
                # Success - move PDF to output directory
                output_filename = f"{job_fingerprint}_{resume_fingerprint}.pdf"
                output_path = self.output_dir / output_filename
                
                shutil.copy2(pdf_file, output_path)
                
                return True, str(output_path), None
            else:
                # Failure - capture error log
                error_log = f"Return code: {result.returncode}\n\nSTDOUT:\n{result.stdout}\n\nSTDERR:\n{result.stderr}"
                return False, None, error_log
                
        except subprocess.TimeoutExpired:
            error_log = "pdflatex compilation timed out after 30 seconds"
            return False, None, error_log
            
        except FileNotFoundError:
            error_log = "pdflatex not found. Please install LaTeX (e.g., TeX Live, MiKTeX)"
            return False, None, error_log
            
        except Exception as e:
            error_log = f"Unexpected error during compilation: {str(e)}"
            return False, None, error_log
    
    def _render_template(self, template_data: Dict[str, Any]) -> str:
        """
        Render Jinja2 template with provided data.
        
        Args:
            template_data: Dictionary of data for template
            
        Returns:
            Rendered LaTeX content as string
        """
        template = self.jinja_env.get_template('master_resume.tex.j2')
        return template.render(**template_data)
    
    def compile_application(self, application: Dict) -> bool:
        """
        Compile a single TailoredApplication to PDF.
        
        Args:
            application: TailoredApplication document from MongoDB
            
        Returns:
            bool: True if successful, False otherwise
        """
        job_fingerprint = application.get("job_fingerprint", "unknown")
        resume_fingerprint = application.get("resume_fingerprint", "unknown")
        
        print(f"  Compiling: {job_fingerprint}...")
        
        # Parse structured content
        structured_content_dict = application.get("structured_content")
        if not structured_content_dict:
            print(f"    ✗ No structured_content found")
            return False
        
        try:
            content = TailoredContent(**structured_content_dict)
        except Exception as e:
            print(f"    ✗ Failed to parse TailoredContent: {e}")
            return False
        
        # Prepare template data
        template_data = self._prepare_template_data(content)
        
        # Render template
        try:
            tex_content = self._render_template(template_data)
        except Exception as e:
            print(f"    ✗ Template rendering failed: {e}")
            return False
        
        # Compile in temporary directory
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            
            success, pdf_path, error_log = self._compile_pdf(
                tex_content=tex_content,
                temp_dir=temp_path,
                job_fingerprint=job_fingerprint,
                resume_fingerprint=resume_fingerprint
            )
            
            if success:
                # Update status to "completed"
                self.db.tailored_applications.update_one(
                    {
                        "job_fingerprint": job_fingerprint,
                        "resume_fingerprint": resume_fingerprint
                    },
                    {
                        "$set": {
                            "status": "completed",
                            "final_pdf_path": pdf_path,
                            "last_updated": datetime.utcnow()
                        }
                    }
                )
                print(f"    ✓ PDF compiled: {pdf_path}")
                return True
            else:
                # Update status to "failed" with error log
                self.db.tailored_applications.update_one(
                    {
                        "job_fingerprint": job_fingerprint,
                        "resume_fingerprint": resume_fingerprint
                    },
                    {
                        "$set": {
                            "status": "failed",
                            "compile_log": error_log,
                            "last_updated": datetime.utcnow()
                        }
                    }
                )
                print(f"    ✗ Compilation failed")
                if error_log:
                    print(f"      Error: {error_log[:200]}...")
                return False
    
    def run_compiler_pipeline(self, batch_size: int = 10):
        """
        Run the complete compilation pipeline.
        
        Pipeline logic:
        1. Query db.tailored_applications for status="ready_to_compile"
        2. For each application:
           - Update status to "compiling"
           - Render template with sanitized data
           - Compile PDF in sandboxed temp directory
           - Update status to "completed" or "failed"
        
        Args:
            batch_size: Maximum number of applications to process in one run
        """
        print("\n" + "="*60)
        print("LATEX ARCHITECT - COMPILATION PIPELINE")
        print("="*60)
        
        # Query for applications ready to compile
        applications = list(
            self.db.tailored_applications.find(
                {"status": "ready_to_compile"}
            ).limit(batch_size)
        )
        
        if not applications:
            print(f"✓ No applications found with status='ready_to_compile'")
            return
        
        print(f"Found {len(applications)} applications to compile")
        
        processed = 0
        successful = 0
        failed = 0
        
        for i, app in enumerate(applications, 1):
            job_fingerprint = app.get("job_fingerprint", "unknown")
            resume_fingerprint = app.get("resume_fingerprint", "unknown")
            
            print(f"\n[{i}/{len(applications)}] Processing application...")
            print(f"  Job: {job_fingerprint}")
            print(f"  Resume: {resume_fingerprint}")
            
            # Update status to "compiling"
            self.db.tailored_applications.update_one(
                {
                    "job_fingerprint": job_fingerprint,
                    "resume_fingerprint": resume_fingerprint
                },
                {
                    "$set": {
                        "status": "compiling",
                        "last_updated": datetime.utcnow()
                    }
                }
            )
            
            # Compile
            success = self.compile_application(app)
            
            if success:
                successful += 1
            else:
                failed += 1
            
            processed += 1
        
        # Summary
        print("\n" + "="*60)
        print("PIPELINE SUMMARY")
        print("="*60)
        print(f"Processed: {processed}")
        print(f"Successful: {successful}")
        print(f"Failed: {failed}")
        print(f"\n✓ Pipeline complete! Check MongoDB 'tailored_applications' collection.")


def main():
    """Main execution function."""
    print("\n" + "="*60)
    print("LATEX ARCHITECT - THE TYPESETTER")
    print("="*60)
    
    try:
        # Initialize architect
        architect = LatexArchitect()
        
        # Run compilation pipeline
        architect.run_compiler_pipeline(batch_size=10)
        
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
