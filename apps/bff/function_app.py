import datetime
import json
from typing import Any, Callable, Dict, Optional

import azure.functions as func

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

OPENAPI_PATHS: Dict[str, Dict[str, Dict[str, Any]]] = {}
OPENAPI_SCHEMAS: Dict[str, Dict[str, Any]] = {}


def register_openapi(
    *,
    path: str,
    method: str,
    operation: Dict[str, Any],
    schemas: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Callable:
    def decorator(func_handler: Callable) -> Callable:
        if path not in OPENAPI_PATHS:
            OPENAPI_PATHS[path] = {}
        OPENAPI_PATHS[path][method.lower()] = operation

        if schemas:
            OPENAPI_SCHEMAS.update(schemas)

        return func_handler

    return decorator


def _openapi_spec() -> Dict[str, Any]:
    return {
        "openapi": "3.0.3",
        "info": {
            "title": "Mind Inbox Backend API",
            "version": "1.0.0",
            "description": "Azure Functions backend API",
        },
        "servers": [{"url": "/api"}],
        "paths": OPENAPI_PATHS,
        "components": {
            "schemas": OPENAPI_SCHEMAS,
        },
    }


HEALTH_RESPONSE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "example": "ok"},
        "service": {
            "type": "string",
            "example": "mind-inbox-backend",
        },
        "timestamp": {
            "type": "string",
            "format": "date-time",
            "example": "2026-03-22T00:00:00+00:00",
        },
    },
    "required": ["status", "service", "timestamp"],
}

HEALTH_OPERATION: Dict[str, Any] = {
    "summary": "Health check",
    "operationId": "health",
    "responses": {
        "200": {
            "description": "Service is healthy",
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/HealthResponse"}
                }
            },
        }
    },
}


@app.route(route="health", methods=["GET"])
@register_openapi(
    path="/health",
    method="get",
    operation=HEALTH_OPERATION,
    schemas={"HealthResponse": HEALTH_RESPONSE_SCHEMA},
)
def health(req: func.HttpRequest) -> func.HttpResponse:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    body = {
        "status": "ok",
        "service": "mind-inbox-backend",
        "timestamp": now,
    }
    return func.HttpResponse(
        json.dumps(body),
        status_code=200,
        mimetype="application/json",
    )


@app.route(route="swagger.json", methods=["GET"])
def swagger_json(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(_openapi_spec()),
        status_code=200,
        mimetype="application/json",
    )


@app.route(route="docs", methods=["GET"])
def swagger_ui(req: func.HttpRequest) -> func.HttpResponse:
    html = """<!doctype html>
<html lang=\"en\">
    <head>
        <meta charset=\"UTF-8\" />
        <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />
        <title>Mind Inbox API Docs</title>
        <link
            rel=\"stylesheet\"
            href=\"https://unpkg.com/swagger-ui-dist@5/swagger-ui.css\"
        />
    </head>
    <body>
        <div id=\"swagger-ui\"></div>
        <script src=\"https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js\"></script>
        <script>
            window.onload = () => {
                window.ui = SwaggerUIBundle({
                    url: '/api/swagger.json',
                    dom_id: '#swagger-ui'
                });
            };
        </script>
    </body>
</html>
"""
    return func.HttpResponse(html, status_code=200, mimetype="text/html")
