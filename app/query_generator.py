from pydantic_ai import Agent, RunContext
from pydantic import BaseModel, Field
from pydantic_ai.exceptions import ModelRetry
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

class SingleQueryMapping(BaseModel):
    query: str = Field(description="The search query.")
    reasoning: str = Field(description="Why this query fits the scenario.")
    scenario: str = Field(description="The exact scenario name from the input list.")

#base model for the query output
class MealQueryOutput(BaseModel):
    mappings: list[SingleQueryMapping] = Field(
        min_length=5, max_length=5, 
        description="List of 5 mappings containing query, reasoning, and the linked scenario."
    )

    @property
    def queries(self) -> list[str]:
        return [m.query for m in self.mappings]

    @property
    def reasoning(self) -> list[str]:
        return [m.reasoning for m in self.mappings]
    
    @property
    def scenarios(self) -> list[str]:
        return [m.scenario for m in self.mappings]
    

#! query agent creator 
def query_agent_creator(config_file:OmegaConf) -> Agent:

    logger.info(f"[Query Agent] Initializing with model")

    #! define the model provider
    model_provider = model_api_selector(config_file, model_name_override=config_file.agent.query_model_name)

    #! define the agent with system prompt for role
    agent = Agent(
        model_provider,
        deps_type=CombinedInput,
        system_prompt=config_file.agent.base_system_prompt,
        output_type=MealQueryOutput,  
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
    
    @agent.output_validator
    def validate_scenarios(ctx: RunContext[CombinedInput], result: MealQueryOutput) -> MealQueryOutput:
        allowed = ctx.deps.client_scenario_list
        for m in result.mappings:
            if m.scenario not in allowed:
                # If it's a small typo, we could fix it, but raising ModelRetry 
                # tells the LLM: "You messed up, look at the list again."
                raise ModelRetry(
                    f"Invalid scenario '{m.scenario}'. "
                    f"You MUST use the exact strings from this list: {allowed}"
                )
        return result

    return agent
