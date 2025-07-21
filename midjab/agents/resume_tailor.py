import pandas as pd
import json
import os
import re
import subprocess
import logging
import shutil
import configparser
import ollama
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple, Optional
import time

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/resume_tailor.log', mode='a'),
        logging.StreamHandler()
    ]
)

class ResumeTailor:
    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
        
        self.base_resume_path = Path('inputs/resume.tex')
        self.shortlisted_jobs_path = Path('outputs/shortlisted_jobs.csv')
        self.final_output_dir = Path('final_applications')
        self.backup_dir = Path('backups/resume_tailoring')
        self.logs_dir = Path('logs')
        
        self._ensure_directories()
        
        self.latex_timeout = 30
        self.max_compilation_attempts = 3
        self.llm_timeout = 60
        self.max_llm_retries = 2
        self.min_skills_length = 50
        self.max_skills_length = 500
        self.word_count_tolerance = 3
        
        self.logger.info("ResumeTailor initialized successfully")
    
    def _ensure_directories(self):
        directories = [
            self.final_output_dir,
            self.backup_dir,
            self.logs_dir
        ]
        
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
            self.logger.debug(f"Ensured directory exists: {directory}")
    
    def _create_backup(self) -> str:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = self.backup_dir / f"backup_{timestamp}"
        backup_path.mkdir(exist_ok=True)
        
        if self.base_resume_path.exists():
            shutil.copy2(self.base_resume_path, backup_path / "resume.tex")
        
        if self.shortlisted_jobs_path.exists():
            shutil.copy2(self.shortlisted_jobs_path, backup_path / "shortlisted_jobs.csv")
        
        self.logger.info(f"Created backup at: {backup_path}")
        return str(backup_path)
    
    def _validate_inputs(self) -> bool:
        if not self.base_resume_path.exists():
            self.logger.error(f"Base resume not found at: {self.base_resume_path}")
            return False
        
        if not self.shortlisted_jobs_path.exists():
            self.logger.error(f"Shortlisted jobs file not found at: {self.shortlisted_jobs_path}")
            return False
        
        try:
            with open(self.base_resume_path, 'r', encoding='utf-8') as f:
                resume_content = f.read()
            
            editable_sections = self._extract_editable_sections(resume_content)
            if not editable_sections['skills'] and not editable_sections['experience']:
                self.logger.error("No editable sections found in resume. Please add BEGIN/END markers.")
                return False
            
            self.logger.info(f"Found {len(editable_sections['experience'])} experience sections and skills section")
            
        except Exception as e:
            self.logger.error(f"Error validating resume file: {str(e)}")
            return False
        
        try:
            df = pd.read_csv(self.shortlisted_jobs_path)
            required_columns = ['company', 'title', 'description']
            missing_columns = [col for col in required_columns if col not in df.columns]
            
            if missing_columns:
                self.logger.error(f"Missing required columns in shortlisted jobs: {missing_columns}")
                return False
            
            if len(df) == 0:
                self.logger.warning("No shortlisted jobs found")
                return False
            
            self.logger.info(f"Validated {len(df)} shortlisted jobs")
            
        except Exception as e:
            self.logger.error(f"Error validating shortlisted jobs file: {str(e)}")
            return False
        
        return True
    
    def _extract_editable_sections(self, resume_content: str) -> Dict[str, any]:
        editable_sections = {
            'skills': '',
            'experience': [],
            'projects': [],
            'summary': ''
        }
        
        try:
            skills_matches = re.findall(
                r'% BEGIN_SKILLS_EDIT\n(.*?)\n% END_SKILLS_EDIT', 
                resume_content, 
                re.DOTALL
            )
            if skills_matches:
                editable_sections['skills'] = skills_matches[0].strip()
                self.logger.debug("Extracted skills section")
            
            experience_matches = re.findall(
                r'% BEGIN_EXPERIENCE_EDIT\n(.*?)\n% END_EXPERIENCE_EDIT', 
                resume_content, 
                re.DOTALL
            )
            editable_sections['experience'] = [match.strip() for match in experience_matches]
            self.logger.debug(f"Extracted {len(editable_sections['experience'])} experience sections")
            
            project_matches = re.findall(
                r'% BEGIN_PROJECT_EDIT\n(.*?)\n% END_PROJECT_EDIT', 
                resume_content, 
                re.DOTALL
            )
            editable_sections['projects'] = [match.strip() for match in project_matches]
            
            summary_matches = re.findall(
                r'% BEGIN_SUMMARY_EDIT\n(.*?)\n% END_SUMMARY_EDIT', 
                resume_content, 
                re.DOTALL
            )
            if summary_matches:
                editable_sections['summary'] = summary_matches[0].strip()
            
        except Exception as e:
            self.logger.error(f"Error extracting editable sections: {str(e)}")
            raise
        
        return editable_sections
    
    def _count_words(self, text: str) -> int:
        clean_text = re.sub(r'\\[a-zA-Z]+\*?(\{[^}]*\})*', '', text)
        clean_text = re.sub(r'[{}\\]', '', clean_text)
        words = clean_text.split()
        return len([word for word in words if word.strip()])
    
    def _get_llm_tailored_content(self, job_desc: str, editable_sections: Dict[str, any], 
                                  company_name: str, job_title: str) -> Dict[str, any]:
        system_prompt = f"""You are an expert resume optimization specialist. Your task is to tailor resume content for a specific job application while maintaining complete accuracy and authenticity.

CONTEXT:
- Company: {company_name}
- Position: {job_title}
- You must preserve all facts, achievements, and technical accuracy
- Only rephrase to better align with job requirements

STRICT RULES:
1. SKILLS SECTION: Reorder and emphasize skills mentioned in the job description. Do not add skills the candidate doesn't have.
2. EXPERIENCE BULLETS: Rephrase using similar language to the job description while preserving exact facts and achievements.
3. WORD COUNT: Each rephrased experience bullet must be within ±{self.word_count_tolerance} words of the original.
4. NO FABRICATION: Never add experience, skills, or achievements not present in the original.
5. MAINTAIN LATEX: Preserve all LaTeX formatting commands exactly as they appear.

OUTPUT FORMAT:
Return a JSON object with these exact keys:
- "tailored_skills": string (the rewritten skills section)
- "tailored_experience": array of strings (each rephrased bullet point)
- "tailored_projects": array of strings (if projects exist)
- "tailored_summary": string (if summary exists)

Do not include any other text, explanations, or formatting outside the JSON."""

        original_word_counts = [self._count_words(exp) for exp in editable_sections['experience']]
        
        user_content = f"""JOB DESCRIPTION:
{job_desc}

ORIGINAL RESUME CONTENT:

SKILLS SECTION:
{editable_sections['skills']}

EXPERIENCE BULLETS (with word counts):
"""
        
        for i, exp in enumerate(editable_sections['experience']):
            user_content += f"\n{i+1}. [{original_word_counts[i]} words] {exp}"
        
        if editable_sections['projects']:
            user_content += "\n\nPROJECT BULLETS:\n"
            for i, proj in enumerate(editable_sections['projects']):
                user_content += f"{i+1}. {proj}\n"
        
        if editable_sections['summary']:
            user_content += f"\n\nSUMMARY:\n{editable_sections['summary']}"
        
        config = configparser.ConfigParser()
        config.read('config/config.ini')
        mode = config.get('LLM', 'mode')
        
        for attempt in range(self.max_llm_retries + 1):
            try:
                self.logger.info(f"Making LLM call (attempt {attempt + 1}) for {company_name} - {job_title}")
                
                if mode == 'live':
                    remote_host = config.get('LLM', 'remote_host')
                    model_name = config.get('LLM', 'model_name')
                    
                    client = ollama.Client(host=remote_host)
                    response = client.chat(
                        model=model_name,
                        messages=[
                            {'role': 'system', 'content': system_prompt},
                            {'role': 'user', 'content': user_content}
                        ]
                    )
                    llm_response = response['message']['content']
                    
                else:  # mock mode
                    llm_response = json.dumps({
                        "tailored_skills": editable_sections['skills'],
                        "tailored_experience": editable_sections['experience'],
                        "tailored_projects": editable_sections.get('projects', []),
                        "tailored_summary": editable_sections.get('summary', '')
                    })
                
                try:
                    tailored_content = json.loads(llm_response)
                except json.JSONDecodeError:
                    json_match = re.search(r'\{.*\}', llm_response, re.DOTALL)
                    if json_match:
                        tailored_content = json.loads(json_match.group())
                    else:
                        raise ValueError("No valid JSON found in LLM response")
                
                if 'tailored_experience' in tailored_content:
                    new_word_counts = [self._count_words(exp) for exp in tailored_content['tailored_experience']]
                    for i, (orig, new) in enumerate(zip(original_word_counts, new_word_counts)):
                        if abs(orig - new) > self.word_count_tolerance:
                            self.logger.warning(f"Word count mismatch in bullet {i+1}: {orig} -> {new}")
                
                skills_length = len(tailored_content.get('tailored_skills', ''))
                if skills_length < self.min_skills_length or skills_length > self.max_skills_length:
                    self.logger.warning(f"Skills section length outside expected range: {skills_length}")
                
                self.logger.info("LLM tailoring completed successfully")
                return tailored_content
                
            except Exception as e:
                self.logger.warning(f"LLM call attempt {attempt + 1} failed: {str(e)}")
                if attempt < self.max_llm_retries:
                    time.sleep(2 ** attempt)
                else:
                    self.logger.error(f"All LLM attempts failed for {company_name} - {job_title}")
                    return {
                        "tailored_skills": editable_sections['skills'],
                        "tailored_experience": editable_sections['experience'],
                        "tailored_projects": editable_sections.get('projects', []),
                        "tailored_summary": editable_sections.get('summary', '')
                    }
    
    def _apply_tailored_content(self, resume_content: str, original_sections: Dict[str, any], 
                               tailored_content: Dict[str, any]) -> str:
        updated_content = resume_content
        
        try:
            if original_sections['skills'] and 'tailored_skills' in tailored_content:
                old_skills_pattern = f"% BEGIN_SKILLS_EDIT\n{re.escape(original_sections['skills'])}\n% END_SKILLS_EDIT"
                new_skills = f"% BEGIN_SKILLS_EDIT\n{tailored_content['tailored_skills']}\n% END_SKILLS_EDIT"
                updated_content = re.sub(old_skills_pattern, new_skills, updated_content)
                self.logger.debug("Applied tailored skills section")
            
            if original_sections['experience'] and 'tailored_experience' in tailored_content:
                for i, (orig_exp, new_exp) in enumerate(zip(original_sections['experience'], 
                                                           tailored_content['tailored_experience'])):
                    old_exp_pattern = f"% BEGIN_EXPERIENCE_EDIT\n{re.escape(orig_exp)}\n% END_EXPERIENCE_EDIT"
                    new_exp_text = f"% BEGIN_EXPERIENCE_EDIT\n{new_exp}\n% END_EXPERIENCE_EDIT"
                    updated_content = re.sub(old_exp_pattern, new_exp_text, updated_content, count=1)
                
                self.logger.debug(f"Applied {len(original_sections['experience'])} tailored experience sections")
            
            if original_sections['projects'] and 'tailored_projects' in tailored_content:
                for orig_proj, new_proj in zip(original_sections['projects'], tailored_content['tailored_projects']):
                    old_proj_pattern = f"% BEGIN_PROJECT_EDIT\n{re.escape(orig_proj)}\n% END_PROJECT_EDIT"
                    new_proj_text = f"% BEGIN_PROJECT_EDIT\n{new_proj}\n% END_PROJECT_EDIT"
                    updated_content = re.sub(old_proj_pattern, new_proj_text, updated_content, count=1)
            
            if original_sections['summary'] and 'tailored_summary' in tailored_content:
                old_summary_pattern = f"% BEGIN_SUMMARY_EDIT\n{re.escape(original_sections['summary'])}\n% END_SUMMARY_EDIT"
                new_summary = f"% BEGIN_SUMMARY_EDIT\n{tailored_content['tailored_summary']}\n% END_SUMMARY_EDIT"
                updated_content = re.sub(old_summary_pattern, new_summary, updated_content)
            
        except Exception as e:
            self.logger.error(f"Error applying tailored content: {str(e)}")
            raise
        
        return updated_content
    
    def _sanitize_filename(self, text: str) -> str:
        sanitized = re.sub(r'[^\w\s-]', '', text)
        sanitized = re.sub(r'[\s]+', '_', sanitized)
        return sanitized[:50]
    
    def _compile_to_pdf(self, tex_file_path: Path, output_dir: Path) -> Tuple[bool, str]:
        for attempt in range(self.max_compilation_attempts):
            try:
                self.logger.info(f"Compiling LaTeX (attempt {attempt + 1}): {tex_file_path.name}")
                
                output_dir.mkdir(parents=True, exist_ok=True)
                
                command = [
                    'pdflatex',
                    '-interaction=nonstopmode',
                    '-halt-on-error',
                    f'-output-directory={output_dir}',
                    str(tex_file_path)
                ]
                
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=self.latex_timeout,
                    cwd=output_dir
                )
                
                if result.returncode == 0:
                    pdf_path = output_dir / f"{tex_file_path.stem}.pdf"
                    if pdf_path.exists():
                        self.logger.info(f"Successfully compiled: {pdf_path}")
                        self._cleanup_latex_files(output_dir, tex_file_path.stem)
                        return True, ""
                    else:
                        error_msg = "PDF file was not created despite successful compilation"
                        self.logger.error(error_msg)
                        return False, error_msg
                else:
                    error_msg = f"LaTeX compilation failed (exit code {result.returncode})"
                    if result.stderr:
                        error_msg += f"\nSTDERR: {result.stderr}"
                    if result.stdout:
                        error_lines = [line for line in result.stdout.split('\n') if 'error' in line.lower()]
                        if error_lines:
                            error_msg += f"\nErrors: {'; '.join(error_lines[:3])}"
                    
                    self.logger.warning(error_msg)
                    
                    if attempt < self.max_compilation_attempts - 1:
                        time.sleep(1)
                    else:
                        return False, error_msg
                        
            except subprocess.TimeoutExpired:
                error_msg = f"LaTeX compilation timed out after {self.latex_timeout} seconds"
                self.logger.error(error_msg)
                if attempt == self.max_compilation_attempts - 1:
                    return False, error_msg
                    
            except FileNotFoundError:
                error_msg = "pdflatex not found. Please install LaTeX (e.g., TeX Live, MiKTeX)"
                self.logger.error(error_msg)
                return False, error_msg
                
            except Exception as e:
                error_msg = f"Unexpected error during compilation: {str(e)}"
                self.logger.error(error_msg)
                if attempt == self.max_compilation_attempts - 1:
                    return False, error_msg
        
        return False, "All compilation attempts failed"
    
    def _cleanup_latex_files(self, output_dir: Path, base_name: str):
        aux_extensions = ['.aux', '.log', '.fls', '.fdb_latexmk', '.synctex.gz', '.out', '.toc']
        
        for ext in aux_extensions:
            aux_file = output_dir / f"{base_name}{ext}"
            if aux_file.exists():
                try:
                    aux_file.unlink()
                    self.logger.debug(f"Cleaned up: {aux_file}")
                except Exception as e:
                    self.logger.debug(f"Could not clean up {aux_file}: {str(e)}")
    
    def _generate_summary_report(self, results: List[Dict]) -> str:
        total_jobs = len(results)
        successful = sum(1 for r in results if r['success'])
        failed = total_jobs - successful
        
        report = f"""
=== RESUME TAILORING SUMMARY REPORT ===
Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

Total Jobs Processed: {total_jobs}
Successful Compilations: {successful}
Failed Compilations: {failed}
Success Rate: {(successful/total_jobs)*100:.1f}%

DETAILED RESULTS:
"""
        
        for i, result in enumerate(results, 1):
            status = "✓ SUCCESS" if result['success'] else "✗ FAILED"
            report += f"\n{i:2d}. {result['company']} - {result['job_title'][:40]:<40} {status}"
            if not result['success']:
                report += f"\n    Error: {result['error'][:100]}"
        
        if failed > 0:
            report += f"\n\nFailed compilations may be due to:\n"
            report += "- LaTeX syntax errors in original resume\n"
            report += "- Missing LaTeX packages\n"
            report += "- LLM-generated content with LaTeX issues\n"
            report += "- System configuration problems\n"
        
        return report
    
    def run_tailoring_pipeline(self) -> bool:
        start_time = time.time()
        self.logger.info("Starting Resume Tailoring Pipeline")
        
        try:
            backup_path = self._create_backup()
            
            if not self._validate_inputs():
                self.logger.error("Input validation failed")
                return False
            
            df = pd.read_csv(self.shortlisted_jobs_path)
            self.logger.info(f"Loaded {len(df)} shortlisted jobs")
            
            with open(self.base_resume_path, 'r', encoding='utf-8') as f:
                base_resume_content = f.read()
            
            base_editable_sections = self._extract_editable_sections(base_resume_content)
            
            results = []
            for index, job in df.iterrows():
                job_start_time = time.time()
                company = job['company']
                title = job['title']
                description = job['description']
                
                self.logger.info(f"Processing job {index + 1}/{len(df)}: {company} - {title}")
                
                try:
                    dir_name = self._sanitize_filename(f"{company}_{title}")
                    job_output_dir = self.final_output_dir / dir_name
                    job_output_dir.mkdir(parents=True, exist_ok=True)
                    
                    tailored_content = self._get_llm_tailored_content(
                        description, base_editable_sections, company, title
                    )
                    
                    tailored_resume_content = self._apply_tailored_content(
                        base_resume_content, base_editable_sections, tailored_content
                    )
                    
                    tailored_tex_path = job_output_dir / f"{dir_name}_resume.tex"
                    with open(tailored_tex_path, 'w', encoding='utf-8') as f:
                        f.write(tailored_resume_content)
                    
                    success, error_msg = self._compile_to_pdf(tailored_tex_path, job_output_dir)
                    
                    job_info = {
                        'company': company,
                        'job_title': title,
                        'processed_at': datetime.now().isoformat(),
                        'success': success,
                        'tex_file': str(tailored_tex_path),
                        'pdf_file': str(job_output_dir / f"{dir_name}_resume.pdf") if success else None,
                        'error': error_msg if not success else None
                    }
                    
                    with open(job_output_dir / 'job_info.json', 'w') as f:
                        json.dump(job_info, f, indent=2)
                    
                    results.append(job_info)
                    
                    job_duration = time.time() - job_start_time
                    if success:
                        self.logger.info(f"✓ Completed {company} - {title} in {job_duration:.1f}s")
                    else:
                        self.logger.error(f"✗ Failed {company} - {title}: {error_msg}")
                
                except Exception as e:
                    error_msg = f"Unexpected error processing {company} - {title}: {str(e)}"
                    self.logger.error(error_msg)
                    results.append({
                        'company': company,
                        'job_title': title,
                        'success': False,
                        'error': error_msg
                    })
            
            summary_report = self._generate_summary_report(results)
            
            report_path = self.final_output_dir / 'tailoring_summary.txt'
            with open(report_path, 'w') as f:
                f.write(summary_report)
            
            print(summary_report)
            
            total_duration = time.time() - start_time
            successful_jobs = sum(1 for r in results if r['success'])
            
            self.logger.info(f"Pipeline completed in {total_duration:.1f}s. "
                           f"Successfully processed {successful_jobs}/{len(results)} jobs")
            
            return successful_jobs > 0
            
        except Exception as e:
            self.logger.error(f"Pipeline failed: {str(e)}")
            return False