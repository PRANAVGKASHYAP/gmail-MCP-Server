# test_monitor.py

import time
import os
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# --- Copy your build_index function here ---
from build import build_index 

SOURCE_DIRECTORY = "attachments"

class AttachmentManager(FileSystemEventHandler):
    def on_created(self, event):
        if not event.is_directory:
            print(f"✅ Event Detected: New file -> {os.path.basename(event.src_path)}")
            build_index()

    def on_deleted(self, event):
        if not event.is_directory:
            print(f"❌ Event Detected: Deleted file -> {os.path.basename(event.src_path)}")
            build_index()

def start_file_monitor():
    os.makedirs(SOURCE_DIRECTORY, exist_ok=True)
    
    event_handler = AttachmentManager()
    observer = Observer()
    observer.schedule(event_handler, SOURCE_DIRECTORY, recursive=False)
    observer.start()
    print("--- ✅ File monitor is running. Waiting for changes... ---")
    return observer

if __name__ == "__main__":
    # Start the monitor
    observer = start_file_monitor()
    try:
        # Keep the script running forever to let the monitor work
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        # Stop the monitor cleanly when you press Ctrl+C
        observer.stop()
    observer.join()
    print("--- File monitor stopped. ---")