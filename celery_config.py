from celery import Celery
import os
from dotenv import load_dotenv

#!load environment variables
load_dotenv()

#! Reddis url
REDIS_URL = os.getenv("REDIS_URL","redis://[::1]:6379/0")

#! App iniate
app = Celery(
    'agents',
    broker="redis://127.0.0.1:6379/0",
    backend="redis://127.0.0.1:6379/0",
    include=['worker_process'] 
)

#! App config
app.conf.update(
    task_serializer='json',
    result_serializer='json',
    accept_content=['json'],
    result_expires=3600,
)