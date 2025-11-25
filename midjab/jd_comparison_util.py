#!/usr/bin/env python3
"""
JD Comparison Utility Script for midjab
========================================

A comprehensive utility for comparing user profiles to job descriptions (JDs).
Provides various analysis features including skill mapping, gap analysis, 
similarity scoring, and detailed reporting.

Usage:
    python jd_comparison_util.py [options]

Features:
    - Skill mapping and gap analysis
    - Semantic similarity analysis
    - Requirement matching
    - Detailed comparison reports
    - Export functionality
    - Interactive analysis mode
"""

import json
import pandas as pd
import numpy as np
import argparse
import sys
import os
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass
from pathlib import Path
import re
from collections import Counter, defaultdict

# Import existing modules
try:
    from sentence_transformers import SentenceTransformer
    from sklearn.metrics.pairwise import cosine_similarity
    from sklearn.feature_extraction.text import TfidfVectorizer
    HAS_ML_DEPS = True
except ImportError:
    HAS_ML_DEPS = False
    print("Warning: ML dependencies not available. Some features will be limited.")

@dataclass
class ComparisonResult:
    """Data class to store comparison results"""
    job_id: str
    job_title: str
    company: str
    overall_score: float
    skill_match_score: float
    semantic_score: float
    requirement_fit_score: float
    missing_skills: List[str]
    matched_skills: List[str]
    skill_gaps: Dict[str, int]  # skill -> gap level (1-5)
    recommendations: List[str]

class JDComparisonUtil:
    """
    Comprehensive utility for comparing user profiles to job descriptions.
    """
    
    def __init__(self, profile_path: str = "outputs/user_profile.json", 
                 jobs_path: str = "outputs/shortlisted_jobs.csv"):
        """
        Initialize the JD comparison utility.
        
        Args:
            profile_path: Path to user profile JSON file
            jobs_path: Path to jobs CSV file
        """
        self.profile_path = profile_path
        self.jobs_path = jobs_path
        self.user_profile = None
        self.jobs_df = None
        self.user_skills = []
        self.user_profile_text = ""
        
        # ML models (if available)
        self.sentence_model = None
        if HAS_ML_DEPS:
            try:
                self.sentence_model = SentenceTransformer('all-MiniLM-L6-v2')
            except Exception as e:
                print(f"Warning: Could not load sentence transformer: {e}")
        
        # Skill categories and synonyms
        self.skill_synonyms = {
            'python': ['python', 'py', 'python3', 'python 3'],
            'machine learning': ['ml', 'machine learning', 'ai', 'artificial intelligence', 'deep learning'],
            'data science': ['data science', 'data scientist', 'data analysis', 'analytics'],
            'sql': ['sql', 'mysql', 'postgresql', 'database', 'db'],
            'aws': ['aws', 'amazon web services', 'cloud', 'ec2', 's3'],
            'docker': ['docker', 'containerization', 'containers'],
            'kubernetes': ['kubernetes', 'k8s', 'orchestration'],
            'git': ['git', 'version control', 'github', 'gitlab'],
            'javascript': ['javascript', 'js', 'node.js', 'nodejs'],
            'react': ['react', 'reactjs', 'react.js'],
            'java': ['java', 'spring', 'spring boot'],
            'tensorflow': ['tensorflow', 'tf', 'keras'],
            'pytorch': ['pytorch', 'torch'],
            'pandas': ['pandas', 'dataframe', 'data manipulation'],
            'numpy': ['numpy', 'numerical computing'],
            'scikit-learn': ['scikit-learn', 'sklearn', 'scikit learn'],
            'spark': ['spark', 'apache spark', 'pyspark'],
            'kafka': ['kafka', 'apache kafka', 'streaming'],
            'elasticsearch': ['elasticsearch', 'elastic search', 'elk'],
            'redis': ['redis', 'caching', 'cache'],
            'mongodb': ['mongodb', 'mongo', 'nosql'],
            'postgresql': ['postgresql', 'postgres', 'postgresql'],
            'linux': ['linux', 'unix', 'ubuntu', 'centos'],
            'bash': ['bash', 'shell scripting', 'shell'],
            'jenkins': ['jenkins', 'ci/cd', 'continuous integration'],
            'terraform': ['terraform', 'infrastructure as code', 'iac'],
            'ansible': ['ansible', 'configuration management'],
        }
        
        # Requirement phrases for requirement matching
        self.requirement_phrases = [
            'must have', 'required', 'minimum qualifications', 
            'key qualifications', 'essential', 'mandatory',
            'prerequisite', 'minimum requirements', 'necessary',
            'required skills', 'must possess', 'should have'
        ]
        
        self.load_data()
    
    def load_data(self):
        """Load user profile and jobs data"""
        try:
            # Load user profile
            with open(self.profile_path, 'r') as f:
                self.user_profile = json.load(f)
            print(f"✓ Loaded user profile: {self.user_profile.get('full_name', 'Unknown')}")
            
            # Load jobs data
            self.jobs_df = pd.read_csv(self.jobs_path)
            print(f"✓ Loaded {len(self.jobs_df)} job opportunities")
            
            # Extract user skills and create profile text
            self._extract_user_skills()
            self._create_profile_text()
            
        except FileNotFoundError as e:
            print(f"Error: Could not find required file - {e}")
            raise
        except Exception as e:
            print(f"Error loading data: {e}")
            raise
    
    def _extract_user_skills(self):
        """Extract and normalize user skills from profile"""
        self.user_skills = []
        
        # Extract from skills section
        if 'skills' in self.user_profile:
            if isinstance(self.user_profile['skills'], list):
                self.user_skills.extend([skill.lower() for skill in self.user_profile['skills']])
            elif isinstance(self.user_profile['skills'], dict):
                for category, skills in self.user_profile['skills'].items():
                    if isinstance(skills, list):
                        self.user_skills.extend([skill.lower() for skill in skills])
        
        # Extract from experience descriptions
        if 'experience' in self.user_profile:
            for exp in self.user_profile['experience']:
                if 'description_points' in exp:
                    for desc in exp['description_points']:
                        # Extract technical terms from descriptions
                        tech_terms = self._extract_tech_terms(desc.lower())
                        self.user_skills.extend(tech_terms)
        
        # Extract from projects
        if 'projects' in self.user_profile:
            for project in self.user_profile['projects']:
                if 'description_points' in project:
                    for desc in project['description_points']:
                        tech_terms = self._extract_tech_terms(desc.lower())
                        self.user_skills.extend(tech_terms)
        
        # Normalize and deduplicate skills
        self.user_skills = list(set(self.user_skills))
        print(f"✓ Extracted {len(self.user_skills)} unique skills from profile")
    
    def _extract_tech_terms(self, text: str) -> List[str]:
        """Extract technical terms from text"""
        tech_terms = []
        
        # Common technical patterns
        patterns = [
            r'\b(?:python|java|javascript|sql|aws|docker|kubernetes|git|react|tensorflow|pytorch|pandas|numpy|scikit-learn|spark|kafka|elasticsearch|redis|mongodb|postgresql|linux|bash|jenkins|terraform|ansible)\b',
            r'\b(?:machine learning|deep learning|data science|artificial intelligence|nlp|computer vision|reinforcement learning)\b',
            r'\b(?:api|rest|graphql|microservices|serverless|lambda|ec2|s3|rds|vpc|iam)\b',
            r'\b(?:agile|scrum|devops|ci/cd|tdd|bdd|oop|functional programming)\b'
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            tech_terms.extend([match.lower() for match in matches])
        
        return tech_terms
    
    def _create_profile_text(self):
        """Create comprehensive profile text for semantic analysis"""
        profile_parts = []
        
        # Add summary if available
        if 'summary' in self.user_profile:
            profile_parts.append(self.user_profile['summary'])
        
        # Add experience descriptions
        if 'experience' in self.user_profile:
            for exp in self.user_profile['experience']:
                if 'description_points' in exp:
                    profile_parts.extend(exp['description_points'])
        
        # Add project descriptions
        if 'projects' in self.user_profile:
            for project in self.user_profile['projects']:
                if 'description_points' in project:
                    profile_parts.extend(project['description_points'])
        
        # Add skills as text
        profile_parts.extend(self.user_skills)
        
        self.user_profile_text = " ".join(profile_parts)
    
    def compare_to_job(self, job_row: pd.Series) -> ComparisonResult:
        """
        Compare user profile to a specific job.
        
        Args:
            job_row: Pandas Series containing job data
            
        Returns:
            ComparisonResult object with detailed comparison
        """
        job_id = str(job_row.get('id', ''))
        job_title = str(job_row.get('title', ''))
        company = str(job_row.get('company', ''))
        job_description = str(job_row.get('description', ''))
        
        # Calculate skill match score
        skill_match_score, matched_skills, missing_skills = self._calculate_skill_match(job_description)
        
        # Calculate semantic similarity
        semantic_score = self._calculate_semantic_similarity(job_description)
        
        # Calculate requirement fit score
        requirement_fit_score = self._calculate_requirement_fit(job_description)
        
        # Calculate overall score
        overall_score = self._calculate_overall_score(skill_match_score, semantic_score, requirement_fit_score)
        
        # Identify skill gaps
        skill_gaps = self._identify_skill_gaps(job_description)
        
        # Generate recommendations
        recommendations = self._generate_recommendations(missing_skills, skill_gaps, job_description)
        
        return ComparisonResult(
            job_id=job_id,
            job_title=job_title,
            company=company,
            overall_score=overall_score,
            skill_match_score=skill_match_score,
            semantic_score=semantic_score,
            requirement_fit_score=requirement_fit_score,
            missing_skills=missing_skills,
            matched_skills=matched_skills,
            skill_gaps=skill_gaps,
            recommendations=recommendations
        )
    
    def _calculate_skill_match(self, job_description: str) -> Tuple[float, List[str], List[str]]:
        """Calculate skill matching score and identify matched/missing skills"""
        job_desc_lower = job_description.lower()
        matched_skills = []
        missing_skills = []
        
        # Check each user skill against job description
        for skill in self.user_skills:
            skill_variants = self.skill_synonyms.get(skill, [skill])
            found = False
            
            for variant in skill_variants:
                if variant in job_desc_lower:
                    matched_skills.append(skill)
                    found = True
                    break
            
            if not found:
                missing_skills.append(skill)
        
        # Also check for skills mentioned in job description that user doesn't have
        job_skills = self._extract_tech_terms(job_desc_lower)
        for job_skill in job_skills:
            if job_skill not in self.user_skills and job_skill not in missing_skills:
                # Check if it's a synonym of a skill user has
                is_synonym = False
                for user_skill, synonyms in self.skill_synonyms.items():
                    if user_skill in self.user_skills and job_skill in synonyms:
                        is_synonym = True
                        break
                
                if not is_synonym:
                    missing_skills.append(job_skill)
        
        # Calculate score (0-1)
        total_skills = len(matched_skills) + len(missing_skills)
        if total_skills == 0:
            skill_match_score = 0.0
        else:
            skill_match_score = len(matched_skills) / total_skills
        
        return skill_match_score, matched_skills, missing_skills
    
    def _calculate_semantic_similarity(self, job_description: str) -> float:
        """Calculate semantic similarity between user profile and job description"""
        if not self.sentence_model or not job_description.strip():
            return 0.0
        
        try:
            # Encode both texts
            profile_embedding = self.sentence_model.encode([self.user_profile_text])
            job_embedding = self.sentence_model.encode([job_description])
            
            # Calculate cosine similarity
            similarity = cosine_similarity(profile_embedding, job_embedding)[0][0]
            return float(similarity)
        except Exception as e:
            print(f"Warning: Could not calculate semantic similarity: {e}")
            return 0.0
    
    def _calculate_requirement_fit(self, job_description: str) -> float:
        """Calculate how well user profile fits job requirements"""
        job_desc_lower = job_description.lower()
        requirement_sentences = []
        
        # Split description into sentences
        sentences = re.split(r'[.\n]', job_desc_lower)
        
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            
            # Check if sentence contains requirement phrases
            has_requirement_phrase = any(phrase in sentence for phrase in self.requirement_phrases)
            if has_requirement_phrase:
                requirement_sentences.append(sentence)
        
        if not requirement_sentences:
            return 0.5  # Neutral score if no clear requirements found
        
        # Check how many requirement sentences match user skills
        matched_requirements = 0
        for sentence in requirement_sentences:
            for skill in self.user_skills:
                skill_variants = self.skill_synonyms.get(skill, [skill])
                if any(variant in sentence for variant in skill_variants):
                    matched_requirements += 1
                    break
        
        return matched_requirements / len(requirement_sentences)
    
    def _calculate_overall_score(self, skill_score: float, semantic_score: float, requirement_score: float) -> float:
        """Calculate overall compatibility score"""
        # Weighted combination
        weights = {'skill': 0.4, 'semantic': 0.3, 'requirement': 0.3}
        overall = (weights['skill'] * skill_score + 
                  weights['semantic'] * semantic_score + 
                  weights['requirement'] * requirement_score)
        
        # Normalize to 0-10 scale
        return round(overall * 10, 2)
    
    def _identify_skill_gaps(self, job_description: str) -> Dict[str, int]:
        """Identify skill gaps with severity levels (1-5)"""
        job_desc_lower = job_description.lower()
        skill_gaps = {}
        
        # Extract all technical terms from job description
        job_skills = self._extract_tech_terms(job_desc_lower)
        
        for skill in job_skills:
            if skill not in self.user_skills:
                # Determine gap severity based on context
                gap_level = 1  # Default low severity
                
                # Check if skill is in requirements section
                sentences = re.split(r'[.\n]', job_desc_lower)
                for sentence in sentences:
                    if any(phrase in sentence for phrase in self.requirement_phrases):
                        if skill in sentence:
                            gap_level = 5  # High severity for required skills
                            break
                    elif skill in sentence:
                        gap_level = max(gap_level, 3)  # Medium severity
                
                # Check frequency in description
                frequency = job_desc_lower.count(skill)
                if frequency > 3:
                    gap_level = max(gap_level, 4)
                
                skill_gaps[skill] = gap_level
        
        return skill_gaps
    
    def _generate_recommendations(self, missing_skills: List[str], skill_gaps: Dict[str, int], 
                                job_description: str) -> List[str]:
        """Generate actionable recommendations"""
        recommendations = []
        
        # High priority skill gaps
        high_priority_gaps = [skill for skill, level in skill_gaps.items() if level >= 4]
        if high_priority_gaps:
            recommendations.append(f"Priority: Learn {', '.join(high_priority_gaps[:3])} - these are frequently mentioned requirements")
        
        # Missing skills
        if missing_skills:
            recommendations.append(f"Consider gaining experience with: {', '.join(missing_skills[:5])}")
        
        # General recommendations based on job type
        if 'senior' in job_description.lower():
            recommendations.append("Focus on leadership and mentoring skills for senior role")
        elif 'junior' in job_description.lower() or 'entry' in job_description.lower():
            recommendations.append("Emphasize learning ability and foundational skills")
        
        # Technology stack recommendations
        if 'cloud' in job_description.lower() and 'aws' not in self.user_skills:
            recommendations.append("Consider AWS certification for cloud roles")
        
        if 'data' in job_description.lower() and 'sql' not in self.user_skills:
            recommendations.append("Strengthen SQL and database skills for data roles")
        
        return recommendations[:5]  # Limit to top 5 recommendations
    
    def analyze_all_jobs(self) -> List[ComparisonResult]:
        """Analyze all jobs and return comparison results"""
        results = []
        
        print("Analyzing job matches...")
        for idx, row in self.jobs_df.iterrows():
            try:
                result = self.compare_to_job(row)
                results.append(result)
                print(f"  ✓ Analyzed: {result.job_title} at {result.company} (Score: {result.overall_score}/10)")
            except Exception as e:
                print(f"  ✗ Error analyzing job {idx}: {e}")
        
        # Sort by overall score
        results.sort(key=lambda x: x.overall_score, reverse=True)
        return results
    
    def generate_report(self, results: List[ComparisonResult], output_file: str = None) -> str:
        """Generate detailed comparison report"""
        report_lines = []
        
        # Header
        report_lines.append("=" * 80)
        report_lines.append("JOB DESCRIPTION COMPARISON REPORT")
        report_lines.append("=" * 80)
        report_lines.append(f"User: {self.user_profile.get('full_name', 'Unknown')}")
        report_lines.append(f"Analysis Date: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report_lines.append(f"Total Jobs Analyzed: {len(results)}")
        report_lines.append("")
        
        # Summary statistics
        if results:
            scores = [r.overall_score for r in results]
            report_lines.append("SUMMARY STATISTICS")
            report_lines.append("-" * 40)
            report_lines.append(f"Average Score: {np.mean(scores):.2f}/10")
            report_lines.append(f"Highest Score: {max(scores):.2f}/10")
            report_lines.append(f"Lowest Score: {min(scores):.2f}/10")
            report_lines.append(f"Jobs with Score ≥ 7: {len([s for s in scores if s >= 7])}")
            report_lines.append(f"Jobs with Score ≥ 5: {len([s for s in scores if s >= 5])}")
            report_lines.append("")
        
        # Top matches
        report_lines.append("TOP MATCHES")
        report_lines.append("-" * 40)
        for i, result in enumerate(results[:10], 1):
            report_lines.append(f"{i}. {result.job_title} at {result.company}")
            report_lines.append(f"   Overall Score: {result.overall_score}/10")
            report_lines.append(f"   Skill Match: {result.skill_match_score:.2f} | "
                              f"Semantic: {result.semantic_score:.2f} | "
                              f"Requirements: {result.requirement_fit_score:.2f}")
            report_lines.append(f"   Matched Skills: {', '.join(result.matched_skills[:5])}")
            if result.missing_skills:
                report_lines.append(f"   Missing Skills: {', '.join(result.missing_skills[:5])}")
            report_lines.append("")
        
        # Skill gap analysis
        report_lines.append("SKILL GAP ANALYSIS")
        report_lines.append("-" * 40)
        
        # Aggregate skill gaps across all jobs
        all_gaps = defaultdict(list)
        for result in results:
            for skill, level in result.skill_gaps.items():
                all_gaps[skill].append(level)
        
        # Calculate average gap severity
        gap_analysis = {}
        for skill, levels in all_gaps.items():
            gap_analysis[skill] = {
                'avg_severity': np.mean(levels),
                'frequency': len(levels),
                'max_severity': max(levels)
            }
        
        # Sort by frequency and severity
        sorted_gaps = sorted(gap_analysis.items(), 
                           key=lambda x: (x[1]['frequency'], x[1]['avg_severity']), 
                           reverse=True)
        
        report_lines.append("Most Common Skill Gaps:")
        for skill, analysis in sorted_gaps[:10]:
            report_lines.append(f"  • {skill}: Avg severity {analysis['avg_severity']:.1f}/5 "
                              f"(appears in {analysis['frequency']} jobs)")
        
        report_lines.append("")
        
        # Recommendations
        report_lines.append("GENERAL RECOMMENDATIONS")
        report_lines.append("-" * 40)
        
        # Aggregate recommendations
        all_recommendations = []
        for result in results[:5]:  # Top 5 jobs
            all_recommendations.extend(result.recommendations)
        
        # Count recommendation frequency
        rec_counter = Counter(all_recommendations)
        for rec, count in rec_counter.most_common(5):
            report_lines.append(f"  • {rec}")
        
        report_lines.append("")
        report_lines.append("=" * 80)
        
        report_text = "\n".join(report_lines)
        
        # Save to file if specified
        if output_file:
            with open(output_file, 'w') as f:
                f.write(report_text)
            print(f"✓ Report saved to {output_file}")
        
        return report_text
    
    def export_detailed_results(self, results: List[ComparisonResult], output_file: str = "jd_comparison_results.csv"):
        """Export detailed results to CSV"""
        data = []
        
        for result in results:
            data.append({
                'job_id': result.job_id,
                'job_title': result.job_title,
                'company': result.company,
                'overall_score': result.overall_score,
                'skill_match_score': result.skill_match_score,
                'semantic_score': result.semantic_score,
                'requirement_fit_score': result.requirement_fit_score,
                'matched_skills': '; '.join(result.matched_skills),
                'missing_skills': '; '.joiimport pandas as pd
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
                    })import pandas as pd
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
                        'success': False,import pandas as pd
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
    def interactive_analysis(self):
        """Interactive analysis mode"""
        print("\n" + "=" * 60)
        print("INTERACTIVE JD ANALYSIS MODE")
        print("=" * 60)
        
        while True:
            print("\nOptions:")
            print("1. Analyze all jobs")
            print("2. Analyze specific job by ID")
            print("3. Show skill gap summary")
            print("4. Generate report")
            print("5. Export results")
            print("6. Exit")
            
            choice = input("\nEnter your choice (1-6): ").strip()
            
            if choice == '1':
                results = self.analyze_all_jobs()
                print(f"\nAnalysis complete! Analyzed {len(results)} jobs.")
                
            elif choice == '2':
                job_id = input("Enter job ID: ").strip()
                job_row = self.jobs_df[self.jobs_df['id'] == job_id]
                if not job_row.empty:
                    result = self.compare_to_job(job_row.iloc[0])
                    self._print_job_analysis(result)
                else:
                    print("Job ID not found!")
                    
            elif choice == '3':
                if 'results' in locals():
                    self._print_skill_gap_summary(results)
                else:
                    print("Please analyze jobs first (option 1)")
                    
            elif choice == '4':
                if 'results' in locals():
                    output_file = input("Enter output filename (or press Enter for default): ").strip()
                    if not output_file:
                        output_file = f"jd_analysis_report_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.txt"
                    report = self.generate_report(results, output_file)
                    print("\nReport generated!")
                else:
                    print("Please analyze jobs first (option 1)")
                    
            elif choice == '5':
                if 'results' in locals():
                    output_file = input("Enter CSV filename (or press Enter for default): ").strip()
                    if not output_file:
                        output_file = f"jd_comparison_results_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.csv"
                    self.export_detailed_results(results, output_file)
                else:
                    print("Please analyze jobs first (option 1)")
                    
            elif choice == '6':
                print("Goodbye!")
                break
            else:
                print("Invalid choice. Please try again.")
    
    def _print_job_analysis(self, result: ComparisonResult):
        """Print detailed analysis for a single job"""
        print(f"\n{'='*60}")
        print(f"JOB ANALYSIS: {result.job_title}")
        print(f"Company: {result.company}")
        print(f"{'='*60}")
        print(f"Overall Score: {result.overall_score}/10")
        print(f"Skill Match: {result.skill_match_score:.2f}")
        print(f"Semantic Similarity: {result.semantic_score:.2f}")
        print(f"Requirement Fit: {result.requirement_fit_score:.2f}")
        print(f"\nMatched Skills ({len(result.matched_skills)}):")
        for skill in result.matched_skills:
            print(f"  ✓ {skill}")
        print(f"\nMissing Skills ({len(result.missing_skills)}):")
        for skill in result.missing_skills:
            print(f"  ✗ {skill}")
        print(f"\nRecommendations:")
        for rec in result.recommendations:
            print(f"  • {rec}")
    
    def _print_skill_gap_summary(self, results: List[ComparisonResult]):
        """Print skill gap summary"""
        print(f"\n{'='*60}")
        print("SKILL GAP SUMMARY")
        print(f"{'='*60}")
        
        # Aggregate skill gaps
        all_gaps = defaultdict(list)
        for result in results:
            for skill, level in result.skill_gaps.items():
                all_gaps[skill].append(level)
        
        # Calculate statistics
        gap_stats = {}
        for skill, levels in all_gaps.items():
            gap_stats[skill] = {
                'avg_severity': np.mean(levels),
                'frequency': len(levels),
                'max_severity': max(levels)
            }
        
        # Sort by frequency and severity
        sorted_gaps = sorted(gap_stats.items(), 
                           key=lambda x: (x[1]['frequency'], x[1]['avg_severity']), 
                           reverse=True)
        
        print("Top Skill Gaps (by frequency and severity):")
        for i, (skill, stats) in enumerate(sorted_gaps[:15], 1):
            print(f"{i:2d}. {skill:<25} | Freq: {stats['frequency']:2d} | "
                  f"Avg Severity: {stats['avg_severity']:.1f}/5 | "
                  f"Max: {stats['max_severity']}/5")


def main():
    """Main function with command line interface"""
    parser = argparse.ArgumentParser(description="JD Comparison Utility for midjab")
    parser.add_argument('--profile', default='outputs/user_profile.json',
                       help='Path to user profile JSON file')
    parser.add_argument('--jobs', default='outputs/shortlisted_jobs.csv',
                       help='Path to jobs CSV file')
    parser.add_argument('--output', help='Output file for report')
    parser.add_argument('--export', help='Export detailed results to CSV')
    parser.add_argument('--interactive', action='store_true',
                       help='Run in interactive mode')
    parser.add_argument('--top', type=int, default=10,
                       help='Number of top matches to show')
    
    args = parser.parse_args()
    
    try:
        # Initialize utility
        util = JDComparisonUtil(args.profile, args.jobs)
        
        if args.interactive:
            util.interactive_analysis()
        else:
            # Run analysis
            print("Starting JD comparison analysis...")
            results = util.analyze_all_jobs()
            
            # Generate report
            if args.output:
                report = util.generate_report(results, args.output)
            else:
                report = util.generate_report(results)
                print("\n" + report)
            
            # Export results if requested
            if args.export:
                util.export_detailed_results(results, args.export)
            
            # Show top matches
            print(f"\nTOP {args.top} MATCHES:")
            print("-" * 50)
            for i, result in enumerate(results[:args.top], 1):
                print(f"{i:2d}. {result.job_title} at {result.company}")
                print(f"    Score: {result.overall_score}/10 | "
                      f"Skills: {result.skill_match_score:.2f} | "
                      f"Semantic: {result.semantic_score:.2f}")
                print()
    
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()









