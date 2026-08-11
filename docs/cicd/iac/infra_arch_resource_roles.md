# Resource Roles

| Name | Type | RG | Role | Note |
|---|---|---|---|---|
| ca-dev-mindbox-ai-agent | microsoft.app/containerapps | rg-dev-mind-inbox | AI Agent service | FastAPI + Semantic Kernel; orchestrates Azure OpenAI calls and human-in-the-loop tool approval |
| ca-dev-mindbox-voicevox | microsoft.app/containerapps | rg-dev-mind-inbox | VOICEVOX TTS engine | Speech synthesis runtime (open-source VOICEVOX engine) |
| ca-dev-mindbox-vv-wrap | microsoft.app/containerapps | rg-dev-mind-inbox | VOICEVOX wrapper API | FastAPI; bridges BFF and the VOICEVOX engine, handles audio post-processing |
| cae-dev-mindbox | microsoft.app/managedenvironments | rg-dev-mind-inbox | Container app environment | Shared runtime/network for Container Apps |
| oai-dev-mindbox | microsoft.cognitiveservices/accounts | rg-dev-mind-inbox | Azure OpenAI account | GPT-4o for AI Agent inference |
| spch-dev-mindbox | microsoft.cognitiveservices/accounts | rg-dev-mind-inbox | Azure Speech (STT) | Server-side speech-to-text (F0). BFF issues short-lived tokens via managed identity; the browser streams audio to Speech directly over WebSocket (ADR 0023) |
| cosmos-dev-mindbox | microsoft.documentdb/databaseaccounts | rg-dev-mind-inbox | Problem persistence store (Cosmos DB) | Single persistence store behind the BFF for Problem data (ADR 0030); no TTL is set — items persist until explicitly deleted. history container remains provisioned but unreferenced since ADR 0034 |
| law-dev-mindbox-ops | microsoft.operationalinsights/workspaces | rg-dev-mind-inbox | Central Log Analytics workspace | Aggregates logs/metrics from BFF, Container Apps, and platform |
| stdevmindboxfunc | microsoft.storage/storageaccounts | rg-dev-mind-inbox | Function runtime storage | Required by Azure Functions for state/queues/triggers |
| asp-dev-mindbox-func | microsoft.web/serverfarms | rg-dev-mind-inbox | Function App Service plan | Compute capacity for the BFF Function App |
| func-dev-mindbox | microsoft.web/sites | rg-dev-mind-inbox | BFF (Azure Functions + tRPC) | Single tRPC entrypoint; orchestrates AI Agent / VOICEVOX wrapper, NOT a chat passthrough |
| swa-dev-mindbox | microsoft.web/staticsites | rg-dev-mind-inbox | Frontend SPA (React + Vite + MUI) | Serves the SPA bundle; the browser calls the BFF Function App directly (no linked backend), auth enforced by Functions EasyAuth 401 (ADR 0013) |
