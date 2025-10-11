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

SOURCE_DIRECTORY = "attachments"
PERSIST_DIR = "faiss_index"

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
    def on_create(self , event):
        fileName = event.src_path
        if fileName.find(".pdf") != -1:
            print(f"New pdf file added {fileName}")
            buildIndex()
    
    def on_delete(self , event):
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
            embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
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
            
    
    

# === Run server ===
if __name__ == "__main__":
    
    transport = "sse"

    # on the start of this mcp server we will start to moniter the attachments folder 
    buildIndex()

    #creating thread for file monitoring
    moniter_thread = threading.Thread(target=start_file_monitor , daemon=True)
    moniter_thread.start()

    print("********** MCP SERVER STARTING ******************")
    
    print("********** MCP SERVER STARTING ******************")
    # The main thread runs the blocking MCP server
    mcp.run(transport="sse")
    print("********** MCP SERVER STOPPED ******************")
