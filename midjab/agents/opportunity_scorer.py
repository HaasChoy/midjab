import json
import re
import ast
import ollama
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import hashlib

from core.db import get_db
from core.models import JobScore, ScoringLog


class OpportunityScorerV2:
    """
    MongoDB-Driven OpportunityScorer V2 with Local Ollama Integration.
    
    Implements hybrid scoring engine:
    1. Keyword Match Score - Fast, deterministic skill matching
    2. LLM Contextual Score - Deep semantic analysis via local Llama 3.2
    3. Requirement Fit Score - Hard requirement validation
    
    Features:
    - MongoDB integration with proper data models
    - Comprehensive logging via ScoringLog collection
    - Local LLM via Ollama (no API costs)
    - Robust JSON parsing for small models
    - Automatic pipeline status management
    """
    
    def __init__(self, 
                 llm_model='phi3.5',
                 ollama_host='http://localhost:11434',
                 resume_fingerprint=None):
        """
        Initialize the OpportunityScorer V2.
        
        Args:
            llm_model (str): Ollama model name (default: phi3.5)
            ollama_host (str): Ollama server URL
            resume_fingerprint (str): Fingerprint of the resume being scored against
        """
        self.db = get_db()
        self.llm_model = llm_model
        self.ollama_host = ollama_host
        
        self.user_profile = None
        self.user_skills = []
        self.resume_fingerprint = resume_fingerprint or self._generate_default_resume_fingerprint()
        
        # Configure Ollama client
        ollama.Client(host=ollama_host)
        
        # Requirement phrases for hard requirement detection
        self.requirement_phrases = [
            'must have', 'required', 'minimum qualifications',
            'key qualifications', 'essential', 'mandatory',
            'prerequisite', 'minimum requirements', 'necessary',
            'required experience', 'required skills'
        ]
        
        self._log_system("INFO", f"OpportunityScorer V2 initialized with model: {llm_model}")
        print(f"✓ OpportunityScorer V2 initialized")
        print(f"  - Model: {llm_model}")
        print(f"  - Ollama Host: {ollama_host}")
        print(f"  - Resume Fingerprint: {self.resume_fingerprint}")
    
    def _generate_default_resume_fingerprint(self) -> str:
        """Generate a default resume fingerprint if none provided."""
        timestamp = datetime.utcnow().isoformat()
        return hashlib.md5(f"default_resume_{timestamp}".encode()).hexdigest()
    
    def _log_system(self, level: str, message: str):
        """Write system log entry to MongoDB."""
        log_entry = ScoringLog(
            job_fingerprint="SYSTEM",
            resume_fingerprint=self.resume_fingerprint,
            score_type_requested="SYSTEM_EVENT",
            timestamp=datetime.utcnow(),
            llm_used=self.llm_model if level != "ERROR" else None,
            raw_prompt=message if level == "ERROR" else None,
            parsed_result={"level": level, "message": message}
        )
        self.db.scoring_logs.insert_one(log_entry.dict(by_alias=True))
    
    def _log_scoring(self, job_fingerprint: str, keyword_score: float, 
                     llm_score: float, requirement_score: float, 
                     final_score: int, error_message: Optional[str] = None):
        """Write scoring log entry to MongoDB."""
        log_entry = ScoringLog(
            job_fingerprint=job_fingerprint,
            resume_fingerprint=self.resume_fingerprint,
            score_type_requested="FINAL_SCORING_SUMMARY",
            timestamp=datetime.utcnow(),
            llm_used=self.llm_model,
            parsed_result={
                "keyword_score": keyword_score,
                "llm_score": llm_score,
                "requirement_score": requirement_score,
                "final_score": final_score,
                "error_message": error_message
            }
        )
        self.db.scoring_logs.insert_one(log_entry.dict(by_alias=True))
    
    def load_user_profile(self, profile_path='outputs/user_profile.json'):
        """Load user profile and extract skills."""
        try:
            with open(profile_path, 'r') as f:
                self.user_profile = json.load(f)
            
            # Extract skills from all sections
            self.user_skills = []
            skills_sections = ['technical_skills', 'soft_skills', 'languages']
            
            for section in skills_sections:
                if section in self.user_profile:
                    if isinstance(self.user_profile[section], list):
                        self.user_skills.extend([s.lower() for s in self.user_profile[section]])
                    elif isinstance(self.user_profile[section], dict):
                        for category, skills in self.user_profile[section].items():
                            if isinstance(skills, list):
                                self.user_skills.extend([s.lower() for s in skills])
            
            self._log_system("INFO", f"User profile loaded: {len(self.user_skills)} skills extracted")
            print(f"✓ User profile loaded: {len(self.user_skills)} skills")
            
        except FileNotFoundError:
            self._log_system("ERROR", f"User profile not found: {profile_path}")
            raise
    
    def _calculate_keyword_score(self, title: str, description: str) -> float:
        """
        Calculate keyword match score based on skill frequency and prominence.
        
        Args:
            title (str): Job title
            description (str): Job description
            
        Returns:
            float: Keyword score (0.0 to unbounded)
        """
        title_lower = title.lower()
        description_lower = description.lower()
        score = 0.0
        
        for skill in self.user_skills:
            if skill in description_lower:
                # Base weight + frequency bonus
                weight = 1.0 + (0.3 * description_lower.count(skill))
                
                # Title prominence bonus
                if skill in title_lower:
                    weight += 1.5
                
                score += weight
        
        return score
    
    def _calculate_requirement_score(self, description: str) -> float:
        """
        Calculate requirement fit score by matching skills to hard requirements.
        
        Args:
            description (str): Job description
            
        Returns:
            float: Requirement fit score (count of matched requirements)
        """
        description_lower = description.lower()
        sentences = re.split(r'[.\n]', description_lower)
        score = 0.0
        
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            
            # Check for requirement phrases
            has_requirement = any(phrase in sentence for phrase in self.requirement_phrases)
            
            if has_requirement:
                # Check if user has skills mentioned in this requirement
                skills_found = [skill for skill in self.user_skills if skill in sentence]
                if skills_found:
                    score += 1.0
        
        return score
    
    def _call_llm(self, prompt: str) -> Optional[Dict]:
        """
        Call local Ollama LLM with structured JSON output.
        
        Args:
            prompt (str): The prompt to send to the LLM
            
        Returns:
            dict: Parsed JSON response from LLM or None if failed
        """
        try:
            response = ollama.chat(
                model=self.llm_model,
                messages=[{'role': 'user', 'content': prompt}],
                format='json',  # CRITICAL: Force JSON output
                options={
                    'temperature': 0.3,  # Lower temperature for more consistent output
                    'num_predict': 500   # Limit response length
                }
            )
            
            # Extract content
            content = response['message']['content']
            
            # Parse JSON with robust error handling
            return self._parse_llm_json(content)
            
        except Exception as e:
            self._log_system("ERROR", f"LLM call failed: {str(e)}")
            return None
    
    def _parse_llm_json(self, content: str) -> Optional[Dict]:
        """
        Multi-strategy JSON parser for LLM outputs with enhanced robustness.
        
        Handles:
        - Standard JSON with double quotes
        - Markdown code blocks (```json ... ```)
        - Extra text before/after JSON
        - Python-style dictionaries with single quotes
        - Malformed or incomplete JSON
        
        Parsing Strategies (in order):
        1. Direct Parse - Standard json.loads()
        2. Markdown Cleanup - Remove code fences
        3. Substring Extraction - Extract content between first { and last }
        4. AST Literal Eval - Handle single-quoted dictionaries
        5. Manual Key Extraction - Extract score and reasoning with regex
        
        Args:
            content (str): Raw LLM output
            
        Returns:
            dict: Parsed JSON object or None if all strategies fail
        """
        if not content or not isinstance(content, str):
            self._log_system("ERROR", "Empty or invalid content provided to parser")
            return None
        
        # Strategy 1: Direct JSON parsing
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass
        
        # Strategy 2: Remove markdown code blocks
        cleaned_content = re.sub(r'```json\s*|\s*```', '', content).strip()
        try:
            return json.loads(cleaned_content)
        except json.JSONDecodeError:
            pass
        
        # Strategy 3: Extract content between first { and last }
        try:
            start = cleaned_content.find('{')
            end = cleaned_content.rfind('}')
            if start != -1 and end != -1 and end > start:
                json_str = cleaned_content[start:end+1]
                return json.loads(json_str)
        except (json.JSONDecodeError, ValueError):
            pass
        
        # Strategy 4: AST Literal Eval (handles single-quoted Python dictionaries)
        try:
            start = cleaned_content.find('{')
            end = cleaned_content.rfind('}')
            if start != -1 and end != -1 and end > start:
                dict_str = cleaned_content[start:end+1]
                result = ast.literal_eval(dict_str)
                
                # Validate it's a dictionary with expected keys
                if isinstance(result, dict):
                    return result
        except (ValueError, SyntaxError):
            pass
        
        # Strategy 5: Manual key extraction with regex (last resort)
        try:
            score_match = re.search(r'["\']?score["\']?\s*:\s*([0-9.]+)', content, re.IGNORECASE)
            reasoning_match = re.search(
                r'["\']?reasoning["\']?\s*:\s*["\']([^"\']+)["\']', 
                content, 
                re.IGNORECASE | re.DOTALL
            )
            
            if score_match:
                score = float(score_match.group(1))
                reasoning = reasoning_match.group(1) if reasoning_match else "Extracted via fallback parsing"
                
                self._log_system("WARNING", f"Used fallback regex parsing for LLM output")
                return {
                    "score": score,
                    "reasoning": reasoning.strip()
                }
        except (ValueError, AttributeError):
            pass
        
        # All strategies failed
        self._log_system("ERROR", f"All parsing strategies failed. Content preview: {content[:300]}")
        return None
    
    def _calculate_llm_score(self, job_fingerprint: str, title: str, description: str) -> Tuple[float, str]:
        """
        Use LLM to evaluate job fit with contextual reasoning.
        
        Args:
            job_fingerprint (str): Job fingerprint for logging
            title (str): Job title
            description (str): Job description
            
        Returns:
            tuple: (score: float 0-10, reasoning: str)
        """
        # Build user context
        user_context = {
            "skills": self.user_skills[:20],  # Limit to top 20 skills for token efficiency
            "summary": self.user_profile.get('summary', 'Not provided')[:300]
        }
        
        prompt = f"""You are an expert career advisor. Analyze how well this job matches the candidate's profile.

CANDIDATE PROFILE:
- Skills: {', '.join(user_context['skills'])}
- Summary: {user_context['summary']}

JOB POSTING:
Title: {title}
Description: {description[:1000]}

Evaluate the match on a scale of 0-10 considering:
1. Skill alignment (technical + soft skills)
2. Career trajectory fit
3. Role responsibility match

Respond with ONLY valid JSON in this exact format:
{{
    "score": 7.5,
    "reasoning": "Brief explanation of the score"
}}"""

        result = self._call_llm(prompt)
        
        if result and 'score' in result:
            score = float(result['score'])
            reasoning = result.get('reasoning', 'No reasoning provided')
            self._log_system("INFO", f"LLM scored job {job_fingerprint}: {score}/10")
            return score, reasoning
        else:
            self._log_system("WARNING", f"LLM returned invalid response for job {job_fingerprint}")
            return 5.0, "LLM evaluation failed - default score applied"
    
    def score_job(self, job: Dict) -> Optional[Dict]:
        """
        Score a single job using hybrid scoring engine.
        
        Args:
            job (dict): Job document from MongoDB (UnifiedJob model)
            
        Returns:
            dict: Scoring results or None if failed
        """
        try:
            job_fingerprint = job.get('fingerprint')
            if not job_fingerprint:
                self._log_system("ERROR", f"Job missing fingerprint: {job.get('_id')}")
                return None
            
            title = job.get('title', '')
            description = job.get('description', '')
            
            # Calculate scores
            keyword_score = self._calculate_keyword_score(title, description)
            requirement_score = self._calculate_requirement_score(description)
            llm_score, llm_reasoning = self._calculate_llm_score(job_fingerprint, title, description)
            
            # Combine scores using weighted formula
            # Formula: final = (keyword * 0.3) + (llm * 0.5) + (requirement * 0.2)
            # Then normalize to 1-10 scale
            weighted_sum = (keyword_score * 0.3) + (llm_score * 0.5) + (requirement_score * 0.2)
            
            # Normalize to 1-10 (assuming max reasonable score is ~15)
            final_score = min(10, max(1, round(1 + (weighted_sum / 15) * 9)))
            
            # Create JobScore object
            job_score = JobScore(
                job_fingerprint=job_fingerprint,
                resume_fingerprint=self.resume_fingerprint,
                match_score=final_score,
                keyword_score=keyword_score,
                semantic_score=llm_score,
                requirement_score=requirement_score,
                llm_reasoning=llm_reasoning,
                scoring_timestamp=datetime.utcnow(),
                model_version=self.llm_model
            )
            
            # Upsert to job_scores collection (prevent duplicates)
            self.db.job_scores.update_one(
                {
                    "job_fingerprint": job_fingerprint,
                    "resume_fingerprint": self.resume_fingerprint
                },
                {"$set": job_score.dict(by_alias=True)},
                upsert=True
            )
            
            # Update job status in jobs collection
            self.db.jobs.update_one(
                {"_id": job['_id']},
                {
                    "$set": {
                        "status": "scored",
                        "match_score": final_score,
                        "last_updated": datetime.utcnow()
                    }
                }
            )
            
            # Log scoring event
            self._log_scoring(
                job_fingerprint=job_fingerprint,
                keyword_score=keyword_score,
                llm_score=llm_score,
                requirement_score=requirement_score,
                final_score=final_score
            )
            
            return {
                'job_fingerprint': job_fingerprint,
                'title': title,
                'keyword_score': keyword_score,
                'llm_score': llm_score,
                'requirement_score': requirement_score,
                'final_score': final_score,
                'reasoning': llm_reasoning
            }
            
        except Exception as e:
            error_msg = f"Failed to score job {job.get('fingerprint', 'unknown')}: {str(e)}"
            self._log_system("ERROR", error_msg)
            
            # Log failed scoring attempt
            if job.get('fingerprint'):
                self._log_scoring(
                    job_fingerprint=job['fingerprint'],
                    keyword_score=0.0,
                    llm_score=0.0,
                    requirement_score=0.0,
                    final_score=0,
                    error_message=error_msg
                )
            
            return None
    
    def run_full_scoring(self, batch_size: int = 50):
        """
        Score all pending jobs in MongoDB.
        
        Args:
            batch_size (int): Number of jobs to process in one run (default: 50)
        """
        # Query for pending jobs
        pending_jobs = list(self.db.jobs.find(
            {"status": "pending_review"}
        ).limit(batch_size))
        
        if not pending_jobs:
            print("✓ No pending jobs found for scoring")
            return
        
        print(f"\nScoring {len(pending_jobs)} pending jobs...")
        self._log_system("INFO", f"Starting batch scoring for {len(pending_jobs)} jobs")
        
        scored = 0
        failed = 0
        
        for i, job in enumerate(pending_jobs, 1):
            job_title = job.get('title', 'Unknown')
            print(f"  [{i}/{len(pending_jobs)}] Scoring: {job_title[:50]}...", end='')
            
            result = self.score_job(job)
            
            if result:
                print(f" ✓ Score: {result['final_score']}/10")
                scored += 1
            else:
                print(" ✗ Failed")
                failed += 1
        
        self._log_system("INFO", f"Batch scoring complete: {scored} scored, {failed} failed")
        print(f"\n✓ Scoring complete: {scored}/{len(pending_jobs)} jobs scored successfully")
        
        if failed > 0:
            print(f"⚠ {failed} jobs failed to score (check scoring_logs collection)")
    
    def get_shortlisted_jobs(self, threshold: int = 4, limit: int = 50) -> List[Dict]:
        """
        Retrieve shortlisted jobs from MongoDB.
        
        Args:
            threshold (int): Minimum match_score (default: 4)
            limit (int): Maximum number of results (default: 50)
            
        Returns:
            list: List of job dictionaries with scores
        """
        pipeline = [
            # Join jobs with job_scores
            {
                "$lookup": {
                    "from": "job_scores",
                    "localField": "fingerprint",
                    "foreignField": "job_fingerprint",
                    "as": "scores"
                }
            },
            # Unwind scores array
            {"$unwind": "$scores"},
            # Filter by resume and threshold
            {
                "$match": {
                    "scores.resume_fingerprint": self.resume_fingerprint,
                    "scores.match_score": {"$gte": threshold}
                }
            },
            # Project desired fields
            {
                "$project": {
                    "_id": 0,
                    "fingerprint": 1,
                    "title": 1,
                    "company": 1,
                    "location": 1,
                    "url": 1,
                    "match_score": "$scores.match_score",
                    "keyword_score": "$scores.keyword_score",
                    "semantic_score": "$scores.semantic_score",
                    "requirement_score": "$scores.requirement_score",
                    "llm_reasoning": "$scores.llm_reasoning",
                    "scoring_timestamp": "$scores.scoring_timestamp"
                }
            },
            # Sort by match_score descending
            {"$sort": {"match_score": -1, "semantic_score": -1}},
            # Limit results
            {"$limit": limit}
        ]
        
        results = list(self.db.jobs.aggregate(pipeline))
        
        print(f"✓ Retrieved {len(results)} shortlisted jobs (threshold: {threshold})")
        return results


def test_ollama_connection(host='http://localhost:11434', model='phi3.5'):
    """Test connection to local Ollama instance."""
    print("\n" + "="*60)
    print("TESTING OLLAMA CONNECTION")
    print("="*60)
    
    try:
        response = ollama.chat(
            model=model,
            messages=[{
                'role': 'user',
                'content': 'Respond with only this JSON: {"status": "connected", "model": "' + model + '"}'
            }],
            format='json'
        )
        
        content = response['message']['content']
        result = json.loads(content)
        
        print(f"✓ Connection successful!")
        print(f"  Model: {result.get('model', 'unknown')}")
        print(f"  Status: {result.get('status', 'unknown')}")
        return True
        
    except Exception as e:
        print(f"✗ Connection failed: {str(e)}")
        print(f"\nTroubleshooting:")
        print(f"  1. Ensure Ollama is running: ollama serve")
        print(f"  2. Verify model is installed: ollama list")
        print(f"  3. Pull model if needed: ollama pull {model}")
        return False


def main():
    """Main execution pipeline."""
    print("\n" + "="*60)
    print("OPPORTUNITYSCORER V2 - MONGODB EDITION")
    print("="*60)
    
    # Test Ollama connection first
    if not test_ollama_connection():
        print("\n⚠ Please fix Ollama connection before proceeding.")
        return
    
    # Initialize scorer
    scorer = OpportunityScorerV2()
    
    # Load user profile
    scorer.load_user_profile()
    
    # Run scoring on pending jobs
    scorer.run_full_scoring(batch_size=50)
    
    # Retrieve and display shortlisted jobs
    results = scorer.get_shortlisted_jobs(threshold=4, limit=50)
    
    if not results:
        print("\n⚠ No jobs met the threshold. Try lowering the threshold or scoring more jobs.")
        return
    
    # Display top results
    print("\n" + "="*60)
    print("TOP 5 RECOMMENDED OPPORTUNITIES")
    print("="*60)
    
    for job in results[:5]:
        print(f"\n Score: {job['match_score']}/10")
        print(f"   Title: {job['title']}")
        print(f"   Company: {job.get('company', 'N/A')}")
        print(f"   Location: {job.get('location', 'N/A')}")
        print(f"   Breakdown:")
        print(f"     • Keyword: {job['keyword_score']:.1f}")
        print(f"     • LLM: {job['semantic_score']:.1f}")
        print(f"     • Requirements: {job['requirement_score']:.1f}")
        print(f"   Reasoning: {job['llm_reasoning'][:100]}...")
        print(f"   URL: {job.get('url', 'N/A')}")
        print("-" * 60)
    
    print(f"\n✓ Pipeline complete! Check MongoDB 'scoring_logs' collection for detailed logs.")


if __name__ == "__main__":
    main()