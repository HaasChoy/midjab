from agents.profile_parser import parse_resume_from_latex, extract_structured_data
from agents.discovery_engine import DiscoveryEngine
from agents.opportunity_scorer import OpportunityScorer
import json
import os 
from agents.resume_tailor import ResumeTailor

def main():
    print("--- Starting midjab v0.1 ---")
    raw_resume_text = parse_resume_from_latex()
    if raw_resume_text:
        print("\n--- Extracted Resume Text ---")
        print(raw_resume_text)
        print("-----------------------------\n")
        structured_resume_data = extract_structured_data(raw_resume_text)
        if structured_resume_data:
            print("INFO: [main] Successfully received structured data.")
            
            try:
                with open("outputs/user_profile.json", 'w') as f:
                    json.dump(structured_resume_data, f, indent=4)
                print("INFO: [main] Structured profile saved to 'outputs/user_profile.json'")
                 
            except Exception as e:
                print(f"ERROR: [main] Failed to save structured data to file. Error: {e}")
            print("\nINFO: [main] Initializing Discovery Engine...")
            discovery_agent = DiscoveryEngine()
            discovery_agent.run_broad_scan()
            if os.path.exists('outputs/raw_jobs.csv'):
                print("\nINFO: [main] Initializing Opportunity Scorer...")
                scorer_agent = OpportunityScorer()
                scorer_agent.run_scoring_pipeline()
            else:
                print("WARNING: [main] 'raw_jobs.csv' not found. Skipping scoring step.")
            if os.path.exists('outputs/shortlisted_jobs.csv'):
                print("\nINFO: [main] Initializing Resume Tailor...")
                tailor_agent = ResumeTailor()
                tailor_agent.run_tailoring_pipeline()   
            else:
                print("WARNING: [main] 'shortlisted_jobs.csv' not found. Skipping resume tailoring.")
        else:
            print("CRITICAL: [main] Could not extract structured data from resume text. Exiting.")
            return
    else:
        print("CRITICAL: Could not parse resume. Exiting.")
        return
    
    print("\n--- midjab run complete, support us by contributing to the project ---")

if __name__ == "__main__":
    main()