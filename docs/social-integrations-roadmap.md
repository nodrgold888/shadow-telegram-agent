# Shadow: social and daily-app integrations

This roadmap prepares Shadow to add Instagram, YouTube, and other services without coupling their credentials, permissions, or data to Telegram. It is a plan only; no external service credentials or integrations are added by this document.

## Progress

- The first safe foundation is in place: an authenticated service catalog shows Telegram's live connection state and marks Instagram and YouTube as planned in the dashboard.
- This catalog is descriptive only. OAuth, token storage, sync, and service actions are not implemented yet; planned cards do not offer fake connect buttons.

## Product direction

- Keep Telegram working as its own integration.
- Give every connected service its own connection state, account identity, permissions, settings, and activity history.
- Let the owner choose which capabilities to enable per account and per service.
- Start with read-only features. Add posting, messaging, or other write actions only after explicit per-action consent and clear confirmation in the dashboard.
- Never combine private data across connected accounts unless the owner explicitly asks for that view.

## Current starting point

- The FastAPI application and dashboard are in `shadow/app.py` and `shadow/dashboard.html`.
- Telegram lifecycle, dialogs, replies, and account switching live primarily in `shadow/telegram_agent.py`.
- Telegram account-specific state is already scoped in `shadow/persist.py` and `shadow/telegram_agent.py`.
- AI providers have a small configuration layer in `shadow/ai_slots.py`; it is an example of keeping provider-specific details out of UI handlers.
- Public Instagram and TikTok video-link downloading exists in `shadow/video_download.py`. This is a media utility, not an authenticated Instagram integration.
- There is not yet a general social integration registry, OAuth connection flow, or account store for non-Telegram services.
- A non-secret service catalog now lives in `shadow/integrations/catalog.py`; it reports the active Telegram account label and explicitly marks future services as planned.

## Proposed architecture

### 1. Integration contract

Create a small `shadow/integrations/` package. Each adapter should declare a stable service key, display name, supported capabilities, required permissions, connection checks, and methods for only the capabilities it implements. Keep provider-specific API calls inside the adapter; dashboard routes should call the contract rather than contain Instagram or YouTube logic.

Initial capability families:

- `identity`: connected profile name, avatar, and service account ID.
- `read`: explicitly permitted profile, media, channel, or activity data.
- `publish`: create or edit content, when official APIs permit it and the owner enables it.
- `messages`: read or send messages only where the service officially supports the requested operation and permission.
- `analytics`: account insights only with the required service permissions.

Capabilities should be discovered from the adapter and granted per connection. Do not assume every service supports every capability.

### 2. Connection and data isolation

Use a provider-neutral connection record with fields equivalent to:

`id`, `owner_id`, `service`, `external_account_id`, `display_name`, `granted_scopes`, `status`, `created_at`, `updated_at`, and a secret reference.

Store tokens and refresh tokens in a server-side secret store or encrypted persistence. The browser receives only masked account identity, granted permissions, and connection status. Never put OAuth secrets, API tokens, authorization codes, or refresh tokens in dashboard HTML, local storage, logs, or ordinary activity records.

Every service request and stored item must be scoped by both `owner_id` and `connection_id`. Account switching must change the active scope before any data is fetched. Disconnecting a service should revoke credentials when supported and remove or retain its data according to a clear owner-facing choice.

### 3. Dashboard experience

Add an **Integrations** area with:

- A service catalog showing available, connected, and action-needed states.
- One card per connected account, including service, account nickname/handle, status, granted permissions, and last successful sync.
- Separate connect, reconnect, permission review, sync, and disconnect actions.
- Capability toggles and action confirmations that are specific to each account.
- A unified activity view only as an optional owner-selected view; its filters must preserve the source service and account.

Keep the existing Telegram pages and account controls intact. Social integrations should not appear as Telegram chats or silently inherit Telegram allowlists, reply settings, or memory.

### 4. First implementation sequence

1. **Discovery and API feasibility:** choose the first user workflows, verify official API access, permissions, review requirements, rate limits, and deployment constraints for each service.
2. **Integration foundation:** add the adapter contract, registry, connection model, secret handling, audit-safe logs, and dashboard catalog with no write actions.
3. **First read-only integration:** choose one service based on the owner's actual use and available official API access. Validate connect, identity display, permission visibility, sync, reconnect, and disconnect.
4. **Instagram:** decide whether the use case is professional-account insights/content management through Meta's official APIs or public-link media analysis. Keep these as different capabilities; a public downloader does not grant account access.
5. **YouTube:** begin with authorized channel identity and read-only channel/video data through Google's official APIs. Treat upload or content-management scopes as a later, separately confirmed feature.
6. **Daily-app candidates:** rank services from the owner's routine (for example, calendar, email, cloud files, notes, or task tools) before adding connectors. Prefer official OAuth APIs and minimum scopes.
7. **Write actions:** add one at a time, with preview, explicit confirmation, idempotency, rate limits, audit history, and a way to revoke access.

## Security and reliability requirements

- Use official APIs and OAuth flows; never ask the owner to paste a service password into Shadow.
- Request the minimum permission set and explain each scope in plain Uzbek and English.
- Keep development, test, and production connections separate.
- Redact credentials and private message/content bodies from logs by default.
- Handle token expiry, revocation, rate limits, retries, and partial sync without exposing secrets.
- Make sync read-only and bounded by time/page limits; show last sync and errors per connection.
- Require ownership checks on every connection, settings, data, and action endpoint.
- Add per-integration tests for account isolation, permission enforcement, token refresh, disconnect, and error handling before enabling a connector in production.

## Decisions to make when implementation starts

1. Which first outcome matters most: view social accounts, summarize new content, create drafts, publish content, or manage messages?
2. Which Instagram account type and YouTube channel should be supported first?
3. Which daily apps should follow those two, ranked by importance?
4. Should the first integrations be read-only, or is there a specific write action to design with confirmation?
5. What hosting-backed secret store is available for non-Telegram OAuth credentials?

## Completion criteria for the foundation

- Telegram behavior remains unchanged.
- Two connections for the same service can be displayed and managed independently.
- Switching active connections never shows another connection's data or settings.
- A disconnected or expired account is clearly identified and can be reconnected or removed.
- No service token or message content is exposed in browser storage, URLs, or routine logs.
- New adapters can be added without putting service-specific API logic in the dashboard page or Telegram agent.
