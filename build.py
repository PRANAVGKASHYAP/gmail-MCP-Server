# this is a class that is used to create a vector embeddings for the docs in the attachments folder 
# this file runs only once to create the vector embeddings 

from langchain_community.vectorstores import FAISS
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.document_loaders import DirectoryLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter

# The folder where your 'read_mail' tool saves attachments
SOURCE_DIRECTORY = "attachments"
# The folder where we will save the persistent vector store
PERSIST_DIRECTORY = "faiss_index"


# class createStore:
    
#     def create_vector_and_save_to_disk(self):
        
#         #1 load the documents in the attachment folder 
#         print("Loading the documents from the attachments folder...")

#         loader = DirectoryLoader(SOURCE_DIRECTORY , glob="**/*", show_progress=True)
#         documents = loader.load()

#         print("Splitting the documents into chunks")
#         recurssive_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
#         docs = recurssive_splitter.split_documents(documents)

#         print("converting the doc splits into vector embeddings/index")
#         embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
#         vectorstore = FAISS.from_documents(docs, embeddings)

#         print("svaing the vectorstore to disk")
#         vectorstore.save_local(PERSIST_DIRECTORY)
#         print("Vector store saved to disk successfully")

def build_index():
    """Function to build the vector index and save it to disk."""

    print(f"starting t build the index from scatch to make the embeddings and store it in the disc")

    loader = DirectoryLoader(SOURCE_DIRECTORY , glob="**/*", show_progress=True)
    curr_docs = loader.load()

    if not curr_docs:
        print("no ducuents to build an index ....")
        return
    
    print("chunking the documents ....")
    splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=100)
    doc_splits = splitter.split_documents(curr_docs)

    embeddingModel = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
    vectorStore = FAISS.from_documents(doc_splits , embeddingModel)
    vectorStore.save_local(PERSIST_DIRECTORY)

    print("the index is built and stored into the disc")


if __name__ == '__main__':
    build_index()