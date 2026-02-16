# Enhanced Project Instructions

## 1. Packaging Instructions

### Step 1: Move Docker Compose to Project Root
- Locate the existing `docker-compose.yml` file (likely in a subfolder like `services/` or `config/`).
- Move it to the root directory of the project (e.g., `/project-root/docker-compose.yml`).
- Update any relative paths in the file (e.g., volume mounts or build contexts) to reflect the new location. For example, if a service builds from `./app`, change it to `./` if the app code is now at the root.

### Step 2: Configure Dockerfile for Serving the FastAPI App
- Create or update `Dockerfile` in the project root (if it doesn't exist, base it on Python 3.12-slim for efficiency).
- Ensure it installs dependencies, copies the code, and runs the FastAPI app with Uvicorn (a production-ready ASGI server).
- Example `Dockerfile` (optimized for size and security):
  ```
  FROM python:3.12-slim

  # Set working directory
  WORKDIR /app

  # Install dependencies
  COPY requirements.txt .
  RUN pip install --no-cache-dir -r requirements.txt

  # Copy application code
  COPY . .

  # Expose port for FastAPI
  EXPOSE 8000

  # Run FastAPI with Uvicorn (production mode: no reload, multiple workers)
  CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
  ```
- Best Practice: Use a non-root user for security. Add these lines before CMD:
  ```
  RUN useradd -m appuser
  USER appuser
  ```
- Build and test: Run `docker build -t my-agentic-app .` and `docker run -p 8000:8000 my-agentic-app` to verify.

## 2. FastAPI Configuration and Containerization

### Step 2.1: Apply Resource Limits to Docker Services
To prevent CPU/RAM monopolization, set limits in `docker-compose.yml` based on Docker best practices. These are conservative starting points for a typical FastAPI + Celery + Redis setup (adjust after load testing; e.g., use tools like `docker stats` to monitor).

- For FastAPI service: Limit to 1 CPU and 500MB RAM (prevents overload on lightweight apps).
- For Celery worker: Limit to 2 CPUs and 1GB RAM (handles background tasks without starving the API).
- For Redis: Limit to 0.5 CPU and 256MB RAM (Redis is memory-efficient but can grow with queues).

Example updates to `docker-compose.yml` (add under each service):
```
version: '3.8'

services:
  fastapi:
    build: .
    ports:
      - "8000:8000"
    deploy:
      resources:
        limits:
          cpus: '1.0'
          memory: 500m
        reservations:
          memory: 300m  # Soft limit for low-memory scenarios

  celery-worker:
    build: .
    command: celery -A your_app.celery worker --loglevel=info --concurrency=4
    depends_on:
      - redis
    deploy:
      resources:
        limits:
          cpus: '2.0'
          memory: 1g
        reservations:
          memory: 512m

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    deploy:
      resources:
        limits:
          cpus: '0.5'
          memory: 256m
```
- Disable swap for performance: Add `command: ["--maxmemory-policy", "noeviction"]` to Redis if needed.
- Test: Run `docker compose up` and simulate load (e.g., with `ab` or Locust) to ensure limits aren't breached.

### Step 2.2: Convert Console App to FastAPI Application
Convert the existing console app (with Pydantic agent) to a FastAPI web app. This involves wrapping logic in API endpoints.

- Install FastAPI and Uvicorn: Add to `requirements.txt`: `fastapi==0.115.2` and `uvicorn==0.32.0`.
- Create `main.py` (or update existing entry point):
  1. Import FastAPI: `from fastapi import FastAPI`.
  2. Create app instance: `app = FastAPI(title="Agentic Project API", version="1.0")`.
  3. Migrate console logic: Wrap agent functions in path operations (e.g., `@app.post("/process")`).
  4. Add dependencies: If needed, use `Depends` for shared logic like database connections.
- Run locally: `uvicorn main:app --reload`.
- Best Practices:
  - Use async where possible (e.g., `async def` for I/O-bound tasks).
  - Validate inputs/outputs with Pydantic models.
  - Generate docs: Access `/docs` for Swagger UI.

## 3. API Endpoints

Define these endpoints in `main.py`. Use Pydantic for request/response models to ensure validation and serialization.

### Proposed Response Structure
For informative, consistent responses, use a standardized JSON format:
- **Success**: `{"data": <payload>, "meta": {"status": "success", "message": <optional str>, "timestamp": <ISO datetime>}}`
- **Error**: `{"error": {"code": <HTTP code>, "message": <str>, "details": <optional dict>}, "meta": {"timestamp": <ISO datetime>}}`
- Define base models in a `models.py` file:
  ```
  from pydantic import BaseModel
  from datetime import datetime

  class Meta(BaseModel):
      status: str = "success"
      message: str | None = None
      timestamp: datetime = datetime.utcnow()

  class ErrorResponse(BaseModel):
      error: dict[str, any]
      meta: Meta = Meta(status="error")
  ```
- Benefits: Consistent, easy to parse, includes metadata for debugging/logging.

### Endpoint 1: Batch Recipe Evidence Processing
- Purpose: Process multiple recipe evidences asynchronously via Celery.
- Method: POST `/batch-process`
- Request: JSON array of evidences (use Pydantic model, e.g., `class Evidence(BaseModel): recipe_id: int, details: str`).
- Response: Job ID(s) for tracking.
- Example:
  ```
  from fastapi import Body
  from your_app.celery_tasks import process_evidence

  @app.post("/batch-process", response_model=dict)
  async def batch_process(evidences: list[Evidence] = Body(...)):
      job_ids = [process_evidence.delay(ev.data()) for ev in evidences]  # Assuming Celery task
      return {"data": {"job_ids": job_ids}, "meta": {"message": "Batch processing started"}}
  ```

### Endpoint 2: Fetch Recipe Evidence (Paginated, with Search and Filtering)
- Purpose: Retrieve evidences with pagination, search (e.g., by keyword), and filters (e.g., by scenario).
- Method: GET `/evidences`
- Query Params: `page: int = 1`, `limit: int = 10`, `search: str | None`, `filter_scenario: str | None`.
- Response: Paginated list with meta (total count, pages).
- Example:
  ```
  from fastapi import Query

  class EvidenceResponse(BaseModel):
      id: int
      recipe_id: int
      details: str
      scenario: str

  @app.get("/evidences", response_model=dict)
  async def fetch_evidence(page: int = Query(1), limit: int = Query(10), search: str | None = Query(None), filter_scenario: str | None = Query(None)):
      # Query logic (e.g., from DB or Redis)
      results = get_evidence_from_storage(page, limit, search, filter_scenario)  # Implement this
      total = len(results)  # Or fetch total count
      return {"data": {"evidences": results, "pagination": {"page": page, "limit": limit, "total": total}}, "meta": {}}
  ```

### Endpoint 3: Celery Job Monitoring
- Purpose: Check status of jobs (running, progress, failure, completion).
- Method: GET `/jobs/{job_id}` (for single) or GET `/jobs` (for all current).
- Use Celery's `AsyncResult` for status querying.
- Response: Job details with progress (if task supports it via `update_state`).
- Implementation:
  - In Celery tasks, use `current_app.current_task.update_state` for progress.
  - Example:
    ```
    from celery.result import AsyncResult

    class JobStatus(BaseModel):
        id: str
        status: str  # PENDING, STARTED, SUCCESS, FAILURE
        progress: float | None  # 0-100
        result: any | None
        error: str | None

    @app.get("/jobs/{job_id}", response_model=dict)
    async def get_job_status(job_id: str):
        result = AsyncResult(job_id)
        status = JobStatus(id=job_id, status=result.status, progress=result.info.get('progress') if result.info else None,
                           result=result.result if result.successful() else None, error=str(result.info) if result.failed() else None)
        return {"data": status.dict(), "meta": {}}
    ```
- For all jobs: Use `celery.control.inspect()` to list active/reserved tasks.

## 4. Implement Celery Monitoring with Flower
For comprehensive monitoring (beyond API), add Flower—a web-based tool for real-time Celery oversight.

- Install: Add `flower==2.0.1` to `requirements.txt`.
- Add to `docker-compose.yml`:
  ```
  flower:
    build: .
    command: celery -A your_app.celery flower --port=5555
    ports:
      - "5555:5555"
    depends_on:
      - redis
    deploy:
      resources:
        limits:
          cpus: '0.5'
          memory: 256m
  ```
- Access: `http://localhost:5555` for dashboard (view tasks, workers, stats).
- Best Practice: Secure with basic auth (`--basic_auth=user:pass` in command). Scale workers via `docker compose up --scale celery-worker=3`.

## 5. Add Ollama Service to Root Docker Compose
Integrate Ollama for AI/model serving.

- Add to `docker-compose.yml`:
  ```
  ollama:
    image: ollama/ollama:latest
    ports:
      - "11434:11434"
    volumes:
      - ollama-models:/root/.ollama  # Persist models
    deploy:
      resources:
        limits:
          cpus: '1.0'
          memory: 2g  # Adjust for model size
  ```
- Usage: Interact via API (e.g., `http://ollama:11434`). Pull models: `docker exec ollama ollama pull llama2`.
- Test: Ensure FastAPI/Celery can call Ollama endpoints if integrated.

## Final Testing and Deployment
- Local: `docker compose up --build` and test endpoints with curl/Postman.
- Monitor: Use Flower and `docker stats` for monitoring.


















