# mcp_gmail.py
# Modern Gmail MCP integration: label & category management, search by category,
# attachment handling, and production-grade improvements.
#
# Requirements:
#   pip install google-auth google-auth-oauthlib google-api-python-client
#   FastMCP available in your environment
#
# Notes:
# - Ensure credentials.json is the OAuth client secret (desktop or web)
# - token.json will be created/updated.
# - This script uses gmail.modify scope so it can add/remove labels and modify messages.

import asyncio
import os
import os.path
import base64
import json
import logging
from typing import List, Optional, Dict

from mcp.server.fastmcp import FastMCP

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ---------------------------
# Config / Constants
# ---------------------------
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
ATTACHMENTS_DIR = "attachments"
TOKEN_PATH = "token.json"
CREDENTIALS_PATH = "credentials.json"

# Category -> Gmail category mapping (used in search queries)
CATEGORY_KEYWORDS = {
    "primary": "category:primary",
    "promotions": "category:promotions",
    "social": "category:social",
    "updates": "category:updates",
    "forums": "category:forums",
    # "purchases" has no system category; we can search for common purchase keywords or use label.
}

# System category label IDs used in message.labelIds (read-only values assigned by Gmail)
SYSTEM_CATEGORY_LABELS = {
    "promotions": "CATEGORY_PROMOTIONS",
    "social": "CATEGORY_SOCIAL",
    "updates": "CATEGORY_UPDATES",
    "forums": "CATEGORY_FORUMS",
    "primary": "INBOX",  # primary messages are typically in INBOX (not a category label)
    # Purchases: use custom label (see create_label_if_not_exists)
}

# Ensure attachments directory exists
os.makedirs(ATTACHMENTS_DIR, exist_ok=True)

# ---------------------------
# MCP server
# ---------------------------
mcp = FastMCP("gmailmcp", host="0.0.0.0", port=8080)
logging.basicConfig(level=logging.INFO)


# ---------------------------
# Auth helpers
# ---------------------------
def authenticate() -> Credentials:
    """Authenticate and return Google credentials (persist token.json)."""
    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_PATH):
                raise FileNotFoundError(f"{CREDENTIALS_PATH} missing: place your OAuth client secrets there.")
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
            creds = flow.run_local_server(port=0)

        # Save credentials
        with open(TOKEN_PATH, "w") as token:
            token.write(creds.to_json())

    return creds


def build_service():
    creds = authenticate()
    return build("gmail", "v1", credentials=creds)


# ---------------------------
# Gmail Label & Message Utilities
# ---------------------------
def list_labels(service) -> List[Dict]:
    """Return list of labels for the user."""
    try:
        resp = service.users().labels().list(userId="me").execute()
        return resp.get("labels", [])
    except HttpError as e:
        logging.exception("Failed to list labels: %s", e)
        raise


def create_label_if_not_exists(service, label_name: str, label_type: str = "user") -> Dict:
    """
    Create a label if it doesn't exist. Returns the label resource.
    label_type: "user" (custom) or "system" (system labels cannot be created)
    """
    labels = list_labels(service)
    for l in labels:
        if l.get("name", "").lower() == label_name.lower():
            return l

    body = {
        "name": label_name,
        "labelListVisibility": "labelShow",
        "messageListVisibility": "show",
        # color and other metadata can be added here
    }
    try:
        created = service.users().labels().create(userId="me", body=body).execute()
        logging.info("Created label: %s", created.get("id"))
        return created
    except HttpError as e:
        logging.exception("Failed to create label '%s': %s", label_name, e)
        raise


def patch_label_visibility(service, label_id: str, list_visibility: str = "labelShow", message_visibility: str = "show") -> Dict:
    """
    Patch label visibility. list_visibility: 'labelShow' or 'labelHide'
    message_visibility: 'show' or 'hide' (controls whether messages show in message list)
    """
    body = {
        "labelListVisibility": list_visibility,
        "messageListVisibility": message_visibility
    }
    try:
        patched = service.users().labels().patch(userId="me", id=label_id, body=body).execute()
        logging.info("Patched label visibility for %s -> %s / %s", label_id, list_visibility, message_visibility)
        return patched
    except HttpError as e:
        logging.exception("Failed to patch label visibility: %s", e)
        raise


def add_labels_to_message(service, message_id: str, label_ids: List[str]):
    """Add label(s) to a message."""
    body = {"addLabelIds": label_ids}
    try:
        service.users().messages().modify(userId="me", id=message_id, body=body).execute()
        logging.info("Added labels %s to message %s", label_ids, message_id)
    except HttpError as e:
        logging.exception("Failed to add labels: %s", e)
        raise


def remove_labels_from_message(service, message_id: str, label_ids: List[str]):
    """Remove label(s) from a message."""
    body = {"removeLabelIds": label_ids}
    try:
        service.users().messages().modify(userId="me", id=message_id, body=body).execute()
        logging.info("Removed labels %s from message %s", label_ids, message_id)
    except HttpError as e:
        logging.exception("Failed to remove labels: %s", e)
        raise


# ---------------------------
# Message retrieval & processing
# ---------------------------
def _save_attachments_from_parts(service, message_id: str, parts: List[Dict]):
    """Iterate parts and save attachments (if any)."""
    saved = []
    for part in parts:
        filename = part.get("filename")
        body = part.get("body", {})
        mime_type = part.get("mimeType", "")
        if filename:
            att_id = body.get("attachmentId")
            if att_id:
                att = service.users().messages().attachments().get(userId="me", messageId=message_id, id=att_id).execute()
                raw = att.get("data", "")
                file_data = base64.urlsafe_b64decode(raw.encode("UTF-8"))
                path = os.path.join(ATTACHMENTS_DIR, filename)
                with open(path, "wb") as f:
                    f.write(file_data)
                saved.append(path)
                logging.info("Saved attachment %s", path)
        # Some emails nest parts (multipart/*)
        if part.get("parts"):
            saved.extend(_save_attachments_from_parts(service, message_id, part.get("parts")))
    return saved


def _get_text_from_payload(payload: Dict) -> str:
    """
    Extract plain text from payload. Handles simple and multipart messages.
    Returns decoded string (utf-8) or empty string.
    """
    def decode_data(data_str):
        if not data_str:
            return ""
        try:
            return base64.urlsafe_b64decode(data_str.encode("UTF-8")).decode("utf-8", errors="replace")
        except Exception:
            return ""

    # If 'parts' exists, walk it
    parts = payload.get("parts")
    if parts:
        for p in parts:
            # prefer text/plain
            if p.get("mimeType") == "text/plain":
                data = p.get("body", {}).get("data")
                if data:
                    return decode_data(data)
            # nested parts
            if p.get("parts"):
                for np in p.get("parts"):
                    if np.get("mimeType") == "text/plain":
                        data = np.get("body", {}).get("data")
                        if data:
                            return decode_data(data)
        # fallback to first part's body
        first = parts[0]
        return decode_data(first.get("body", {}).get("data"))
    else:
        # direct body
        data = payload.get("body", {}).get("data")
        return decode_data(data)


def get_latest_message_from_sender(service, sender_email: str, category: Optional[str] = None):
    """
    Retrieve latest message id from a sender; optionally filter by category (primary, promotions, social, updates, forums).
    category param is case-insensitive and maps to Gmail category search.
    """
    q = f"from:{sender_email}"
    if category:
        cat_key = category.lower()
        if cat_key in CATEGORY_KEYWORDS:
            q += f" {CATEGORY_KEYWORDS[cat_key]}"
        else:
            # for purchases or unknown categories, attempt to search by label name
            q += f" label:{category}"
    try:
        resp = service.users().messages().list(userId="me", q=q, maxResults=1).execute()
        msgs = resp.get("messages", [])
        if not msgs:
            return None
        msg_id = msgs[0]["id"]
        full = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
        payload = full.get("payload", {})
        text = _get_text_from_payload(payload)
        subject = ""
        for h in payload.get("headers", []):
            if h.get("name", "").lower() == "subject":
                subject = h.get("value", "")
                break
        attachments = []
        # Save attachments if present
        parts = payload.get("parts", [])
        if parts:
            attachments = _save_attachments_from_parts(service, msg_id, parts)
        # persist to file for showcase convenience
        out_path = "latest_email.txt"
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(f"Subject: {subject}\n\nBody:\n{text}\n\nAttachments:\n{json.dumps(attachments, indent=2)}")
        return {
            "id": msg_id,
            "subject": subject,
            "body": text,
            "attachments": attachments,
            "raw_full": full
        }
    except HttpError as e:
        logging.exception("Error retrieving message: %s", e)
        raise

def get_messages_by_label(service, label_name: str, max_results: int = 50) -> List[Dict]:
    """
    Fetch up to `max_results` latest emails filtered purely by label or category.
    Returns simplified message summaries (id, subject, snippet).
    """
    # Try to match a system label (CATEGORY_PROMOTIONS, etc.)
    label_id = None
    label_name_l = label_name.lower()

    # Determine label ID
    if label_name_l in SYSTEM_CATEGORY_LABELS:
        label_id = SYSTEM_CATEGORY_LABELS[label_name_l]
    else:
        # Search for user-created labels
        labels = list_labels(service)
        for lbl in labels:
            if lbl.get("name", "").lower() == label_name_l:
                label_id = lbl["id"]
                break
        if not label_id:
            raise ValueError(f"Label '{label_name}' not found. Check spelling or create it first.")

    # Fetch messages by label
    try:
        resp = service.users().messages().list(
            userId="me",
            labelIds=[label_id],
            maxResults=max_results
        ).execute()
        messages = resp.get("messages", [])
        results = []

        for msg in messages:
            msg_id = msg["id"]
            full = service.users().messages().get(
                userId="me",
                id=msg_id,
                format="metadata",
                metadataHeaders=["Subject", "From", "Date"]
            ).execute()

            headers = {h["name"].lower(): h["value"] for h in full.get("payload", {}).get("headers", [])}
            results.append({
                "id": msg_id,
                "subject": headers.get("subject", "(no subject)"),
                "from": headers.get("from", ""),
                "date": headers.get("date", ""),
                "snippet": full.get("snippet", "")
            })

        return results
    except HttpError as e:
        logging.exception("Failed to fetch messages by label '%s': %s", label_name, e)
        raise



# ---------------------------
# Visibility / Show-Hide approaches
# ---------------------------
# Important notes & limitations:
# - The Gmail API allows patching visibility on *user-created labels* (labelListVisibility / messageListVisibility).
# - System labels (CATEGORY_PROMOTIONS etc.) are not modifiable in the same way.
# Two practical approaches:
#  1) For categories: create a mirror user label (e.g., "mirror_promotions") and set its visibility via labels.patch.
#     Then programmatically mirror messages from 'CATEGORY_PROMOTIONS' into that user label (for quick toggle control).
#  2) For inbox visibility "hide in inbox" -> remove 'INBOX' label (archive) for messages; "show in inbox" -> add 'INBOX'.
#
# Approach 1 gives UI-level control of a custom label; approach 2 affects inbox presence directly.
#
# The toggle_category_visibility tool below implements both: 
# - for system categories we recommend Approach 2 (archive/unarchive)
# - for custom categories (like Purchases) we create a user-label and patch its visibility.

def toggle_category_visibility(service, category: str, action: str = "hide", apply_to_existing: bool = True):
    """
    Toggle visibility for a category.
    - category: e.g., "promotions", "social", "updates", "forums", "purchases"
    - action: "hide" or "show"
    - apply_to_existing: whether to update existing messages in inbox (archive/unarchive)
    Returns a dict with the operation result.
    """
    action = action.lower()
    if action not in ("hide", "show"):
        raise ValueError("action must be 'hide' or 'show'")

    cat = category.lower()
    # If category is one of system categories, use archive/unarchive semantics for inbox visibility
    if cat in SYSTEM_CATEGORY_LABELS and cat != "primary":
        # Find messages in that category (labelId CATEGORY_*)
        query = CATEGORY_KEYWORDS.get(cat)
        if not query:
            query = f"label:{cat}"
        # Fetch a batch of messages (be careful in prod: pagination & rate limits)
        resp = service.users().messages().list(userId="me", q=query, maxResults=200).execute()
        msgs = resp.get("messages", [])
        modified = 0
        for m in msgs:
            mid = m["id"]
            if action == "hide":
                # archive -> remove INBOX label
                try:
                    remove_labels_from_message(service, mid, ["INBOX"])
                    modified += 1
                except Exception:
                    continue
            else:
                # show -> add INBOX label
                try:
                    add_labels_to_message(service, mid, ["INBOX"])
                    modified += 1
                except Exception:
                    continue
        return {"category": cat, "action": action, "modified_messages": modified, "method": "archive_unarchive"}
    else:
        # Treat as user label (e.g., Purchases). Create if missing, then patch visibility
        label_name = category if category.lower() != "purchases" else "Purchases"
        label = create_label_if_not_exists(service, label_name)
        # map action to label visibility
        list_vis = "labelHide" if action == "hide" else "labelShow"
        message_vis = "hide" if action == "hide" else "show"
        patched = patch_label_visibility(service, label["id"], list_visibility=list_vis, message_visibility=message_vis)
        # optional: apply to existing messages - add/remove the label itself
        modified = 0
        if apply_to_existing:
            # find messages in a likely source (e.g., query for label or keywords)
            # caution: this is heuristic for purchases; in real production, you'd have precise rules
            search_q = f'label:{label["name"]}'
            resp = service.users().messages().list(userId="me", q=search_q, maxResults=200).execute()
            msgs = resp.get("messages", [])
            for m in msgs:
                try:
                    if action == "hide":
                        remove_labels_from_message(service, m["id"], [label["id"]])
                    else:
                        add_labels_to_message(service, m["id"], [label["id"]])
                    modified += 1
                except Exception:
                    continue
        return {"category": cat, "action": action, "modified_messages": modified, "method": "patch_label_visibility"}

from difflib import get_close_matches
from rapidfuzz import fuzz, process

async def unified_message_search(
    service,
    category: Optional[str] = None,  # label name, system label ID, or user label ID
    sender: Optional[str] = None,
    keywords: Optional[str] = None,
    match_mode: str = "exact",
    max_results: int = 50,
) -> List[Dict]:
    """
    Intelligent Unified Search API:
    - Supports sender, category (label/system/user), and keywords.
    - match_mode: exact | fuzzy | regex
    """
    match_mode = match_mode.lower().strip()

    search_terms = []

    # If category is provided, determine if it's system label ID or user label name
    if category:
        # If it looks like a system label (CATEGORY_*, SPAM, TRASH, etc.), use labelIds
        if category.startswith("CATEGORY_") or category.upper() in ("SPAM", "TRASH", "IMPORTANT", "INBOX"):
            search_terms.append(f"label:{category}")
        else:
            # Otherwise, treat as user-created label name
            search_terms.append(f"label:{category}")

    if sender:
        search_terms.append(f"from:{sender}")

    if keywords:
        search_terms.append(f'"{keywords}"')

    if not search_terms:
        raise ValueError("At least one of category, sender, or keywords must be provided.")

    gmail_query = " OR ".join(search_terms)
    logging.info(f"[UnifiedSearch] Executing query: {gmail_query}")

    try:
        resp = await asyncio.to_thread(
            service.users().messages().list,
            **{"userId": "me", "q": gmail_query, "maxResults": max_results}
        )
        resp = resp.execute()
        msgs = resp.get("messages", [])
        results = []

        for msg in msgs:
            msg_id = msg["id"]
            full = service.users().messages().get(
                userId="me", id=msg_id, format="full"
            ).execute()
            payload = full.get("payload", {})
            text = _get_text_from_payload(payload)
            headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
            subject = headers.get("subject", "(no subject)")
            sender_field = headers.get("from", "")
            date_field = headers.get("date", "")
            snippet = full.get("snippet", "")

            # Fuzzy / regex filtering (post-Gmail filter)
            match_score = 100
            if keywords and match_mode != "exact":
                if match_mode == "fuzzy":
                    match_score = fuzz.partial_ratio(keywords.lower(), text.lower())
                    if match_score < 60:
                        continue
                elif match_mode == "regex":
                    import re
                    if not re.search(keywords, text, re.IGNORECASE):
                        continue

            results.append({
                "id": msg_id,
                "subject": subject,
                "from": sender_field,
                "date": date_field,
                "snippet": snippet,
                "match_score": match_score,
            })

        # Sort by match_score descending
        return sorted(results, key=lambda x: x["match_score"], reverse=True)[:max_results]

    except HttpError as e:
        logging.exception("Unified search failed: %s", e)
        raise


# ---------------------------
# MCP Tools (exposed over MCP)
# ---------------------------

@mcp.tool()
def hello(name: str) -> str:
    return f"Hello, {name}! This is MCP 🚀"


@mcp.resource("resource://welcome")
def welcome():
    return "Welcome to the MCP server!"


@mcp.tool()
def list_gmail_labels() -> List[Dict]:
    """List all Gmail labels (id + name)."""
    service = build_service()
    labels = list_labels(service)
    # return a simplified view
    return [{"id": l.get("id"), "name": l.get("name"), "type": l.get("type", "")} for l in labels]


@mcp.tool()
def create_label(name: str) -> Dict:
    """Create a user label with the given name (if missing)."""
    service = build_service()
    return create_label_if_not_exists(service, name)


@mcp.tool()
def get_latest_from(sender_email: str, category: Optional[str] = None) -> Dict:
    """
    Retrieve the latest message from 'sender_email'.
    Optional category filter: primary, promotions, social, updates, forums, purchases.
    """
    service = build_service()
    result = get_latest_message_from_sender(service, sender_email, category)
    if not result:
        return {"status": "no_message", "message": f"No messages found for {sender_email} with category={category}"}
    return {"status": "ok", "message_id": result["id"], "subject": result["subject"], "attachments": result["attachments"]}


@mcp.tool()
def add_label(message_id: str, label_name: str) -> Dict:
    """Create (if missing) and add label to message."""
    service = build_service()
    label = create_label_if_not_exists(service, label_name)
    add_labels_to_message(service, message_id, [label["id"]])
    return {"status": "ok", "label_id": label["id"], "label_name": label["name"], "message_id": message_id}


@mcp.tool()
def remove_label(message_id: str, label_name: str) -> Dict:
    """Remove a label (if exists) from a message."""
    service = build_service()
    labels = list_labels(service)
    target = None
    for l in labels:
        if l.get("name", "").lower() == label_name.lower():
            target = l
            break
    if not target:
        return {"status": "not_found", "message": f"Label '{label_name}' not found"}
    remove_labels_from_message(service, message_id, [target["id"]])
    return {"status": "ok", "removed_label_id": target["id"], "message_id": message_id}


@mcp.tool()
def toggle_category(category: str, action: str = "hide", apply_to_existing: bool = True) -> Dict:
    """
    Toggle visibility for a category (hide/show). 
    - category: promotions/social/updates/forums/purchases/primary
    - action: hide or show
    """
    service = build_service()
    return toggle_category_visibility(service, category, action, apply_to_existing)


# For backwards compatibility with your original read_mail
@mcp.tool()
def read_mail(sender_email: str) -> Dict:
    """Retrieve the most recent email from the specified sender and save to latest_email.txt"""
    service = build_service()
    result = get_latest_message_from_sender(service, sender_email)
    if not result:
        return {"status": "empty", "message": f"No emails found from {sender_email}"}
    return {"status": "ok", "subject": result["subject"], "body_snippet": (result["body"][:1024] + "...") if len(result["body"])>1024 else result["body"], "attachments": result["attachments"]}

@mcp.tool()
def fetch_mails_by_label(label_name: str, limit: int = 50) -> Dict:
    """
    Fetch a specified number of latest mails under a given Gmail label or category.
    - label_name: 'promotions', 'social', 'updates', 'forums', or any custom user label.
    - limit: number of emails to retrieve (default 5).
    """
    service = build_service()
    mails = get_messages_by_label(service, label_name, limit)
    if not mails:
        return {"status": "empty", "message": f"No mails found for label '{label_name}'"}
    return {"status": "ok", "count": len(mails), "label": label_name, "mails": mails}
@mcp.tool()
def batch_retreival_mail(
    category: Optional[str] = None,
    sender: Optional[str] = None,
    keywords: Optional[str] = None,
    match_mode: str = "exact",
    limit: int = 5
) -> dict:
    """
    Unified Gmail batch retrieval tool:
    - Accepts arguments directly like other MCP tools
    - Dynamically fetches Gmail labels (system + user-created)
    - match_mode: exact | fuzzy | regex
    """
    service = build_service()

    # Strip and normalize inputs
    category = category.strip() if category else None
    sender = sender.strip() if sender else None
    keywords = keywords.strip() if keywords else None
    match_mode = match_mode.lower().strip() if match_mode else "exact"

    if not any([category, sender, keywords]):
        return {"status": "error", "message": "At least one of category, sender, or keywords must be provided."}

    # Fetch labels dynamically
    try:
        label_results = service.users().labels().list(userId="me").execute()
        labels = label_results.get("labels", [])
        label_lookup = {lbl["name"].lower(): lbl["id"] for lbl in labels}
    except Exception as e:
        return {"status": "error", "message": f"Label fetch failed: {str(e)}"}

    # Gmail system labels
    system_labels = {
        "primary": "CATEGORY_PRIMARY",
        "social": "CATEGORY_SOCIAL",
        "promotions": "CATEGORY_PROMOTIONS",
        "updates": "CATEGORY_UPDATES",
        "forums": "CATEGORY_FORUMS",
        "spam": "SPAM",
        "trash": "TRASH",
        "important": "IMPORTANT"
    }

    # Map category to label ID if provided
    label_id = None
    if category:
        key = category.lower()
        label_id = label_lookup.get(key) or system_labels.get(key) or category

    async def _runner():
        return await unified_message_search(
            service,
            category=label_id,
            sender=sender,
            keywords=keywords,
            match_mode=match_mode,
            max_results=limit
        )

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import nest_asyncio
        nest_asyncio.apply()
        results = loop.run_until_complete(_runner())
    else:
        results = asyncio.run(_runner())

    if not results:
        return {"status": "empty", "message": "No matches found."}

    return {
        "status": "ok",
        "count": len(results),
        "results": results[:limit],
        "matched_label": label_id
    }


# ---------------------------
# Run server
# ---------------------------
if __name__ == "__main__":
    transport = "sse"
    if transport == "stdio":
        print("Starting MCP server with stdio transport...")
        mcp.run(transport="stdio")
    else:
        print("Starting MCP server with sse transport...")
        mcp.run(transport="sse")
