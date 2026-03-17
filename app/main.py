from fastapi import FastAPI, HTTPException, Body, Query, BackgroundTasks
from pydantic import BaseModel, Field
from datetime import datetime
from bson.objectid import ObjectId
from typing import List, Optional, Any
from dotenv import load_dotenv
import os

import pymongo

from utils.logging_helper import get_my_logger
from omegaconf import OmegaConf
from utils.mongo_db_helper import connect_to_mongo_db

# Import your celery tasks and app
from celery.result import AsyncResult
from celery_config import app as celery_app
from worker_process import (
    query_genaration, 
    search_agent, 
    validator_agent, 
    user_details_gen_agent_worker,
    run_recipe_agentic_context_pipeline
)

from fastapi import APIRouter
import redis
import json

# --- CONFIGURATION & SETUP ---
load_dotenv()
config_file = OmegaConf.load(os.getenv("CONFIG_FILE"))
logger = get_my_logger(__name__)

mongo_db_client = connect_to_mongo_db(
    connection_type="local",
    username=os.getenv("MONGODB_USERNAME"),
    password=os.getenv("MONGODB_PASSWORD")
)

# Initialize FastAPI
app = FastAPI(title="Recipe Agent API", version="1.0")
redis_client = redis.Redis.from_url(os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0"))

# --- PYDANTIC MODELS (Standardized Responses) ---
class Meta(BaseModel):
    status: str = "success"
    message: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class SuccessResponse(BaseModel):
    data: Any
    meta: Meta = Meta()

class ErrorDetail(BaseModel):
    code: int
    message: str
    details: Optional[dict] = None

class ErrorResponse(BaseModel):
    error: ErrorDetail
    meta: Meta = Meta(status="error")

# Request Model for Batch Processing
class RecipeRequest(BaseModel):
    recipe_ids: List[str] = Field(..., description="List of Recipe IDs to process")

class JobStatus(BaseModel):
    id: str
    status: str  # PENDING, STARTED, SUCCESS, FAILURE
    progress: float | None  # 0-100
    result: Any | None
    error: str | None

PROGRESS_MAP = {
    "1/4: Generating Queries": 10.0,
    "2/4: Searching Evidences": 40.0,
    "3/4: Validating evidences": 70.0,
    "4/4: Generating user details from evidences": 90.0,
    "COMPLETE": 100.0
}

# --- ENDPOINT 1: BATCH PROCESSING ---
@app.post("/api/v1/batch-process", response_model=SuccessResponse)
async def batch_process(request: RecipeRequest = Body(...)):
    """
    Triggers the agentic pipeline for a batch of recipe IDs asynchronously.
    """
    job_ids = []
    
    for r_id in request.recipe_ids:
        try:
                   
            task = run_recipe_agentic_context_pipeline.delay(r_id)
            job_ids.append({"recipe_id": r_id, "job_id": r_id})
            logger.info(f"Queued pipeline for recipe {r_id}. Tracking ID: {r_id}")
            
        except Exception as e:
            logger.error(f"Failed to queue recipe {r_id}: {e}")
            
    return SuccessResponse(
        data={"queued_jobs": job_ids},
        meta=Meta(message=f"Successfully queued {len(job_ids)} recipes for processing.")
    )


# --- ENDPOINT 2: FETCH EVIDENCE (Paginated) ---
@app.get("/api/v1/evidences", response_model=SuccessResponse)
async def fetch_evidence(
    page: int = Query(1, ge=1), 
    limit: int = Query(10, ge=1, le=100), 
    search: Optional[str] = Query(None), 
    filter_scenario: Optional[str] = Query(None)
):
    """
    Retrieve evidences from MongoDB with pagination and filtering.
    """
    try:
        database = mongo_db_client[os.getenv("MONGO_DB_NAME_VALIDATOR_TOOL_RESULTS")]
        collection = database[os.getenv("MONGO_COLLECTION_EVIDENCE")]

        collection.create_index([("$**", pymongo.TEXT)], name="GlobalTextSearch")
        collection.create_index("scenario", name="scenario_filter_index")
        
        query = {}
        
        # Add Full-Text Search (if provided and not empty)
        if search and search.strip():
            query["$text"] = {"$search": search.strip()} 
            
        # Add Exact Match Filter (if provided and not empty)
        if filter_scenario and filter_scenario.strip():
            query["scenario"] = filter_scenario.strip()
            
        # --- 2. CALCULATE PAGINATION ---
        skip = (page - 1) * limit
        
        # --- 3. FETCH DATA ---
        cursor = collection.find(query).skip(skip).limit(limit)
        
        # --- 4. MAP TO RESPONSE MODEL ---
        results = []
        for doc in cursor:
            # We map your MongoDB fields to the requested 'EvidenceResponse' format
            results.append({
                "id": str(doc.get("_id")), 
                "recipe_id": str(doc.get("related_recipe_record_id", "")),
                "details": doc.get("reasoning", "No details provided."), # Mapping reasoning to details
                "scenario": doc.get("scenario", "Unknown")
            })
            
        total_count = collection.count_documents(query)
        
        return {
            "data": {
                "evidences": results,
                "pagination": {
                    "page": page,
                    "limit": limit,
                    "total": total_count,
                    "total_pages": (total_count + limit - 1) // limit if limit > 0 else 0
                }
            },
            "meta": {
                "message": "Evidences retrieved successfully."
            }
        }
    
    except Exception as e:
        logger.error(f"Error fetching evidences: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/jobs/{job_id}", response_model=dict)
async def get_job_status(job_id: str):
    # Fetch the state from Redis in ~1 millisecond
    raw_data = redis_client.get(f"pipeline_status:{job_id}")
    
    if not raw_data:
        # If it's not in Redis, it's either in the queue waiting to start, or invalid
        status = JobStatus(id=job_id, status="PENDING", progress=0.0, result=None, error=None)
        return {"data": status.dict(), "meta": {}}
        
    # Parse the Redis JSON
    status_data = json.loads(raw_data)
    
    internal_stage = status_data.get("current_stage", "")
    internal_status = status_data.get("status", "PROCESSING")
    error_msg = status_data.get("error_message")
    
    # Calculate progress based on our map
    progress = PROGRESS_MAP.get(internal_stage, 0.0)
    
    # Translate our internal status to the Celery terms the client expects
    client_status = "STARTED"
    if internal_status == "SUCCESS":
        client_status = "SUCCESS"
        progress = 100.0
    elif internal_status == "FAILED" or internal_status == "FAILURE":
        client_status = "FAILURE"
        
    # Optional: Send back a nice result payload on success
    result_payload = "Pipeline Finished Successfully" if client_status == "SUCCESS" else None

    # Build the final response using the client's model
    status = JobStatus(
        id=job_id, 
        status=client_status, 
        progress=progress,
        result=result_payload, 
        error=error_msg
    )
    
    return {"data": status.dict(), "meta": {}}

@app.get("/api/v1/jobs", response_model=dict)
async def get_all_jobs_status():
    """
    Fetches the status of all active and recently completed jobs.
    Uses Redis MGET for blazing fast retrieval.
    """
    try:
        # 1. Find all keys in Redis that start with our tracking prefix
        # (Note: In massive production systems, you'd use scan_iter, but keys() is fine here 
        # since we set them to expire after 24 hours).
        keys = redis_client.keys("pipeline_status:*")
        
        if not keys:
            return {"data": [], "meta": {"message": "No active jobs found.", "total": 0}}
            
        # 2. Grab all the JSON payloads at once in a single Redis trip
        raw_data_list = redis_client.mget(keys)
        
        jobs_list = []
        
        # 3. Loop through and format them exactly like the single endpoint
        for raw_data in raw_data_list:
            if not raw_data:
                continue
                
            status_data = json.loads(raw_data)
            
            job_id = status_data.get("recipe_id")
            internal_stage = status_data.get("current_stage", "")
            internal_status = status_data.get("status", "PROCESSING")
            error_msg = status_data.get("error_message")
            
            progress = PROGRESS_MAP.get(internal_stage, 0.0)
            
            client_status = "STARTED"
            if internal_status == "SUCCESS":
                client_status = "SUCCESS"
                progress = 100.0
            elif internal_status in ["FAILED", "FAILURE"]:
                client_status = "FAILURE"
                
            result_payload = "Pipeline Finished Successfully" if client_status == "SUCCESS" else None
            
            # Use the Pydantic model to guarantee the schema
            job_status = JobStatus(
                id=job_id, 
                status=client_status, 
                progress=progress,
                result=result_payload, 
                error=error_msg
            )
            jobs_list.append(job_status.dict())
            
        return {
            "data": jobs_list, 
            "meta": {"total": len(jobs_list), "message": "Successfully retrieved all jobs."}
        }
        
    except Exception as e:
        logger.error(f"Failed to fetch all jobs from Redis: {e}")
        raise HTTPException(status_code=500, detail="Internal server error while fetching jobs.")