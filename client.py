import nest_asyncio
nest_asyncio.apply()
import asyncio

from llama_index.llms.ollama import Ollama
from llama_index.core import Settings
import os
from llama_index.llms.google_genai import GoogleGenAI
from llama_index.core import Settings

from dotenv import load_dotenv
# --- Load environment variables ---
load_dotenv()  # Loads variables from .env file
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

if not GOOGLE_API_KEY:
    raise ValueError("❌ GOOGLE_API_KEY not found in environment. Please add it to your .env file.")

from llama_index.llms.google_genai import GoogleGenAI
from llama_index.core import Settings

llm = GoogleGenAI(
    model="gemini-2.5-pro",   # or "gemini-2.5-pro"
    api_key= GOOGLE_API_KEY,
    temperature=0.2,
    max_output_tokens=2048
)
Settings.llm = llm



from llama_index.tools.mcp import BasicMCPClient, McpToolSpec

# FIXED PORT → match server (8080)
mcp_client = BasicMCPClient("http://127.0.0.1:8080/sse")
mcp_tools = McpToolSpec(client=mcp_client)

async def list_tools():
    tools = await mcp_tools.to_tool_list_async()
    for tool in tools:
        print(tool.metadata.name, tool.metadata.description)

SYSTEM_PROMPT = """ 

You are an ai assistant that reads the users mail in the gmail inbox and provides valuable isights 
You are also capable of answering users querries about the mails and prvide elaborative informative summaries

""" 

from llama_index.tools.mcp import McpToolSpec
from llama_index.core.agent.workflow import FunctionAgent

async def get_agent(tools: McpToolSpec):
    tools = await tools.to_tool_list_async()
    agent = FunctionAgent(
        name="Agent",
        description="An agent that can work with Our Database software.",
        tools=tools,
        llm=llm,
        system_prompt=SYSTEM_PROMPT,
        use_llm_tool_calling=False
    )
    return agent



from llama_index.core.agent.workflow import (
    FunctionAgent, 
    ToolCallResult, 
    ToolCall)

from llama_index.core.workflow import Context

async def handle_user_message(
    message_content: str,
    agent: FunctionAgent,
    agent_context: Context,
    verbose: bool = False,
):
    handler = agent.run(message_content, ctx=agent_context)
    async for event in handler.stream_events():
        if verbose and type(event) == ToolCall:
            print(f"Calling tool {event.tool_name} with kwargs {event.tool_kwargs}")
        elif verbose and type(event) == ToolCallResult:
            print(f"Tool {event.tool_name} returned {event.tool_output}")

    response = await handler
    return str(response)


async def setup():
    agent = await get_agent(mcp_tools)
    agent_context = Context(agent)
    return agent, agent_context

agent, agent_context = asyncio.run(setup())

asyncio.run(list_tools())

# run the client
async def run_agent():
    while True:
        user_input = input("Enter your message: ")
        if user_input == "exit":
            break
        print("User: ", user_input)
        response = await handle_user_message(user_input, agent, agent_context, verbose=True)
        print("Agent: ", response)

asyncio.run(run_agent())
