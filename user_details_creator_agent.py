#this code is to create the user details llm generator
from pydantic_ai import Agent, RunContext
from pydantic import BaseModel, Field
from omegaconf import OmegaConf
from typing import Literal
from typing import List, Dict, Any
from difflib import SequenceMatcher
import os

from utils.logging_helper import get_my_logger
from model_selector import model_api_selector

from selector_tool_agent import clean_text

#! logger
logger = get_my_logger(__name__)

#base model for the inputs
class UserDetailsInput(BaseModel):
    recipe: dict
    evidence: List[Dict[str, Any]]
    user_scenario_list: List[str]

#base model for the outputs
class UserDetailsOutput(BaseModel):
    #! DEV NOTE: Scenarios are currently hardcoded as a Literal for strict validation.
    #TODO: In the future, fetch these dynamically from the DB and use a custom validator 
    #! to ensure the LLM output matches the exact strings in the 'client_scenarios' collection.
    user_scenario: List[str] = Field(
        min_length=5, max_length=5
    )
    height: List[float] = Field(min_length=5, max_length=5,description="List of 5 heights in CM. Must be between 50 and 300.")
    weight: List[float] = Field(min_length=5, max_length=5, description="List of 5 weights in KG. Must be between 20 and 500.")
    age: List[int] = Field(min_length=5, max_length=5,description="List of 5 ages. Must be between 16 and 120.")
    lifestyle : List[Literal['Sedentary', 'Lightly Active', 'Moderately Active', 'Very Active']] = Field(
        min_length=5, max_length=5
    )
    reasoning: List[str] = Field(min_length=5, max_length=5)

    
#!this is the code to create the agent and return the results
def user_details_generator_agent(config_file:OmegaConf)-> Agent:

    logger.info("[User Details Agent] Initializing...")

    #! define the model provider
    model_provider = model_api_selector(config_file)
    #! define the agent with system prompt for role
    agent = Agent(
        model_provider,
        deps_type=UserDetailsInput,
        system_prompt=config_file.agent.base_system_prompt,
        retries=5,
        output_type=UserDetailsOutput
    )

    #! define system prompt for task instructions
    @agent.system_prompt
    def user_details_generator_system_prompt(ctx: RunContext[UserDetailsInput]) -> str:
        
        # Access the combined input data from the context
        meal = ctx.deps.recipe
        ctx_client_scenario_list = ctx.deps.user_scenario_list
        evidence_list = ctx.deps.evidence

        logger.info(f"[User Details Agent] Generating prompt for Recipe: '{meal.get('title', 'Unknown')}' with {len(evidence_list)} evidence items.")

        instruction_prompt = config_file.agent.user_details_generation_prompt.format(
            meal_title=meal["title"],
            meal_description=meal["description"],
            meal_ingredients=meal["ingredients"],
            meal_nutrition_facts=meal["nutrition"],
            client_scenario_list=ctx_client_scenario_list,
            evidence_list=evidence_list
        )
        
        return instruction_prompt
    
    #! define the validator with the fuzzy logic
    @agent.output_validator
    def validate_user_details_output(ctx: RunContext[UserDetailsInput], result:UserDetailsOutput)-> UserDetailsOutput:
        
        official_list = ctx.deps.user_scenario_list
        clean_scenarios = []

        logger.info(f"[User Details Validator] validating {len(result.user_scenario)} generated scenarios...")

        for generated_item in result.user_scenario:
            
            gen_clean = clean_text(generated_item)
            gen_tokens = set(gen_clean.split())
            
            best_match = None
            highest_score = 0.0

            # ---------------------------------------------------------
            # COMPETITIVE SCORING LOOP
            # We check EVERY official scenario and pick the single best one.
            # ---------------------------------------------------------
            for official in official_list:
                off_clean = clean_text(official)
                off_tokens = set(off_clean.split())
                
                if not off_tokens: continue

                # --- SCORE 1: Token Overlap (Precision) ---                
                common_tokens = off_tokens.intersection(gen_tokens)
                token_score = len(common_tokens) / len(off_tokens)

                # --- SCORE 2: Fuzzy Ratio (Backup for Typos) ---
                fuzzy_score = 0.0
                if token_score < 1.0:
                     fuzzy_score = SequenceMatcher(None, gen_clean, off_clean).ratio()

                current_score = max(token_score, fuzzy_score)
                
                if current_score > highest_score:
                    highest_score = current_score
                    best_match = official
                elif current_score == highest_score and highest_score > 0:
                    # Tie-breaker: Pick the one with more words (More specific)
                    if len(off_tokens) > len(clean_text(best_match).split()):
                        best_match = official

            # ---------------------------------------------------------
            # THRESHOLD CHECK
            # ---------------------------------------------------------
            # We only accept the winner if it meets a strict standard.
            # 0.8 means "Almost all words must be there" or "Very close typo"
            if highest_score >= 0.8:
                clean_scenarios.append(best_match)
                if generated_item != best_match:
                     logger.debug(f"[Validator Fixed] '{generated_item[:20]}...' -> '{best_match}' (Score: {highest_score:.2f})")
            else:
                logger.warning(f"[Validator Dropped] '{generated_item}' (Best match: '{best_match}' with Score {highest_score:.2f} - Too Low)")
            
        result.user_scenario = clean_scenarios
        logger.info("[User Details Validator] Validation Complete.")
        return result

    return agent
