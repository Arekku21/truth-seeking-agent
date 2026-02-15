# Packaging Instructions
move docker compose file to the root folder of the project. COnfigure the Dockerfile to serve

## Fast API Config and Container
Apply resource limits to prevent monopolising the CPU/RAM. Check docs [here](https://docs.docker.com/engine/containers/resource_constraints/#cpu)

Configure Application into a fastapi application. Check docs [here](https://fastapi.tiangolo.com/tutorial/first-steps/)

### Endpoints
- Batch Recipe Evidence Processing
- Fetch Recipe Evidence (Paginated, Allow search and filtering ) - Evidence & Scenarios etc.
- Celery Job Monitoring for job status -> Current Job(s), progress, job failure, completion

## Add Ollama Service to teh root docker compose file
Ollama docs reference [here](https://docs.ollama.com/docker)
