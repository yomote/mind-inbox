# Resource Roles

| Name | Type | RG | Role | Note |
|---|---|---|---|---|
| ca-dev-mindbox-ai-agent | microsoft.app/containerapps | rg-dev-mind-inbox | AI Agent service | FastAPI + Semantic Kernel; orchestrates Azure OpenAI calls and human-in-the-loop tool approval |
| ca-dev-mindbox-voicevox | microsoft.app/containerapps | rg-dev-mind-inbox | VOICEVOX TTS engine | Speech synthesis runtime (open-source VOICEVOX engine) |
| ca-dev-mindbox-vv-wrap | microsoft.app/containerapps | rg-dev-mind-inbox | VOICEVOX wrapper API | FastAPI; bridges BFF and the VOICEVOX engine, handles audio post-processing |
| cae-dev-mindbox | microsoft.app/managedenvironments | rg-dev-mind-inbox | Container app environment | Shared runtime/network for Container Apps |
| oai-dev-mindbox | microsoft.cognitiveservices/accounts | rg-dev-mind-inbox | Azure OpenAI account | GPT-4o for AI Agent inference |
| spch-dev-mindbox | microsoft.cognitiveservices/accounts | rg-dev-mind-inbox | LLM endpoint | Azure OpenAI account for model inference |
| cosmos-dev-mindbox | microsoft.documentdb/databaseaccounts | rg-dev-mind-inbox | General Azure resource | Role not yet classified |
| vnet-dev-mindbox | microsoft.network/virtualnetworks | rg-dev-mind-inbox | Network boundary | Private address space and subnet isolation |
| law-dev-mindbox-ops | microsoft.operationalinsights/workspaces | rg-dev-mind-inbox | Central Log Analytics workspace | Aggregates logs/metrics from BFF, Container Apps, and platform |
| stdevmindboxfunc | microsoft.storage/storageaccounts | rg-dev-mind-inbox | Function runtime storage | Required by Azure Functions for state/queues/triggers |
| asp-dev-mindbox-func | microsoft.web/serverfarms | rg-dev-mind-inbox | Function App Service plan | Compute capacity for the BFF Function App |
| func-dev-mindbox | microsoft.web/sites | rg-dev-mind-inbox | BFF (Azure Functions + tRPC) | Single tRPC entrypoint; orchestrates AI Agent / VOICEVOX wrapper, NOT a chat passthrough |
| swa-dev-mindbox | microsoft.web/staticsites | rg-dev-mind-inbox | Frontend SPA (React + Vite + MUI) | SWA Standard SKU; linked-backend proxies /api/* to BFF, built-in Entra auth |
