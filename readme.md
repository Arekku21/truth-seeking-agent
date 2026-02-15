# Truth-Seeking Recipe Assignment Agent 

>[Note] The code here is tested on Ollama with the models `gpt-oss:20b` and `qwen3-coder:30b`. The `gpt-oss:20b` is used for any generation for either query or user details. The `qwen3-coder:30b` is used for the more agentic task of selecting the correct valiation links. I found this mix is the best set up. 

## Project Overview

The **Truth-Seeking Recipe Assignment Agent** is an autonomous pipeline designed to validate recipes against specific user health scenarios (e.g., Diabetes, Weight Loss). It utilizes a multi-step agentic process involving Large Language Models (LLMs) and web scraping to:

1.  **Analyze** a recipe from the database.
2.  **Generate** targeted search queries to validate health claims.
3.  **Search** the web for evidence using an agent.
4.  **Validate** and rank the evidence to ensure it supports the health scenario.
5.  **Generate** detailed user personas (User Details) based on the validated findings.

Built with **Python**, **Celery**, **Docker**, **MongoDB**, **Redis**, and **PydanticAI**.

## Quick Start & Setup

### 1. Infrastructure (Docker)
The entire project runs on docker and requires Redis to handle the Celery task queue. A `docker-compose.yml` file is provided.

```bash
docker-compose up -d
```

* **Redis**: Port `6379`
* **MongoDB**: Runs on port `27017` (if using the local container).

### Environment Configuration

Create a `.env` file in the root directory. You must define the following variables for the agents and database connections to function:

```ini
#mongodb configuration if local
MONGODB_USERNAME=<username>
MONGODB_PASSWORD=<password>
MONGO_HOST=<ip address>

#mongo atlas uri if using atlas
MONGO_URI=<mongo_db_atlas_uri>

#Path to your OmegaConf YAML
CONFIG_FILE=<path to your config.yaml>

#queue server
REDIS_URL=<reddis url>

#model provider and logfire for logging
MODEL_PROVIDER_API_KEY=<groq_key/openai/etc>
LOGFIRE_WRITE_TOKEN=<logfire write token>
ENVIRONMENT=development

#mongodb configuration
MONGO_DB_NAME_Q_GEN=recipe_crawler
MONGO_COLLECTION_NAME_Q_GEN=meals

MONGO_DB_NAME_Q_GEN_RESULTS=recipe_crawler
MONGO_COLLECTION_NAME_Q_GEN_RESULTS=query_gen_collection

MONGO_DB_NAME_SEARCH_AGENT_RESULTS=recipe_crawler
MONGO_COLLECTION_NAME_SEARCH_AGENT_RESULTS=search_agent_collection

MONGO_DB_NAME_VALIDATOR_TOOL_RESULTS=recipe_crawler
MONGO_COLLECTION_NAME_VALIDATOR_TOOL_RESULTS=search_agent_collection

MONGO_DB_NAME_VALIDATOR_TOOL_RESULTS=recipe_crawler
MONGO_COLLECTION_EVIDENCE= evidence_collection
MONGO_COLLECTION_USER_SCENARIO=user_scenarios_collection
MONGO_COLLECTION_USER_DETAILS=user_details_collection
MONGO_COLLECTION_COMBINED_OUTPUT=recipe_agent_output_collection
```

### Logfire Authentication & Telemetry Setup

This project uses Logfire to visualize agent traces and debug execution.

This project uses Logfire to visualize agent traces and debug execution.

* **Option A: Local Development** Run logfire auth in your terminal and follow the browser login steps.

* **Option B: Production / Docker** Get a Write Token from logfire.pydantic.dev, and add it to your .env as LOGFIRE_WRITE_TOKEN.

---

### Running the system

1. **Celery worker**

The worker process handles all the needed process of AI generation queries, web searching, validation and storing in the database. This is already automatically started with the docker file. 

But in case you want to change certains things. The celery trigger code is in [Dockerfile](Dockerfile). You might wanna change the concurrency for example of so. 

```bash
#actual code in the Dockerfile
CMD ["celery", "-A", "celery_config", "worker", "--loglevel=info", "--concurrency=8"]

# For Linux/Mac
celery -A celery_config worker --loglevel=info

# For Windows (requires pool=solo)
celery -A celery_config worker --loglevel=info --pool=solo
```

### **Pipeline trigger**

To start processing recipes, run the main entry point. This script fetches recipes from MongoDB and dispatches tasks to the Celery workers.

```bash
python main.py
```

## Module Guide

### Pipeline Orchestrator (`pipeline_func.py`)

* **Role**: This module manages the flow of data.
* **Logic**: It triggers the `query_genaration` task, waits for the result, then "fans out" multiple `search_agent` tasks in parallel. It waits for all searches to complete before triggering the `validator_agent` and finally the `user_details_gen_agent_worker`.

### Query Generator (`query_generator.py`)

* **Role**: Analyzes the recipe title, description, ingredients, and nutrition.
* **Output**: Produces a strict list of 5 search queries aimed at validating the recipe against specific client scenarios.

### Search Agent (`search_agent.py`)

* **Role**: Performs web searches using `DDGS` (DuckDuckGo).
* **Output**: Returns a list of 10 links to websites accoridng to the generated search query and the given reason. 

### Validator Tool (`selector_tool_agent.py`)

* **Role**: This module is responsible for checking if the given links are reachable, contains the search notes from DDGS and selects only the top 5 that are closest to their query using BM25 algorithm. 
* **Output**: Returns the top-ranked, validated evidence.

### User Details Creator (`user_details_creator_agent.py`)

* **Role**: Generates a specific user persona fit the fits the scenarios and evidences.
* **Output**: Generates attributes like height, weight, age, and lifestyle based on the findings.

## Troubleshooting & Notes

* **Ollama "NIL" Responses:**: There is this error where Ollama returns a `<NIL>` or an empty response. This issue is cause of an issue where sometimes the model uses the tool without saying anything. This is not accepted by Pydantic-AI so it crashes.
    * **Fix**: Try using `VLLM` instead of `Ollama` for hosting local models. Other solution is use cloud APIs or use a stronger model and force it to say something before using a tool. 

* **MongoDB Connection**: The `utils/mongo_db_helper.py` script defaults to `localhost:27017`. If connecting to MongoDB Atlas, ensure you set `connection_type="cloud"` and provide the URI in the helper call.

* **Config File**: The application relies heavily on `OmegaConf`. Ensure the `config.yaml` file (pointed to by your `.env`) exists and contains the necessary prompt templates (`agent.base_system_prompt`, etc.).

* **Search Failures**: If the `search_agent` fails frequently, it may be due to the `MIN_REQUIRED` validation check. If the LLM cannot find enough valid links for a difficult query, it will retry until it hits the limit.

* **Scraping Issues**: The `selector_tool_agent` uses `trafilatura`. Some websites block scrapers, which will result in "Cannot reach webpage" logs. This is expected behavior and handled by the try/except blocks.
