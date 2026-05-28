# Frontend / IHM Specification - Chatbot Application
## Version 2.0 - Documentation complète post-implémentation

---

## Vue d'ensemble

Interface web moderne pour chatbot avec système d'autorisation à 3 niveaux (user/manager/root) et 3 modes d'authentification (none/local/sso).

### Stack technique

- **Framework**: Next.js 16.0.4 (App Router, export statique)
- **Language**: TypeScript 5.x
- **UI Library**: React 18.3.1
- **Chat Interface**: NLUX 2.0 (streaming SSE)
- **Styling**: Tailwind CSS v4 (PostCSS)
- **Icons**: Lucide React
- **File Upload**: react-dropzone
- **Testing**: Jest + React Testing Library + Playwright
- **Linting**: ESLint 9.15.0 (flat config)
- **Deployment**: Docker (Caddy web server)

### Architecture globale

```
┌─────────────────────────────────────────────────────────────┐
│                        Browser                               │
│  ┌───────────────────────────────────────────────────────┐  │
│  │              React Application                         │  │
│  │  ┌──────────────────────────────────────────────┐    │  │
│  │  │           Custom Hooks Layer                  │    │  │
│  │  │  - useAuth (authentication)                   │    │  │
│  │  │  - useConversations (state + CRUD)            │    │  │
│  │  │  - useSettings (user preferences)             │    │  │
│  │  │  - useFileUpload (upload + progress)          │    │  │
│  │  └──────────────────────────────────────────────┘    │  │
│  │  ┌──────────────────────────────────────────────┐    │  │
│  │  │            API Client Layer                   │    │  │
│  │  │  - apiClient (HTTP methods)                   │    │  │
│  │  │  - authApi (authentication endpoints)         │    │  │
│  │  │  - conversationsApi (CRUD operations)         │    │  │
│  │  │  - groupsApi (conversation groups)            │    │  │
│  │  │  - filesApi (upload/delete)                   │    │  │
│  │  │  - settingsApi (user settings)                │    │  │
│  │  │  - userManagementApi (admin operations)       │    │  │
│  │  └──────────────────────────────────────────────┘    │  │
│  │  ┌──────────────────────────────────────────────┐    │  │
│  │  │           Component Tree                      │    │  │
│  │  │  - page.tsx (root)                            │    │  │
│  │  │  - Sidebar (conversations management)         │    │  │
│  │  │  - ChatInterface (NLUX + SSE streaming)       │    │  │
│  │  │  - SettingsPanel (user preferences)           │    │  │
│  │  │  - UserManagementPanel (admin)                │    │  │
│  │  └──────────────────────────────────────────────┘    │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
           │                           │
           │ REST API (JSON)           │ SSE (text/event-stream)
           │ + FormData (upload)       │
           ▼                           ▼
┌─────────────────────────────────────────────────────────────┐
│                   FastAPI Backend                            │
│  - Base URL: http://localhost:8000 (dev)                    │
│  - Authentication: Bearer token (Authorization header)       │
│  - Content-Type: application/json                            │
│  - Streaming: Server-Sent Events (SSE)                       │
│  - File Storage: MinIO                                       │
│  - Database: ArangoDB                                        │
└─────────────────────────────────────────────────────────────┘
```

---

## 1. Communication Backend - Architecture détaillée

### 1.1 Client API - Configuration de base

**Fichier**: `src/lib/api/client.ts`

**Configuration**:
```typescript
// Base URL (configurable via env)
const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

// Headers par défaut pour toutes les requêtes
headers: {
  'Content-Type': 'application/json',
  'Authorization': `Bearer ${token}`,  // Token récupéré depuis localStorage
}
```

**Gestion des erreurs**:
- Classe `ApiError` personnalisée avec `status`, `message`, `data`
- Status HTTP < 200 ou >= 300 → erreur
- Network error → `ApiError(0, 'Network error or server unreachable')`
- Parsing JSON automatique des réponses
- Parsing JSON automatique des erreurs backend

**Méthodes disponibles**:
- `apiClient.get<T>(endpoint, options?)` → GET request
- `apiClient.post<T>(endpoint, data?, options?)` → POST request
- `apiClient.put<T>(endpoint, data?, options?)` → PUT request
- `apiClient.delete<T>(endpoint, options?)` → DELETE request
- `apiClient.uploadFile(endpoint, file, onProgress?)` → XMLHttpRequest avec progress tracking

### 1.2 Endpoints d'authentification

**Fichier**: `src/lib/hooks/useAuth.ts` (exports `authApi`, `sessionApi`, `userManagementApi`)

#### GET /api/auth/config
**Description**: Récupère la configuration d'authentification du serveur  
**Headers**: Aucun (public)  
**Response**:
```typescript
{
  "config": {
    "mode": "none" | "local" | "sso",
    "allowMultiLogin": boolean,
    "maintenanceMode": boolean,
    "ssoConfig"?: {
      "tokenHeader": string,
      "nameHeader"?: string,
      "emailHeader"?: string,
      "firstNameHeader"?: string,
      "lastNameHeader"?: string
    }
  }
}
```

#### GET /api/auth/generic
**Description**: Récupère l'utilisateur générique (mode "none")  
**Headers**: Authorization (Bearer token)  
**Response**:
```typescript
{
  "user": {
    "id": string,
    "email": string,
    "role": "user" | "manager" | "root",
    "createdAt": string (ISO 8601),
    "firstName"?: string,
    "lastName"?: string,
    "isActive"?: boolean
  }
}
```

#### GET /api/auth/verify
**Description**: Vérifie un token JWT et retourne l'utilisateur (mode "local")  
**Headers**: Authorization (Bearer token)  
**Response**: Même structure que `/api/auth/generic`

#### GET /api/auth/sso/verify
**Description**: Vérifie la session SSO via les headers HTTP (mode "sso")  
**Headers**: Authorization + headers SSO configurés côté serveur  
**Response**: Même structure que `/api/auth/generic`

#### POST /api/auth/login
**Description**: Authentification username/password (mode "local")  
**Headers**: Content-Type: application/json  
**Request Body**:
```typescript
{
  "username": string,  // ou "email"
  "password": string
}
```
**Response**:
```typescript
{
  "token": string,  // JWT token
  "user": {
    "id": string,
    "email": string,
    "role": "user" | "manager" | "root",
    "createdAt": string,
    // ... autres champs
  }
}
```
**Comportement frontend**:
1. Appel API
2. Stockage token dans `localStorage.setItem('auth_token', token)`
3. Mise à jour state global avec user
4. Redirect vers application principale

#### POST /api/auth/logout
**Description**: Déconnexion de la session courante  
**Headers**: Authorization (Bearer token)  
**Response**: `204 No Content` ou `200 OK`

#### POST /api/auth/revoke-own-session
**Description**: Force logout - révoque la session propre de l'utilisateur  
**Headers**: Authorization (Bearer token)  
**Response**: `204 No Content`  
**Comportement frontend**:
1. Appel API
2. `localStorage.removeItem('auth_token')`
3. `window.location.reload()` pour forcer rechargement complet

#### POST /api/auth/revoke-all-sessions
**Description**: Révoque toutes les sessions (root uniquement)  
**Headers**: Authorization (Bearer token)  
**Permission**: Uniquement pour rôle "root"  
**Response**: `204 No Content`

#### GET /api/auth/sessions
**Description**: Récupère toutes les sessions actives (root uniquement)  
**Headers**: Authorization (Bearer token)  
**Response**:
```typescript
{
  "sessions": Array<{
    "id": string,
    "userId": string,
    "createdAt": string,
    "expiresAt": string,
    "ip": string,
    "userAgent": string
  }>
}
```

### 1.3 Endpoints de gestion des utilisateurs

**Fichier**: `src/lib/hooks/useAuth.ts` (export `userManagementApi`)

#### GET /api/users
**Description**: Liste tous les utilisateurs (manager/root)  
**Permission**: Manager (leurs groupes) ou Root (tous)  
**Response**:
```typescript
{
  "users": Array<User>
}
```

#### GET /api/users/:userId
**Description**: Récupère un utilisateur par ID  
**Permission**: Manager (si dans leurs groupes) ou Root  
**Response**:
```typescript
{
  "user": User
}
```

#### POST /api/users
**Description**: Crée un nouvel utilisateur (root uniquement)  
**Request Body**:
```typescript
{
  "email": string,
  "password": string,
  "role": "user" | "manager" | "root",
  "firstName"?: string,
  "lastName"?: string
}
```
**Response**:
```typescript
{
  "user": User
}
```

#### PUT /api/users/:userId
**Description**: Met à jour un utilisateur (manager/root)  
**Request Body**:
```typescript
{
  "firstName"?: string,
  "lastName"?: string,
  "email"?: string,
  "isActive"?: boolean
}
```
**Response**:
```typescript
{
  "user": User
}
```

#### PUT /api/users/:userId/role
**Description**: Assigne un rôle à un utilisateur (root uniquement)  
**Request Body**:
```typescript
{
  "role": "user" | "manager" | "root"
}
```
**Response**:
```typescript
{
  "user": User
}
```

#### DELETE /api/users/:userId
**Description**: Supprime un utilisateur (root uniquement)  
**Response**: `204 No Content`

### 1.4 Endpoints de gestion des groupes utilisateurs

**Fichier**: `src/lib/hooks/useAuth.ts` (export `userManagementApi`)

#### GET /api/user-groups
**Description**: Liste tous les groupes d'utilisateurs  
**Response**:
```typescript
{
  "groups": Array<{
    "id": string,
    "name": string,
    "managerIds": string[],
    "memberIds": string[],
    "createdAt": string
  }>
}
```

#### POST /api/user-groups
**Description**: Crée un groupe d'utilisateurs (root uniquement)  
**Request Body**:
```typescript
{
  "name": string
}
```
**Response**:
```typescript
{
  "group": UserGroup
}
```

#### PUT /api/user-groups/:groupId
**Description**: Met à jour le nom d'un groupe  
**Request Body**:
```typescript
{
  "name": string
}
```
**Response**:
```typescript
{
  "group": UserGroup
}
```

#### DELETE /api/user-groups/:groupId
**Description**: Supprime un groupe d'utilisateurs (root uniquement)  
**Response**: `204 No Content`

#### POST /api/user-groups/:groupId/members
**Description**: Ajoute un utilisateur à un groupe  
**Request Body**:
```typescript
{
  "userId": string
}
```
**Response**:
```typescript
{
  "group": UserGroup  // avec memberIds mis à jour
}
```

#### DELETE /api/user-groups/:groupId/members/:userId
**Description**: Retire un utilisateur d'un groupe  
**Response**:
```typescript
{
  "group": UserGroup  // avec memberIds mis à jour
}
```

#### POST /api/user-groups/:groupId/managers
**Description**: Assigne un manager à un groupe (root uniquement)  
**Request Body**:
```typescript
{
  "userId": string
}
```
**Response**:
```typescript
{
  "group": UserGroup  // avec managerIds mis à jour
}
```

#### DELETE /api/user-groups/:groupId/managers/:userId
**Description**: Retire un manager d'un groupe (root uniquement)  
**Response**:
```typescript
{
  "group": UserGroup  // avec managerIds mis à jour
}
```

#### POST /api/admin/maintenance
**Description**: Active/désactive le mode maintenance (root uniquement)  
**Request Body**:
```typescript
{
  "enabled": boolean
}
```
**Response**: `200 OK`

### 1.5 Endpoints des conversations

**Fichier**: `src/lib/api/conversations.ts` (exports `conversationsApi`, `groupsApi`)

#### GET /api/conversations
**Description**: Liste toutes les conversations de l'utilisateur courant  
**Headers**: Authorization (Bearer token)  
**Response**:
```typescript
{
  "conversations": Array<{
    "id": string,
    "title": string,
    "userId": string,
    "createdAt": string (ISO 8601),
    "updatedAt": string (ISO 8601),
    "groupId"?: string,  // undefined = ungrouped
    "sharedWithGroups"?: string[],
    "messageCount"?: number
  }>
}
```

#### GET /api/conversations/:id
**Description**: Récupère une conversation par ID  
**Response**:
```typescript
{
  "conversation": Conversation
}
```

#### GET /api/conversations/:id/messages
**Description**: Récupère les messages d'une conversation  
**Response**:
```typescript
{
  "messages": Array<{
    "id": string,
    "conversationId": string,
    "role": "user" | "assistant",
    "content": string,
    "timestamp": string (ISO 8601),
    "metadata"?: object
  }>
}
```

#### POST /api/conversations
**Description**: Crée une nouvelle conversation  
**Request Body**:
```typescript
{
  "title": string
}
```
**Response**:
```typescript
{
  "conversation": Conversation
}
```

#### PUT /api/conversations/:id
**Description**: Met à jour une conversation (titre ou groupId)  
**Request Body**:
```typescript
{
  "title"?: string,
  "groupId"?: string | undefined  // undefined pour dégrouper
}
```
**Response**:
```typescript
{
  "conversation": Conversation  // avec champs mis à jour
}
```
**Note importante**: Le frontend envoie `undefined` pour dégrouper, pas `null`

#### DELETE /api/conversations/:id
**Description**: Supprime une conversation  
**Response**: `204 No Content`

#### POST /api/conversations/:id/share
**Description**: Partage une conversation avec des groupes d'utilisateurs  
**Request Body**:
```typescript
{
  "groupIds": string[]  // IDs des user groups
}
```
**Response**:
```typescript
{
  "conversation": Conversation  // avec sharedWithGroups mis à jour
}
```

#### POST /api/conversations/:id/unshare
**Description**: Retire le partage d'une conversation  
**Request Body**:
```typescript
{
  "groupIds": string[]  // IDs des user groups à retirer
}
```
**Response**:
```typescript
{
  "conversation": Conversation  // avec sharedWithGroups mis à jour
}
```

#### GET /api/conversations/shared
**Description**: Récupère les conversations partagées avec l'utilisateur  
**Response**:
```typescript
{
  "conversations": Array<Conversation>
}
```

### 1.6 Endpoints des groupes de conversations

**Fichier**: `src/lib/api/conversations.ts` (export `groupsApi`)

#### GET /api/groups
**Description**: Liste tous les groupes de conversations de l'utilisateur  
**Response**:
```typescript
{
  "groups": Array<{
    "id": string,
    "name": string,
    "userId": string,
    "conversationIds": string[],
    "createdAt": string,
    "updatedAt": string
  }>
}
```

#### GET /api/groups/:id
**Description**: Récupère un groupe par ID  
**Response**:
```typescript
{
  "group": ConversationGroup
}
```

#### POST /api/groups
**Description**: Crée un nouveau groupe de conversations  
**Request Body**:
```typescript
{
  "name": string
}
```
**Response**:
```typescript
{
  "group": ConversationGroup
}
```

#### PUT /api/groups/:id
**Description**: Met à jour le nom d'un groupe  
**Request Body**:
```typescript
{
  "name": string
}
```
**Response**:
```typescript
{
  "group": ConversationGroup
}
```

#### DELETE /api/groups/:id
**Description**: Supprime un groupe de conversations  
**Note**: Les conversations ne sont PAS supprimées, seulement dégroupées  
**Response**: `204 No Content`

#### POST /api/groups/:groupId/conversations
**Description**: Ajoute une conversation à un groupe  
**Request Body**:
```typescript
{
  "conversationId": string
}
```
**Response**:
```typescript
{
  "group": ConversationGroup  // avec conversationIds mis à jour
}
```

#### DELETE /api/groups/:groupId/conversations/:conversationId
**Description**: Retire une conversation d'un groupe  
**Response**:
```typescript
{
  "group": ConversationGroup  // avec conversationIds mis à jour
}
```

### 1.7 Endpoint de chat streaming (SSE)

**Fichier**: `src/components/chat/ChatInterface.tsx`

#### POST /api/chat/stream
**Description**: Envoie un message et reçoit la réponse en streaming SSE  
**Headers**:
```
Content-Type: application/json
Authorization: Bearer {token}
```
**Request Body**:
```typescript
{
  "message": string,
  "conversationId": string,
  "promptCustomization"?: string  // system prompt override
}
```
**Response**: `text/event-stream` (Server-Sent Events)

**Format SSE**:
```
data: chunk1
data: chunk2
data: chunk3
data: [DONE]
```

**Implémentation frontend** (streaming adapter NLUX):
```typescript
const adapter = {
  streamText: async (message: string, observer: StreamObserver) => {
    // 1. Envoi POST avec fetch
    const response = await fetch(API_ENDPOINTS.CHAT_STREAM, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`,
      },
      body: JSON.stringify({ message, conversationId, promptCustomization }),
    });

    // 2. Lecture du stream
    const reader = response.body.getReader();
    const decoder = new TextDecoder();

    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        observer.complete();
        break;
      }

      // 3. Décodage et parsing SSE
      const chunk = decoder.decode(value, { stream: true });
      const lines = chunk.split('\n');

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const data = line.substring(6).trim();
          if (data && data !== '[DONE]') {
            observer.next(data);  // Envoi à NLUX
          }
        }
      }
    }
  }
};
```

**Gestion des erreurs**:
- Pas de conversationId → `observer.error(new Error('No conversation selected'))`
- HTTP error → `observer.error(new Error('HTTP {status}'))`
- Pas de response.body → `observer.error(new Error('No response body'))`
- Stream error → `observer.error(error)`

### 1.8 Endpoints de fichiers

**Fichier**: `src/lib/api/files.ts` (export `filesApi`)

#### POST /api/files/upload
**Description**: Upload d'un fichier avec suivi de progression  
**Headers**:
```
Authorization: Bearer {token}
Content-Type: multipart/form-data
```
**Request Body**: FormData avec clé `file`

**Implémentation frontend** (XMLHttpRequest pour progress tracking):
```typescript
const xhr = new XMLHttpRequest();
const formData = new FormData();
formData.append('file', file);

xhr.upload.addEventListener('progress', (e) => {
  if (e.lengthComputable) {
    const progress = (e.loaded / e.total) * 100;
    onProgress(progress);  // Callback pour UI
  }
});

xhr.open('POST', `${API_BASE_URL}/api/files/upload`);
xhr.setRequestHeader('Authorization', `Bearer ${token}`);
xhr.send(formData);
```

**Response**:
```typescript
{
  "file": {
    "id": string,
    "name": string,
    "size": number,
    "type": string,
    "url": string,  // URL MinIO
    "status": "completed",
    "uploadedAt": string (ISO 8601)
  }
}
```

**Contraintes frontend**:
- Max file size: 10MB (`UI_CONSTANTS.MAX_FILE_SIZE`)
- Max files per upload: 5 (`UI_CONSTANTS.MAX_FILES_PER_UPLOAD`)
- Types autorisés: PDF, TXT, CSV, JSON, Markdown, PNG, JPEG, GIF, WebP

#### GET /api/files
**Description**: Liste tous les fichiers uploadés par l'utilisateur  
**Response**:
```typescript
{
  "files": Array<UploadedFile>
}
```

#### DELETE /api/files/:fileId
**Description**: Supprime un fichier  
**Response**: `204 No Content`

### 1.9 Endpoints de settings

**Fichier**: `src/lib/api/settings.ts` (export `settingsApi`)

#### GET /api/settings
**Description**: Récupère les préférences de l'utilisateur  
**Response**:
```typescript
{
  "settings": {
    "id": string,
    "userId": string,
    "promptCustomization": string,  // system prompt override
    "theme"?: "light" | "dark",  // future
    "language"?: string,  // future
    "updatedAt": string
  }
}
```

#### PUT /api/settings
**Description**: Met à jour les préférences de l'utilisateur  
**Request Body**:
```typescript
{
  "promptCustomization"?: string,
  "theme"?: "light" | "dark",
  "language"?: string
}
```
**Response**:
```typescript
{
  "settings": UserSettings  // avec champs mis à jour
}
```

---

## 2. Architecture Frontend - Analyse détaillée du code

### 2.1 Structure des dossiers

```
src/
├── app/
│   ├── globals.css              # Tailwind imports + custom styles
│   ├── layout.tsx               # Root layout (metadata, fonts, MSW)
│   └── page.tsx                 # Main application page (single page app)
│
├── components/
│   ├── MSWProvider.tsx          # Mock Service Worker provider (tests)
│   │
│   ├── auth/
│   │   └── LoginForm.tsx        # Login form (mode "local")
│   │
│   ├── chat/
│   │   ├── ChatContainer.tsx    # Layout wrapper (sidebar + chat)
│   │   └── ChatInterface.tsx    # NLUX chat + SSE streaming adapter
│   │
│   ├── maintenance/
│   │   ├── MaintenanceBanner.tsx  # Sticky banner (maintenance mode)
│   │   └── MaintenancePage.tsx    # Full page (maintenance mode)
│   │
│   ├── settings/
│   │   ├── PromptCustomization.tsx  # System prompt override
│   │   └── SettingsPanel.tsx        # Modal with tabs (settings + admin)
│   │
│   ├── admin/
│   │   └── UserManagementPanel.tsx  # User/group management (manager/root)
│   │
│   ├── sharing/
│   │   └── ShareConversationModal.tsx  # Share conversation with user groups
│   │
│   ├── sidebar/
│   │   ├── Sidebar.tsx                # Main sidebar component
│   │   ├── ConversationList.tsx       # List with groups + drag-and-drop
│   │   ├── ConversationItem.tsx       # Single conversation item
│   │   ├── ConversationGroup.tsx      # Collapsible group with conversations
│   │   └── NewConversationButton.tsx  # Button to create conversation
│   │
│   ├── upload/
│   │   ├── FileUploadPanel.tsx   # Modal with dropzone + file list
│   │   ├── FileDropzone.tsx      # Drag-and-drop area (react-dropzone)
│   │   └── FileList.tsx          # List of pending + uploaded files
│   │
│   └── ui/                       # Reusable UI components
│       ├── Button.tsx            # Primary/secondary/danger variants
│       ├── Input.tsx             # Text/email/password inputs
│       ├── Modal.tsx             # Overlay modal with close button
│       ├── IconButton.tsx        # Icon-only button (sm/md size)
│       └── ContextMenu.tsx       # Right-click menu (rename/delete/share)
│
├── lib/
│   ├── api/                      # API client functions
│   │   ├── client.ts             # Base HTTP client (GET/POST/PUT/DELETE/upload)
│   │   ├── conversations.ts      # conversationsApi, groupsApi
│   │   ├── files.ts              # filesApi
│   │   └── settings.ts           # settingsApi
│   │
│   ├── hooks/                    # Custom React hooks
│   │   ├── useAuth.ts            # Authentication state + permissions
│   │   ├── useConversations.ts   # Conversations + groups state
│   │   ├── useSettings.ts        # User settings state
│   │   └── useFileUpload.ts      # File upload with progress tracking
│   │
│   └── utils/                    # Utility functions
│       ├── constants.ts          # API endpoints, storage keys, UI constants
│       ├── permissions.ts        # Calculate permissions from role
│       └── storage.ts            # localStorage wrapper (tokens)
│
├── types/                        # TypeScript type definitions
│   ├── auth.ts                   # User, ServerAuthConfig, UserSession, etc.
│   ├── conversation.ts           # Conversation, ConversationGroup, Message
│   ├── file.ts                   # PendingFile, UploadedFile
│   └── settings.ts               # UserSettings, UpdateSettingsRequest
│
└── mocks/                        # MSW mock handlers (development/tests)
    ├── browser.ts                # Browser MSW setup
    ├── server.ts                 # Node MSW setup (tests)
    ├── handlers/                 # Request handlers par endpoint
    └── data/                     # Mock data fixtures
```

### 2.2 Hooks personnalisés - Détails d'implémentation

#### useAuth Hook
**Fichier**: `src/lib/hooks/useAuth.ts`

**State géré**:
```typescript
interface AuthState {
  user: User | null;
  authMode: AuthMode;  // 'none' | 'local' | 'sso'
  serverConfig: ServerAuthConfig | null;
  loading: boolean;
  error: string | null;
}
```

**Fonctions exposées**:
- `login(email, password)` → Promise<User>
- `logout()` → Promise<void>
- `forceLogout()` → Promise<void> (revoke + reload)
- `checkAuth()` → Vérifie token/session au mount

**Exports**:
- `useAuth()` - Hook principal
- `authApi` - Endpoints d'authentification
- `sessionApi` - Gestion des sessions
- `userManagementApi` - Gestion users/groups (admin)

**Logique d'initialisation** (mount):
1. Appel `authApi.getServerConfig()` → récupère mode
2. Si mode === 'none':
   - Appel `authApi.getGenericUser()`
   - Set user générique
3. Si mode === 'local':
   - Récupère token depuis `storage.getAuthToken()`
   - Si token existe:
     - Appel `authApi.verifyToken(token)`
     - Si valide: set user
     - Si invalide: clear token, show login
   - Si pas de token: show login
4. Si mode === 'sso':
   - Appel `authApi.verifySsoSession()`
   - Si valide: set user
   - Si invalide: show error

**Calcul des permissions**:
```typescript
const permissions = calculatePermissions(user.role, serverConfig);
```
Utilise `src/lib/utils/permissions.ts` qui retourne un objet `UserPermissions`.

#### useConversations Hook
**Fichier**: `src/lib/hooks/useConversations.ts`

**State géré**:
```typescript
interface ConversationsState {
  conversations: Conversation[];
  groups: ConversationGroup[];
  currentConversationId: string | null;
  loading: boolean;
  error: string | null;
}
```

**Fonctions exposées**:
- `loadConversations()` → Promise<void>
- `loadGroups()` → Promise<void>
- `createConversation(title)` → Promise<Conversation>
- `updateConversation(id, data)` → Promise<Conversation>
- `deleteConversation(id)` → Promise<void>
- `createGroup(name)` → Promise<ConversationGroup>
- `updateGroup(id, name)` → Promise<ConversationGroup>
- `deleteGroup(id)` → Promise<void>
- `setCurrentConversationId(id)` → void (+ localStorage)

**Logique d'initialisation** (mount):
1. Appel `conversationsApi.getAll()` → load conversations
2. Appel `groupsApi.getAll()` → load groups
3. Récupère `currentConversationId` depuis localStorage
4. Set state

**Storage persistant**:
- `currentConversationId` stocké dans `localStorage.setItem('current_conversation', id)`
- Récupéré au mount pour restaurer la conversation active

#### useSettings Hook
**Fichier**: `src/lib/hooks/useSettings.ts`

**State géré**:
```typescript
interface SettingsState {
  settings: UserSettings | null;
  loading: boolean;
  isSaving: boolean;
  error: string | null;
}
```

**Fonctions exposées**:
- `loadSettings()` → Promise<void>
- `updateSettings(data)` → Promise<void>
- `updatePromptCustomization(prompt)` → Promise<void>

**Logique d'initialisation** (mount):
1. Appel `settingsApi.get()` → load settings
2. Set state

**Debouncing**:
- `updateSettings` est debounced pour éviter trop de requêtes
- Délai typique: 500ms après dernière modification

#### useFileUpload Hook
**Fichier**: `src/lib/hooks/useFileUpload.ts`

**State géré**:
```typescript
interface FileUploadState {
  pendingFiles: PendingFile[];      // Files en cours d'upload
  uploadedFiles: UploadedFile[];    // Files uploadés avec succès
  isUploading: boolean;
}
```

**Fonctions exposées**:
- `uploadFiles(files)` → Promise<void>
- `removePendingFile(id)` → void
- `removeUploadedFile(id)` → Promise<void>
- `clearAllFiles()` → void

**Gestion du state des fichiers**:
```typescript
type FileStatus = 'pending' | 'uploading' | 'completed' | 'error';

interface PendingFile {
  id: string;
  name: string;
  size: number;
  type: string;
  file: File;
  status: FileStatus;
  progress?: number;    // 0-100
  error?: string;
}
```

**Logique d'upload**:
1. Validation des fichiers (taille, type, nombre)
2. Ajout dans `pendingFiles` avec status 'pending'
3. Pour chaque fichier:
   - Status → 'uploading'
   - Appel `filesApi.upload(file, onProgress)`
   - Callback `onProgress` → update progress
   - Si succès: status → 'completed', déplacer vers `uploadedFiles`
   - Si erreur: status → 'error', set error message
4. Cleanup des fichiers completed après 3 secondes

### 2.3 Composants principaux - Analyse

#### page.tsx (Root Application)
**Fichier**: `src/app/page.tsx`

**Responsabilités**:
- Point d'entrée de l'application
- Gestion du state d'authentification
- Routing conditionnel basé sur auth state
- Affichage des différentes pages selon le contexte

**Logic flow**:
```typescript
export default function Page() {
  const { user, loading, authMode, serverConfig } = useAuth();

  // Loading state
  if (loading) return <LoadingSpinner />;

  // Maintenance mode (non-root users)
  if (serverConfig?.maintenanceMode && user?.role !== 'root') {
    return <MaintenancePage />;
  }

  // Login form (mode local, no user)
  if (authMode === 'local' && !user) {
    return <LoginForm onLogin={handleLogin} />;
  }

  // Main application
  if (user) {
    return <ChatContainer />;
  }

  // Error state
  return <ErrorPage />;
}
```

#### ChatContainer.tsx
**Fichier**: `src/components/chat/ChatContainer.tsx`

**Layout**:
```
┌────────────────────────────────────────────────────┐
│  Header [Logo] [User Info] [Settings] [Admin]     │
├──────────┬─────────────────────────────────────────┤
│          │                                          │
│ Sidebar  │       ChatInterface                      │
│ (320px)  │       (flex-1)                           │
│          │                                          │
└──────────┴─────────────────────────────────────────┘
```

**Props passées**:
- À Sidebar: `conversations`, `groups`, `currentConversationId`, callbacks
- À ChatInterface: `conversationId`, `promptCustomization`

#### Sidebar.tsx
**Fichier**: `src/components/sidebar/Sidebar.tsx`

**State local**:
```typescript
interface SidebarState {
  showNewGroupModal: boolean;
  showRenameModal: boolean;
  showRenameGroupModal: boolean;
  groupName: string;
  conversationTitle: string;
  renameConversationId: string | null;
  renameGroupId: string | null;
}
```

**Fonctionnalités**:
1. **New Conversation Button**:
   - Appelle `createConversation('New Conversation')`
   - Auto-sélectionne la nouvelle conversation
   - Scroll vers le haut de la liste

2. **Conversation Context Menu** (clic droit):
   - Rename → ouvre modal avec input pré-rempli
   - Delete → confirmation + appel `deleteConversation(id)`
   - Share → ouvre ShareConversationModal

3. **Group Management**:
   - Create group → modal avec input nom
   - Rename group → modal avec input nom pré-rempli
   - Delete group → confirmation + appel `deleteGroup(id)`
   - Les conversations ne sont pas supprimées, juste dégroupées

4. **Drag and Drop**:
   - Conversations draggables
   - Drop zones: groupes + zone "Ungrouped"
   - `handleMoveConversationToGroup(convId, groupId | null)`
   - Appel `updateConversation(convId, { groupId: groupId ?? undefined })`

#### ChatInterface.tsx
**Fichier**: `src/components/chat/ChatInterface.tsx`

**Intégration NLUX**:
```typescript
import { AiChat } from '@nlux/react';
import { useAsStreamAdapter } from '@nlux/react';
import '@nlux/themes/nova.css';

const adapter = useAsStreamAdapter({
  streamText: async (message, observer) => {
    // Implementation SSE streaming
  }
});

return (
  <AiChat
    adapter={adapter}
    personaOptions={{
      assistant: {
        name: 'AI Assistant',
        avatar: '...',
        tagline: 'Your intelligent assistant'
      },
      user: {
        name: currentUser.name,
        avatar: '...'
      }
    }}
    conversationOptions={{
      conversationStarters: [...],
      initialMessages: loadedMessages
    }}
  />
);
```

**Chargement de l'historique**:
1. useEffect sur `conversationId`
2. Si conversationId existe:
   - Appel `conversationsApi.getMessages(conversationId)`
   - Conversion des messages en format NLUX (`ChatItem[]`)
   - Set `initialMessages` pour NLUX
3. Si conversationId null:
   - Clear initialMessages
   - Show empty state

**Streaming SSE** (détaillé section 1.7):
- Fetch POST vers `/api/chat/stream`
- Lecture du ReadableStream
- Parsing ligne par ligne ("data: chunk\n")
- Envoi des chunks à NLUX via `observer.next(chunk)`
- Gestion erreurs via `observer.error(error)`
- Completion via `observer.complete()`

#### LoginForm.tsx
**Fichier**: `src/components/auth/LoginForm.tsx`

**Props**:
```typescript
interface LoginFormProps {
  onLogin: (email: string, password: string) => Promise<User>;
  loading?: boolean;
  error?: string;
}
```

**Validation client-side**:
- Email required: `if (!email.trim()) setLocalError('Email is required')`
- Password required: `if (!password.trim()) setLocalError('Password is required')`
- Priorité: email checked avant password
- Erreur parent (props) prioritaire sur erreur locale

**Submit flow**:
1. Validation des champs
2. Clear local error
3. Appel `onLogin(email, password)` (parent)
4. Parent gère le storage du token et la mise à jour du state

#### FileUploadPanel.tsx
**Fichier**: `src/components/upload/FileUploadPanel.tsx`

**Composants enfants**:
- `FileDropzone`: Zone drag-and-drop (react-dropzone)
- `FileList`: Affichage pending + uploaded files avec progress bars

**Validation des fichiers**:
```typescript
const validateFile = (file: File): string | null => {
  // Taille max
  if (file.size > UI_CONSTANTS.MAX_FILE_SIZE) {
    return `File too large (max ${UI_CONSTANTS.MAX_FILE_SIZE / 1024 / 1024}MB)`;
  }

  // Types autorisés
  if (!UI_CONSTANTS.ALLOWED_FILE_TYPES.includes(file.type)) {
    return `File type not allowed (${file.type})`;
  }

  return null;  // OK
};
```

**Flow d'upload**:
1. User drop files ou click browse
2. Validation des fichiers
3. Ajout dans pendingFiles (status 'pending')
4. Pour chaque fichier valide:
   - Status → 'uploading'
   - `uploadFiles([file])` du hook
   - Progress bar update via callback
   - Status → 'completed' ou 'error'
5. Fichiers completed déplacés dans uploadedFiles après 3s

#### UserManagementPanel.tsx
**Fichier**: `src/components/admin/UserManagementPanel.tsx`

**Tabs**:
1. **Users Tab** (manager/root):
   - Liste tous les utilisateurs (ou ceux des groupes gérés)
   - Actions: Create, Edit, Delete, Assign Role, Toggle Active
   - Filtres: par rôle, par statut

2. **User Groups Tab** (root):
   - Liste tous les groupes d'utilisateurs
   - Actions: Create, Rename, Delete, Assign Manager, Add/Remove Members
   - Affichage: managers + membres pour chaque groupe

3. **System Tab** (root):
   - Toggle maintenance mode
   - Revoke all sessions
   - View active sessions

**Permissions conditionnelles**:
- Manager: ne voit que les groupes qu'il gère
- Root: voit tout, peut tout modifier

### 2.4 Types TypeScript - Structures de données

**Fichier**: Types disponibles dans `/mnt/project/` (pas de structure src/)

#### Types d'authentification
```typescript
// User
interface User {
  id: string;
  email: string;
  role: 'user' | 'manager' | 'root';
  createdAt: string;
  firstName?: string;
  lastName?: string;
  isActive?: boolean;
}

// Auth mode
type AuthMode = 'none' | 'local' | 'sso';

// Server config
interface ServerAuthConfig {
  mode: AuthMode;
  allowMultiLogin: boolean;
  maintenanceMode: boolean;
  ssoConfig?: {
    tokenHeader: string;
    nameHeader?: string;
    emailHeader?: string;
    firstNameHeader?: string;
    lastNameHeader?: string;
  };
}

// Login request/response
interface LoginRequest {
  username: string;
  password: string;
}

interface LoginResponse {
  token: string;
  user: User;
}

// User session
interface UserSession {
  id: string;
  userId: string;
  createdAt: string;
  expiresAt: string;
  ip: string;
  userAgent: string;
}

// User group (admin)
interface UserGroup {
  id: string;
  name: string;
  managerIds: string[];
  memberIds: string[];
  createdAt: string;
}
```

#### Types de conversations
```typescript
// Conversation
interface Conversation {
  id: string;
  title: string;
  userId: string;
  createdAt: string;
  updatedAt: string;
  groupId?: string;           // undefined = ungrouped
  sharedWithGroups?: string[]; // user group IDs
  messageCount?: number;
}

// Conversation group (sidebar)
interface ConversationGroup {
  id: string;
  name: string;
  userId: string;
  conversationIds: string[];
  createdAt: string;
  updatedAt: string;
}

// Message
interface Message {
  id: string;
  conversationId: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: string;
  metadata?: Record<string, any>;
}

// CRUD requests
interface CreateConversationRequest {
  title: string;
}

interface CreateConversationResponse {
  conversation: Conversation;
}

interface UpdateConversationRequest {
  title?: string;
  groupId?: string | undefined;  // undefined to ungroup
}

interface CreateGroupRequest {
  name: string;
}

interface CreateGroupResponse {
  group: ConversationGroup;
}
```

#### Types de fichiers
```typescript
// File status
type FileStatus = 'pending' | 'uploading' | 'completed' | 'error';

// Pending file (during upload)
interface PendingFile {
  id: string;
  name: string;
  size: number;
  type: string;
  file: File;
  status: FileStatus;
  progress?: number;  // 0-100
  error?: string;
}

// Uploaded file (completed)
interface UploadedFile {
  id: string;
  name: string;
  size: number;
  type: string;
  url: string;         // MinIO URL
  status: 'completed';
  uploadedAt: string;
}

// Upload response
interface FileUploadResponse {
  file: UploadedFile;
}
```

#### Types de settings
```typescript
// User settings
interface UserSettings {
  id: string;
  userId: string;
  promptCustomization: string;  // system prompt override
  theme?: 'light' | 'dark';     // future
  language?: string;            // future
  updatedAt: string;
}

// Update request/response
interface UpdateSettingsRequest {
  promptCustomization?: string;
  theme?: 'light' | 'dark';
  language?: string;
}

interface UpdateSettingsResponse {
  settings: UserSettings;
}
```

#### Types de permissions
```typescript
interface UserPermissions {
  // User permissions
  canUseApp: boolean;
  canManageOwnPreferences: boolean;
  canShareOwnConversations: boolean;
  canForceLogout: boolean;
  
  // Manager permissions
  canManageGroupMembers: boolean;
  canActivateDeactivateGroupMembers: boolean;
  canManageGroups: boolean;
  
  // Root permissions
  canManageAllUsers: boolean;
  canCreateGroups: boolean;
  canAssignManagers: boolean;
  canRevokeAllSessions: boolean;
  canToggleMaintenanceMode: boolean;
}
```

**Calcul des permissions** (`src/lib/utils/permissions.ts`):
```typescript
export function calculatePermissions(
  role: 'user' | 'manager' | 'root',
  config: ServerAuthConfig | null
): UserPermissions {
  const isNoneMode = config?.mode === 'none';

  return {
    // User
    canUseApp: true,
    canManageOwnPreferences: !isNoneMode,
    canShareOwnConversations: !isNoneMode,
    canForceLogout: !isNoneMode,
    
    // Manager
    canManageGroupMembers: role === 'manager' || role === 'root',
    canActivateDeactivateGroupMembers: role === 'manager' || role === 'root',
    canManageGroups: role === 'manager' || role === 'root',
    
    // Root
    canManageAllUsers: role === 'root',
    canCreateGroups: role === 'root',
    canAssignManagers: role === 'root',
    canRevokeAllSessions: role === 'root',
    canToggleMaintenanceMode: role === 'root',
  };
}
```

---

## 3. Configuration et Build

### 3.1 Next.js Configuration

**Fichier**: `next.config.js`

```javascript
const nextConfig = {
  output: 'export',          // Static export
  trailingSlash: true,       // URLs avec trailing slash
  images: {
    unoptimized: true        // Pas d'optimization (export statique)
  },
  reactStrictMode: true,
  
  // Environment variables exposed to browser
  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL,
    NEXT_PUBLIC_MOCK_TOKEN: process.env.NEXT_PUBLIC_MOCK_TOKEN,
  }
};

module.exports = nextConfig;
```

### 3.2 ESLint Configuration (Flat Config)

**Fichier**: `eslint.config.mjs`

```javascript
import js from '@eslint/js';
import tsPlugin from '@typescript-eslint/eslint-plugin';
import tsParser from '@typescript-eslint/parser';
import reactPlugin from 'eslint-plugin-react';
import reactHooksPlugin from 'eslint-plugin-react-hooks';
import globals from 'globals';

export default [
  // Source files
  {
    files: ['src/**/*.{ts,tsx,js,jsx}'],
    languageOptions: {
      parser: tsParser,
      parserOptions: {
        ecmaVersion: 2021,
        sourceType: 'module',
        ecmaFeatures: { jsx: true }
      },
      globals: {
        ...globals.browser,
        ...globals.node,
        ...globals.es2021,
        React: 'readonly'
      }
    },
    plugins: {
      'react': reactPlugin,
      'react-hooks': reactHooksPlugin,
      '@typescript-eslint': tsPlugin
    },
    rules: {
      'no-undef': 'off',           // TypeScript gère
      'no-console': 'warn',
      '@typescript-eslint/no-explicit-any': 'warn'
    }
  },
  
  // Test files
  {
    files: ['tests/**/*.{ts,tsx,js}', '**/*.test.{ts,tsx,js}'],
    languageOptions: {
      globals: {
        ...globals.jest,  // describe, it, expect, beforeEach, etc.
      }
    },
    rules: {
      'no-console': 'off',
      '@typescript-eslint/no-explicit-any': 'off'
    }
  },
  
  // CommonJS files
  {
    files: ['__mocks__/**/*.js', '*.config.js'],
    languageOptions: {
      sourceType: 'commonjs',
      globals: {
        ...globals.node,
        ...globals.commonjs  // module, exports, require
      }
    },
    rules: {
      'no-undef': 'off'
    }
  }
];
```

**Notes importantes**:
- eslint-config-next incompatible avec ESLint 9
- Configuration native ESLint 9 flat config
- Globals explicites nécessaires (jest, React, commonjs)
- `no-undef: off` car TypeScript gère les variables non définies

### 3.3 Tailwind CSS v4 Configuration

**Fichier**: `postcss.config.js`

```javascript
module.exports = {
  plugins: {
    '@tailwindcss/postcss': {},
  },
};
```

**Fichier**: `Tailwind.config.ts`

```typescript
import type { Config } from 'tailwindcss';

const config: Config = {
  content: [
    './src/pages/**/*.{js,ts,jsx,tsx,mdx}',
    './src/components/**/*.{js,ts,jsx,tsx,mdx}',
    './src/app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        primary: {
          50: '#eff6ff',
          500: '#3b82f6',
          600: '#2563eb',
          700: '#1d4ed8',
        },
      },
    },
  },
  plugins: [],
};

export default config;
```

**Fichier**: `src/app/globals.css`

```css
@import 'tailwindcss';

/* Custom utility classes */
@layer utilities {
  .scrollbar-hide {
    -ms-overflow-style: none;
    scrollbar-width: none;
  }
  .scrollbar-hide::-webkit-scrollbar {
    display: none;
  }
}
```

### 3.4 Jest Configuration

**Fichier**: `jest.config.js`

```javascript
const nextJest = require('next/jest');

const createJestConfig = nextJest({
  dir: './',
});

const customJestConfig = {
  setupFilesAfterEnv: ['<rootDir>/jest.setup.js'],
  testEnvironment: 'jest-environment-jsdom',
  moduleNameMapper: {
    '^@/(.*)$': '<rootDir>/src/$1',
    '\\.(css|less|scss|sass)$': '<rootDir>/__mocks__/styleMock.js',
    '\\.(jpg|jpeg|png|gif|webp|svg)$': '<rootDir>/__mocks__/fileMock.js',
  },
  testMatch: [
    '**/tests/unit/**/*.test.{ts,tsx}',
    '**/tests/integration/**/*.test.{ts,tsx}',
  ],
  collectCoverageFrom: [
    'src/**/*.{ts,tsx}',
    '!src/**/*.d.ts',
    '!src/**/*.stories.tsx',
    '!src/mocks/**',
  ],
  coverageThreshold: {
    global: {
      statements: 70,
      branches: 60,
      functions: 70,
      lines: 70,
    },
  },
};

module.exports = createJestConfig(customJestConfig);
```

**Fichier**: `jest.setup.js`

```javascript
import '@testing-library/jest-dom';

// Mock window.matchMedia
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: jest.fn().mockImplementation(query => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: jest.fn(),
    removeListener: jest.fn(),
    addEventListener: jest.fn(),
    removeEventListener: jest.fn(),
    dispatchEvent: jest.fn(),
  })),
});

// Mock window.location.reload
delete window.location;
window.location = { ...window.location, reload: jest.fn() };
```

### 3.5 Playwright Configuration

**Fichier**: `playwright.config.ts`

```typescript
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: 'html',
  
  use: {
    baseURL: 'http://localhost:3000',
    trace: 'on-first-retry',
  },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'firefox',
      use: { ...devices['Desktop Firefox'] },
    },
    {
      name: 'webkit',
      use: { ...devices['Desktop Safari'] },
    },
  ],

  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:3000',
    reuseExistingServer: !process.env.CI,
  },
});
```

### 3.6 Docker Configuration

**Fichier**: `Dockerfile`

```dockerfile
# Stage 1: Build
FROM node:20-alpine AS builder

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .
RUN npm run build

# Stage 2: Production
FROM caddy:2-alpine

COPY --from=builder /app/out /usr/share/caddy
COPY Caddyfile /etc/caddy/Caddyfile

EXPOSE 80
```

**Fichier**: `Caddyfile`

```
:80 {
    root * /usr/share/caddy
    encode gzip
    file_server
    
    # SPA fallback
    try_files {path} /index.html
    
    # Security headers
    header {
        X-Content-Type-Options "nosniff"
        X-Frame-Options "DENY"
        X-XSS-Protection "1; mode=block"
        Referrer-Policy "strict-origin-when-cross-origin"
    }
}
```

**Fichier**: `docker-compose.yml`

```yaml
version: '3.8'

services:
  frontend:
    build: .
    ports:
      - "80:80"
    environment:
      - NEXT_PUBLIC_API_URL=http://backend:8000
    networks:
      - chatbot-network

  backend:
    image: chatbot-backend:latest
    ports:
      - "8000:8000"
    networks:
      - chatbot-network

networks:
  chatbot-network:
    driver: bridge
```

### 3.7 Makefile

**Fichier**: `Makefile`

```makefile
.PHONY: install dev build test lint format clean

install:
	npm install

dev:
	npm run dev

build:
	npm run lint
	prettier --write "src/**/*.{ts,tsx,js,jsx,json,css,md}"
	npm run build --no-turbo

test:
	npm run test:unit
	npm run test:integration

test-unit:
	npx jest tests/unit

test-int:
	npx jest tests/integration

test-e2e:
	npx playwright test

lint:
	npm run lint

lint-fix:
	npm run lint:fix

format:
	prettier --write "src/**/*.{ts,tsx,js,jsx,json,css,md}"

coverage:
	npm run test:coverage

clean:
	rm -rf node_modules .next out coverage

docker-build:
	docker build -t chatbot-frontend:latest .

docker-run:
	docker run -p 80:80 chatbot-frontend:latest

list-files:
	find . -type f \
		-not -path "./node_modules/*" \
		-not -path "./.next/*" \
		-not -path "./coverage/*" \
		-not -path "./.git/*" \
		-not -name "*.log" \
		| sort
```

**Note**: Build utilise `--no-turbo` pour éviter l'erreur "Unknown system error -35" avec Turbopack.

---

## 4. Tests

### 4.1 Structure des tests

```
tests/
├── e2e/                           # Tests end-to-end (Playwright)
│   ├── admin-features.spec.e2e.ts    # Features admin (manager/root)
│   ├── auth-modes.spec.e2e.ts        # Modes d'authentification
│   ├── drag-drop.spec.e2e.ts         # Drag-and-drop conversations
│   └── user-journey.spec.e2e.ts      # Parcours utilisateur complet
│
├── integration/                   # Tests d'intégration (Jest + RTL)
│   ├── auth/
│   │   ├── login.test.integration.tsx     # Login flow
│   │   └── logout.test.integration.tsx    # Logout flow
│   ├── conversations/
│   │   ├── create.test.integration.tsx    # Create conversation
│   │   ├── delete.test.integration.tsx    # Delete conversation
│   │   └── share.test.integration.tsx     # Share conversation
│   ├── files/
│   │   └── upload.test.integration.tsx    # File upload
│   └── admin/
│       ├── user-management.test.integration.tsx   # User CRUD
│       └── group-management.test.integration.tsx  # Group CRUD
│
├── unit/                          # Tests unitaires (Jest + RTL)
│   ├── components/               # Tests de composants
│   │   ├── Button.test.unit.tsx
│   │   ├── Input.test.unit.tsx
│   │   ├── Modal.test.unit.tsx
│   │   ├── LoginForm.test.unit.tsx
│   │   ├── ChatInterface.test.unit.tsx
│   │   ├── Sidebar.test.unit.tsx
│   │   ├── ConversationList.test.unit.tsx
│   │   ├── FileUploadPanel.test.unit.tsx
│   │   └── ... (27 fichiers de tests composants)
│   ├── hooks/                    # Tests de hooks
│   │   ├── useAuth.test.unit.ts
│   │   ├── useConversations.test.unit.ts
│   │   ├── useSettings.test.unit.ts
│   │   └── useFileUpload.test.unit.ts
│   ├── lib/                      # Tests des API clients
│   │   ├── client.test.unit.ts
│   │   ├── auth.test.unit.ts
│   │   ├── conversations.test.unit.ts
│   │   └── files.test.unit.ts
│   └── utils/                    # Tests des utilitaires
│       ├── storage.test.unit.ts
│       └── permissions.test.unit.ts
│
├── mocks/                         # Mock Service Worker (MSW)
│   ├── browser.ts                 # MSW setup pour dev
│   ├── server.ts                  # MSW setup pour tests
│   ├── handlers/                  # Request handlers par endpoint
│   │   ├── auth.ts
│   │   ├── conversations.ts
│   │   ├── files.ts
│   │   ├── settings.ts
│   │   ├── users.ts
│   │   └── sse.ts
│   ├── data/                      # Fixtures de données
│   │   ├── users.ts
│   │   ├── conversations.ts
│   │   ├── files.ts
│   │   └── settings-data.ts
│   └── scenarios/                 # Scénarios de test
│       ├── auth-modes.ts
│       ├── errors.ts
│       └── permissions.ts
│
└── helpers/                       # Utilitaires de test
    ├── render.tsx                 # renderWithProviders (RTL + contexts)
    ├── assertions.ts              # Custom matchers
    └── wait.ts                    # waitFor helpers
```

### 4.2 Couverture des tests

**Résultats actuels**:
- **Suites de tests**: 35 passed
- **Tests individuels**: 474 passed
- **Couverture globale**: 87.52%

**Détails par catégorie**:
```
Category               | Stmts   | Branch  | Funcs   | Lines   |
-----------------------|---------|---------|---------|---------|
All files              | 87.52%  | 77.98%  | 85.71%  | 88.62%  |
  app/                 | 100%    | 100%    | 100%    | 100%    |
  components/admin/    | 87.05%  | 88.31%  | 87.5%   | 87.5%   |
  components/auth/     | 80%     | 75%     | 100%    | 80%     |
  components/chat/     | 61.19%  | 31.25%  | 83.33%  | 60.6%   |
  components/sidebar/  | 82.22%  | 70.83%  | 68.51%  | 83.72%  |
  components/ui/       | 91%     | 89.55%  | 90.47%  | 93.33%  |
  components/upload/   | 88.46%  | 68.57%  | 84.61%  | 90%     |
  lib/api/             | 98.6%   | 91.3%   | 98.3%   | 98.59%  |
  lib/hooks/           | 89.42%  | 74.32%  | 85.39%  | 89.27%  |
  lib/utils/           | 85.22%  | 83.33%  | 84.61%  | 92.2%   |
```

### 4.3 Mock Service Worker (MSW)

**Configuration**:
- MSW v1.x (v2 incompatible avec Node < 18.17)
- Mode browser: pour développement local
- Mode server: pour tests Jest

**Endpoints mockés**:
- `/api/auth/*` - Authentification (all modes)
- `/api/users/*` - Gestion utilisateurs
- `/api/user-groups/*` - Gestion groupes
- `/api/conversations/*` - CRUD conversations
- `/api/groups/*` - Groupes de conversations
- `/api/files/*` - Upload/delete files
- `/api/settings` - User settings
- `/api/chat/stream` - SSE streaming

**Exemple de handler**:
```typescript
// tests/mocks/handlers/conversations.ts
import { rest } from 'msw';
import { conversations } from '../data/conversations';

export const conversationHandlers = [
  // GET /api/conversations
  rest.get('http://localhost:8000/api/conversations', (req, res, ctx) => {
    return res(
      ctx.status(200),
      ctx.json({ conversations })
    );
  }),

  // POST /api/conversations
  rest.post('http://localhost:8000/api/conversations', async (req, res, ctx) => {
    const { title } = await req.json();
    const newConv = {
      id: `conv-${Date.now()}`,
      title,
      userId: 'user-1',
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    };
    conversations.push(newConv);
    return res(
      ctx.status(201),
      ctx.json({ conversation: newConv })
    );
  }),
];
```

---

## 5. Déploiement et environnements

### 5.1 Variables d'environnement

**Fichier**: `.env.local` (development)

```env
# API Backend URL
NEXT_PUBLIC_API_URL=http://localhost:8000

# Mock token for development
NEXT_PUBLIC_MOCK_TOKEN=dev-token-12345

# MinIO endpoint (file storage)
NEXT_PUBLIC_MINIO_ENDPOINT=http://localhost:9000
```

**Production**:
```env
NEXT_PUBLIC_API_URL=https://api.production.com
NEXT_PUBLIC_MINIO_ENDPOINT=https://minio.production.com
```

### 5.2 Process de build

```bash
# 1. Install dependencies
npm ci

# 2. Lint + format
npm run lint
prettier --write "src/**/*.{ts,tsx}"

# 3. Build static export
npm run build --no-turbo

# 4. Output directory
# → /out (HTML, CSS, JS, assets)

# 5. Serve with Caddy
caddy run --config Caddyfile
```

### 5.3 Commandes npm

```json
{
  "scripts": {
    "dev": "next dev",
    "build": "next build --no-turbo",
    "start": "next start",
    "lint": "eslint .",
    "lint:fix": "eslint . --fix",
    "test": "jest",
    "test:unit": "jest tests/unit",
    "test:integration": "jest tests/integration",
    "test:e2e": "playwright test",
    "test:coverage": "jest --coverage",
    "format": "prettier --write \"src/**/*.{ts,tsx,js,jsx,json,css,md}\""
  }
}
```

---

## 6. Sécurité

### 6.1 Token management

**Storage**: localStorage (mode "local")
```typescript
// Set token
storage.setAuthToken(token);
// → localStorage.setItem('auth_token', token)

// Get token
const token = storage.getAuthToken();
// → localStorage.getItem('auth_token')

// Clear token
storage.clearAuthToken();
// → localStorage.removeItem('auth_token')
```

**Inclusion dans les requêtes**:
```typescript
headers: {
  'Authorization': `Bearer ${token}`
}
```

**Expiration**: Géré côté backend, frontend vérifie à chaque requête

### 6.2 Protection XSS

- React échappe automatiquement les variables dans JSX
- Pas de `dangerouslySetInnerHTML`
- Content Security Policy headers via Caddy

### 6.3 Protection CSRF

- Pas de cookies (token Bearer uniquement)
- SameSite strict si cookies utilisés à l'avenir

### 6.4 CORS

Géré côté backend FastAPI:
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "https://app.production.com"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

---

## Annexes

### A. Dépendances npm principales

```json
{
  "dependencies": {
    "next": "16.0.4",
    "react": "18.3.1",
    "react-dom": "18.3.1",
    "@nlux/react": "^2.0.0",
    "lucide-react": "^0.263.1",
    "react-dropzone": "^14.2.3",
    "tailwindcss": "^4.0.0-alpha.25",
    "@tailwindcss/postcss": "^4.0.0-alpha.25"
  },
  "devDependencies": {
    "@types/react": "^18.3.3",
    "@types/react-dom": "^18.3.0",
    "typescript": "^5.x",
    "eslint": "9.15.0",
    "@eslint/js": "^9.15.0",
    "@typescript-eslint/eslint-plugin": "^8.16.0",
    "@typescript-eslint/parser": "^8.16.0",
    "eslint-plugin-react": "^7.37.2",
    "eslint-plugin-react-hooks": "^5.0.0",
    "globals": "^15.12.0",
    "jest": "^29.7.0",
    "@testing-library/react": "^14.0.0",
    "@testing-library/jest-dom": "^6.1.5",
    "msw": "^1.3.2",
    "@playwright/test": "^1.40.0",
    "prettier": "^3.1.0"
  }
}
```

### B. Points d'attention pour le backend

**Headers attendus par le frontend**:
- `Content-Type: application/json` pour toutes les réponses
- `Authorization: Bearer {token}` dans toutes les requêtes (sauf endpoints publics)

**Formats de réponse**:
- Toujours wrapper dans un objet: `{ "user": {...} }`, `{ "conversations": [...] }`
- Status HTTP appropriés: 200 (OK), 201 (Created), 204 (No Content), 400 (Bad Request), 401 (Unauthorized), 403 (Forbidden), 404 (Not Found), 500 (Server Error)

**Streaming SSE**:
- Content-Type: `text/event-stream`
- Format: `data: {chunk}\n\n`
- Signal de fin: `data: [DONE]\n\n`

**Upload de fichiers**:
- Accept: `multipart/form-data`
- Clé FormData: `file`
- Retourner URL MinIO dans la réponse

**Gestion des erreurs**:
- Toujours inclure un champ `message` dans les erreurs JSON
- Frontend parse automatiquement `errorData.message`

---

**Fin de la spécification Frontend v2.0**