#import needed packages
from dotenv import load_dotenv
import os
from bson.objectid import ObjectId

from utils.logging_helper import get_my_logger
from omegaconf import OmegaConf

# import the mongo db helper functions
from utils.mongo_db_helper import connect_to_mongo_db

from worker_process import query_genaration, search_agent, validator_agent,user_details_gen_agent_worker

#load environment variables
load_dotenv()

#!load the config yaml file
config_file = OmegaConf.load(os.getenv("CONFIG_FILE"))

#! logger
logger = get_my_logger(__name__)

#!shared mongo_db_client
mongo_db_client = connect_to_mongo_db(
    connection_type="local",
    username=os.getenv("MONGODB_USERNAME"),
    password=os.getenv("MONGODB_PASSWORD")
)

def run_recipe_agentic_context_pipeline(recipe_id:str)->str:

    logger.info(f"--- STARTING PIPELINE FOR RECIPE: {recipe_id} ---")

    try:

        #!database
        database = mongo_db_client[os.getenv("MONGO_DB_NAME_Q_GEN")]
        user_scenario_collection = database[os.getenv("MONGO_COLLECTION_USER_SCENARIO")]
        scenario_list = user_scenario_collection.distinct("user_scenario")

        # --- STEP 1: Generate Queries ---
        logger.info("[1] Generating Queries...")
        query_gen_result = query_genaration.delay(recipe_id,scenario_list).get()

        #retrieve the record to get the queries
        q_gen_collection = database[os.getenv("MONGO_COLLECTION_NAME_Q_GEN_RESULTS")]
        record = q_gen_collection.find_one({"_id": ObjectId(query_gen_result)})
        
        logger.info(f"-> Generated {len(record.get("generated_query"))} queries.")

        # --- STEP 2: Fan-Out Search (The Loop) ---
        logger.info("[2] firing Search Agents (Parallel)...")
        search_tasks_context = []

        #loop thru the generated queries
        for idx in range(len(record.get("generated_query"))):

            #get the recorded recipe id
            recipe_record_id = str(record.get("related_recipe_id"))

            #get the recipe_qgen_id
            recipe_q_gen_id = str(record.get("_id"))

            #get the search query
            search_query = record.get("generated_query")[idx]

            #get the reasoning
            reasoning = record.get("generated_query_reasoning")[idx]

            #send the task in parallel
            task = search_agent.delay(
                recipe_record_id = recipe_record_id,
                recipe_qgen_record_id = recipe_q_gen_id,
                search_query = search_query,
                reasoning = reasoning
            )

            search_tasks_context.append({
                "task_handle": task,
                "query": search_query,
                "reasoning": reasoning
            })

            logger.info(f"-> Fired task {task.id} for query: '{search_query[:30]}...'")

        # --- The Barrier: Wait for all searches to finish ---
        logger.info("[2.5] Waiting for searches to complete...")
        search_result_ids = []
        
        for context in search_tasks_context:
            task = context["task_handle"]
            query_text = context["query"]

            try:
                result_id = task.get()
                search_result_ids.append(result_id)
            except Exception as e:
                logger.error(
                    f"[SEARCH FAILED] Task ID: {task.id} \n"
                    f"FAILED QUERY: '{query_text}' \n"
                    f"ERROR: {e}"
                )
                # raise RuntimeError(f"[!] One search task failed: {e}")
                continue

        if not search_result_ids:
            logger.error("[Error] All searches failed. Aborting pipeline.")
            raise RuntimeError("[Error] All searches failed. Aborting pipeline.")
            

        logger.info(f"-> Collected {len(search_result_ids)} search results.")

        # --- STEP 3: Validator ---
        logger.info("[3] Running Validator...")
        validator_task = validator_agent.delay(query_gen_result).get()
        
        logger.info(f" -> Validator finished. Combined Record: {validator_task}")

        # --- STEP 4: User Details ---
        logger.info("[4] Generating User Details...")
        final_task = user_details_gen_agent_worker.delay(validator_task).get()

        logger.info(f"--- PIPELINE COMPLETE! Final result stored in: {final_task} ---")

        return final_task
    
    except Exception as e:
        logger.error(f"[PIPELINE FAILED] Recipe {recipe_id} failed: {e}", exc_info=True)
        raise e