from agents.data_factory import process_raw_job

sample_job = {
    "id": "12345",
    "title": "Senior Data Scientist",
    "description": "Build ML pipelines, handle big data, productionize AI systems.",
    "company": "OpenAI",
    "location": "San Francisco, CA, USA",
    "salary_min": 150000,
    "salary_max": 220000,
    "job_url": "https://example.com/job/12345",
    "date_posted": "2024-01-15T10:30:00Z",
    "job_type": "Full-time",
    "job_level": "Senior",
    "is_remote": False,
}

result = process_raw_job(sample_job, source="test_source")

print("\n=== RESULT ===")
print(result)
