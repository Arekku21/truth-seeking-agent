from pydantic_ai import Agent, RunContext
from pydantic import BaseModel, Field
from omegaconf import OmegaConf
from utils.logging_helper import get_my_logger

from model_selector import model_api_selector

#! logger
logger = get_my_logger(__name__)

#base model for the meal input
class MealQueryInput(BaseModel):
    meal_title: str 
    meal_description: str
    meal_ingredients: list = Field(min_length=1)
    meal_nutrition_facts: dict = Field(min_length=1)

# combined basemodel for both meal and client scenarios list input
class CombinedInput(BaseModel):
    meal: MealQueryInput
    client_scenario_list: list[str]

#base model for the query output
class MealQueryOutput(BaseModel):
    queries: list[str] = Field(min_length=5, max_length=5, description="List of exactly 5 queries generated based on the meal information provided.")
    reasoning: list[str] = Field(min_length=5, max_length=5, description="List of exactly 5 reasoning for each one query generated.")

#! query agent creator 
def query_agent_creator(config_file:OmegaConf) -> Agent:

    logger.info(f"[Query Agent] Initializing with model")

    #! define the model provider
    model_provider = model_api_selector(config_file, model_name_override=config_file.agent.query_model_name)

    #! define the agent with system prompt for role
    agent = Agent(
        model_provider,
        deps_type=CombinedInput,
        system_prompt=config_file.agent.base_system_prompt  
    )

    #! define system prompt for task instructions
    @agent.system_prompt
    def query_generator_system_prompt(ctx: RunContext[CombinedInput]) -> str:
        
        # Access the combined input data from the context
        meal = ctx.deps.meal
        ctx_client_scenario_list = ctx.deps.client_scenario_list

        logger.info(f"[Query Agent] Generating prompt for Meal: '{meal.meal_title}'")

        instruction_prompt = config_file.agent.query_generation_prompt.format(
            meal_title=meal.meal_title,
            meal_description=meal.meal_description,
            meal_ingredients=meal.meal_ingredients,
            meal_nutrition_facts=meal.meal_nutrition_facts,
            client_scenario_list=ctx_client_scenario_list
        )
        
        return instruction_prompt

    return agent
