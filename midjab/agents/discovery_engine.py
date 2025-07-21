from jobspy import scrape_jobs
import pandas as pd
import time
class DiscoveryEngine:
    """
    Agent 1: DiscoveryEngine - Phase 1 (Broad Scan)
    This Engine is responsible for proactive crawling on all major job boards, this needs active maintenence
    """
    def __init__(self):
        self.search_parameters = {
            "search_term": "Data Scientist",
            "location": "Hyderabad, India",
            "results_wanted": 25,  
            "hours_old": 72  #hours 
        }

    def run_broad_scan(self):
        print(" DiscoveryEngine initialized")
        job_boards = ["linkedin", "indeed", "naukri_com", "glassdoor", "zip_recruiter", "google"]
        all_successful_jobs = []
        for board in job_boards:
            print(f"\n--- Scanning: {board} ---")
            current_params = self.search_parameters.copy()
            current_params["site_name"] = [board]
            try:
                jobs_df = scrape_jobs(**current_params)
                if not jobs_df.empty:
                    print(f"SUCCESS: Found {len(jobs_df)} jobs on {board}.")
                    all_successful_jobs.append(jobs_df)
                else:
                    print(f"INFO: Scan successful, but no relevant jobs found on {board}.")
            except Exception as e:
                print(f"FAILURE: Could not scrape from {board}. Error: {e}")
            time.sleep(2)
        
        
        if all_successful_jobs:
            final_jobs_df = pd.concat(all_successful_jobs, ignore_index=True)
            final_jobs_df.drop_duplicates(subset='description', inplace=True)
            final_jobs_df.to_csv('outputs/raw_jobs.csv', index=False)
            print(f"\nINFO: [DiscoveryEngine] Broad Scan complete. Found a total of {len(final_jobs_df)} unique jobs. Saved to 'outputs/raw_jobs.csv'.")
        else:
            print("\nINFO: [DiscoveryEngine] Broad Scan complete. No jobs were found across all platforms.")



if __name__ == "__main__":
    engine = DiscoveryEngine()
    engine.run_broad_scan()