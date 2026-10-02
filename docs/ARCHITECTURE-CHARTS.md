# Odoo — Architecture Charts

> Repository: `appolon1908/Odoo`
> Baseline branch: `main`
> Repository-local visual architecture. Update with every material boundary, persistence, integration or deployment change.

## 1. System context
```mermaid
flowchart LR
 A["Staff / Middleware"] --> B["Odoo application + custom addons"]
 B --> R["Odoo<br/>Odoo 19 CRM/business application authority"]
 R --> S["PostgreSQL / filestore"]
 R --> D["Middleware / approved services"]
```

## 2. Internal architecture
```mermaid
flowchart TB
 I["Entrypoint / UI / API / CLI"] --> P["Identity, policy, validation"]
 P --> C["Core domain / orchestration"]
 C --> S["State / configuration / persistence"]
 C --> A["Adapters / integrations"]
 A --> X["Approved dependencies"]
 C --> O["Metrics, logs, traces, audit"]
```

## 3. Critical flow
```mermaid
sequenceDiagram
 participant U as Caller
 participant B as Odoo
 participant P as Policy
 participant C as Core
 participant S as State
 participant X as Dependency
 U->>B: Request / event / action
 B->>P: Authenticate + validate
 P-->>B: Decision
 B->>C: Business record, workflow, addon logic and readback
 C->>S: Read / persist
 C->>X: Bounded integration
 X-->>C: Result / readback
 C-->>U: Normalized response
```

## 4. Deployment and promotion
```mermaid
flowchart LR
 F["Feature branch"] --> T["Tests / validation"]
 T --> PR["Pull request + review"]
 PR --> CI["Required CI green"]
 CI --> MAIN["Protected main merge"]
 MAIN --> CAND["Immutable candidate from exact verified main SHA"]
 CAND --> ST["Staging verification using that candidate"]
 ST --> EX["Readback + exact-SHA certification"]
 EX --> G{"Production approval?"}
 G -- No --> ST
 G -- Yes --> P["Promote the same immutable candidate"]
 P --> H["Health/readiness + rollback check"]
```

## 5. Observability and recovery
```mermaid
flowchart LR
 R["Odoo"] --> M["Metrics / monitoring integration where enabled"]
 R --> L["Logs / audit"]
 R -. "runtime tracing exporter not yet verified" .-> T["Trace pipeline target only"]
 M --> O["Observability stack"]
 L --> O
 O --> A["Dashboards / alerts"]
 R --> B["Database + filestore/config recovery point"]
 B --> RR["Restore / rollback rehearsal"]
```

> Runtime trace export is **UNVERIFIED** in the current repository authority. Correlation/monitoring contracts do not by themselves prove application tracing is active.

## Ownership notes
- **Role:** Odoo 19 CRM/business application authority
- **Primary boundary:** Odoo application + custom addons
- **State/config:** PostgreSQL / filestore
- **Dependencies/consumers:** Middleware / approved services
- Cross-repository effects must use reviewed contracts; production effects remain separately gated.
