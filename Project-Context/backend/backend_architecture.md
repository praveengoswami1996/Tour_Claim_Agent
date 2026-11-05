# Backend Architecture — TourProgram API (ASP.NET Core, net6.0)

**Summary.** A classic 3-tier monolith: `TourProgram.API` (controllers + one global authorization filter) → `TourProgram.BAL` (≈27k lines of "Repository" classes that hold *all* business logic, plus a few services) → `TourProgram.DAL` (EF Core 6 `IdentityDbContext`, entities, audit interceptors, migrations) → SQL Server. Authentication is **not** ASP.NET JwtBearer: a global MVC filter (`SSOTokenValidateFilter`) introspects every bearer token against the DFCCIL SSO server. Employee master data is read from a SQL view; approver/CGM/finance roles come from an external **Organization** service over HTTP. One hosted background service (OCR worker) runs in-process. Responses are mostly wrapped in `ResponeModel` (`statusCode, message, data, …`).

## 1. Layer diagram

```mermaid
flowchart TB
  FE[Tour_React SPA / mobile<br/>Bearer OIDC token + DeviceType header] -->|HTTPS REST| MW
  subgraph API[TourProgram.API]
    MW[Middleware: ForwardedHeaders → ExeceptionMiddleware → SwaggerBasicAuth → Swagger → HttpsRedirect → StaticFiles → AuthN/AuthZ (no-op) → CORS]
    F[Global filter SSOTokenValidateFilter<br/>(token introspection)]
    C[19 controller files<br/>BaseController parses JWT claims]
    W[OcrProcessingWorker<br/>BackgroundService]
    MW --> F --> C
  end
  subgraph BAL[TourProgram.BAL]
    R[Repository/Concrete/*Repository.cs<br/>business rules, workflow, PDFs, mail]
    S[Service/*: OcrProcessingService, TokenService (unused), JwtTokenInfo (unused), UserExtensionRepository]
    H[Helpers/TourOverlapValidator]
  end
  subgraph DAL[TourProgram.DAL]
    DB[TourProgramDbContext : IdentityDbContext]
    A[Audit interceptors → AuditLog (hash chain)]
  end
  C --> R
  W --> S
  R --> DB
  S --> DB
  DB --> A
  DB --> SQL[(SQL Server<br/>tables + view vMStEmployeeMasterNG)]
  R -->|HttpClient| ORG[Organization service<br/>hierarchy, cadre/CGM, finance users, role push]
  R -->|HttpClient| WA[WhatsApp via org NotificationManage API]
  R -->|MailKit SMTP| SMTP[Mail server]
  S -->|HttpClient 'Ocr'| OCR[OCR provider API]
  F -->|POST /connect/introspect1| SSO[DFCCIL SSO / IdentityServer]
  EXT[SAP integration / cron] -->|APIKey header, AllowAnonymous| C
```

## 2. Projects & references (from .csproj)

| Project | References | NuGet packages (version) |
|---|---|---|
| TourProgram.API | BAL | Microsoft.AspNetCore.Authentication.JwtBearer 6.0.0 (registered nowhere — see §5), EF Core Design/SqlServer/Tools 6.0.0, Serilog.AspNetCore 6.0.0, Serilog.Settings.Configuration 7.0.0, Serilog.Sinks.MSSqlServer 6.3.0, Swashbuckle.AspNetCore 6.2.3 |
| TourProgram.BAL | DAL | MailKit 4.2.0, Microsoft.AspNetCore.Identity 2.1.39, Microsoft.Extensions.Http 10.0.8, Newtonsoft.Json 13.0.3, PdfSharpCore 1.3.63, System.IdentityModel.Tokens.Jwt 6.8.0 |
| TourProgram.DAL | Logger, Model, Utility | Microsoft.AspNetCore.Identity.EntityFrameworkCore 6.0.0, EF Core Design/SqlServer/Tools 6.0.0 |
| TourProgram.Model | Utility | Microsoft.AspNetCore.Http.Features 2.2.0, Newtonsoft.Json 13.0.3 |
| TourProgram.Utility | — | DinkToPdf 1.0.8 (+CopyDependencies), DocumentFormat.OpenXml 2.20.0, iTextSharp 5.5.13.3, Microsoft.Extensions.Configuration.Abstractions 6.0.0 |
| TourProgram.Logger | — | Microsoft.AspNetCore.Http.Abstractions 2.2.0, Microsoft.Extensions.Hosting.Abstractions 2.2.0, Microsoft.Extensions.Logging.Abstractions 6.0.0 |

Graph: `API → BAL → DAL → {Logger, Model → Utility, Utility}` (strict chain; API sees everything transitively). All target `net6.0`. Note namespace drift: several types live in `HealthClaim.*` namespaces (e.g. `HealthClaim.BAL.Service.TokenService`, `HealthClaim.Utility.Eumus`) — copied from a sibling "HealthClaim/Device" project (**inferred**).

## 3. Program.cs pipeline (`TourProgram.API/Program.cs`)

| Step | Line | Detail |
|---|---|---|
| `AddHttpContextAccessor`, `AddScoped<SSOTokenValidateFilter>` | 19–22 | |
| `AddControllers(o => o.Filters.AddService<SSOTokenValidateFilter>())` | 25–28 | **Global** auth filter on every MVC action |
| Connection string selection | 31–37 | `DeploymentModes` = `PROD_DFCCIL` → `ConnectionStrings:DefaultConnectionProd`; `UAT_DFCCIL` → `DefaultConnectionUAT`; else `DefaultConnectionCetpa` |
| Audit DI + `AddDbContext` with 3 interceptors | 41–50 | `AuditSaveChangesInterceptor`, `AuditTransactionInterceptor` (singletons), `AuditSessionContextInterceptor` (new); `AuditChainVerifier` scoped (no endpoint uses it) |
| Options binding | 53–54 | `MailSettings` → `MailSettingsModel`; `AppConfig` → `ConfigDetails` |
| DinkToPdf `IConverter` singleton | 55 | `SynchronizedConverter(new PdfTools())` |
| Swagger | 56–57 + extension | Registered twice (also in `AddAppliationServices`) |
| `//builder.Services.AddIdentityServices(config);` | 59 | **Commented out** → no JwtBearer scheme, no Identity stores registered |
| `AddAppliationServices(config)` | 60 | see §4 |
| `DomainDetails.Initialize(config)` | 63 | static per-environment base URLs (`TourProgram.Utility/Eumus/RecordMasterEnum.cs:20-52`) |
| Middleware order | 66–98 | `UseForwardedHeaders(XForwardedFor|Proto)` → `MapGet("/health")`, `MapGet("/")` → `ExeceptionMiddleware` → `SwaggerBasicAuthMiddleware` → `UseSwagger`/`UseSwaggerUI` (**both** Dev and non-Dev branches identical, so Swagger is on in prod, gated by basic auth) → `UseHttpsRedirection` → `UseStaticFiles` (serves `wwwroot`, incl. uploaded docs/PDFs) → `UseAuthentication` → `UseAuthorization` → `UseCors("AllowOrigin")` → `MapControllers` |

Observations:
- `UseCors` is placed **after** `UseAuthorization`/`UseStaticFiles` (Program.cs:94); Microsoft guidance puts it before. Static files are therefore served without CORS headers (**inferred**).
- `UseSerilogRequestLogging` is commented out (Program.cs:76) and `UseSerilog()`/`LoggerConfiguration` appear nowhere → the `Serilog` appsettings section (File sink) is **not wired**; logging is the default Microsoft console/debug provider (**inferred** from absence).
- `/health` and `/` are minimal-API endpoints → bypass the MVC SSO filter (unauthenticated).
- `SwaggerBasicAuthMiddleware` (`TourProgram.API/Model/SwaggerBasicAuthMiddleware.cs:16-42`) guards `/swagger*` with Basic auth using **credentials hard-coded in source** (`<redacted>`).
- `ExeceptionMiddleware` (`TourProgram.Logger/ExeceptionMiddleware.cs:23-45`) catches unhandled exceptions → HTTP 500 JSON `{ "statuCode": 500, "message": ex.Message, "details": inner.ToString() }` (camelCase of `AppExecption`; note typo `statuCode`; leaks exception text in all environments — both branches identical).

## 4. DI registrations

`TourProgram.API/Extensions/ApplicationServicesExtensions.cs`:

| Lifetime | Service → Impl | Line |
|---|---|---|
| — | `AddHttpClient()` (default factory) | 16 |
| Scoped | IAccountRepository→AccountRepository, IEmpTourPlanRepository→EmpTourPlanRepository, ITentativeTravelPlanRepository, IActualTravelPlanRepository, ICommanRepository, ITourListingRepository, ITourStatusRepository, IEmployeeRepository, PDFGenerator, IClaimRepository, INotificationRepository, IBulkApprovalRepository, IClaimClarificationRepository, ITourNotAvailedRepostitory, IDashboardRepository, TokenService, IClaimEditRepository, ICGMExcessiveTourRepository, IUserExtensionRepository, ITourRecoveryRepository | 17–39 |
| — | `IProfileRepository` **commented out** | 31 |
| Options | `OcrSettings` ← `Ocr` section | 42 |
| Named HttpClient | `"Ocr"` (`OcrProcessingService.HttpClientName`), BaseAddress=`Ocr:BaseUrl`, Timeout=`Ocr:TimeoutSeconds` | 44–48 |
| Singleton | OcrQueueSignal | 49 |
| Scoped | IOcrRepository→OcrRepository, IOcrProcessingService→OcrProcessingService | 50–51 |
| Hosted | **OcrProcessingWorker** (`TourProgram.API/BackgroundServices/OcrProcessingWorker.cs`) — runs `Ocr:MaxParallel` loops; each claims one pending `OcrDocuments` row via a new DI scope, calls OCR; waits on `OcrQueueSignal` or `Ocr:PollSeconds` | 52 |
| Swagger | `SwaggerDoc v1` + `Bearer` http security scheme | 56–82 |
| CORS | policy `AllowOrigin` = `AllowedOrigins[]` (≈37 DFCCIL/CETPA/localhost/dev origins), any header, any method, no credentials flag | 84–98 |

`TourProgram.API/Extensions/IdentityServicesExtension.cs` (`AddIdentityServices`) — **dead code** (call commented at Program.cs:59). It would register `AddIdentityCore<ApplicationUser>` + EF stores, JwtBearer with symmetric key from `TokenKey`, `ValidateIssuer=false`, `ValidateAudience=false`, `ValidateLifetime=true`, `ClockSkew=0`, `IdentityModelEventSource.ShowPII=true` (lines 18–47). None of this is active.

`TourProgram.API/Extensions/AuditUserProvider.cs` — singleton `IAuditUserProvider`; reads `Authorization` header, decodes JWT **without validation**, returns (`name`|`unique_name`|`UserId`, `UserId`) for audit rows (lines 16–36).

## 5. Data-access pattern

| Element | Evidence | Reality |
|---|---|---|
| `IGenricRepository<T>` / `GenricRepository<T>` | `BAL/Repository/IGenricRepository.cs`, `GenricRepository.cs` (67 lines) | Base class with `GetFirstOrDefault/GetAll/Add/Remove/RemoveRange` over `DbSet<T>`; 14 repositories inherit it but mostly use `_dbContext` directly |
| `IUnitOfWork` / `UnitOfWork` | `BAL/Repository/UnitOfWork.cs:5-24` | Only `Save()`; **not registered in DI, not used anywhere** |
| Transactions | e.g. `EmpTourPlanRepository.cs:~330` `BeginTransactionAsync` + `TourOverlapValidator.LockEmployeeAsync` (`sp_getapplock`, `BAL/Helpers/Touroverlapvalidator.cs:58`) | Explicit EF transactions + SQL app locks to serialize per-employee tour writes |
| DbContext | `DAL/TourProgramDbContext.cs:17` `IdentityDbContext<ApplicationUser, UserRole, string>` | ~50 DbSets; `VEmployeeMaster` keyless mapped `ToView("vMStEmployeeMasterNG")` (line 109) |
| Migrations | `DAL/Migrations` | 51 migrations, 2025‑05‑12 → 2026‑10‑30 (+ snapshot) |

## 6. Response envelope

`TourProgram.Model/Dtos/Response/ResponeModel.cs:10-22`:

| Property | Type | JSON (System.Text.Json default camelCase; no custom JSON options found) |
|---|---|---|
| StatusCode | `System.Net.HttpStatusCode` | `statusCode` — serialized as **integer** (no enum converter registered) |
| Message | string | `message` |
| Data | object | `data` |
| DataLength, PageNumber, PageSize, TotalRecords | int | paging metadata |
| Error | bool | `error` |
| ErrorDetail | object | `errorDetail` |

Behaviour (important for the agent):
- Actions returning `Task<ResponeModel>` (most of Claim/TourPlan/TourListing/TourStatus…) return **HTTP 200 regardless of `statusCode` inside the body** (**inferred** from ASP.NET semantics). The agent must check `body.statusCode` / `body.error`.
- Actions returning `IActionResult` (CGMExcessiveTour, Ocr, TourRecovery, Dashboard, some Claim/ClaimEdit/TourPlan) set the real HTTP status (e.g. `StatusCode((int)response.StatusCode, response)` in `TourRecoveryController.cs:107-122`).
- Non-envelope shapes: SSO filter 401/403 → `{message, statusCode, timestamp}` (`SSOTokenValidateFilter.cs:168-193`); exception middleware → `{statuCode, message, details}`; some Dashboard errors → anonymous `{error, message, statusCode}` (`DashboardController.cs:94-99`).
- There is no shared `BaseController` envelope helper; `BaseController` (`Controllers/BaseController.cs`) only parses the token (see authentication_and_authorization.md).

## 7. Outbound integrations (names / config keys only)

| Target | Called from | Endpoint path (relative) | Base URL source | Client |
|---|---|---|---|---|
| SSO token introspection | `ActionFilter/SSOTokenValidateFilter.cs:216-282` | `/connect/introspect1` (form field `Token`, no client auth) | `ApiBaseUrlsProd:SSO` / `ApiBaseUrlsDfcuat:SSO` / `CETPA:SSO` (last key **absent** in appsettings) | `new HttpClient()` sync `.Result` per request |
| Org hierarchy | `EmployeeRepository.cs:94-117` | `/api/Organization/GetOrganizationHierarchy/` | `DomainDetails.apiBaseUrlOrganization` (hard-coded per mode) | factory |
| Cadre / CGM allotments | `EmployeeRepository.cs:122-161`, `CGMExcessiveTourRepository.cs:417, 503` | `/api/MobileVisibility/GetCadreAllotementsAsync` | `ApiBaseUrlsProd:Organization` / `ApiBaseUrlsDfcuat:Organization` | factory / `new HttpClient()` |
| Finance users per unit | `EmployeeRepository.cs:202-227`, `TourRecoveryRepository.cs:~60` | `/api/AdminManage/GetAdminTourReimbursement` | same | factory |
| Role push to org portal | `EmpTourPlanRepository.cs:301-322`, `TourStatusRepository.cs:~1010` | `POST /api/AdminManage/CreateRolesandPermssion` (fixed roleId, portalId=1) | `DomainDetails.apiBaseUrlOrganization` | `new HttpClient()` / factory |
| WhatsApp | `AccountRepository.cs:88-209` and others | `POST /api/NotificationManage/SendWhatsAppMessageNew` | `ApiBaseUrlsProd:Whatsapp` / `ApiBaseUrlsDfcuat:Whatsapp` | `new HttpClient()` |
| SMTP mail | `NotificationRepository.cs:106-153` | MailKit SMTP | `MailSettings:*` | — |
| OCR provider | `BAL/Service/OcrProcessingService.cs:152-164` | `Ocr:ExtractPath`, header `x-api-key` = `Ocr:ApiKey` | `Ocr:BaseUrl` | named `"Ocr"` |
| DMS | — | **Not found in code** (files stored on local disk under `wwwroot/…` and `Ocr:StoragePath`) | — | — |
| Inbound SAP | `ClaimController.cs:163-232` | `GET GetSapDetails`, `POST UpdateSapDetails` (header `APIKey` = `ApiKeys:Sap`) | — | — |
| Inbound cron | `CGMExcessiveTourController.cs:272-313` | `POST SendMonthlyNotifications` (header `apiKey` = `APIKEYS:MonthlyNotification`) | — | — |

## 8. Audit subsystem (`TourProgram.DAL/Audit/*`, high level)

| File | Role |
|---|---|
| AuditSaveChangesInterceptor.cs (320) | On every `SaveChanges`, writes one `AuditLog` row per changed column (table, record id, action, old/new, changedBy, userId) via a *separate* context, batched 500; Identity credential columns stored as `[redacted]`; rows are SHA‑256 hash-chained |
| AuditTransactionInterceptor.cs (52) | Defers audit rows of saves inside an explicit transaction until commit; drops them on rollback |
| AuditSessionContextInterceptor.cs (33) | On connection open runs `sp_set_session_context 'AuditSource'='App'` (read-only) so DB triggers skip EF writes |
| AuditChain.cs (34) | Hash definition (`prevHash|table|id|action|col|old|new|by|userId|changedAt`), IST timestamps |
| AuditChainVerifier.cs (246) | Re-computes chain, reports breaks; registered but **no API endpoint** exposes it |
| DAL/Scripts/AuditLog_*.sql | Table DDL, DB triggers for non-EF writes, immutability phase-1 |

User identity on audit rows comes from the **unvalidated** JWT claims `UserId` / `name` (`API/Extensions/AuditUserProvider.cs`). Calls made by an agent with a pass-through user token will be audited as that user; a service-account token without `UserId` would be audited with empty user.

## 9. Configuration keys (`TourProgram.API/appsettings.json`, values `<redacted>`)

| Section / key | Used at |
|---|---|
| `TokenKey` | IdentityServicesExtension.cs:28, TokenService.cs:35 (both dead) |
| `DeploymentModes` (`UAT_CETPA` / `UAT_DFCCIL` / `PROD_DFCCIL`; currently UAT_DFCCIL) | Program.cs:31, filter, many repos (13 refs) |
| `ConnectionStrings:{DefaultConnectionCetpa, DefaultConnectionUAT, DefaultConnectionProd}` | Program.cs:33-36 |
| `Logging:LogLevel:*` | framework |
| `AppConfig:OtpCode` → `ConfigDetails.OtpCode` | bound, **never read** |
| `ApiKeys:Sap` | ClaimController.cs:28 |
| `AllowedHosts` | framework |
| `MailSettings:{Server, Port, SenderName, SenderEmail, UserName, Password}` | NotificationRepository |
| `APIKEYS:MonthlyNotification` | CGMExcessiveTourController.cs:281 |
| `Ocr:{BaseUrl, ExtractPath, ApiKey, TimeoutSeconds, MaxParallel, MaxAttempts, RetryDelaySeconds, PollSeconds, MaxFileSizeMB, MaxFilesPerUpload, StoragePath}` | OCR services |
| `Serilog:{Using, MinimumLevel, WriteTo[File], Enrich, Properties}` | not wired (see §3) |
| `ApiBaseUrlsProd:{Organization, SSO, Whatsapp}`, `ApiBaseUrlsDfcuat:{Organization, SSO, Whatsapp}`, `ApiBaseUrlsCetpauat:{Organization, SSO}` | filter, repos |
| `AllowedOrigins[]` | CORS |
| Referenced in code but **absent** from appsettings: `SuperAdmin` (AccountController.cs:68, AccountRepository.cs:301), `CETPA:SSO` (filter:290), `TourExtensionSettings:DefaultExtensionMonths`, `TokenKeysso` (commented) | |

`appsettings.Development.json` only sets `Logging`. Secrets (DB passwords, SMTP password, OCR key, SAP key, OTP, Swagger creds, a fallback SAP API key literal in `ClaimController.cs:24`) are committed in source/config — `<redacted>`.

## 10. Environments / launchSettings

`Properties/launchSettings.json`: profile `TourProgram.API` (Kestrel `https://localhost:7010;http://localhost:5156`, launch `swagger`, `ASPNETCORE_ENVIRONMENT=Development`), `IIS Express` (http 15624 / ssl 44384). Business environment is chosen by `DeploymentModes`, not `ASPNETCORE_ENVIRONMENT`. UAT modes redirect outgoing WhatsApp/e-mail to a fixed test recipient (`AccountRepository.cs:120-124, 262-265`; values omitted).

## Must remain in .NET
- All workflow/state transitions, claim amount calculation, PDF generation, tour-overlap locking, audit hash chain, SAP integration, OCR worker — they live in BAL repositories and DB transactions.
- Token introspection and any future server-side authorization.

## Reusable by the Python agent
- All REST endpoints under `/api/{Controller}/…`, called with the user's bearer token; parse `ResponeModel` and treat `statusCode`/`error` in the body as authoritative.
- `/health` for liveness.
- OCR endpoints (`/api/Ocr/*`) for document extraction instead of re-implementing OCR.

## Gaps (no existing API)
- No endpoint for audit-chain verification / audit history query.
- No server-side "who am I + roles" that works (see auth doc: `/api/Account/profile` is broken).
- No DMS API; documents are only reachable via static `wwwroot` paths or OCR `GetFile`.

## Unresolved questions
1. Is Serilog configured elsewhere at deploy time (e.g. web.config / environment)? Not found in code.
2. Production values of `DeploymentModes` per deployment and whether `UAT_CETPA` is still used (its SSO key `CETPA:SSO` is missing → all tokens rejected in that mode).
3. Is the `wwwroot` upload folder (≈430 files incl. personal documents in repo) intentionally public via `UseStaticFiles`?
4. Is `Microsoft.Extensions.Http 10.0.8` on net6.0 intentional (version mismatch)?
