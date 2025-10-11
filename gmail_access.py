import os.path
import base64
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

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


def read_mail(sender_email : str):
    # this function is to get the most ecent email by the sender and print out all the text info into a file or the terminal 
    
    #1. authenticate the desktop app to access gmail inbox , set the sender email in the querry

    creds = authenticate()
    query = f"from:{sender_email}"
    service = build("gmail" , "v1" , credentials=creds)

    #2. get the id of the email sent by the sender
    # this will get the last mail sent by the sender
    result = service.users().messages().list(userId="me", q=query , maxResults=1).execute()
    messages = result.get("messages", [])

    if not messages:
        print(f"There is not email from this sender :{sender_email}")
    else:
        message_id = messages[0]["id"]

        #3. get the full email 
        full_content = service.users().messages().get(userId="me", id=message_id, format="full").execute()
        # the full content is sent in a web sef encoded format 

        #4. decoding the email full content 
        payload = full_content['payload']
        parts = payload.get("parts", [])
        data = ""

        if parts:
            for part in parts:


                # check for the attachments --> if any attachmnts are there dowload it 
                if part.get("filename"):
                    # get the file name and its attachment id
                    filename = part["filename"]
                    print(f"Attachment found, name of the file is: {filename}")
                    attachment_id = part["body"]["attachmentId"]

                    # make an api call to get the data of this attachment id and download it
                    attachment = service.users().messages().attachments().get(
                        userId="me", messageId=message_id, id=attachment_id
                    ).execute()

                    #decode the attachment and then save it to a file 
                    file_data = base64.urlsafe_b64decode(attachment["data"].encode("UTF-8"))
                    with open(filename, "wb") as f:
                        f.write(file_data)
                        print(f"Attachment {filename} downloaded successfully.")
                
                
                # check for the plain text in mails 
                if part["mimeType"] == "text/plain":
                    data = part["body"]["data"]
                    break

                        
        else:
            data = payload["body"]["data"]

        if data:
            decoded_mail = base64.urlsafe_b64decode(data).decode("utf-8")

            subject = ""
            for header in payload["headers"]:
                if header["name"].lower() == "subject":
                    subject = header["value"]
                    break

            print("\n--- ✅ Email Found! ---")
            print(f"Subject: {subject}\n")
            print("Content:")
            print(decoded_mail)

            full_text_data = f"Subject: {subject}\n\n{decoded_mail}"
            return full_text_data

        else:
            print("Could not find plain text content in the email.")


def main():
    """Shows basic usage of the Gmail API.
    Lists the user's Gmail labels.
    """
    creds = authenticate()

    try:
        # Build the Gmail API service
        service = build("gmail", "v1", credentials=creds)

        # 1. Search for messages from the sender
        query = "from:bytebytego@substack.com"
        print(f"Searching for emails with query: '{query}'...")
        result = service.users().messages().list(userId="me", q=query, maxResults=5).execute()
        messages = result.get("messages", [])

        if not messages:
            print("No messages found.")
            return

        print(f"Found {len(messages)} emails. Fetching the most recent one.")

        # 2. Get the content of the most recent message
        msg_id = messages[0]["id"]
        message = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
        
        # 3. Decode the message body
        payload = message["payload"]
        parts = payload.get("parts", [])
        data = ""

        if parts:
            # Find the plain text part of the email
            for part in parts:
                if part["mimeType"] == "text/plain":
                    data = part["body"]["data"]
                    break
        else:
            # If no parts, the body is in the main payload
            data = payload["body"]["data"]
        
        # The data is base64url encoded, so we decode it
        if data:
            text = base64.urlsafe_b64decode(data).decode("utf-8")
            
            # Extract subject for display
            subject = ""
            for header in payload["headers"]:
                if header["name"] == "Subject":
                    subject = header["value"]
                    break
            
            print("\n--- Most Recent Email ---")
            print(f"Subject: {subject}")
            print(f"Content Snippet:\n{text[:500]}...")
        else:
            print("Could not find plain text content in the email.")

    except HttpError as error:
        print(f"An error occurred: {error}")

if __name__ == "__main__":
    # main()
    read_mail("message@adobe.com")