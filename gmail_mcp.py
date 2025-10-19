import asyncio
from mcp.server.fastmcp import FastMCP
import os.path
import base64
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import json
import quopri
import time
from dotenv import load_dotenv
import discord

# these are the files for the rag application
# using threads 
import threading 
index_lock = threading.Lock() # used to update the vector embeddings atomically

# Create the MCP server
mcp = FastMCP("gmailmcp" , 
              host="0.0.0.0" , 
              port=8080)


# ==== making all the imports and the necessary code implementations for the rag aplications ====
from langchain_community.vectorstores import FAISS
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.document_loaders import DirectoryLoader, PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_ollama import OllamaLLM
from langchain_core.prompts import ChatPromptTemplate
from discord.ext import commands

SOURCE_DIRECTORY = "attachments"
PERSIST_DIR = "faiss_index"


# =====  setting up all the eeded libraries for the discord bot 
load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')

# setting up the intents ---> important to give the bot permissions to perform some tasks 

intents = discord.Intents.default()
intents.message_content = True  
intents.members = True
bot = commands.Bot(command_prefix='!', intents=intents) # typing ! will connect to the bot 

# code to build an index from scratch --> will be called on any change to teh attachment folder
def buildIndex():

    with index_lock:
        print("LOCK ACQUIRED BY THE FILE MONITOR TOOL......")
        print("initiating the index construction .....")

        loader = DirectoryLoader(SOURCE_DIRECTORY , glob = "**/*.*" , show_progress=True, use_multithreading=True )
        docs = loader.load()

        if not docs:
            print("no documents to load into the index ....")
            return 
        
        text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=100)
        doc_split = text_splitter.split_documents(docs)

        print("creating embeddings ....")
        embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
        vectorstore = FAISS.from_documents(doc_split, embeddings)
        vectorstore.save_local(PERSIST_DIR)
        print(f"--- ✅ Index built successfully with {len(docs)} documents. ---")
        print("LOCK RELEASED BY THE FILE MONITOR THEREAD...")

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

class AttachmentManager(FileSystemEventHandler):
    def on_created(self , event):
        fileName = event.src_path
        if fileName.find(".pdf") != -1:
            print(f"New pdf file added {fileName}")
            buildIndex()
    
    def on_deleted(self , event):
        fileName = event.src_path
        if fileName.find(".pdf") != -1:
            print(f"New pdf file deleted {fileName}")
            buildIndex()

def start_file_monitor():

    # this is the function that will be called by the file monitor thread ....
    os.makedirs(SOURCE_DIRECTORY, exist_ok=True)
    event_handler = AttachmentManager()
    observer = Observer()
    observer.schedule(event_handler, SOURCE_DIRECTORY, recursive=False)
    observer.start()
    print("--- ✅ File monitor is running in the background. ---")
    
    try:
        while True:
            time.sleep(3)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


# Example tool
@mcp.tool()
def hello(name: str) -> str:
    """Say hello to someone."""
    return f"Hello, {name}! This is MCP 🚀"

# Example resource
@mcp.resource("resource://welcome")
def welcome():
    """A simple static resource."""
    return "Welcome to the MCP server!"


# creating tools to setup and access gmail using gmail api 

# Define the scopes. This tells Google what permissions we're asking for.
# If you modify scopes, delete the file token.json.
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
def authenticate():
    """Handles the user authentication flow."""
    creds = None
    # The file token.json stores the user's access and refresh tokens.
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    
    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)
        
        # Save the credentials for the next run
        with open("token.json", "w") as token:
            token.write(creds.to_json())
    return creds

@mcp.tool()
def read_mail(sender_email : str):
    """ This tool can be used to retrieve the most recent email from the specified sender (sender_email) and return the data to the user """
    #step 1--> authenticate the desktop app to access gmail inbox , set the sender email in the querry

    creds = authenticate()
    service = build("gmail" , "v1" , credentials=creds)
    querry = f"from:{sender_email}"
    
    #step 2--> check if there are any emails from the sender 
    result = service.users().messages().list(userId="me", q=querry , maxResults=1).execute()
    messages = result.get("messages" , []) # this returns empty if not email for this sender

    if not messages:
        print(f" ------ THERE ARE NO EMAILS FROM THIS SENDER IN YOUR INBOX ------")
        return f"No emails fount from this mail id "
    
    # stp 3---> get the actual mail from the message id
    message_id = messages[0]["id"]
    full_mail = service.users().messages().get(userId="me" , id=message_id , format="full").execute()

    #step 4--> process the payload and get the text and the attachments
    payload = full_mail["payload"]
    parts = payload.get("parts" , [])
    data = "" # if it is a simple mail parts will be empty and data can be accessed directly 
    # initilizing teh text_content variable to store the text content of the email
    text_content = "" # this is the global variable 
    if parts:
        
        for part in parts:

            # 1--> check for plain text content
            if part["mimeType"] == "text/plain":
                data = part["body"]["data"]
                # writing teh text content found to de bug 
                if data :
                    base64_decoded = base64.urlsafe_b64decode(data)
                    quopri_decoded = quopri.decodestring(base64_decoded)
                    text_content = quopri_decoded.decode("utf-8", "replace") # Use 'replace' for safety
                     # It's okay to break here, we found the text.
                    with open("check.txt" , "w" , encoding="utf-8") as f:
                        f.write(text_content)
                    break

        
        # writing a saperate for loop for checking for attachments 
        for part in parts:
            if part.get("filename"):
                file_name = part["filename"]
                print(f"Attachment found: {file_name}")

                # now get the attachment id and acces the attached data from its id 
                attachment_id = part["body"]["attachmentId"]
                attachment = service.users().messages().attachments().get(userId="me", messageId=message_id, id=attachment_id).execute()
                raw_data = attachment["data"]

                #decode this raw data and store this in a folder
                file_data = base64.urlsafe_b64decode(raw_data.encode("UTF-8"))
                path = f"attachments/{file_name}"

                with open(path , "wb") as f:
                    f.write(file_data)

                    print("Attachment downloaded and saved to " + path)
    else:
        data = payload["body"]["data"]
        text_content = base64.urlsafe_b64decode(data).decode("utf-8")
        with open("check.txt" , "w" , encoding="utf-8") as f:
            f.write(text_content)


    # decode the data
    if text_content :
        #readable_data = base64.urlsafe_b64decode(data).decode("utf-8")
        subject = ""

        headers = payload["headers"]
        for header in headers:
            if header["name"].lower() == "subject":
                subject = header["value"]
                break

        with open("latest_email.txt" , "w" , encoding="utf-8") as f:
            f.write(f"Subject : {subject} \n\n Body : {text_content}")

        return f"Subject : {subject} \n\n Body : {text_content}"
    
    else:
        return "No text content found in the email."


@mcp.tool()
def mails_from_date_range(start_date: str, end_date: str , sender_email: str):
    """ 
    This tool is used ot read the mail form a specific date range specified by the user 
    Dates must be in 'YYYY/MM/DD' format

    """

    # 1 clear the attachment folder to store the new attachments
    folder = "attachments"
    for filename in os.listdir(folder):
        file_path = os.path.join(folder, filename)
        try:
            import shutil
            if os.path.isfile(file_path) or os.path.islink(file_path):
                os.unlink(file_path)
            elif os.path.isdir(file_path):
                shutil.rmtree(file_path)
        except Exception as e:
            print(f'Failed to delete {file_path}. Reason: {e}')

    #2 define the querry
    querry = f"after:{start_date} before:{end_date} from:{sender_email}"
    creds = authenticate()
    service = build("gmail" , "v1" , credentials=creds)

    parsed_email_content = {} # this is the object that will store all the emails from the sspecified range

    # 3 get all the messages
    result = service.users().messages().list(userId="me", q=querry , maxResults=10).execute()
    all_messages = result.get("messages" , []) # this returns empty if not email for this sendering out 
    text_contents = []
    attachment_names = []
    for i , message in enumerate(all_messages):
        # for each message get the payload and parse the text content and attachment 

        message_id = message["id"]
        full_email_content = service.users().messages().get(userId="me" , id=message_id , format="full").execute()
        payload = full_email_content["payload"]
        parts = payload.get("parts" , [])
        data = "" # if it is a simple mail parts will be empty and data can be accessed directly
        text_content = ""

        email_number = i+1
        
        # loop for getting all the text messages 
        for part in parts:
            
            if part['mimeType'] == "text/plain":
                data = part["body"]["data"]
                if data :
                    base64_decoded = base64.urlsafe_b64decode(data)

                    try:
                        
                        quopri_decoded = quopri.decodestring(base64_decoded)
                        text_content = quopri_decoded.decode("utf-8", "replace") # Use 'replace' for safety
                    except Exception as e:
                        print("the mail cannot be decided using quopri " , e)
                        text_content = base64_decoded.decode("utf-8", "replace")


                    text_contents.append( text_content )

        #loop for getting all the attachemtns    
        for part in parts:
            if part.get("filename"):
                file_name = part["filename"]
                print(f"Attachment found: {file_name}")

                # now get the attachment id and acces the attached data from its id 
                attachment_id = part["body"]["attachmentId"]
                attachment = service.users().messages().attachments().get(userId="me", messageId=message_id, id=attachment_id).execute()
                raw_data = attachment["data"]

                #decode this raw data and store this in a folder
                file_data = base64.urlsafe_b64decode(raw_data.encode("UTF-8"))
                path = f"attachments/{file_name}"

                with open(path , "wb") as f:
                    f.write(file_data)

                    print("Attachment downloaded and saved to " + path) 
                attachment_names.append( file_name )

    parsed_email_content["text_data"] = text_contents
    parsed_email_content["attachments"] = attachment_names

    return parsed_email_content


# dding the rag tool for teh mcp server 
@mcp.tool()
def querry_documents(query : str):
    """ this tool is used to querry the documents
      in the attachments folder using the rag approach """


    with index_lock:

        print(f"Running the rag tool on the querry {query} after getting lock ")
            
        try:
            from langchain_community.vectorstores import FAISS
            from langchain_community.embeddings import HuggingFaceEmbeddings
            from langchain_community.document_loaders import PyPDFLoader
            from langchain_text_splitters import RecursiveCharacterTextSplitter
            import os
            from llama_index.llms.google_genai import GoogleGenAI
            from llama_index.core import Settings

            if not os.path.exists(PERSIST_DIR):
                return "ERROR IN RAG---> NO VECTOR STORE PRESENT BUILD THE FASSIS INDEX.."

            print(f"LOADING THE FASSIS VECTOE STORE AND THE CORRESPONDING EMBEDDING")
            PERSIST_DIRECTORY = "faiss_index"
            # load the pre built vector fassis index 
            embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
            vectorstore = FAISS.load_local(
                PERSIST_DIRECTORY , 
                embeddings , 
                allow_dangerous_deserialization=True
            )

            # retrieve the relevant documents 
            print("Retrieve the documents .....")
            retriever = vectorstore.as_retriever(search_kwargs={"k": 3}) # Get top 3 results
            relevant_docs = retriever.invoke(query)
            context_text = "\n\n".join([doc.page_content for doc in relevant_docs])

            # --- 3. Generate the answer ---
            print("   - Generating answer...")
            from langchain_ollama import OllamaLLM
            llm = OllamaLLM(model="llama3")
            from langchain_core.prompts import ChatPromptTemplate
            prompt = ChatPromptTemplate.from_template(
                "Answer the following question based only on the provided context. "
                "If the context does not contain the answer, say that you don't know.\n\n"
                "Context:\n{context}\n\n"
                "Question: {question}"
            )
            
            chain = prompt | llm
            response = chain.invoke({"context": context_text, "question": query})

            return response
        
        except Exception as e:
            return f"an eceprint ouucred uring RAG implementations {e}"
            
# MCP TOOLS FOR DISCORD BOT 

@mcp.tool()
def list_all_channels():
    """ This tool can be used to list all the channels that the bot is aldready a part of """
    channels_list = []

    for guilds in bot.guilds:
        for channels in guilds.text_channels:
            channels_list.append({
                "server": guilds.name,
                "channel_name": channels.name,
                "channel_id": channels.id
            })
    return channels_list

@mcp.tool()
def send_message_to_channel(channel_name: str , message : str):
    """ This tools can be used to send a message to a specific channel , it will require a channel name and the 
    message that the user wants to send to that channel  """

    #1. get the id of the channel 
    id = -1
    for guilds in bot.guilds:
        for channel in guilds.text_channels:
            if channel.name == channel_name:
                id = int(channel.id)
                break
        if id != -1 :
            break
    
    if id == -1:
        return f"❌ Channel with name {channel_name} not found."

    #2. send message to the channel
    try:
            
        #fetch the channel object in thread safe way 
        channel_obj = bot.get_channel(id)
        if channel_obj is None:
            result = asyncio.run_coroutine_threadsafe(bot.fetch_channel(id) , discord_loop)
            channel_obj = result.result(timeout=10)
        
        if channel_obj:
            result = asyncio.run_coroutine_threadsafe(channel_obj.send(message) , discord_loop)
            status = result.result(timeout=10)  # Wait for the message to be sent
            return f"✅ Message sent to channel {channel_name} successfully."
    except Exception as e:
        return f"❌ Failed to send message to channel {channel_name}: {e}"


@mcp.tool()
def get_recent_n_messages(channel_name:str , limit:int):
    """ This tool can be used to fetch the most recent n messages from a specific channel 
        it will require the channel name and the number of messages to be fetched 
    """

    #1 get the channel id from the channel name
    id = -1
    channel_obj = None

    for guild in bot.guilds:
        for channel in guild.text_channels:
            if channel.name == channel_name:
                id = int(channel.id)
                channel_obj = channel
                break
        if id != -1 :
            break


    if id == -1:
        return f"❌ Channel with name {channel_name} not found."
    
    try:

        async def fetch_msg():
            messages = []
            async for msg in channel_obj.history(limit=limit):
                messages.append({
                    "author": msg.author.name,
                    "timestamp": str(msg.created_at),
                    "content": msg.content
                })
            return messages

        #fetch the messages 
        result = asyncio.run_coroutine_threadsafe(fetch_msg(), discord_loop)
        messages = result.result(timeout=10)

        if not messages:
            return f"No messages found in channel {channel_name}."

        # Format a readable summary for display
        formatted = "\n".join(
            [f"🗨️ {m['author']} ({m['timestamp']}): {m['content']}" for m in messages]
        )        

        return f"✅ Fetched {len(messages)} messages from '{channel_name}':\n\n{formatted}"
    
    except Exception as e:
        return f"❌ Failed to fetch messages from channel {channel_name}: {e}"

@mcp.tool()
def mention_user_int_channel(user_name: str , channel_name: str , message : str):

    """ This tool can be used to mention a user in a specific channel 
        it will require the user name , channel name and the message to be sent 
    """

    #1 get the channel id from the channel name
    id = -1
    channel_obj = None

    for guild in bot.guilds:
        for channel in guild.text_channels:
            if channel.name == channel_name:
                id = int(channel.id)
                channel_obj = channel
                break
        if id != -1 :
            break

    if id == -1:
        return f"❌ Channel with name {channel_name} not found."
    
    member_obj = None 
    for member in channel_obj.members:
        if member.name == user_name or member.display_name == user_name:
            member_obj = member
            break
    
    if  not member_obj:
        return f"❌ User with name {user_name} not found."

    mentionChat = f"<@{member_obj.id}>{message}"

    # 4. Send the message
    try:

        asyncio.run_coroutine_threadsafe(channel_obj.send(mentionChat), discord_loop)
        return f"✅ Mentioned {member_obj.name} in {channel_name}"
    except Exception as e:
        return f"❌ Failed to mention {member_obj.name} in {channel_name}: {e}"    

@mcp.tool()
def create_poll(channel_name: str, question: str, options: list[str], emojis: list[str]):
    """Create a poll in a given channel with custom options and emojis.
    the length of the options and emojis must be same
    this is an important input validaation
    """

    # 1. Find the channel
    target_channel = None
    for guild in bot.guilds:
        for channel in guild.text_channels:
            if channel.name == channel_name:
                target_channel = channel
                break
        if target_channel:
            break

    if not target_channel:
        return f"❌ Channel '{channel_name}' not found."

    # 2. Validate input
    if len(emojis) != len(options):
        return "❌ The number of emojis must match the number of options."

    # 3. Construct the poll text
    poll_text = f"📊 **{question}**\n\n"
    for emoji, option in zip(emojis, options):
        poll_text += f"{emoji} {option}\n"

    # 4. Send the message to Discord
    future = asyncio.run_coroutine_threadsafe(
        target_channel.send(poll_text),
        discord_loop
    )
    msg = future.result(timeout=10)

    # 5. Add reactions
    for emoji in emojis:
        asyncio.run_coroutine_threadsafe(msg.add_reaction(emoji), discord_loop)

    return f"✅ Poll created successfully in #{channel_name}."

discord_loop = None
def start_discord_bot():

    global discord_loop
    discord_loop = (asyncio.new_event_loop())
    asyncio.set_event_loop(discord_loop)
    print("Starting the discord bot in a new thread ....")

    async def runner():
        try:
            await bot.start(DISCORD_TOKEN)
        except asyncio.CancelledError:
            await bot.close()
        finally:
            await bot.close()

    discord_loop.create_task(runner())
    try:
        discord_loop.run_forever()
    finally:
        discord_loop.close()
        print("Discord bot stopped")

def extract(headers , data):

    obj = {}

    for header in headers:
        if header["name"].lower() == "subject":
            obj["subject"] = header["value"]

        if header["name"].lower() == "Date":
            obj["subject"] = header["value"]

        if header["name"].lower() == "from":
            obj["subject"] = header["value"]

    return obj



@mcp.tool()
def get_unread_mails(sender= "" , label = ""):

    """ This is a tool that can be used to get information about any mails from the sender in the mail box that are unread .
    the input paraameter can either be a sender of a label .either one of them can be none  """
    # make a querry with unread parameter
    creds = authenticate()
    service = build("gmail" , "v1" , credentials=creds)

    if not sender and not label:
        return "You need to provide atleast sender or the label"
    
    querry = ["is:unread"]

    if label != "":
        querry.append(f"label:{label}")

    elif sender != "":
        querry.append(f"from:{sender}")

    full_querry = " ".join(querry)

    print(f"the full querry for unread mails is {full_querry}")

    list_request = service.users().messages().list(userId="me", q=full_querry , maxResults=10).execute()
    result = list_request

    messages = result.get("messages" , [])
    count = result.get('resultSizeEstimate', 0)
    
    print(f"got the results for unread mails count is {count}")

    if count == 0:
        return f"There are no unread mails matching your criteria."
    
    metadata = []

    for msg in messages:

        print(f"processing the message id {msg['id']}")
        msg_id = msg["id"]
        get_msg_request = service.users().messages().get(userId="me" , id=msg_id , format="metadata").execute()
        full_msg =get_msg_request

        curr_metadata = {
            "subject" : next(header["value"] for header in full_msg["payload"]["headers"] if header["name"].lower() == "subject"),
            "snippet" : full_msg.get("snippet" , "") , 
            "id" : msg_id,
            "from" : next(header["value"] for header in full_msg["payload"]["headers"] if header["name"].lower() == "from"),
            "date" : next(header["value"] for header in full_msg["payload"]["headers"] if header["name"].lower() == "date")
        }

        metadata.append(curr_metadata)
    
    return metadata
    



# === Run server ===
if __name__ == "__main__":
    
    transport = "sse"

    # on the start of this mcp server we will start to moniter the attachments folder 
    buildIndex()

    #creating thread for file monitoring
    moniter_thread = threading.Thread(target=start_file_monitor , daemon=True)
    moniter_thread.start()

    # starting the discord bot in a new thread 
    discord_thread = threading.Thread(target=start_discord_bot, daemon=True)
    discord_thread.start()

    print("********** MCP SERVER STARTING ******************")
    
    # The main thread runs the blocking MCP server
    mcp.run(transport="sse")
    print("********** MCP SERVER STOPPED ******************")

    if discord_loop and discord_loop.is_running():
        discord_loop.call_soon_threadsafe(lambda: asyncio.create_task(bot.close()))
        discord_thread.join(timeout=5)
        print("Discord bot stopped")
