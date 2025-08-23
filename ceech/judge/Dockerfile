# 1. Start from Python 3.11 slim image
FROM python:3.11-slim

# 2. Set working directory
WORKDIR /app

# 3. Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. Copy the judge.py script
COPY judge.py .

# 5. Run the script
CMD ["python", "judge.py"]
