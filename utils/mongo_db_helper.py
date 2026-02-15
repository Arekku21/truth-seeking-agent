#tools codes for MongoDB operations

import pymongo
from pymongo import MongoClient
from dotenv import load_dotenv
import os

import certifi
ca = certifi.where()

#connect to database
def connect_to_mongo_db(connection_type="local",username=None, password=None, uri=None):
    if connection_type == "local":
        host = os.getenv("MONGO_HOST", "localhost")
        mongo_client = MongoClient(f"mongodb://{username}:{password}@{host}:27017/")
    elif connection_type == "cloud":
        if uri is None:
            raise ValueError("URI must be provided for cloud connection")
        mongo_client = MongoClient(uri, tlsCAFile=ca, tlsAllowInvalidCertificates=True, connect=False)
    else:
        raise ValueError("Invalid connection type. Use 'local' or 'cloud'.")
    
    return mongo_client

#helper function to print records from a collection
def print_collection_records(db, collection_name, query={}, limit=5):
    collection = db[collection_name]
    records = collection.find(query).limit(limit)
    
    for record in records:
        print(record)



