"""
LLM configuration and client factory.
Supports both Gemini (Google AI, default) and Ollama (local fallback).
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

from dotenv import load_dotenv

load_dotenv()


def call_llm(
    prompt: str,
    model: str | None = None,
    temperature: float = 0.3,
    format_json: bool = True,
    num_predict: int = 1000,
) -> Optional[dict[str, Any]]:
    """
    Make LLM call using Gemini (if GOOGLE_API_KEY set) or Ollama (fallback).
    
    Returns dict with structure: {"message": {"content": "..."}}
    """
    api_key = os.getenv("GOOGLE_API_KEY")
    
    if api_key:
        # Use Gemini
        try:
            import google.generativeai as genai
            genai.configure(api_key=api_key)
            
            model_name = model or "gemini-1.5-flash"
            model_instance = genai.GenerativeModel(model_name)
            
            generation_config = {
                "temperature": temperature,
                "max_output_tokens": num_predict,
            }
            if format_json:
                generation_config["response_mime_type"] = "application/json"
            
            response = model_instance.generate_content(
                prompt,
                generation_config=generation_config,
            )
            return {"message": {"content": response.text}}
        except Exception as e:
            print(f"Gemini API error: {e}")
            # Fall through to Ollama
    
    # Fallback to Ollama
    try:
        import ollama
        ollama_model = model or os.getenv("OLLAMA_MODEL", "phi3.5")
        ollama_host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
        
        client = ollama.Client(host=ollama_host)
        response = client.chat(
            model=ollama_model,
            messages=[{"role": "user", "content": prompt}],
            format="json" if format_json else None,
            options={"temperature": temperature, "num_predict": num_predict},
        )
        return response
    except Exception as e:
        print(f"Ollama error: {e}")
        return None


def parse_llm_json(content: str) -> Optional[dict]:
    """Parse JSON from LLM response, handling markdown code fences."""
    if not content:
        return None
    
    # Strategy 1: direct parse
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    
    # Strategy 2: strip markdown fences
    cleaned = re.sub(r"```json\s*|\s*```", "", content).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    
    # Strategy 3: extract first JSON object
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    
    return None
