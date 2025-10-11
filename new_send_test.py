import base64
import mimetypes
import os
from email.message import EmailMessage
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials
import google.auth
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly",
          "https://www.googleapis.com/auth/gmail.send",
          "https://www.googleapis.com/auth/gmail.compose"]


def gmail_create_draft(to_email, subject, message_text, attachment_path=None):
    """Create and insert a draft email with optional attachment."""
    creds = Credentials.from_authorized_user_file("token.json", SCOPES)

    try:
        service = build("gmail", "v1", credentials=creds)
        mime_message = EmailMessage()

        # Set headers
        mime_message["To"] = to_email
        mime_message["From"] = "me"
        mime_message["Subject"] = subject

        # Set email body
        mime_message.set_content(message_text)

        # Add attachment if provided
        if attachment_path:
            type_subtype, _ = mimetypes.guess_type(attachment_path)
            if type_subtype is None:
                maintype, subtype = "application", "octet-stream"
            else:
                maintype, subtype = type_subtype.split("/")

            with open(attachment_path, "rb") as fp:
                mime_message.add_attachment(
                    fp.read(),
                    maintype=maintype,
                    subtype=subtype,
                    filename=os.path.basename(attachment_path)
                )

        # Encode message
        encoded_message = base64.urlsafe_b64encode(mime_message.as_bytes()).decode()
        create_draft_request_body = {"message": {"raw": encoded_message}}

        # Create draft
        draft = service.users().drafts().create(userId="me", body=create_draft_request_body).execute()
        print(f"\nDraft created with ID: {draft['id']}")
        return draft, mime_message

    except HttpError as error:
        print(f"An error occurred: {error}")
        return None, None

def send_draft(draft_id):
    """Send a Gmail draft by draft ID."""
    creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    try:
        service = build("gmail", "v1", credentials=creds)
        sent_message = service.users().drafts().send(userId="me", body={"id": draft_id}).execute()
        print(f"\nDraft sent! Message ID: {sent_message['id']}")
    except HttpError as error:
        print(f"An error occurred while sending the draft: {error}")

def preview_draft(mime_message):
    """Preview draft content before sending."""
    print("\n------ Draft Preview ------")
    print(f"To: {mime_message['To']}")
    print(f"From: {mime_message['From']}")
    print(f"Subject: {mime_message['Subject']}")

    print("\nContent:")
    if mime_message.is_multipart():
        # Iterate through parts and print only text parts
        for part in mime_message.walk():
            content_type = part.get_content_type()
            if content_type == "text/plain":
                try:
                    print(part.get_content())
                except:
                    print("[Unable to decode content]")
    else:
        print(mime_message.get_content())

    # List attachments
    attachments = [part.get_filename() for part in mime_message.iter_attachments()]
    if attachments:
        print("\nAttachments:", ", ".join(attachments))
    else:
        print("\nAttachments: None")
    print("---------------------------\n")


if __name__ == "__main__":
    # Take user input
    to_email = input("Enter recipient email: ").strip()
    subject = input("Enter subject: ").strip()
    message_text = input("Enter email content: ").strip()

    # Ask if user wants to attach a file
    attachment_path = None
    attach_confirm = input("Do you want to attach a file? (yes/no): ").strip().lower()
    if attach_confirm in ["yes", "y"]:
        while True:
            attachment_path_input = input("Enter attachment path: ").strip()
            if os.path.isfile(attachment_path_input):
                attachment_path = attachment_path_input
                break
            else:
                print(f"Invalid file path: '{attachment_path_input}'. Please try again.")

    # Create draft
    draft, mime_message = gmail_create_draft(to_email, subject, message_text, attachment_path)

    if draft:
        # Preview draft
        preview_draft(mime_message)

        # Confirm send
        confirm = input("Do you want to send this draft? (yes/no): ").strip().lower()
        if confirm in ["yes", "y"]:
            send_draft(draft["id"])
        else:
            print("Draft not sent.")
