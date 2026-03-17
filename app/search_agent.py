#load the environment variables
from dotenv import load_dotenv
import os, string, re
from typing import List, Set

from pydantic_ai import Agent, RunContext, Tool
from pydantic import BaseModel, Field
from omegaconf import OmegaConf

from pydantic_ai.exceptions import AgentRunError, ModelRetry

from typing import List, Optional

from model_selector import model_api_selector

#import the dataclass for the mealqueryoutput for easier coding
from query_generator import MealQueryOutput

from utils.logging_helper import get_my_logger

#! logger
logger = get_my_logger(__name__)

#!import needed packages for tool creation
from ddgs import DDGS

# this is the needed code to create the search agent

#! custom data basemodel so it is 1 reason and query at a time
class SearchQueryReasoningInput(BaseModel):
    query: str
    reasoning: str
    search_results_cache: dict[str, str] = Field(default_factory=dict, exclude=True) # this is later to check the valid list link
    tool_use_counter : int = Field(default=0, exclude=True) # stop the llm from keep on going a loop of using the tool

# #! custom data basemodel for the output 
# class SearchAgentOutput(BaseModel):
#     source: list[str]
#     source_link : list[str] = Field(alias='link')
#     reasoning : list[str]
#     source_content: list[str] = Field(default_factory=list)

#! custom data basemodel for the output
class SearchResultItem(BaseModel):
    """A single search result item containing the link, title, and reasoning."""
    link: str = Field(description="The exact URL link from the search results.")
    title: str = Field(description="The title of the source.")
    reasoning: str = Field(description="Why this specific source validates the user scenario.")
    content: Optional[str] = Field(default=None, description="The full body text (filled by validator).")

class SearchAgentOutput(BaseModel):
    """The final output containing a list of validated search items."""
    results: List[SearchResultItem] = Field(
        description="A list of 5-10 validated search results."
    )

#! search tool using DDGS
def websearch_tool(ctx: RunContext[SearchQueryReasoningInput]) -> str:

    #context from dependcies
    search_query = ctx.deps.query
    number_of_access = ctx.deps.tool_use_counter

    #log
    logger.info(f"[Tool] Agent requesting search for: '{search_query}' (Attempt {number_of_access+1})")

    #checker to stop the llm going on a loop
    if number_of_access >= 3:
        logger.error("[Tool] LLM is looping. Force quitting.")
        raise AgentRunError("Tool loop detected. Exiting to protect Celery worker.")

    #set the tool use counter to 1
    ctx.deps.tool_use_counter+=1

    #search logic
    try:

        results = []
        
        #ddgs search function
        results = list(DDGS(timeout=20).text(search_query, region='us-en', max_results=20, page=1, backend="auto"))

        # with DDGS(timeout=20) as ddgs:
        #     # We iterate manually to prevent infinite generator loops
        #     for idx, r in enumerate(ddgs.text(search_query, region='us-en', max_results=20, backend="auto")):
        #         results.append(r)
        #         # Hard stop at 15 results no matter what the library tries to do
        #         if idx >= 15: 
        #             break

        logger.info(f"[Tool] DDGS returned {len(results)} raw results.")

        if not results:
            logger.warning(f"[Tool] No results found for query: '{search_query}'")
            raise AgentRunError("Empty Result from DDGS")
        
        #loop and put the links in the dict for verification later
        formatted_results = []
        
        for item in results:
            link = item.get("href")
            title = item.get("title")
            body = item.get("body", "")
            
            # Skip empty results
            if not link:
                continue

            # Populate Cache for Validator
            ctx.deps.search_results_cache[link] = body
            
            # Truncate body to 400 chars to save tokens
            clean_body = body[:400].replace("\n", " ") + "..."
            
            # Create a clean block for the LLM
            formatted_entry = (
                f"Title: {title}\n"
                f"Link: {link}\n"
                f"Snippet: {clean_body}\n"
                f"---"
            )
            formatted_results.append(formatted_entry)

        # Join them into one readable string
        final_output = "\n".join(formatted_results)
        
        if not final_output.strip():
            logger.error("[Tool] Results existed but formatting failed (Empty Output).")
            raise AgentRunError("Search returned results but they were not readable.")

        return final_output

    except Exception as e:
        logger.error(f"[Tool] Critical Search Error: {e}", exc_info=True)
        raise AgentRunError(f"Search API crashed or timed out: {e}")
    
def search_agent_creator(config_file:OmegaConf) -> Agent:

    #! define the model provider
    model_provider = model_api_selector(config_file, model_name_override=config_file.agent.search_model_name)

    #! define the agent with system prompt for role
    agent = Agent(
        model_provider,
        deps_type=SearchQueryReasoningInput,
        tools=[websearch_tool],
        system_prompt=config_file.agent.base_system_prompt,
        retries=2,
        output_type=SearchAgentOutput
    )

    #! define the system prompt for the task instrutions
    @agent.system_prompt
    def search_agent_system_prompt(ctx: RunContext) -> str:

        # #access the dependecies
        search_query = ctx.deps.query
        search_reasoning = ctx.deps.reasoning

        instruction_prompt = config_file.agent.search_agent_prompt.format(
            search_query=search_query,
            search_query_reasoning =search_reasoning
        )
        return instruction_prompt

    @agent.output_validator
    def validate_search_result_links(ctx: RunContext, result:SearchAgentOutput) -> SearchAgentOutput:

        logger.info(f"[Validator] Validating {len(result.results)} proposed links...")

        #in case we cant validate and LLM dont have a search list
        cache = ctx.deps.search_results_cache
        if not cache:
            if result.results: 
                logger.warning("[Validator] Agent hallucinated answers without using the tool! Triggering Retry.")
                # If we have no cache but LLM gave links, force a retry (tool didn't run properly)
                raise ModelRetry(
                    "CRITICAL ERROR: You attempted to answer without using the 'websearch_tool'. "
                    "I have no search results in memory. "
                    "You MUST call the 'websearch_tool' function with the query string before you can generate an answer."

                )
            return result

        valid_items = []
        hallucinated_count = 0

        # Iterate through the model's proposed results
        for item in result.results:
            
            # CHECK: Does this link exist in our actual search results?
            if item.link in cache:
                # YES: Enrich it with the real content from the tool
                # This ensures the 'content' field is 100% real, not hallucinated
                item.content = cache[item.link]
                valid_items.append(item)
            else:
                # NO: This is a fake link. Log it, but don't crash yet.
                hallucinated_count += 1
                logger.warning(f"[Validator] Dropping hallucinated link: {item.link}")

        MIN_REQUIRED = 5
        
        if len(valid_items) < MIN_REQUIRED:
            logger.warning(f"[Validator Failed] Only {len(valid_items)} valid links found (Min: {MIN_REQUIRED}). Retrying...")
            
            raise ModelRetry(
                f"Validation Failed: You provided {len(result.results)} items, but only {len(valid_items)} were real links. "
                f"I discarded {hallucinated_count} fake links. "
                f"Please search again and ensure you return at least {MIN_REQUIRED} valid links from the tool output."
            )

        result.results = valid_items
        
        logger.info(f"[Validator Success] Returning {len(valid_items)} verified items.")
        return result
    
    return agent