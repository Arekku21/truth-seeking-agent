from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.messages import ModelRequest, TextPart, ToolCallPart

class OllamaFixedModel(OpenAIChatModel):
    """
    Safe wrapper for Ollama.
    It ONLY fixes 'Silent Tool Calls' from the Assistant.
    It does NOT touch User/Tool messages to avoid breaking PydanticAI validation.
    """
    
    async def _completions_create(self, messages, *args, **kwargs):
        for i, msg in enumerate(messages):
            
            # We ONLY touch ModelRequest (The AI's messages)
            if isinstance(msg, ModelRequest):
                
                # Check 1: Does it have any text?
                has_text = any(
                    isinstance(p, TextPart) and p.content and p.content.strip() 
                    for p in msg.parts
                )
                
                # Check 2: Does it have tool calls?
                has_tool_calls = any(isinstance(p, ToolCallPart) for p in msg.parts)
                
                # THE FIX: 
                # If the AI calls a tool but stays silent (No text), Ollama crashes.
                # We inject a thought bubble to satisfy the API.
                if has_tool_calls and not has_text:
                    print(f"[Ollama Fix] FIXED Assistant Message {i}: Injected placeholder for silent tool call.")
                    
                    # Create new parts list with a thought at the beginning
                    new_parts = [TextPart(content="Thinking...")] + list(msg.parts)
                    
                    # Update in place
                    msg.parts = new_parts

        return await super()._completions_create(messages, *args, **kwargs)