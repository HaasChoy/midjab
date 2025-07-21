import pandas as pd
import json
import re  
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np
import os


class OpportunityScorer:
    """
    Advanced OpportunityScorer Agent implementing the "Inferred Importance" engine.
    
    This agent calculates:
    1. Skill Relevance Score - Dynamic scoring based on skill frequency and prominence
    2. Semantic Context Score - AI-powered similarity analysis
    3. Requirement Fit Score - Matching against hard requirements
    
    Combines scores using multiplicative formula and normalizes to 1-10 scale.
    """
    
    def __init__(self):
        self.user_profile = None
        self.raw_jobs_df = None
        self.user_skills = []
        self.user_profile_embedding = None
        self.model = SentenceTransformer('all-MiniLM-L6-v2') 
        
        # Define the "power phrases" for the Requirement Fit Score 
        #Note - Requirement phrases must be populated eventually and automatically while we scrape and watching the market demands
        self.requirement_phrases = [
            'must have', 'required', 'minimum qualifications', 
            'key qualifications', 'essential', 'mandatory',
            'prerequisite', 'minimum requirements', 'necessary'
        ]
        print("OpportunityScorer initialized with AI model loaded.")
    
    def load_data(self):
        """Load user profile and job data from files."""
        try:
            # Load user profile
            with open('outputs/user_profile.json', 'r') as f:
                self.user_profile = json.load(f)
            print(" User profile loaded successfully")
            
            # Load raw jobs data
            self.raw_jobs_df = pd.read_csv('outputs/raw_jobs.csv')
            print(f" Loaded {len(self.raw_jobs_df)} job opportunities")
            
        except FileNotFoundError as e:
            print(f"Error: Could not find required file - {e}")
            raise
        except Exception as e:
            print(f"Error loading data: {e}")
            raise
    
    def prepare_user_profile(self):
        """Extract skills and create user profile embedding."""
        if not self.user_profile:
            raise ValueError("User profile not loaded. Call load_data() first.")
        self.user_skills = []
        
        # Get skills from different sections
        skills_sections = ['technical_skills', 'soft_skills', 'languages']
        for section in skills_sections:
            if section in self.user_profile:
                if isinstance(self.user_profile[section], list):
                    self.user_skills.extend([skill.lower() for skill in self.user_profile[section]])
                elif isinstance(self.user_profile[section], dict):
                    # Handle nested skill structures
                    for category, skills in self.user_profile[section].items():
                        if isinstance(skills, list):
                            self.user_skills.extend([skill.lower() for skill in skills])
        
        # Create user profile text for embedding
        profile_text = ""
        if 'summary' in self.user_profile:
            profile_text += self.user_profile['summary'] + " "
        
        # Add experience descriptions
        if 'experience' in self.user_profile:
            for exp in self.user_profile['experience']:
                if 'description' in exp:
                    profile_text += exp['description'] + " "
        
        # Add skills as text
        profile_text += " ".join(self.user_skills)
        
        # Generate embedding
        self.user_profile_embedding = self.model.encode([profile_text])
        
        print(f" Extracted {len(self.user_skills)} skills from user profile")
        print(f" Generated user profile embedding")
    
    def _calculate_dynamic_scores(self, title, description):
        """
        Calculate Skill Relevance Score and Requirement Fit Score for a job.
        
        Args:
            title (str): Job title
            description (str): Job description
            
        Returns:
            tuple: (skill_relevance_score, requirement_fit_score)
        """
        title_lower = title.lower()
        description_lower = description.lower()
        
        # 1. Calculate Skill Relevance Score
        skill_relevance_score = 0.0
        
        for skill in self.user_skills:
            if skill in description_lower:
                # Dynamic weight based on frequency
                dynamic_weight = 1 + (0.5 * description_lower.count(skill))
                
                # Prominence bonus if skill is in title
                if skill in title_lower:
                    dynamic_weight += 2.0
                
                skill_relevance_score += dynamic_weight
        
        # 2. Calculate Requirement Fit Score
        requirement_fit_score = 0.0
        
        # Split description into sentences
        sentences = re.split(r'[.\n]', description_lower)
        
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
                
            # Check if sentence contains requirement phrases
            has_requirement_phrase = any(phrase in sentence for phrase in self.requirement_phrases)
            
            if has_requirement_phrase:
                # Check if user skills are mentioned in this requirement sentence
                skills_in_sentence = [skill for skill in self.user_skills if skill in sentence]
                if skills_in_sentence:
                    requirement_fit_score += 1.0
        
        return skill_relevance_score, requirement_fit_score
    
    def run_scoring_pipeline(self):
        """
        Execute the complete scoring pipeline.
        
        This method orchestrates the entire scoring process:
        1. Loads data and prepares user profile
        2. Calculates semantic scores (batch processing)
        3. Calculates skill relevance and requirement fit scores
        4. Combines scores using multiplicative formula
        5. Normalizes to 1-10 scale and saves results
        """
        print("Starting OpportunityScorer pipeline...")
        
        # Step 1: Load data and prepare user profile
        self.load_data()
        self.prepare_user_profile()
        
        # Step 2: Calculate Semantic Scores (Batch)
        print("Calculating semantic similarity scores...")
        job_descriptions = self.raw_jobs_df['description'].fillna('').tolist()
        job_embeddings = self.model.encode(job_descriptions)
        
        # Calculate cosine similarity between user profile and all jobs
        semantic_scores = cosine_similarity(self.user_profile_embedding, job_embeddings)[0]
        
        # Step 3: Calculate Other Scores (Loop)
        print("Calculating skill relevance and requirement fit scores...")
        skill_relevance_scores = []
        requirement_fit_scores = []
        
        for idx, row in self.raw_jobs_df.iterrows():
            title = str(row.get('title', ''))
            description = str(row.get('description', ''))
            
            skill_score, req_score = self._calculate_dynamic_scores(title, description)
            skill_relevance_scores.append(skill_score)
            requirement_fit_scores.append(req_score)
        
        # Step 4: Combine Scores (Vectorized)
        print("Combining scores using multiplicative formula...")
        semantic_scores_array = np.array(semantic_scores)
        skill_relevance_scores_array = np.array(skill_relevance_scores)
        requirement_fit_scores_array = np.array(requirement_fit_scores)
        
        # Apply vigorous multiplicative formula
        raw_final_scores = semantic_scores_array * (1 + skill_relevance_scores_array) * (1 + requirement_fit_scores_array)
        
        # Step 5: Normalize Scores to 1-10 Scale
        print("Normalizing scores to 1-10 scale...")
        min_score = np.min(raw_final_scores)
        max_score = np.max(raw_final_scores)
        
        normalized_scores = []
        
        if max_score == min_score:
            # All scores are the same, assign middle value
            normalized_scores = [5.0] * len(raw_final_scores)
        else:
            # Apply Min-Max scaling to 1-10 scale
            for score in raw_final_scores:
                normalized_score = 1 + (score - min_score) * 9 / (max_score - min_score)
                normalized_scores.append(normalized_score)
        
        # Step 6: Update DataFrame & Save
        print("Finalizing results...")
        
        # Add scores to DataFrame
        self.raw_jobs_df['semantic_score'] = semantic_scores
        self.raw_jobs_df['skill_relevance_score'] = skill_relevance_scores
        self.raw_jobs_df['requirement_fit_score'] = requirement_fit_scores
        self.raw_jobs_df['raw_combined_score'] = raw_final_scores
        self.raw_jobs_df['match_score'] = [round(score) for score in normalized_scores]
        
        # Sort by match_score in descending order
        self.raw_jobs_df = self.raw_jobs_df.sort_values('match_score', ascending=False)
        
        # Filter out jobs below threshold (score of 4)
        shortlisted_df = self.raw_jobs_df[self.raw_jobs_df['match_score'] >= 4].copy()
        
        # Ensure outputs directory exists
        os.makedirs('outputs', exist_ok=True)
        
        # Save results
        shortlisted_df.to_csv('outputs/shortlisted_jobs.csv', index=False)
        
        print(f" Pipeline completed successfully!")
        print(f" {len(shortlisted_df)} jobs shortlisted (score ≥ 4)")
        print(f" Results saved to outputs/shortlisted_jobs.csv")
        print(f" Score distribution:")
        print(f"   - Highest score: {shortlisted_df['match_score'].max()}")
        print(f"   - Average score: {shortlisted_df['match_score'].mean():.1f}")
        print(f"   - Lowest score: {shortlisted_df['match_score'].min()}")
        
        return shortlisted_df


def main():
    """Main function to run the OpportunityScorer."""
    try:
        scorer = OpportunityScorer()
        results = scorer.run_scoring_pipeline()
        
        # Display top 5 results
        print("\n" + "="*60)
        print("TOP 5 RECOMMENDED OPPORTUNITIES:")
        print("="*60)
        
        top_jobs = results.head(5)
        for idx, job in top_jobs.iterrows():
            print(f"\n{job['match_score']}/10 - {job['title']}")
            if 'company' in job:
                print(f"Company: {job['company']}")
            print(f"Semantic: {job['semantic_score']:.3f} | "
                  f"Skills: {job['skill_relevance_score']:.1f} | "
                  f"Requirements: {job['requirement_fit_score']:.1f}")
            print("-" * 40)
        
    except Exception as e:
        print(f"Error in OpportunityScorer: {e}")
        raise


if __name__ == "__main__":
    main()