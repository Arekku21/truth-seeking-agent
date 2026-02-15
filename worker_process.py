#import needed packages
#load the environment variables, conifg file
from dotenv import load_dotenv
import os
from omegaconf import OmegaConf
import datetime

from celery_config import app 
from celery import Task

from pydantic_ai.exceptions import UnexpectedModelBehavior

#celery imports
from celery import Celery
from celery import shared_task, Task
from celery.utils.log import get_task_logger
from celery import Task

#import mongodb helper
from utils.mongo_db_helper import connect_to_mongo_db
from bson.objectid import ObjectId

from celery.exceptions import SoftTimeLimitExceeded
from pydantic_ai.exceptions import AgentRunError, ModelHTTPError

#logging
import logfire
from utils.logging_helper import setup_logfire_logging,get_my_logger

#import the query agent creator
from query_generator import query_agent_creator, MealQueryInput,MealQueryOutput,CombinedInput

#import the search agent creator
from search_agent import search_agent_creator, SearchQueryReasoningInput, SearchAgentOutput

#import thr validator tools
from selector_tool_agent import validator_tool

#import the user details generator
from user_details_creator_agent import UserDetailsInput, UserDetailsOutput, user_details_generator_agent

#!load environment variables
load_dotenv()

#!load the config yaml file
config_file = OmegaConf.load(os.getenv("CONFIG_FILE"))

if os.getenv("LOGFIRE_WRITE_TOKEN"):
    setup_logfire_logging(
        api_key=os.getenv("LOGFIRE_WRITE_TOKEN"), 
        app_name="recipe-agent-worker"
    )

#! logger
logger = get_my_logger(__name__)

#!shared mongo_db_client
mongo_db_client = connect_to_mongo_db(
    connection_type="local",
    username=os.getenv("MONGODB_USERNAME"),
    password=os.getenv("MONGODB_PASSWORD")
)

logger.info("Worker process started!")

#! task to create queries
@app.task(bind=True)
def query_genaration(self:Task,recipe_record_id:str,client_scenarios:list[str]) -> str:

    logger.info(f"\n\n[Start] Query generation for ID: {recipe_record_id}")

    #!variable to return
    record_id = ""

    #retrieve the record
    try:
        #selected database to query the recipes
        database = mongo_db_client[os.getenv("MONGO_DB_NAME_Q_GEN")]
        meals_collection = database[os.getenv("MONGO_COLLECTION_NAME_Q_GEN")]
        record = meals_collection.find_one({"_id": ObjectId(recipe_record_id)})

        if not record:
            raise ValueError(f"Record not found: {recipe_record_id}")
        
        logger.info(f"Record found: {record.get("title")}")

        #put the needed info in the pydantic dataclass
        meal_input = MealQueryInput(
            meal_title=record.get("title"),
            meal_description=record.get("description"),
            meal_ingredients=record.get("ingredients"),
            meal_nutrition_facts=record.get("nutrition")
        )

        #put in the combined output
        combined_input = CombinedInput(
            meal=meal_input,
            client_scenario_list=client_scenarios
        )

        logger.info(f"Creating agent to run for record: {record.get("title")}")

        #! create the query agent
        query_agent = query_agent_creator(config_file=config_file)

        logger.info(f"Agent to run for record: {record.get("title")}")

        #run the agent
        result = query_agent.run_sync(
            "Generate queries for the meal and client scenario",deps=combined_input, 
            output_type=MealQueryOutput
        )

        logger.info(f"Result created for record: {record.get("title")}")

        #easier access
        result_clean = result.output

        #! add the item to the collection in mongodb
        #select the collection
        query_gen_collection = database[os.getenv("MONGO_COLLECTION_NAME_Q_GEN_RESULTS")] 

        #fix the result
        new_record = {
            "related_recipe_id" : ObjectId(recipe_record_id),
            "generated_query" : result_clean.queries,
            "generated_query_reasoning" : result_clean.reasoning,
            "created_at": datetime.datetime.now(datetime.timezone.utc)
        }

        #insert to db
        result_db_insert = query_gen_collection.insert_one(new_record)

        logger.info(f"[Success] Record inserted with id: {result_db_insert.inserted_id}")

        record_id = str(result_db_insert.inserted_id)

    except Exception as e:
        logger.error(f"[Error] Failed processing {recipe_record_id}: {e}", exc_info=True)

        raise e

    return record_id


#! task to search the internet evidences
@app.task(
    bind=True,
    autoretry_for=(AgentRunError, ModelHTTPError, ValueError, AttributeError),
    retry_kwargs={'max_retries': 3},
)
def search_agent(self:Task,recipe_record_id:str,recipe_qgen_record_id:str,search_query:str,reasoning:str) -> str:

    logger.info(f"\n\n[Start] Search evidences for ID: {recipe_qgen_record_id} with query: {search_query}")

    #!variable to return
    record_id = ""

    try:
        logger.info(f"Creating agent to run for record id: {recipe_qgen_record_id} with query: {search_query}")

        #! create the search query agent
        agent = search_agent_creator(config_file)

        #create the pydantic class for input
        search_input =SearchQueryReasoningInput(
            query=search_query,
            reasoning=reasoning
        )

        logger.info(f"Agent to search for record id: {recipe_qgen_record_id} with query: {search_query}")

        result = agent.run_sync(
            'Generate the list of 10 sources with reasoning and links for the given query and reasoning',
            deps=search_input
        )

        logger.info(f"Agent search result for record id: {recipe_qgen_record_id} with query: {search_query}\n\n {result}")

        #result variable to make it easier
        result_clean = result.output

        if not result_clean.results:
            logger.warning(f"Search Agent returned 0 valid links for query: '{search_query}'. Retrying task...")
            
            # 3. TRIGGER RETRY
            # verify we haven't hit max_retries yet
            if self.request.retries < self.max_retries:
                # This throws a special exception that restarts the task
                # countdown=2 waits 2 seconds before trying again
                raise self.retry(exc=ValueError("Empty Result"), countdown=2)
            else:
                logger.error("Max retries reached. Giving up on this query.")
                return "Failed: No valid links found after retries."

        #! add the item to the collection in mongodb
        database = mongo_db_client[os.getenv("MONGO_DB_NAME_SEARCH_AGENT_RESULTS")]
        search_agent_results_collection = database[os.getenv("MONGO_COLLECTION_NAME_SEARCH_AGENT_RESULTS")] 

        #adjust the code to the new basemodel
        source_list = []
        source_link_list = []
        reasoning_list = []
        source_content_list = []

        result_list = result_clean.results

        for item in result_list:

            source_list.append(item.title or "Unknown Title")
            source_link_list.append(item.link)
            reasoning_list.append(item.reasoning or "No reasoning provided")
            source_content_list.append(item.content or "")
                         

        #fix the result
        new_record = {
            "related_qgen_id" : ObjectId(recipe_qgen_record_id),
            "related_recipe_record_id" : ObjectId(recipe_record_id),
            "source" : source_list,
            "source_link" : source_link_list,
            "reasoning" : reasoning_list,
            "source_content" : source_content_list,
            "search_query" : search_query,
            "created_at": datetime.datetime.now(datetime.timezone.utc)
        }

        #insert to db
        result_db_insert = search_agent_results_collection.insert_one(new_record)

        logger.info(f"[Success] Record inserted with id: {result_db_insert.inserted_id}")

        record_id = str(result_db_insert.inserted_id)

    except (UnexpectedModelBehavior, ValueError) as e:
        # This catches:
        # 1. UnexpectedModelBehavior: Agent failed validation 5 times (hallucination or format error)
        # 2. ValueError: We manually raised this if list was empty
        
        logger.warning(f"[Retry Trigger] Agent failed validation or returned empty results: {e}. Retrying task in 2s...")
        
        # This forces Celery to retry the entire task
        raise self.retry(exc=e, countdown=2)

    except Exception as e:
        logger.error(f"[Critical Error] Failed processing {recipe_qgen_record_id}: {e}", exc_info=True)

        raise self.retry(exc=e)

    return record_id

#! task to select the top 10 from the all the sources searched
@app.task(bind=True)
def validator_agent(self:Task,query_gen_result_id:str) -> str:

    logger.info(f"\n\n[Start] Validating evidences for ID: {query_gen_result_id} ")

    #!variable to return
    combined_input_id = ""

    try:
        #selected database to query the recipes
        database = mongo_db_client[os.getenv("MONGO_DB_NAME_VALIDATOR_TOOL_RESULTS")]
        search_agent_results_collection = database[os.getenv("MONGO_COLLECTION_NAME_SEARCH_AGENT_RESULTS")]

        cursor = search_agent_results_collection.find({
            "related_qgen_id": ObjectId(query_gen_result_id)
        }).limit(100) #!please change this in the future if more records are needed

        doc_count = search_agent_results_collection.count_documents({"related_qgen_id": ObjectId(query_gen_result_id)})

        if doc_count == 0:
            raise ValueError(f"No records found for ID: {query_gen_result_id}")
        
        logger.info(f"Amount of records found: {doc_count}")

        #!list of Evidence_validator_input for ranking and validation
        list_of_evidence_validator_input_records = []

        #! dict of similar query for ranking 
        dict_of_records = {}

        #! unique list of search queries
        unique_s_queries = search_agent_results_collection.distinct(
            "search_query", 
            {"related_qgen_id": ObjectId(query_gen_result_id)}  # Optional Filter
        )

        #create the empty list with search query as the key
        for unique_s_query in unique_s_queries:

            dict_of_records[unique_s_query] = []

        #sort the records into their search query
        for record in cursor:

            q_key = record.get("search_query")

            if q_key in dict_of_records:

                for idx in range(len(record.get("source_link"))):

                    individual_record = {
                        "related_qgen_id": record.get("related_qgen_id"),
                        "related_recipe_record_id": record.get("related_recipe_record_id"),
                        "source": record.get("source")[idx],
                        "source_link":record.get("source_link")[idx],
                        "reasoning":record.get("reasoning")[idx],
                        "source_content":record.get("source_content")[idx],
                        "search_query":q_key
                    }

                    dict_of_records[q_key].append(individual_record)

            else:
                if "Misc" not in dict_of_records: dict_of_records["Misc"] = []
                dict_of_records["Misc"].append(record)


        final_selection = []
        spare_pool = []

        # Track URLs we have already chosen to prevent duplicates
        seen_urls = set()

        #!cleaned records dict
        for search_q_key, record_value in dict_of_records.items():

            logger.info(f"Validating group: {search_q_key} ({len(record_value)} items)")

            validated_list = validator_tool(search_q_key, record_value)

            if validated_list:
                # Try to find a winner that we haven't seen yet
                winner = None
                for item in validated_list:
                    if item["source_link"] not in seen_urls:
                        winner = item
                        break
                
                # If all items were duplicates, just take the first one (we will filter it later) 
                # or better, skip it and use spares. Let's just take the first unique if possible.
                if not winner:
                    # Fallback: If the top result is a duplicate, we usually prefer to NOT add it again 
                    # and instead let the "spare pool" fill this slot with a unique link later.
                    logger.info(f"Skipping duplicate winner for query '{search_q_key}'")
                else:
                    final_selection.append(winner)
                    seen_urls.add(winner["source_link"])

                # Add everything else to spare pool
                if len(validated_list) > 0:
                    for item in validated_list:
                        # Don't add the winner we just picked to spares
                        if winner and item["source_link"] == winner["source_link"]:
                            continue
                        spare_pool.append(item)

            else:
                logger.warning(f"Query '{search_q_key}' had NO valid results.")

        #! Fill missing slots with UNIQUE spares
        # Recalculate missing slots based on unique final_selection
        missing_slots = len(unique_s_queries) - len(final_selection)
        
        if missing_slots > 0 and spare_pool:
            logger.info(f"Filling {missing_slots} missing slots from spare pool.")
            
            # Sort spares globally by their score 
            spare_pool.sort(key=lambda x: x.get("bm25_score", 0), reverse=True)
            
            filled_count = 0
            for filler in spare_pool:
                if filled_count >= missing_slots:
                    break
                
                # CRITICAL CHECK: Is this spare link already in our selection?
                if filler["source_link"] not in seen_urls:
                    filler["selection_status"] = "Fallback Result"
                    final_selection.append(filler)
                    seen_urls.add(filler["source_link"])
                    filled_count += 1

        logger.info(f"Results validated. Attempting to add to database")

        #! this is where we add all the items into the database
        #itll be messy coz its the final addtions of the agents above.
        # database remains the same as above just need to put the collections

        #!collections to put items into
        # search_agent_results_collection = database[os.getenv("MONGO_COLLECTION_NAME_SEARCH_AGENT_RESULTS")]
        evidence_collection = database[os.getenv("MONGO_COLLECTION_EVIDENCE")]
        combined_agents_output_collection = database[os.getenv("MONGO_COLLECTION_COMBINED_OUTPUT")]

        #this will be the final output
        new_record_combined_out = {
            "recipe_id":ObjectId(final_selection[0]["related_recipe_record_id"]),
            "evidence_id_list":[]
        }

        #loop thru the selected evidences to add to evidence collection
        for evidence in final_selection:

            new_evidence_record = evidence.copy()

            #check in case this link has been added before
            evidence_source_link = new_evidence_record["source_link"]

            existing_record = evidence_collection.find_one({
                "source_link": evidence_source_link
            })

            evidence_id = None

            if not existing_record:

                #remove the id from this record
                if "_id" in new_evidence_record:
                    del new_evidence_record["_id"]

                #remove the related qgen_id not neeed
                if "related_qgen_id" in new_evidence_record:
                    del new_evidence_record["related_qgen_id"]

                #fixed the numpy issue
                if "bm25_score" in new_evidence_record:
                    raw_score = new_evidence_record["bm25_score"]
                    new_evidence_record["bm25_score"] = round(float(raw_score), 3)

                #add the new fields
                #if the validator picked it means that 200 worked and source matched either fuzzy or exact
                new_evidence_record["link_status"] = 200
                new_evidence_record["link_notes_in_content"] = True

                #add to the evidence collection
                new_evidence_record_inserted = evidence_collection.insert_one(new_evidence_record)
                evidence_id = str(new_evidence_record_inserted.inserted_id)

                logger.info(f"[Success] Evidence record inserted with id: {evidence_id}")
            else:
                evidence_id = str(existing_record["_id"])

                logger.info(f"Existing evidence record found with id: {evidence_id}")

            #add into the evidence list of the combined output
            new_record_combined_out["evidence_id_list"].append(evidence_id)

        #check in case combined output has this before
        target_recipe_id = new_record_combined_out["recipe_id"]
        target_evidence_ids = [ObjectId(id_str) for id_str in new_record_combined_out["evidence_id_list"]]

        existing_combined_record = {
            "recipe_id": target_recipe_id,
            "evidence_id_list": {
                "$size": len(target_evidence_ids),  # Length must match
                "$all": target_evidence_ids         # Must contain all these items
            }
        }

        existing_record = combined_agents_output_collection.find_one(existing_combined_record)

        if not existing_record:

            combined_output_inserted = combined_agents_output_collection.insert_one(new_record_combined_out)

            logger.info(f"[Success] Combined output inserted with id: {str(combined_output_inserted.inserted_id)}")

            combined_input_id = str(combined_output_inserted.inserted_id)

        else:
            logger.info(f"Duplicate record found with id: {str(existing_record["_id"])}")
            combined_input_id = str(existing_record["_id"])

    except Exception as e:
        logger.error(f"[Error] Failed processing {query_gen_result_id}: {e}", exc_info=True)

        raise e

    return combined_input_id


#! task to create the user details based on the recipe, evidences and list of user scenarios
@app.task(bind=True)
def user_details_gen_agent_worker(self:Task,combined_input_id:str) -> str:

    #! variable to return
    combined_input_id_updated = ""

    logger.info(f"\n\n[Start] User details generation for ID: {combined_input_id}")

    #!variable to return
    record_id = ""

    #retrieve the record
    try:

        logger.info(f"Querying needed info for record: {combined_input_id}")
        
        #selected database to query the recipes
        database = mongo_db_client[os.getenv("MONGO_DB_NAME_Q_GEN")]
        combined_output_collection = database[os.getenv("MONGO_COLLECTION_COMBINED_OUTPUT")]
        record = combined_output_collection.find_one({"_id": ObjectId(combined_input_id)})

        if not record:
            raise ValueError(f"Record not found: {combined_input_id}")
        
        logger.info(f"Record found: {record.get("_id")}")

        #find the recipe record
        recipe_id_to_query = record.get("recipe_id")
        recipe_collection = database[os.getenv("MONGO_COLLECTION_NAME_Q_GEN")]
        recipe_record = recipe_collection.find_one({"_id": ObjectId(recipe_id_to_query)})

        #get the list of actual evidence not just the ids
        list_of_evidence_ids = record.get("evidence_id_list")
        evidence_collection = database[os.getenv("MONGO_COLLECTION_NAME_Q_GEN")]

        cursor = evidence_collection.find({
            "_id": {"$in": list_of_evidence_ids}
        })

        evidence_list = list(cursor)

        #get the user scenario list
        user_scenarios_collection = database[os.getenv("MONGO_COLLECTION_USER_SCENARIO")]
        list_of_user_scenarios = user_scenarios_collection.distinct("user_scenario")

        #put the data into the pydantic dataclass
        user_details_input = UserDetailsInput(
            recipe=recipe_record,
            evidence=evidence_list,
            user_scenario_list=list_of_user_scenarios
        )

        logger.info(f"Create agent to run for record: {combined_input_id}")

        #!create the user detail gen agent
        user_details_agent = user_details_generator_agent(config_file)

        logger.info(f"Agent to run for record: {combined_input_id}")
        
        #run the agent
        result = user_details_agent.run_sync(
            "Generate 5 user details for the meal, client scenario and evidences",
            deps=user_details_input
        )

        logger.info(f"Result created for record: {combined_input_id}")

        #easier access
        result_clean = result.output

        #! add the item to the collection in mongodb
        #select the collection
        # combined_output_collection
        user_details_collection = database[os.getenv("MONGO_COLLECTION_USER_DETAILS")]

        #list of ids of added user_details
        list_of_user_details_ids_added = []

        #add the user details record first
        for idx in range(len(result_clean.user_scenario)):

            new_user_details_record = {
                "height": result_clean.height[idx],
                "weight": result_clean.weight[idx],
                "age": result_clean.age[idx],
                "lifestyle": result_clean.lifestyle[idx],
                "reasoning": result_clean.reasoning[idx]
            }

            insert_new_user_details = user_details_collection.insert_one(new_user_details_record)

            #get the ids
            list_of_user_details_ids_added.append(insert_new_user_details.inserted_id)

        #! now to add to combined output collection 

        #fix the user_scenarios and just get the ids
        llm_chosen_scenarios_list_unique = list(set(result_clean.user_scenario))

        cursor = user_scenarios_collection.find(
            {"user_scenario": {"$in": llm_chosen_scenarios_list_unique}},
            {"_id": 1}         
        )

        data_to_add_combined_output = {
            "user_details" : list_of_user_details_ids_added,
            "user_scenarios" : list(cursor)
        }

        result = combined_output_collection.update_one(
            {"_id": ObjectId(combined_input_id)}, 
            {"$set": data_to_add_combined_output}
        )

        if result.matched_count == 0:
            logger.error(f"[Error] No record found with ID: {combined_input_id}")
            raise ValueError(f"Failed to update: Record {combined_input_id} does not exist.")
        elif result.modified_count > 0:
            logger.info(f"[Success] Record updated successfully. ID: {combined_input_id}")
        else:
            logger.info(f"[Info] Record found (ID: {combined_input_id}), but no changes were needed (data was identical).")

    except Exception as e:
        logger.error(f"[Error] Failed processing {combined_input_id}: {e}", exc_info=True)

        raise e

    return combined_input_id