# Vera Composer deployment package

## Run locally
```bash
pip install -r requirements.txt
uvicorn app:app --reload
```

Endpoints:
- GET /healthz
- GET /metadata
- POST /compose

## Render
Create a new Web Service from this directory/repository. Render will use `render.yaml` or the equivalent build/start commands.
