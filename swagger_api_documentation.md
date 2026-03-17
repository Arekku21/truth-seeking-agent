# Recipe Agent API
## Version: 1.0

### /api/v1/batch-process

#### POST
##### Summary:

Batch Process

##### Description:

Triggers the agentic pipeline for a batch of recipe IDs asynchronously.

##### Responses

| Code | Description |
| ---- | ----------- |
| 200 | Successful Response |
| 422 | Validation Error |

### /api/v1/evidences

#### GET
##### Summary:

Fetch Evidence

##### Description:

Retrieve evidences from MongoDB with pagination and filtering.

##### Parameters

| Name | Located in | Description | Required | Schema |
| ---- | ---------- | ----------- | -------- | ---- |
| page | query |  | No | integer |
| limit | query |  | No | integer |
| search | query |  | No |  |
| filter_scenario | query |  | No |  |

##### Responses

| Code | Description |
| ---- | ----------- |
| 200 | Successful Response |
| 422 | Validation Error |

### /api/v1/jobs/{job_id}

#### GET
##### Summary:

Get Job Status

##### Parameters

| Name | Located in | Description | Required | Schema |
| ---- | ---------- | ----------- | -------- | ---- |
| job_id | path |  | Yes | string |

##### Responses

| Code | Description |
| ---- | ----------- |
| 200 | Successful Response |
| 422 | Validation Error |

### /api/v1/jobs

#### GET
##### Summary:

Get All Jobs Status

##### Description:

Fetches the status of all active and recently completed jobs.
Uses Redis MGET for blazing fast retrieval.

##### Responses

| Code | Description |
| ---- | ----------- |
| 200 | Successful Response |
