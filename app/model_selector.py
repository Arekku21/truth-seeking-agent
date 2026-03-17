#load the environment variables
from dotenv import load_dotenv
from omegaconf import OmegaConf
import os

from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.ollama import OllamaProvider
from pydantic_ai.models.groq import GroqModel

from ollama_fix import OllamaFixedModel

#logging
import logfire
from utils.logging_helper import setup_logfire_logging,get_my_logger

#! logger
logger = get_my_logger(__name__)

#!load environment variables
load_dotenv()

#!load the config yaml file
config_file = OmegaConf.load(os.getenv("CONFIG_FILE"))

def model_api_selector(config: OmegaConf,model_name_override: str = None):
    """
    Selects and instantiates the appropriate Large Language Model (LLM) provider based on the configuration.

    This function reads the 'provider' field from the configuration object (e.g., 'ollama', 'groq', 'openai').
    It verifies that the necessary environment variables (API keys) are present for cloud providers
    and returns an initialized PydanticAI model instance ready for use by an Agent.

    Args:
        config (OmegaConf): The configuration object loaded from YAML, expected to contain an 'agent' section with 'provider', 'model_name', and optional 'base_url'.

    Returns:
        ModelType: An instance of a PydanticAI compatible model (e.g., OpenAIChatModel, GroqModel).

    Raises:
        ValueError: If the provider specified in the config is not supported.
        EnvironmentError: If a required API key (e.g., GROQ_API_KEY) is missing from environment variables.
    """

    # 1. Get the provider string (lowercase for consistency)
    # We default to 'ollama' if not specified to prevent crashes
    provider = getattr(config.agent, "model_provider", "ollama").lower()
    
    # USE THE OVERRIDE IF PROVIDED, OTHERWISE USE DEFAULT
    model_name = model_name_override if model_name_override else config.agent.model_name

    print(f"[Model Selector] Loading Provider: {provider.upper()} | Model: {model_name}")

    # --- OPTION 1: OLLAMA (Local) ---
    if provider == "ollama":
        # Ensure base_url is present, default to standard localhost if missing
        base_url = getattr(config.agent, "base_url", "http://localhost:11434/v1")
        
        return OllamaFixedModel(
            model_name=model_name,
            provider=OllamaProvider(base_url=base_url),
        )

    # --- OPTION 2: GROQ (Cloud - Fast) ---
    elif provider == "groq":
        api_key = os.getenv("MODEL_PROVIDER_API_KEY")
        if not api_key:
            raise EnvironmentError("Missing 'MODEL_PROVIDER_API_KEY'. Please add it to your .env file.")
        
        # GroqModel auto-reads the env var, but we checked it above for safety
        return GroqModel(model_name=model_name)

    # --- OPTION 3: OPENAI (Cloud - Standard) ---
    elif provider == "openai":
        api_key = os.getenv("MODEL_PROVIDER_API_KEY")
        if not api_key:
            raise EnvironmentError("Missing 'MODEL_PROVIDER_API_KEY'. Please add it to your .env file.")
        
        return OpenAIChatModel(model_name=model_name)

    # --- UNKNOWN PROVIDER ---
    else:
        raise ValueError(
            f"Unsupported provider: '{provider}'. "
            "Supported options are: 'ollama', 'groq', 'openai'."
        )

