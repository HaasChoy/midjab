# core/adapter_enum_test.py
from agents.data_factory import adapt_jobspy_to_unified, _serialize_for_mongo
# create a fake payload mimicking the jobspy example
fake = {
    "id": "li-4321895945",
    "title": "Software Quality Assurance Engineer (Testing)",
    "company_name": "Acme Corp",
    "job_url": "https://www.linkedin.com/jobs/view/4321895945",
    "location": {"country": None, "city": "Hyderabad", "state": "Telangana"},
    "job_type": ["fulltime"],  # simulate simplified version
    "description": "<div>...html...</div>",
    "is_remote": True,
}
# adapt
job = adapt_jobspy_to_unified(fake, source="linkedin")
# make sure raw payload is serialized
print("raw is type:", type(job.raw))
print("raw.job_type:", job.raw.get("job_type"))
print("title:", job.title)
