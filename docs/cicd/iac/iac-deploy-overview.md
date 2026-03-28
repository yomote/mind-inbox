# インフラ / デプロイ概要

このプロジェクトは、Azure リソース作成、認証設定、成果物デプロイ、削除が別レイヤーに分かれています。
混線しやすいので、流れを Mermaid で整理します。

## 1. 全体像

```mermaid
flowchart TD
    U[運用者]
    R1[cicd/iac/main-bootstrap.bicep]
    R2[cicd/iac/main-config.bicep]
    M1[bootstrap-core.bicep]
    M2[static-site-auth.bicep]
    RG[リソース グループ]
    SWA[Azure Static Web Apps]
    FUNC[Azure Functions]
    SQL[Azure SQL]
    KV[Key Vault]
    LAW[Log Analytics]
    UAMI[共有 Bootstrap UAMI<br/>環境 RG の外で管理]
    ENTRA[Entra アプリ登録]
    FE[フロントエンドのビルドとデプロイ]
    BE[バックエンドのデプロイ]
    USERS[Entra ユーザー]

    U --> R1
    U --> R2
    U --> FE
    U --> BE
    U --> USERS

    R1 --> M1
    R2 --> M2

    M1 --> RG
    RG --> SWA
    RG --> FUNC
    RG --> SQL
    RG --> KV
    RG --> LAW

    UAMI --> M2
    M2 --> ENTRA
    M2 --> SWA
    M2 --> FUNC

    FE --> SWA
    BE --> FUNC
    USERS --> ENTRA
```

## 2. 構築と更新の責務分離

```mermaid
flowchart LR
    subgraph 初回構築
        B1[main-bootstrap.bicep]
        B2[bootstrap-core.bicep]
        B3[ベース Azure リソース]
        B1 --> B2 --> B3
    end

    subgraph 認証設定
        C1[main-config.bicep]
        C2[static-site-auth.bicep]
        C3[Entra アプリ自動作成<br/>または既存アプリひも付け]
        C4[SWA app settings と<br/>Function authsettingsV2]
        C1 --> C2 --> C3 --> C4
    end

    subgraph 成果物デプロイ
        D1[cicd/scripts/deploy/deploy-frontend.sh]
        D2[cicd/scripts/deploy/deploy-backend.sh]
        D3[フロントエンド / バックエンド成果物を反映]
        D1 --> D3
        D2 --> D3
    end
```

## 3. Entra 認証の流れ

```mermaid
sequenceDiagram
    participant Admin as 運用者
    participant UAMI as 共有 UAMI
    participant Config as main-config.bicep
    participant Script as デプロイスクリプト
    participant Entra as Entra ID
    participant SWA as Static Web Apps
    participant User as 利用者

    Admin->>Config: main-config.bicep を実行
    Config->>Script: static-site-auth の deployment script を実行
    Script->>UAMI: az login --identity
    UAMI->>Entra: アプリ登録を作成または更新
    Entra-->>Script: clientId, secret, appObjectId
    Script->>SWA: Set AZURE_CLIENT_ID / SECRET / TENANT_ID
    Script->>SWA: 認証設定を反映

    User->>SWA: / にアクセス
    SWA->>Entra: ログインへリダイレクト
    Entra-->>SWA: callback に code と id_token を返却
    SWA-->>User: 認証済みセッションでアプリ利用開始
```

## 4. UAMI が必要な理由

```mermaid
flowchart TD
    A[Entra アプリ登録を作成したい]
    B[テナント レベル権限が必要]
    C[ARM の RBAC だけでは足りない]
    D[環境内リソースだけでは自己完結できない]
    E[環境外で用意した共有 UAMI を使う]

    A --> B --> C --> D --> E
```

## 5. 削除の流れ

```mermaid
flowchart TD
    OP[運用者が cicd/scripts/env/cleanup-env.sh を実行]
    OUT[deployment outputs を取得]
    CHECK{Entra アプリは自動作成か}
    DELAPP[Entra アプリ登録を削除]
    KEEPAPP[手動管理アプリは残す]
    DELRG[リソース グループを削除]
    KEEPID[共有 bootstrap UAMI は残す]

    OP --> OUT --> CHECK
    CHECK -->|はい| DELAPP --> DELRG
    CHECK -->|いいえ| KEEPAPP --> DELRG
    DELRG --> KEEPID
```

## 6. 実運用メモ

- `main-bootstrap.bicep` は Azure リソース作成を担当
- `main-config.bicep` は Entra 認証設定を担当
- frontend/backend の成果物反映は別スクリプトで行う
- Entra アプリ自動作成を使うなら、事前に共有 UAMI を準備する
- 環境削除では、自動作成した Entra アプリ登録だけを消し、共有 UAMI は残す
