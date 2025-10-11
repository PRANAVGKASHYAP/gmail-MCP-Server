# this file is used to moniter the attachment folder 
# this is used to dynamically update the vector embeddings 

import time
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from langchain_community.vectorstores import FAISS
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
import os
from build import build_index

PERSIST_DIRECTORY = "faiss_index"
SOURCE_DIRECTORY = "attachments"

class AttachmentManager(FileSystemEventHandler):

    def on_create(self , event):
        # this method is to handes events when the mail reader adds files to the attachment folder
        print("new files is added .... updating teh index ")
        file_name = event.src_path

        if not event.is_directory and file_name.find(".pdf") != -1 :
            print(f"✅ New file detected: {os.path.basename(event.src_path)}. Triggering index rebuild.")
            build_index()

    def on_delete(self , event):
        # this method just re builds the embeddings 
        print("a file is deleted ... rebuilding the index ")
        file_name = event.src_path
        if not event.is_directory and file_name.find(".pdf") != -1:
            print(f"the file {event.src_path} is deleted ")
            build_index()




def start_file_monitor():
    """Initializes and starts the file system monitor in the background."""
    print("--- Starting attachment folder monitor ---")
    
    os.makedirs(SOURCE_DIRECTORY , exist_ok=True)
    eventHandler = AttachmentManager()
    observer = Observer()
    observer.schedule(eventHandler , SOURCE_DIRECTORY , recursive=False)
    observer.start()
    print("--- ✅ Monitor is running in the background ---")
    return observer

