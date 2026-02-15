#import needed packages
#load the environment variables
from dotenv import load_dotenv
import os
from bson.objectid import ObjectId

from utils.logging_helper import setup_logfire_logging,get_my_logger
from omegaconf import OmegaConf

# import the mongo db helper functions
from utils.mongo_db_helper import connect_to_mongo_db

from concurrent.futures import ThreadPoolExecutor, as_completed

#!load environment variables
load_dotenv()

#!load the config yaml file
config_file = OmegaConf.load(os.getenv("CONFIG_FILE"))

#!load the logger
logger = get_my_logger(__name__)


# import the mongo db helper functions
from utils.mongo_db_helper import connect_to_mongo_db

from concurrent.futures import ThreadPoolExecutor
import time

from pipeline_func import run_recipe_agentic_context_pipeline

#!set up connection to mongodb
mongo_client = connect_to_mongo_db(
    connection_type="local",
    username=os.getenv("MONGODB_USERNAME"),
    password=os.getenv("MONGODB_PASSWORD")
)

#main trigger function
def main():

    #!logfire logging
    if os.getenv("LOGFIRE_WRITE_TOKEN"):
        setup_logfire_logging(
            api_key=os.getenv("LOGFIRE_WRITE_TOKEN"), 
            app_name="recipe-agent-orchestrator"
        )

    logger.info("[Start] Starting main.py trigger file.")

    #selected database to query
    #!db
    database = mongo_client[os.getenv("MONGO_DB_NAME_Q_GEN")]
    #!collections
    meals_collection = database[os.getenv("MONGO_COLLECTION_NAME_Q_GEN")]

    #get the recipe records
    cursor = meals_collection.find({}, {"_id": 1}).limit(1).skip(0) #!limit and skip for testing, remove for production

    recipe_ids = [str(doc["_id"]) for doc in cursor]

    if not recipe_ids:
        logger.warning("[!] No recipes found. Check your collection name or database.")
        return

    logger.info(f"--- STARTING BATCH PROCESSING FOR {len(recipe_ids)} RECIPES ---")
    start_time = time.time()

    #ThreadPoolExecutor to run them in parallel
    with ThreadPoolExecutor(max_workers=1) as executor:
        # Dictionary to map 'Future' objects back to recipe IDs (for logging)
        future_to_id = {
            executor.submit(run_recipe_agentic_context_pipeline, r_id): r_id 
            for r_id in recipe_ids
        }

        # process_as_completed yields futures as they finish (in any order)
        for future in as_completed(future_to_id):
            r_id = future_to_id[future]
            try:
                #get the return value from pipeline function
                result = future.result()
                logger.info(f"[Success] Recipe {r_id}: {result}")
            except Exception as e:
                # if any workers crashes we log it here
                logger.error(f"[Failed] Recipe {r_id} crashed: {e}")

    duration = time.time() - start_time
    logger.info(f"\n[DONE] Processed {len(recipe_ids)} recipes in {duration:.2f} seconds!")

if __name__ == "__main__":
    main()