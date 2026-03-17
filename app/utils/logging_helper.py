import logfire
import logging
import sys

#helper function to setup logfire logging
def setup_logfire_logging(api_key: str, app_name: str):
    logfire.configure(token=api_key, service_name=app_name)
    logging.getLogger().addHandler(logfire.LogfireLoggingHandler())
    logfire.info("Logfire logging initialized and instrumented.")

#local file logger
def get_my_logger(name):
    """
    Creates a consistent logger for both Main script and Celery workers.
    """
    logger = logging.getLogger(name)
    
    # Check if logger already has handlers (prevents duplicate logs)
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        
        # 1. The Format: Time | Level | Script Name | Message
        formatter = logging.Formatter(
            '%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )

        # 2. Handler: Print to Console (Standard Output)
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

        # 3. Handler: Save to File (History)
        file_handler = logging.FileHandler("agent_system.log")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger