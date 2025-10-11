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
# Create the MCP server
mcp = FastMCP("gmailmcp" , 
              host="0.0.0.0" , 
              port=8080)

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
    if parts:
        for part in parts:

            # 1--> check for plain text content
            if part["mimeType"] == "text/plain":
                data = part["body"]["data"]
                break

            #2 check for attachments
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


    # decode the data
    if data :
        readable_data = base64.urlsafe_b64decode(data).decode("utf-8")
        subject = ""

        headers = payload["headers"]
        for header in headers:
            if header["name"].lower() == "subject":
                subject = header["value"]
                break

        with open("latest_email.txt" , "w") as f:
            f.write(f"Subject : {subject} \n\n Body : {readable_data}")

        return f"Subject : {subject} \n\n Body : {readable_data}"

# === Run server ===
if __name__ == "__main__":
    
    transport = "stdio"

    if transport == "stdio":
        print("Starting MCP server with stdio transport...")
        mcp.run(transport="stdio")
    else:
        print("Starting MCP server with sse transport...")
        mcp.run(transport="sse")
    
