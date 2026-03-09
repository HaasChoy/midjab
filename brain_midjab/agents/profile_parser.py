import json
import configparser

from config.llm import call_llm, parse_llm_json


def parse_resume_from_latex(file_path="resume.tex"):
    print("Attempting to read your resume")
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            raw_text = f.read()
        print("Successfully read your profile")
        return raw_text
    except FileNotFoundError:
        print(" The file is not at the position you thought it was, it was either misplaced, file path error or something went wrong")
        return None
    except Exception as e:
        print(f" An unexpected error occurred, ye dekho kya hai: {e}")
        return None


def extract_structured_data(resume_text):
    config = configparser.ConfigParser()
    config.read('config/config.ini')
    llm_mode = config.get('LLM', 'mode', fallback='mock')
    if llm_mode == 'mock':
        print("INFO:  Running in MOCK mode. Loading data from local file...")
        try:
            with open('outputs/mock_user_profile.json', 'r') as f:
                mock_data = json.load(f)
            print("INFO: [Agent Parser] Successfully loaded mock data.")
            return mock_data
        except Exception as e:
            print(f"ERROR: [ProfileParser] Failed to load mock data file. Error: {e}")
            return None 
    elif llm_mode == 'live':
        print(" Sending your resume to the main cognitive engine")
        model_name = config.get('LLM', 'model_name', fallback='gemini-1.5-flash')
        system_prompt = """You are an expert resume parsing AI. Your task is to analyze the provided resume text and extract key information into a structured JSON object.
The JSON object must have the following keys and data types:
- "full_name": string
- "email": string
- "phone": string
- "github_url": string
- "portfolio_url": string
- "skills": A dictionary where keys are skill categories (e.g., "Data Engineering & Cloud") and values are a list of strings (the skills).
- "experience": A list of dictionaries. Each dictionary represents a job and must contain: "title" (string), "company" (string), "location" (string), "duration" (string), and "description_points" (a list of strings).
- "projects": A list of dictionaries. Each dictionary represents a project and must contain: "title" (string), "supervisor" (string), "date" (string), and "description_points" (a list of strings).
- "education": A dictionary with keys: "university" (string), "degree" (string), "details" (string).
Do not add any extra conversational text or explanations. Your output must be only the JSON object."""
        try:
            full_prompt = f"{system_prompt}\n\nResume text:\n{resume_text}"
            response = call_llm(
                prompt=full_prompt,
                model=model_name,
                format_json=True,
                temperature=0.3,
            )
            if response and "message" in response:
                structured_data = parse_llm_json(response["message"]["content"])
                if structured_data:
                    print(" Data has been structured into dictionaries by LLM")
                    return structured_data
            print("ERROR: Failed to parse LLM response")
            return None
        except Exception as e:
            print(f"ERROR aya hai, kuch tho gadbad hai daya, Error: {e}")
            return None
    
    else:
        print(f"ERROR: [ProfileParser] Invalid mode '{llm_mode}' found in config.ini. Please use 'mock' or 'live'.")
        return None