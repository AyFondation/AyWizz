# Backend API Specification - Chatbot Application
## Version 3.0 - Documentation complète basée sur l'implémentation frontend

---

## Vue d'ensemble

API REST backend pour application chatbot avec système d'autorisation à 3 niveaux (user/manager/root) et 3 modes d'authentification (none/local/sso).

### Stack technique recommandée

- **Framework**: FastAPI (Python 3.11+)
- **Base de données**: ArangoDB (NoSQL document store)
- **Stockage fichiers**: MinIO (S3-compatible)
- **Authentication**: JWT tokens (mode local)
- **Streaming**: Server-Sent Events (SSE)
- **API Documentation**: OpenAPI / Swagger
- **Deployment**: Docker + Caddy/Nginx

### Architecture globale

```
┌─────────────────────────────────────────────────────────────┐
│                   Frontend (Next.js)                         │
│  - Base URL: http://localhost:3000 (dev)                    │
│  - Authentication: Bearer token in Authorization header      │
└─────────────────────────────────────────────────────────────┘
           │ REST API (JSON) + SSE + FormData
           ▼
┌─────────────────────────────────────────────────────────────┐
│                   FastAPI Backend                            │
│  ┌───────────────────────────────────────────────────────┐  │
│  │                 API Layer                              │  │
│  │  - Authentication middleware                           │  │
│  │  - Permission checking                                 │  │
│  │  - Request validation                                  │  │
│  │  - Error handling                                      │  │
│  └───────────────────────────────────────────────────────┘  │
│  ┌───────────────────────────────────────────────────────┐  │
│  │              Business Logic Layer                      │  │
│  │  - User management                                     │  │
│  │  - Conversation handling                               │  │
│  │  - Group operations                                    │  │
│  │  - File processing                                     │  │
│  │  - AI chat integration                                 │  │
│  └───────────────────────────────────────────────────────┘  │
│  ┌───────────────────────────────────────────────────────┐  │
│  │               Data Access Layer                        │  │
│  │  - ArangoDB queries                                    │  │
│  │  - MinIO operations                                    │  │
│  │  - Cache management                                    │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
           │                           │
           ▼                           ▼
    ┌──────────────┐         ┌──────────────────┐
    │   ArangoDB   │         │      MinIO       │
    │  (Database)  │         │ (File Storage)   │
    └──────────────┘         └──────────────────┘
```

---

## 1. Niveaux d'autorisation

### 1.1 User Role

**Permissions:**
- ✅ Utiliser l'application
- ✅ Gérer ses propres préférences
- ✅ Partager/retirer le partage de ses conversations avec des groupes d'utilisateurs
- ✅ Force logout (révoquer sa propre session)
- ✅ Créer/modifier/supprimer ses conversations
- ✅ Uploader/supprimer ses propres fichiers
- ✅ Voir les conversations partagées avec lui

**Restrictions:**
- ❌ Ne peut pas gérer d'autres utilisateurs
- ❌ Ne peut pas gérer les groupes
- ❌ Ne peut pas accéder aux fonctionnalités admin

### 1.2 Manager Role

**Hérite de toutes les permissions User, plus:**
- ✅ Ajouter/retirer des utilisateurs aux/des groupes qu'il gère
- ✅ Activer/désactiver les utilisateurs dans les groupes qu'il gère
- ✅ Activer/désactiver les groupes qu'il gère
- ✅ Voir les utilisateurs dans ses groupes
- ✅ Renommer les groupes qu'il gère

**Restrictions:**
- ❌ Ne peut gérer que les utilisateurs dans ses groupes assignés
- ❌ Ne peut pas créer de groupes
- ❌ Ne peut pas assigner de rôles manager
- ❌ Ne peut pas accéder aux fonctionnalités root

### 1.3 Root Role

**Hérite de toutes les permissions Manager, plus:**
- ✅ Activer/désactiver N'IMPORTE QUEL utilisateur
- ✅ Créer des groupes d'utilisateurs
- ✅ Assigner/révoquer les rôles manager
- ✅ Activer/désactiver N'IMPORTE QUEL groupe
- ✅ Révoquer TOUTES les sessions actives
- ✅ Activer/désactiver le mode maintenance
- ✅ Voir toutes les sessions actives
- ✅ Créer/supprimer des utilisateurs
- ✅ Assigner/retirer des managers aux groupes

---

## 2. Modes d'authentification

### 2.1 Mode "none" - Pas d'authentification

**Comportement:**
- Quiconque accède à l'app est automatiquement connecté comme "John Doe" générique
- Pas de groupes d'utilisateurs disponibles
- Pas de partage de conversation disponible
- Pas de rôles manager/root disponibles
- Pas de préférences utilisateur disponibles
- Multi-login autorisé: OUI

**Use case:** Démo publique, tests

**Endpoint utilisé par le frontend:**
```
GET /api/auth/generic
```

### 2.2 Mode "local" - Authentification locale

**Comportement:**
- Formulaire de connexion avec username/password
- Préférences utilisateur disponibles
- Tous les rôles disponibles (user/manager/root)
- Partage de conversation disponible
- Multi-login autorisé: NON (session unique par utilisateur)

**Use case:** Déploiement interne sans SSO

**Endpoints utilisés par le frontend:**
```
POST /api/auth/login        → Connexion
GET  /api/auth/verify       → Vérification du token JWT
POST /api/auth/logout       → Déconnexion
```

### 2.3 Mode "sso" - Single Sign-On

**Comportement:**
- Pas de formulaire de connexion (authentification SSO automatique)
- Backend extrait les infos utilisateur des headers HTTP
- Préférences utilisateur disponibles
- Tous les rôles disponibles (user/manager/root)
- Partage de conversation disponible
- Multi-login autorisé: NON (session unique par utilisateur)

**Use case:** Déploiement en entreprise avec SSO existant

**Endpoints utilisés par le frontend:**
```
GET /api/auth/sso/verify    → Vérification SSO via headers
```

---

## 3. API Endpoints - Documentation complète

### 3.1 Authentication & Configuration

#### 3.1.1 Get Server Configuration
**GET** `/api/auth/config`

**Description**: Récupère la configuration d'authentification du serveur (mode, multi-login, maintenance)

**Authentication**: Aucune (endpoint public)

**Headers**: Aucun requis

**Query Parameters**: Aucun

**Response Success**: `200 OK`
```json
{
  "config": {
    "mode": "local",              // "none" | "local" | "sso"
    "allowMultiLogin": false,     // boolean
    "maintenanceMode": false,     // boolean
    "ssoConfig": null             // null si mode != "sso"
  }
}
```

**Response pour mode SSO**:
```json
{
  "config": {
    "mode": "sso",
    "allowMultiLogin": false,
    "maintenanceMode": false,
    "ssoConfig": {
      "tokenHeader": "X-Auth-Token",
      "nameHeader": "X-User-Name",
      "emailHeader": "X-User-Email",
      "firstNameHeader": "X-User-FirstName",
      "lastNameHeader": "X-User-LastName"
    }
  }
}
```

**Response Errors**: Aucune (toujours 200)

**Notes d'implémentation**:
- Endpoint appelé au chargement de l'app pour déterminer le mode d'auth
- `ssoConfig` est `null` pour modes "none" et "local"
- Configuration stockée dans collection `system_config` (ArangoDB)

---

#### 3.1.2 Get Generic User
**GET** `/api/auth/generic`

**Description**: Récupère l'utilisateur générique (mode "none" uniquement)

**Authentication**: Aucune

**Headers**: Aucun requis

**Available only if**: `server_config.mode === "none"`

**Response Success**: `200 OK`
```json
{
  "user": {
    "id": "user-generic",
    "name": "John Doe",
    "email": "generic@example.com",
    "role": "user",
    "status": "active",
    "groupIds": [],
    "createdAt": "2024-01-01T00:00:00Z"
  }
}
```

**Response Errors**:
- `403 Forbidden` si mode != "none"
```json
{
  "error": "Generic user only available in 'none' auth mode",
  "status": 403
}
```

**Notes d'implémentation**:
- Utilisateur générique créé au démarrage du serveur
- Toujours même user ID et propriétés
- Ne jamais exposer en mode "local" ou "sso"

---

#### 3.1.3 Login
**POST** `/api/auth/login`

**Description**: Authentification avec username/password (mode "local" uniquement)

**Authentication**: Aucune

**Available only if**: `server_config.mode === "local"`

**Headers**:
```
Content-Type: application/json
```

**Request Body**:
```json
{
  "username": "john.doe",      // string, required
  "password": "securepassword" // string, required
}
```

**Response Success**: `200 OK`
```json
{
  "user": {
    "id": "user-john-doe",
    "name": "John Doe",
    "email": "john.doe@example.com",
    "role": "user",                    // "user" | "manager" | "root"
    "status": "active",                 // "active" | "disabled"
    "groupIds": ["group-1", "group-2"], // array of string
    "createdAt": "2024-01-01T00:00:00Z",
    "lastLogin": "2024-01-15T10:30:00Z"
  },
  "token": "jwt-token-here",            // JWT token string
  "expiresAt": "2024-01-15T22:30:00Z"   // ISO 8601 datetime
}
```

**Response Errors**:

`401 Unauthorized` - Identifiants invalides:
```json
{
  "error": "Invalid credentials",
  "status": 401
}
```

`403 Forbidden` - Utilisateur désactivé:
```json
{
  "error": "User account is disabled",
  "status": 403
}
```

`403 Forbidden` - Mode incorrect:
```json
{
  "error": "Login only available in 'local' auth mode",
  "status": 403
}
```

`503 Service Unavailable` - Mode maintenance (sauf root):
```json
{
  "error": "Application is in maintenance mode",
  "status": 503
}
```

**Notes d'implémentation**:
- Vérifier `passwordHash` avec bcrypt/argon2
- Générer JWT token avec expiration (12h recommandé)
- Si `allowMultiLogin === false`: révoquer session existante
- Créer nouvelle session dans collection `sessions`
- Mettre à jour `lastLogin` timestamp
- En mode maintenance: autoriser uniquement root

---

#### 3.1.4 Verify Token
**GET** `/api/auth/verify`

**Description**: Vérifie la validité d'un token JWT (mode "local")

**Authentication**: Bearer token requis

**Available only if**: `server_config.mode === "local"`

**Headers**:
```
Authorization: Bearer <jwt-token>
```

**Response Success**: `200 OK`
```json
{
  "user": {
    "id": "user-john-doe",
    "name": "John Doe",
    "email": "john.doe@example.com",
    "role": "user",
    "status": "active",
    "groupIds": ["group-1"],
    "createdAt": "2024-01-01T00:00:00Z",
    "lastLogin": "2024-01-15T10:30:00Z"
  }
}
```

**Response Errors**:

`401 Unauthorized` - Token invalide/expiré:
```json
{
  "error": "Invalid or expired token",
  "status": 401
}
```

`403 Forbidden` - Utilisateur désactivé:
```json
{
  "error": "User account is disabled",
  "status": 403
}
```

**Notes d'implémentation**:
- Vérifier signature JWT
- Vérifier expiration
- Vérifier que session existe dans DB et est active
- Vérifier status user === "active"
- Retourner user complet avec `groupIds` actualisés

---

#### 3.1.5 Verify SSO Session
**GET** `/api/auth/sso/verify`

**Description**: Vérifie la session SSO via headers HTTP (mode "sso")

**Authentication**: Headers SSO requis (configurés dans ssoConfig)

**Available only if**: `server_config.mode === "sso"`

**Headers** (exemple, selon ssoConfig):
```
X-Auth-Token: <sso-token>
X-User-Name: John Doe
X-User-Email: john.doe@company.com
X-User-FirstName: John     (optionnel)
X-User-LastName: Doe       (optionnel)
```

**Response Success**: `200 OK`
```json
{
  "user": {
    "id": "user-john-doe",
    "name": "John Doe",
    "email": "john.doe@company.com",
    "role": "manager",
    "status": "active",
    "groupIds": ["group-1", "group-2"],
    "createdAt": "2024-01-01T00:00:00Z",
    "lastLogin": "2024-01-15T10:30:00Z"
  }
}
```

**Response Errors**:

`401 Unauthorized` - Headers SSO manquants/invalides:
```json
{
  "error": "Missing or invalid SSO headers",
  "status": 401
}
```

`403 Forbidden` - Utilisateur désactivé:
```json
{
  "error": "User account is disabled",
  "status": 403
}
```

**Notes d'implémentation**:
- Extraire user info des headers configurés dans `ssoConfig`
- Si user n'existe pas: auto-créer avec role "user" par défaut
- Mettre à jour `lastLogin` timestamp
- Vérifier status user === "active"
- Ne PAS créer de session JWT (SSO gère l'authentification)

---

#### 3.1.6 Logout
**POST** `/api/auth/logout`

**Description**: Déconnexion et invalidation de la session courante

**Authentication**: Bearer token requis

**Headers**:
```
Authorization: Bearer <jwt-token>
Content-Type: application/json
```

**Request Body**: Aucun

**Response Success**: `204 No Content`

**Response Errors**:

`401 Unauthorized` - Token invalide:
```json
{
  "error": "Invalid token",
  "status": 401
}
```

**Notes d'implémentation**:
- Invalider le token JWT (blacklist ou supprimer de DB)
- Marquer session comme inactive dans collection `sessions`
- Retourner 204 sans body

---

#### 3.1.7 Revoke Own Session (Force Logout)
**POST** `/api/auth/revoke-own-session`

**Description**: Force la déconnexion immédiate (user permission)

**Authentication**: Bearer token requis

**Permissions**: User (tous les rôles)

**Headers**:
```
Authorization: Bearer <jwt-token>
Content-Type: application/json
```

**Request Body**: Aucun

**Response Success**: `204 No Content`

**Response Errors**:

`401 Unauthorized` - Token invalide:
```json
{
  "error": "Invalid token",
  "status": 401
}
```

**Notes d'implémentation**:
- Révoquer immédiatement la session courante
- Identique à logout mais endpoint séparé pour clarté sémantique
- User doit se ré-authentifier pour continuer

---

#### 3.1.8 Revoke All Sessions
**POST** `/api/auth/revoke-all-sessions`

**Description**: Révoque TOUTES les sessions actives de tous les utilisateurs (root only)

**Authentication**: Bearer token requis

**Permissions**: Root uniquement

**Headers**:
```
Authorization: Bearer <jwt-token>
Content-Type: application/json
```

**Request Body**: Aucun

**Response Success**: `200 OK`
```json
{
  "message": "All sessions revoked",
  "count": 42                        // nombre de sessions révoquées
}
```

**Response Errors**:

`401 Unauthorized` - Token invalide:
```json
{
  "error": "Invalid token",
  "status": 401
}
```

`403 Forbidden` - Permission insuffisante:
```json
{
  "error": "Only root can revoke all sessions",
  "status": 403
}
```

**Notes d'implémentation**:
- Marquer toutes les sessions comme inactives dans collection `sessions`
- Compter le nombre de sessions affectées
- Tous les utilisateurs devront se ré-authentifier
- Action critique: logger dans audit log

---

#### 3.1.9 Get All Sessions
**GET** `/api/auth/sessions`

**Description**: Liste toutes les sessions actives (root only)

**Authentication**: Bearer token requis

**Permissions**: Root uniquement

**Headers**:
```
Authorization: Bearer <jwt-token>
```

**Query Parameters**: Aucun

**Response Success**: `200 OK`
```json
{
  "sessions": [
    {
      "sessionId": "session-uuid-1",
      "userId": "user-john-doe",
      "createdAt": "2024-01-15T10:00:00Z",
      "expiresAt": "2024-01-15T22:00:00Z",
      "ipAddress": "192.168.1.100",
      "userAgent": "Mozilla/5.0..."
    },
    {
      "sessionId": "session-uuid-2",
      "userId": "user-jane-manager",
      "createdAt": "2024-01-15T09:00:00Z",
      "expiresAt": "2024-01-15T21:00:00Z",
      "ipAddress": "192.168.1.101",
      "userAgent": "Chrome/120.0..."
    }
  ]
}
```

**Response Errors**:

`401 Unauthorized` - Token invalide:
```json
{
  "error": "Invalid token",
  "status": 401
}
```

`403 Forbidden` - Permission insuffisante:
```json
{
  "error": "Only root can view all sessions",
  "status": 403
}
```

**Notes d'implémentation**:
- Retourner uniquement les sessions actives (`active: true`)
- Trier par `createdAt` descendant (plus récent en premier)
- Inclure IP et user agent pour monitoring

---

### 3.2 User Management

#### 3.2.1 Get All Users
**GET** `/api/users`

**Description**: Liste tous les utilisateurs (avec filtre selon permissions)

**Authentication**: Bearer token requis

**Permissions**: 
- Manager: voit uniquement les users dans ses groupes gérés
- Root: voit tous les users

**Headers**:
```
Authorization: Bearer <jwt-token>
```

**Query Parameters**: Aucun

**Response Success**: `200 OK`
```json
{
  "users": [
    {
      "id": "user-1",
      "name": "John Doe",
      "email": "john@example.com",
      "role": "user",
      "status": "active",
      "groupIds": ["group-1"],
      "createdAt": "2024-01-01T00:00:00Z",
      "lastLogin": "2024-01-15T10:00:00Z"
    },
    {
      "id": "user-2",
      "name": "Jane Smith",
      "email": "jane@example.com",
      "role": "manager",
      "status": "active",
      "groupIds": ["group-1", "group-2"],
      "createdAt": "2024-01-02T00:00:00Z",
      "lastLogin": "2024-01-15T09:00:00Z"
    }
  ]
}
```

**Response Errors**:

`401 Unauthorized` - Token invalide:
```json
{
  "error": "Invalid token",
  "status": 401
}
```

`403 Forbidden` - Permission insuffisante:
```json
{
  "error": "Manager or Root permission required",
  "status": 403
}
```

**Notes d'implémentation**:
- Si manager: filtrer users où `user.groupIds` intersecte avec groupes gérés
- Si root: retourner tous les users
- Ne jamais exposer `passwordHash` ou `ssoId`
- Trier par `name` (alphabétique)

---

#### 3.2.2 Get User by ID
**GET** `/api/users/{userId}`

**Description**: Récupère un utilisateur spécifique par son ID

**Authentication**: Bearer token requis

**Permissions**: Tous les rôles (pour voir son propre profil ou gérer d'autres users)

**Headers**:
```
Authorization: Bearer <jwt-token>
```

**Path Parameters**:
- `userId` (string, required): ID de l'utilisateur

**Response Success**: `200 OK`
```json
{
  "user": {
    "id": "user-1",
    "name": "John Doe",
    "email": "john@example.com",
    "role": "manager",
    "status": "active",
    "groupIds": ["group-1", "group-2"],
    "createdAt": "2024-01-01T00:00:00Z",
    "lastLogin": "2024-01-15T10:00:00Z"
  }
}
```

**Response Errors**:

`401 Unauthorized` - Token invalide:
```json
{
  "error": "Invalid token",
  "status": 401
}
```

`403 Forbidden` - Tentative d'accès user non autorisé:
```json
{
  "error": "You can only view your own profile",
  "status": 403
}
```

`404 Not Found` - User introuvable:
```json
{
  "error": "User not found",
  "status": 404
}
```

**Notes d'implémentation**:
- User peut voir son propre profil
- Manager peut voir users dans ses groupes
- Root peut voir tous les users

---

#### 3.2.3 Create User
**POST** `/api/users`

**Description**: Crée un nouvel utilisateur (root only)

**Authentication**: Bearer token requis

**Permissions**: Root uniquement

**Headers**:
```
Authorization: Bearer <jwt-token>
Content-Type: application/json
```

**Request Body**:
```json
{
  "name": "New User",                    // string, required
  "email": "newuser@example.com",        // string, required, unique
  "password": "securepassword",          // string, required (mode local)
  "role": "user",                        // "user" | "manager" | "root", default: "user"
  "groupIds": ["group-1"]                // array of string, optional
}
```

**Response Success**: `201 Created`
```json
{
  "user": {
    "id": "user-new",
    "name": "New User",
    "email": "newuser@example.com",
    "role": "user",
    "status": "active",
    "groupIds": ["group-1"],
    "createdAt": "2024-01-15T12:00:00Z"
  }
}
```

**Response Errors**:

`400 Bad Request` - Email déjà utilisé:
```json
{
  "error": "Email already exists",
  "status": 400
}
```

`401 Unauthorized`:
```json
{
  "error": "Invalid token",
  "status": 401
}
```

`403 Forbidden`:
```json
{
  "error": "Only root can create users",
  "status": 403
}
```

**Notes d'implémentation**:
- Hasher le password avec bcrypt/argon2 avant stockage
- Valider format email
- Assigner ID unique (UUID ou auto-generated)
- Status par défaut: "active"
- Logger action dans audit log

---

#### 3.2.4 Update User
**PUT** `/api/users/{userId}`

**Description**: Met à jour les informations d'un utilisateur

**Authentication**: Bearer token requis

**Permissions**: 
- User: peut mettre à jour son propre profil (name uniquement)
- Manager: peut mettre à jour users dans ses groupes
- Root: peut mettre à jour tous les users

**Headers**:
```
Authorization: Bearer <jwt-token>
Content-Type: application/json
```

**Path Parameters**:
- `userId` (string, required): ID de l'utilisateur

**Request Body** (tous les champs optionnels):
```json
{
  "name": "Updated Name",                // string, optional
  "email": "updated@example.com",        // string, optional (root only)
  "password": "newsecurepassword"        // string, optional (self or root)
}
```

**Response Success**: `200 OK`
```json
{
  "user": {
    "id": "user-1",
    "name": "Updated Name",
    "email": "updated@example.com",
    "role": "user",
    "status": "active",
    "groupIds": ["group-1"],
    "createdAt": "2024-01-01T00:00:00Z",
    "lastLogin": "2024-01-15T10:00:00Z"
  }
}
```

**Response Errors**:

`401 Unauthorized`:
```json
{
  "error": "Invalid token",
  "status": 401
}
```

`403 Forbidden`:
```json
{
  "error": "Insufficient permissions",
  "status": 403
}
```

`404 Not Found`:
```json
{
  "error": "User not found",
  "status": 404
}
```

**Notes d'implémentation**:
- Valider permissions selon rôle
- Re-hasher password si fourni
- Ne pas permettre changement de role ici (endpoint séparé)

---

#### 3.2.5 Toggle User Status
**PUT** `/api/users/{userId}/status`

**Description**: Active ou désactive un utilisateur

**Authentication**: Bearer token requis

**Permissions**:
- Manager: peut toggler users dans ses groupes gérés
- Root: peut toggler n'importe quel user

**Headers**:
```
Authorization: Bearer <jwt-token>
Content-Type: application/json
```

**Path Parameters**:
- `userId` (string, required): ID de l'utilisateur

**Request Body**:
```json
{
  "status": "disabled"    // "active" | "disabled"
}
```

**Response Success**: `200 OK`
```json
{
  "user": {
    "id": "user-1",
    "name": "John Doe",
    "email": "john@example.com",
    "role": "user",
    "status": "disabled",
    "groupIds": ["group-1"],
    "createdAt": "2024-01-01T00:00:00Z",
    "lastLogin": "2024-01-15T10:00:00Z"
  }
}
```

**Response Errors**:

`401 Unauthorized`:
```json
{
  "error": "Invalid token",
  "status": 401
}
```

`403 Forbidden`:
```json
{
  "error": "Cannot manage this user",
  "status": 403
}
```

`404 Not Found`:
```json
{
  "error": "User not found",
  "status": 404
}
```

**Notes d'implémentation**:
- Quand désactivé: user ne peut plus se connecter
- Révoquer immédiatement toutes les sessions actives du user
- Logger action dans audit log
- Manager ne peut désactiver que users dans ses groupes
- Ne pas permettre self-disable

---

#### 3.2.6 Assign Role to User
**PUT** `/api/users/{userId}/role`

**Description**: Assigne un rôle à un utilisateur (root only)

**Authentication**: Bearer token requis

**Permissions**: Root uniquement

**Headers**:
```
Authorization: Bearer <jwt-token>
Content-Type: application/json
```

**Path Parameters**:
- `userId` (string, required): ID de l'utilisateur

**Request Body**:
```json
{
  "role": "manager"    // "user" | "manager" | "root"
}
```

**Response Success**: `200 OK`
```json
{
  "user": {
    "id": "user-1",
    "name": "John Doe",
    "email": "john@example.com",
    "role": "manager",
    "status": "active",
    "groupIds": ["group-1"],
    "createdAt": "2024-01-01T00:00:00Z",
    "lastLogin": "2024-01-15T10:00:00Z"
  }
}
```

**Response Errors**:

`401 Unauthorized`:
```json
{
  "error": "Invalid token",
  "status": 401
}
```

`403 Forbidden`:
```json
{
  "error": "Only root can assign roles",
  "status": 403
}
```

`404 Not Found`:
```json
{
  "error": "User not found",
  "status": 404
}
```

**Notes d'implémentation**:
- Action critique: logger dans audit log
- Vérifier que user existe
- Ne pas permettre self-demotion de root
- Si passage de root → autre: vérifier qu'il reste au moins 1 root

---

#### 3.2.7 Delete User
**DELETE** `/api/users/{userId}`

**Description**: Supprime définitivement un utilisateur (root only)

**Authentication**: Bearer token requis

**Permissions**: Root uniquement

**Headers**:
```
Authorization: Bearer <jwt-token>
```

**Path Parameters**:
- `userId` (string, required): ID de l'utilisateur

**Response Success**: `204 No Content`

**Response Errors**:

`401 Unauthorized`:
```json
{
  "error": "Invalid token",
  "status": 401
}
```

`403 Forbidden`:
```json
{
  "error": "Only root can delete users",
  "status": 403
}
```

`404 Not Found`:
```json
{
  "error": "User not found",
  "status": 404
}
```

**Notes d'implémentation**:
- Action IRRÉVERSIBLE: demander confirmation côté frontend
- Supprimer toutes les conversations du user
- Supprimer tous les fichiers uploadés par le user (MinIO)
- Révoquer toutes les sessions
- Retirer des groupes
- Logger dans audit log
- Ne pas permettre self-delete

---

### 3.3 User Group Management

#### 3.3.1 Get All User Groups
**GET** `/api/user-groups`

**Description**: Liste tous les groupes d'utilisateurs (avec filtre selon permissions)

**Authentication**: Bearer token requis

**Permissions**:
- Manager: voit uniquement ses groupes gérés
- Root: voit tous les groupes

**Headers**:
```
Authorization: Bearer <jwt-token>
```

**Query Parameters**: Aucun

**Response Success**: `200 OK`
```json
{
  "groups": [
    {
      "id": "group-1",
      "name": "Engineering Team",
      "status": "active",
      "createdAt": "2024-01-01T00:00:00Z",
      "managerIds": ["user-manager-1"],
      "memberIds": ["user-1", "user-2", "user-3"]
    },
    {
      "id": "group-2",
      "name": "Marketing Team",
      "status": "active",
      "createdAt": "2024-01-02T00:00:00Z",
      "managerIds": ["user-manager-2"],
      "memberIds": ["user-4", "user-5"]
    }
  ]
}
```

**Response Errors**:

`401 Unauthorized`:
```json
{
  "error": "Invalid token",
  "status": 401
}
```

`403 Forbidden`:
```json
{
  "error": "Manager or Root permission required",
  "status": 403
}
```

**Notes d'implémentation**:
- Si manager: filtrer groupes où `managerIds` contient son user ID
- Si root: retourner tous les groupes
- Trier par `name` (alphabétique)
- Inclure count de membres: `memberIds.length`

---

#### 3.3.2 Create User Group
**POST** `/api/user-groups`

**Description**: Crée un nouveau groupe d'utilisateurs (root only)

**Authentication**: Bearer token requis

**Permissions**: Root uniquement

**Headers**:
```
Authorization: Bearer <jwt-token>
Content-Type: application/json
```

**Request Body**:
```json
{
  "name": "Marketing Team"    // string, required
}
```

**Response Success**: `201 Created`
```json
{
  "group": {
    "id": "group-new",
    "name": "Marketing Team",
    "status": "active",
    "createdAt": "2024-01-15T12:00:00Z",
    "managerIds": [],
    "memberIds": []
  }
}
```

**Response Errors**:

`400 Bad Request` - Nom déjà utilisé:
```json
{
  "error": "Group name already exists",
  "status": 400
}
```

`401 Unauthorized`:
```json
{
  "error": "Invalid token",
  "status": 401
}
```

`403 Forbidden`:
```json
{
  "error": "Only root can create groups",
  "status": 403
}
```

**Notes d'implémentation**:
- Assigner ID unique (UUID)
- Status par défaut: "active"
- `managerIds` et `memberIds` vides au départ
- Logger dans audit log

---

#### 3.3.3 Update User Group
**PUT** `/api/user-groups/{groupId}`

**Description**: Met à jour le nom d'un groupe

**Authentication**: Bearer token requis

**Permissions**:
- Manager: peut updater ses groupes gérés
- Root: peut updater tous les groupes

**Headers**:
```
Authorization: Bearer <jwt-token>
Content-Type: application/json
```

**Path Parameters**:
- `groupId` (string, required): ID du groupe

**Request Body**:
```json
{
  "name": "Engineering Team - Updated"    // string, required
}
```

**Response Success**: `200 OK`
```json
{
  "group": {
    "id": "group-1",
    "name": "Engineering Team - Updated",
    "status": "active",
    "createdAt": "2024-01-01T00:00:00Z",
    "managerIds": ["user-manager-1"],
    "memberIds": ["user-1", "user-2"]
  }
}
```

**Response Errors**:

`401 Unauthorized`:
```json
{
  "error": "Invalid token",
  "status": 401
}
```

`403 Forbidden`:
```json
{
  "error": "Cannot manage this group",
  "status": 403
}
```

`404 Not Found`:
```json
{
  "error": "Group not found",
  "status": 404
}
```

**Notes d'implémentation**:
- Vérifier que manager gère ce groupe (via `managerIds`)
- Valider nom non vide

---

#### 3.3.4 Toggle Group Status
**PUT** `/api/user-groups/{groupId}/status`

**Description**: Active ou désactive un groupe

**Authentication**: Bearer token requis

**Permissions**:
- Manager: peut toggler ses groupes gérés
- Root: peut toggler tous les groupes

**Headers**:
```
Authorization: Bearer <jwt-token>
Content-Type: application/json
```

**Path Parameters**:
- `groupId` (string, required): ID du groupe

**Request Body**:
```json
{
  "status": "disabled"    // "active" | "disabled"
}
```

**Response Success**: `200 OK`
```json
{
  "group": {
    "id": "group-1",
    "name": "Engineering Team",
    "status": "disabled",
    "createdAt": "2024-01-01T00:00:00Z",
    "managerIds": ["user-manager-1"],
    "memberIds": ["user-1", "user-2"]
  }
}
```

**Response Errors**:

`401 Unauthorized`:
```json
{
  "error": "Invalid token",
  "status": 401
}
```

`403 Forbidden`:
```json
{
  "error": "Cannot manage this group",
  "status": 403
}
```

`404 Not Found`:
```json
{
  "error": "Group not found",
  "status": 404
}
```

**Notes d'implémentation**:
- Groupes désactivés ne peuvent être utilisés pour partage de conversation
- Users restent membres mais groupe inactif
- Logger dans audit log

---

#### 3.3.5 Delete User Group
**DELETE** `/api/user-groups/{groupId}`

**Description**: Supprime un groupe d'utilisateurs (root only)

**Authentication**: Bearer token requis

**Permissions**: Root uniquement

**Headers**:
```
Authorization: Bearer <jwt-token>
```

**Path Parameters**:
- `groupId` (string, required): ID du groupe

**Response Success**: `204 No Content`

**Response Errors**:

`401 Unauthorized`:
```json
{
  "error": "Invalid token",
  "status": 401
}
```

`403 Forbidden`:
```json
{
  "error": "Only root can delete groups",
  "status": 403
}
```

`404 Not Found`:
```json
{
  "error": "Group not found",
  "status": 404
}
```

**Notes d'implémentation**:
- Retirer le groupe de tous les users (`groupIds`)
- Retirer le groupe de toutes les conversations partagées (`sharedWithGroupIds`)
- Logger dans audit log
- Demander confirmation côté frontend

---

#### 3.3.6 Add User to Group
**POST** `/api/user-groups/{groupId}/members`

**Description**: Ajoute un utilisateur à un groupe

**Authentication**: Bearer token requis

**Permissions**:
- Manager: peut ajouter users à ses groupes gérés
- Root: peut ajouter users à n'importe quel groupe

**Headers**:
```
Authorization: Bearer <jwt-token>
Content-Type: application/json
```

**Path Parameters**:
- `groupId` (string, required): ID du groupe

**Request Body**:
```json
{
  "userId": "user-3"    // string, required
}
```

**Response Success**: `200 OK`
```json
{
  "group": {
    "id": "group-1",
    "name": "Engineering Team",
    "status": "active",
    "createdAt": "2024-01-01T00:00:00Z",
    "managerIds": ["user-manager-1"],
    "memberIds": ["user-1", "user-2", "user-3"]
  }
}
```

**Response Errors**:

`400 Bad Request` - User déjà dans le groupe:
```json
{
  "error": "User already in group",
  "status": 400
}
```

`401 Unauthorized`:
```json
{
  "error": "Invalid token",
  "status": 401
}
```

`403 Forbidden`:
```json
{
  "error": "Cannot manage this group",
  "status": 403
}
```

`404 Not Found` - Groupe ou user introuvable:
```json
{
  "error": "Group or user not found",
  "status": 404
}
```

**Notes d'implémentation**:
- Ajouter `userId` à `group.memberIds`
- Ajouter `groupId` à `user.groupIds`
- Vérifier que user existe
- Éviter doublons

---

#### 3.3.7 Remove User from Group
**DELETE** `/api/user-groups/{groupId}/members/{userId}`

**Description**: Retire un utilisateur d'un groupe

**Authentication**: Bearer token requis

**Permissions**:
- Manager: peut retirer users de ses groupes gérés
- Root: peut retirer users de n'importe quel groupe

**Headers**:
```
Authorization: Bearer <jwt-token>
```

**Path Parameters**:
- `groupId` (string, required): ID du groupe
- `userId` (string, required): ID de l'utilisateur

**Response Success**: `200 OK`
```json
{
  "group": {
    "id": "group-1",
    "name": "Engineering Team",
    "status": "active",
    "createdAt": "2024-01-01T00:00:00Z",
    "managerIds": ["user-manager-1"],
    "memberIds": ["user-1", "user-2"]
  }
}
```

**Response Errors**:

`401 Unauthorized`:
```json
{
  "error": "Invalid token",
  "status": 401
}
```

`403 Forbidden`:
```json
{
  "error": "Cannot manage this group",
  "status": 403
}
```

`404 Not Found`:
```json
{
  "error": "Group or user not found",
  "status": 404
}
```

**Notes d'implémentation**:
- Retirer `userId` de `group.memberIds`
- Retirer `groupId` de `user.groupIds`
- Si user est manager du groupe: le retirer aussi de `managerIds`

---

#### 3.3.8 Assign Manager to Group
**POST** `/api/user-groups/{groupId}/managers`

**Description**: Assigne un manager à un groupe (root only)

**Authentication**: Bearer token requis

**Permissions**: Root uniquement

**Headers**:
```
Authorization: Bearer <jwt-token>
Content-Type: application/json
```

**Path Parameters**:
- `groupId` (string, required): ID du groupe

**Request Body**:
```json
{
  "userId": "user-2"    // string, required
}
```

**Response Success**: `200 OK`
```json
{
  "group": {
    "id": "group-1",
    "name": "Engineering Team",
    "status": "active",
    "createdAt": "2024-01-01T00:00:00Z",
    "managerIds": ["user-manager-1", "user-2"],
    "memberIds": ["user-1", "user-2", "user-3"]
  }
}
```

**Response Errors**:

`400 Bad Request` - User n'a pas role "manager" ou "root":
```json
{
  "error": "User must have manager or root role",
  "status": 400
}
```

`401 Unauthorized`:
```json
{
  "error": "Invalid token",
  "status": 401
}
```

`403 Forbidden`:
```json
{
  "error": "Only root can assign managers",
  "status": 403
}
```

`404 Not Found`:
```json
{
  "error": "Group or user not found",
  "status": 404
}
```

**Notes d'implémentation**:
- Vérifier que `user.role === "manager" || user.role === "root"`
- Ajouter `userId` à `group.managerIds`
- Manager peut ensuite gérer ce groupe
- Logger dans audit log

---

#### 3.3.9 Remove Manager from Group
**DELETE** `/api/user-groups/{groupId}/managers/{userId}`

**Description**: Retire un manager d'un groupe (root only)

**Authentication**: Bearer token requis

**Permissions**: Root uniquement

**Headers**:
```
Authorization: Bearer <jwt-token>
```

**Path Parameters**:
- `groupId` (string, required): ID du groupe
- `userId` (string, required): ID du manager

**Response Success**: `200 OK`
```json
{
  "group": {
    "id": "group-1",
    "name": "Engineering Team",
    "status": "active",
    "createdAt": "2024-01-01T00:00:00Z",
    "managerIds": ["user-manager-1"],
    "memberIds": ["user-1", "user-2", "user-3"]
  }
}
```

**Response Errors**:

`401 Unauthorized`:
```json
{
  "error": "Invalid token",
  "status": 401
}
```

`403 Forbidden`:
```json
{
  "error": "Only root can remove managers",
  "status": 403
}
```

`404 Not Found`:
```json
{
  "error": "Group or user not found",
  "status": 404
}
```

**Notes d'implémentation**:
- Retirer `userId` de `group.managerIds`
- User reste membre si dans `memberIds`
- Logger dans audit log

---

### 3.4 Conversations

#### 3.4.1 Get All Conversations
**GET** `/api/conversations`

**Description**: Liste toutes les conversations de l'utilisateur courant

**Authentication**: Bearer token requis

**Permissions**: User (tous les rôles)

**Headers**:
```
Authorization: Bearer <jwt-token>
```

**Query Parameters**: Aucun

**Response Success**: `200 OK`
```json
{
  "conversations": [
    {
      "id": "conv-1",
      "title": "Discussion technique",
      "groupId": "group-1",
      "createdAt": "2024-01-15T10:00:00Z",
      "updatedAt": "2024-01-15T11:00:00Z",
      "messageCount": 10,
      "ownerId": "user-john-doe",
      "sharedWithGroupIds": [],
      "isShared": false
    },
    {
      "id": "conv-2",
      "title": "Projet marketing",
      "groupId": null,
      "createdAt": "2024-01-14T09:00:00Z",
      "updatedAt": "2024-01-15T10:00:00Z",
      "messageCount": 25,
      "ownerId": "user-john-doe",
      "sharedWithGroupIds": ["group-2"],
      "isShared": true
    }
  ]
}
```

**Response Errors**:

`401 Unauthorized`:
```json
{
  "error": "Invalid token",
  "status": 401
}
```

**Notes d'implémentation**:
- Retourner uniquement conversations où `ownerId === currentUserId`
- Trier par `updatedAt` descendant (plus récent en premier)
- Calculer `messageCount` dynamiquement depuis collection `messages`
- `groupId` peut être `null` (conversation non groupée)
- `isShared` = `true` si `sharedWithGroupIds.length > 0`

---

#### 3.4.2 Get Conversation by ID
**GET** `/api/conversations/{id}`

**Description**: Récupère une conversation spécifique

**Authentication**: Bearer token requis

**Permissions**: Owner ou membre d'un groupe avec qui c'est partagé

**Headers**:
```
Authorization: Bearer <jwt-token>
```

**Path Parameters**:
- `id` (string, required): ID de la conversation

**Response Success**: `200 OK`
```json
{
  "conversation": {
    "id": "conv-1",
    "title": "Discussion technique",
    "groupId": "group-1",
    "createdAt": "2024-01-15T10:00:00Z",
    "updatedAt": "2024-01-15T11:00:00Z",
    "messageCount": 10,
    "ownerId": "user-john-doe",
    "sharedWithGroupIds": ["group-2"],
    "isShared": true
  }
}
```

**Response Errors**:

`401 Unauthorized`:
```json
{
  "error": "Invalid token",
  "status": 401
}
```

`403 Forbidden` - User n'a pas accès:
```json
{
  "error": "Access denied to this conversation",
  "status": 403
}
```

`404 Not Found`:
```json
{
  "error": "Conversation not found",
  "status": 404
}
```

**Notes d'implémentation**:
- Vérifier que `ownerId === currentUserId` OU
- Vérifier que `currentUser.groupIds` intersecte avec `sharedWithGroupIds`
- Calculer `messageCount` dynamiquement

---

#### 3.4.3 Get Conversation Messages
**GET** `/api/conversations/{id}/messages`

**Description**: Récupère tous les messages d'une conversation

**Authentication**: Bearer token requis

**Permissions**: Owner ou membre d'un groupe avec qui c'est partagé

**Headers**:
```
Authorization: Bearer <jwt-token>
```

**Path Parameters**:
- `id` (string, required): ID de la conversation

**Query Parameters**: Aucun

**Response Success**: `200 OK`
```json
{
  "messages": [
    {
      "id": "msg-1",
      "role": "user",
      "content": "Hello, how are you?",
      "timestamp": "2024-01-15T10:00:00Z",
      "conversationId": "conv-1"
    },
    {
      "id": "msg-2",
      "role": "assistant",
      "content": "Hello! I'm doing well, thank you. How can I help you today?",
      "timestamp": "2024-01-15T10:00:05Z",
      "conversationId": "conv-1"
    }
  ]
}
```

**Response Errors**:

`401 Unauthorized`:
```json
{
  "error": "Invalid token",
  "status": 401
}
```

`403 Forbidden`:
```json
{
  "error": "Access denied to this conversation",
  "status": 403
}
```

`404 Not Found`:
```json
{
  "error": "Conversation not found",
  "status": 404
}
```

**Notes d'implémentation**:
- Vérifier permissions comme pour Get Conversation by ID
- Retourner messages triés par `timestamp` ascendant (ordre chronologique)
- Chaque message: `role` = "user" | "assistant"
- Utilisé par frontend pour afficher historique au chargement

---

#### 3.4.4 Create Conversation
**POST** `/api/conversations`

**Description**: Crée une nouvelle conversation

**Authentication**: Bearer token requis

**Permissions**: User (tous les rôles)

**Headers**:
```
Authorization: Bearer <jwt-token>
Content-Type: application/json
```

**Request Body**:
```json
{
  "title": "Ma nouvelle conversation",    // string, optional (default: "New Conversation")
  "groupId": "group-1"                     // string, optional (null si non groupée)
}
```

**Response Success**: `201 Created`
```json
{
  "conversation": {
    "id": "conv-new",
    "title": "Ma nouvelle conversation",
    "groupId": "group-1",
    "createdAt": "2024-01-15T12:00:00Z",
    "updatedAt": "2024-01-15T12:00:00Z",
    "messageCount": 0,
    "ownerId": "user-john-doe",
    "sharedWithGroupIds": [],
    "isShared": false
  }
}
```

**Response Errors**:

`401 Unauthorized`:
```json
{
  "error": "Invalid token",
  "status": 401
}
```

`404 Not Found` - groupId invalide:
```json
{
  "error": "Group not found",
  "status": 404
}
```

**Notes d'implémentation**:
- Assigner ID unique (UUID)
- `ownerId` = current user ID
- `messageCount` = 0 au départ
- `createdAt` = `updatedAt` = now
- Si `groupId` fourni: vérifier que groupe existe
- Titre par défaut: "New Conversation"

---

#### 3.4.5 Update Conversation
**PUT** `/api/conversations/{id}`

**Description**: Met à jour une conversation (titre et/ou groupId)

**Authentication**: Bearer token requis

**Permissions**: Owner uniquement

**Headers**:
```
Authorization: Bearer <jwt-token>
Content-Type: application/json
```

**Path Parameters**:
- `id` (string, required): ID de la conversation

**Request Body** (tous champs optionnels):
```json
{
  "title": "Nouveau titre",       // string, optional
  "groupId": "group-2"             // string | undefined, optional
}
```

**⚠️ IMPORTANT - Frontend behavior**:
- Frontend envoie `undefined` pour dégrouper (pas `null`)
- Backend doit accepter `undefined` et le convertir en `null` en DB

**Response Success**: `200 OK`
```json
{
  "conversation": {
    "id": "conv-1",
    "title": "Nouveau titre",
    "groupId": "group-2",
    "createdAt": "2024-01-15T10:00:00Z",
    "updatedAt": "2024-01-15T12:30:00Z",
    "messageCount": 10,
    "ownerId": "user-john-doe",
    "sharedWithGroupIds": [],
    "isShared": false
  }
}
```

**Response Errors**:

`401 Unauthorized`:
```json
{
  "error": "Invalid token",
  "status": 401
}
```

`403 Forbidden` - Pas le owner:
```json
{
  "error": "Only conversation owner can update",
  "status": 403
}
```

`404 Not Found` - Conversation ou groupId introuvable:
```json
{
  "error": "Conversation or group not found",
  "status": 404
}
```

**Notes d'implémentation**:
- Vérifier que `ownerId === currentUserId`
- Mettre à jour `updatedAt` timestamp
- Si `groupId === undefined`: stocker comme `null` en DB (conversation dégroupée)
- Si `groupId` fourni: vérifier que groupe existe

---

#### 3.4.6 Delete Conversation
**DELETE** `/api/conversations/{id}`

**Description**: Supprime définitivement une conversation

**Authentication**: Bearer token requis

**Permissions**: Owner uniquement

**Headers**:
```
Authorization: Bearer <jwt-token>
```

**Path Parameters**:
- `id` (string, required): ID de la conversation

**Response Success**: `204 No Content`

**Response Errors**:

`401 Unauthorized`:
```json
{
  "error": "Invalid token",
  "status": 401
}
```

`403 Forbidden`:
```json
{
  "error": "Only conversation owner can delete",
  "status": 403
}
```

`404 Not Found`:
```json
{
  "error": "Conversation not found",
  "status": 404
}
```

**Notes d'implémentation**:
- Vérifier que `ownerId === currentUserId`
- Supprimer tous les messages associés (collection `messages`)
- Retirer conversation de tous les groupes (conversation groups)
- Action IRRÉVERSIBLE: demander confirmation côté frontend

---

#### 3.4.7 Share Conversation with Groups
**POST** `/api/conversations/{conversationId}/share`

**Description**: Partage une conversation avec des groupes d'utilisateurs

**Authentication**: Bearer token requis

**Permissions**: Owner de la conversation

**Headers**:
```
Authorization: Bearer <jwt-token>
Content-Type: application/json
```

**Path Parameters**:
- `conversationId` (string, required): ID de la conversation

**Request Body**:
```json
{
  "groupIds": ["group-1", "group-2"]    // array of string, required
}
```

**Response Success**: `200 OK`
```json
{
  "conversation": {
    "id": "conv-1",
    "title": "My Discussion",
    "groupId": "group-1",
    "ownerId": "user-john-doe",
    "sharedWithGroupIds": ["group-1", "group-2"],
    "isShared": true,
    "createdAt": "2024-01-15T10:00:00Z",
    "updatedAt": "2024-01-15T11:00:00Z",
    "messageCount": 10
  }
}
```

**Response Errors**:

`401 Unauthorized`:
```json
{
  "error": "Invalid token",
  "status": 401
}
```

`403 Forbidden`:
```json
{
  "error": "Only owner can share conversation",
  "status": 403
}
```

`404 Not Found`:
```json
{
  "error": "Conversation not found",
  "status": 404
}
```

**Notes d'implémentation**:
- Vérifier que `ownerId === currentUserId`
- Remplacer `sharedWithGroupIds` par nouvelle liste
- Membres des groupes spécifiés peuvent voir conversation (read-only)
- Owner garde contrôle complet (edit/delete)
- Mettre à jour `updatedAt`

---

#### 3.4.8 Unshare Conversation
**POST** `/api/conversations/{conversationId}/unshare`

**Description**: Retire le partage d'une conversation pour certains groupes

**Authentication**: Bearer token requis

**Permissions**: Owner de la conversation

**Headers**:
```
Authorization: Bearer <jwt-token>
Content-Type: application/json
```

**Path Parameters**:
- `conversationId` (string, required): ID de la conversation

**Request Body**:
```json
{
  "groupIds": ["group-2"]    // array of string, required
}
```

**Response Success**: `200 OK`
```json
{
  "conversation": {
    "id": "conv-1",
    "title": "My Discussion",
    "groupId": "group-1",
    "ownerId": "user-john-doe",
    "sharedWithGroupIds": ["group-1"],
    "isShared": true,
    "createdAt": "2024-01-15T10:00:00Z",
    "updatedAt": "2024-01-15T11:00:00Z",
    "messageCount": 10
  }
}
```

**Response Errors**:

`401 Unauthorized`:
```json
{
  "error": "Invalid token",
  "status": 401
}
```

`403 Forbidden`:
```json
{
  "error": "Only owner can unshare conversation",
  "status": 403
}
```

`404 Not Found`:
```json
{
  "error": "Conversation not found",
  "status": 404
}
```

**Notes d'implémentation**:
- Vérifier que `ownerId === currentUserId`
- Retirer `groupIds` spécifiés de `sharedWithGroupIds`
- Si `sharedWithGroupIds` devient vide: `isShared = false`
- Mettre à jour `updatedAt`

---

#### 3.4.9 Get Shared Conversations
**GET** `/api/conversations/shared`

**Description**: Liste les conversations partagées avec l'utilisateur courant

**Authentication**: Bearer token requis

**Permissions**: User (tous les rôles)

**Headers**:
```
Authorization: Bearer <jwt-token>
```

**Query Parameters**: Aucun

**Response Success**: `200 OK`
```json
{
  "conversations": [
    {
      "id": "conv-5",
      "title": "Team Discussion",
      "groupId": "group-1",
      "ownerId": "user-other",
      "sharedWithGroupIds": ["group-1"],
      "isShared": true,
      "createdAt": "2024-01-10T10:00:00Z",
      "updatedAt": "2024-01-14T15:00:00Z",
      "messageCount": 25
    }
  ]
}
```

**Response Errors**:

`401 Unauthorized`:
```json
{
  "error": "Invalid token",
  "status": 401
}
```

**Notes d'implémentation**:
- Retourner conversations où:
  - `ownerId !== currentUserId` ET
  - `sharedWithGroupIds` intersecte avec `currentUser.groupIds`
- User peut voir mais PAS edit/delete ces conversations
- Trier par `updatedAt` descendant
- Affichage read-only côté frontend

---

### 3.5 Conversation Groups

#### 3.5.1 Get All Conversation Groups
**GET** `/api/groups`

**Description**: Liste tous les groupes de conversations de l'utilisateur

**Authentication**: Bearer token requis

**Permissions**: User (tous les rôles)

**Headers**:
```
Authorization: Bearer <jwt-token>
```

**Query Parameters**: Aucun

**Response Success**: `200 OK`
```json
{
  "groups": [
    {
      "id": "group-1",
      "name": "Travail",
      "createdAt": "2024-01-01T00:00:00Z",
      "conversationIds": ["conv-1", "conv-2", "conv-3"],
      "ownerId": "user-john-doe"
    },
    {
      "id": "group-2",
      "name": "Personnel",
      "createdAt": "2024-01-05T00:00:00Z",
      "conversationIds": ["conv-4"],
      "ownerId": "user-john-doe"
    }
  ]
}
```

**Response Errors**:

`401 Unauthorized`:
```json
{
  "error": "Invalid token",
  "status": 401
}
```

**Notes d'implémentation**:
- Retourner uniquement groupes où `ownerId === currentUserId`
- Trier par `name` (alphabétique)
- `conversationIds`: liste des IDs de conversations dans ce groupe

---

#### 3.5.2 Get Conversation Group by ID
**GET** `/api/groups/{id}`

**Description**: Récupère un groupe de conversations spécifique

**Authentication**: Bearer token requis

**Permissions**: Owner du groupe

**Headers**:
```
Authorization: Bearer <jwt-token>
```

**Path Parameters**:
- `id` (string, required): ID du groupe

**Response Success**: `200 OK`
```json
{
  "group": {
    "id": "group-1",
    "name": "Travail",
    "createdAt": "2024-01-01T00:00:00Z",
    "conversationIds": ["conv-1", "conv-2"],
    "ownerId": "user-john-doe"
  }
}
```

**Response Errors**:

`401 Unauthorized`:
```json
{
  "error": "Invalid token",
  "status": 401
}
```

`403 Forbidden`:
```json
{
  "error": "Access denied to this group",
  "status": 403
}
```

`404 Not Found`:
```json
{
  "error": "Group not found",
  "status": 404
}
```

**Notes d'implémentation**:
- Vérifier que `ownerId === currentUserId`

---

#### 3.5.3 Create Conversation Group
**POST** `/api/groups`

**Description**: Crée un nouveau groupe de conversations

**Authentication**: Bearer token requis

**Permissions**: User (tous les rôles)

**Headers**:
```
Authorization: Bearer <jwt-token>
Content-Type: application/json
```

**Request Body**:
```json
{
  "name": "Mon nouveau groupe"    // string, required
}
```

**Response Success**: `201 Created`
```json
{
  "group": {
    "id": "group-new",
    "name": "Mon nouveau groupe",
    "createdAt": "2024-01-15T12:00:00Z",
    "conversationIds": [],
    "ownerId": "user-john-doe"
  }
}
```

**Response Errors**:

`401 Unauthorized`:
```json
{
  "error": "Invalid token",
  "status": 401
}
```

`400 Bad Request` - Nom vide:
```json
{
  "error": "Group name cannot be empty",
  "status": 400
}
```

**Notes d'implémentation**:
- Assigner ID unique (UUID)
- `ownerId` = current user ID
- `conversationIds` vide au départ
- Valider nom non vide

---

#### 3.5.4 Update Conversation Group
**PUT** `/api/groups/{id}`

**Description**: Met à jour le nom d'un groupe

**Authentication**: Bearer token requis

**Permissions**: Owner du groupe

**Headers**:
```
Authorization: Bearer <jwt-token>
Content-Type: application/json
```

**Path Parameters**:
- `id` (string, required): ID du groupe

**Request Body**:
```json
{
  "name": "Nouveau nom"    // string, required
}
```

**Response Success**: `200 OK`
```json
{
  "group": {
    "id": "group-1",
    "name": "Nouveau nom",
    "createdAt": "2024-01-01T00:00:00Z",
    "conversationIds": ["conv-1", "conv-2"],
    "ownerId": "user-john-doe"
  }
}
```

**Response Errors**:

`401 Unauthorized`:
```json
{
  "error": "Invalid token",
  "status": 401
}
```

`403 Forbidden`:
```json
{
  "error": "Only group owner can update",
  "status": 403
}
```

`404 Not Found`:
```json
{
  "error": "Group not found",
  "status": 404
}
```

**Notes d'implémentation**:
- Vérifier que `ownerId === currentUserId`
- Valider nom non vide

---

#### 3.5.5 Delete Conversation Group
**DELETE** `/api/groups/{id}`

**Description**: Supprime un groupe de conversations

**Authentication**: Bearer token requis

**Permissions**: Owner du groupe

**Headers**:
```
Authorization: Bearer <jwt-token>
```

**Path Parameters**:
- `id` (string, required): ID du groupe

**Response Success**: `204 No Content`

**Response Errors**:

`401 Unauthorized`:
```json
{
  "error": "Invalid token",
  "status": 401
}
```

`403 Forbidden`:
```json
{
  "error": "Only group owner can delete",
  "status": 403
}
```

`404 Not Found`:
```json
{
  "error": "Group not found",
  "status": 404
}
```

**Notes d'implémentation**:
- Vérifier que `ownerId === currentUserId`
- Conversations dans le groupe sont dégroupées (pas supprimées)
- Mettre à jour `conversation.groupId` → `null` pour toutes conversations du groupe

---

#### 3.5.6 Add Conversation to Group
**POST** `/api/groups/{groupId}/conversations`

**Description**: Ajoute une conversation à un groupe

**Authentication**: Bearer token requis

**Permissions**: Owner du groupe ET owner de la conversation

**Headers**:
```
Authorization: Bearer <jwt-token>
Content-Type: application/json
```

**Path Parameters**:
- `groupId` (string, required): ID du groupe

**Request Body**:
```json
{
  "conversationId": "conv-3"    // string, required
}
```

**Response Success**: `200 OK`
```json
{
  "group": {
    "id": "group-1",
    "name": "Travail",
    "createdAt": "2024-01-01T00:00:00Z",
    "conversationIds": ["conv-1", "conv-2", "conv-3"],
    "ownerId": "user-john-doe"
  }
}
```

**Response Errors**:

`401 Unauthorized`:
```json
{
  "error": "Invalid token",
  "status": 401
}
```

`403 Forbidden`:
```json
{
  "error": "Access denied",
  "status": 403
}
```

`404 Not Found`:
```json
{
  "error": "Group or conversation not found",
  "status": 404
}
```

**Notes d'implémentation**:
- Vérifier que user est owner du groupe ET de la conversation
- Ajouter `conversationId` à `group.conversationIds`
- Mettre à jour `conversation.groupId` = `groupId`
- Éviter doublons

---

#### 3.5.7 Remove Conversation from Group
**DELETE** `/api/groups/{groupId}/conversations/{conversationId}`

**Description**: Retire une conversation d'un groupe

**Authentication**: Bearer token requis

**Permissions**: Owner du groupe

**Headers**:
```
Authorization: Bearer <jwt-token>
```

**Path Parameters**:
- `groupId` (string, required): ID du groupe
- `conversationId` (string, required): ID de la conversation

**Response Success**: `200 OK`
```json
{
  "group": {
    "id": "group-1",
    "name": "Travail",
    "createdAt": "2024-01-01T00:00:00Z",
    "conversationIds": ["conv-1", "conv-2"],
    "ownerId": "user-john-doe"
  }
}
```

**Response Errors**:

`401 Unauthorized`:
```json
{
  "error": "Invalid token",
  "status": 401
}
```

`403 Forbidden`:
```json
{
  "error": "Only group owner can remove conversations",
  "status": 403
}
```

`404 Not Found`:
```json
{
  "error": "Group or conversation not found",
  "status": 404
}
```

**Notes d'implémentation**:
- Vérifier que `group.ownerId === currentUserId`
- Retirer `conversationId` de `group.conversationIds`
- Mettre à jour `conversation.groupId` = `null`

---

### 3.6 Chat Streaming (SSE)

#### 3.6.1 Stream Chat Response
**POST** `/api/chat/stream`

**Description**: Streaming SSE de la réponse IA en temps réel

**Authentication**: Bearer token requis

**Permissions**: User (tous les rôles)

**Headers**:
```
Authorization: Bearer <jwt-token>
Content-Type: application/json
```

**Request Body**:
```json
{
  "message": "Hello, how are you?",            // string, required
  "conversationId": "conv-1",                   // string, required
  "promptCustomization": "Be concise"           // string, optional
}
```

**Response Success**: `200 OK`
```
Content-Type: text/event-stream
Cache-Control: no-cache
Connection: keep-alive
```

**Format de réponse SSE**:
```
data: Hello
data:  there
data: ! How
data:  can
data:  I
data:  help
data:  you
data:  today
data: ?
data: [DONE]

```

**Notes importantes sur le format**:
- Chaque ligne commence par `data: `
- Chaque chunk est séparé par `\n`
- Fin du stream signalée par `data: [DONE]\n`
- Lignes vides (`\n\n`) peuvent être ignorées
- Pas de JSON wrapping dans les chunks (texte brut uniquement)

**Response Errors**:

`400 Bad Request` - conversationId manquant:
```json
{
  "error": "conversationId is required",
  "status": 400
}
```

`401 Unauthorized`:
```json
{
  "error": "Invalid token",
  "status": 401
}
```

`403 Forbidden` - Pas d'accès à la conversation:
```json
{
  "error": "Access denied to this conversation",
  "status": 403
}
```

`404 Not Found` - Conversation introuvable:
```json
{
  "error": "Conversation not found",
  "status": 404
}
```

`500 Internal Server Error` - Erreur stream:
```json
{
  "error": "Stream generation failed",
  "status": 500
}
```

**Notes d'implémentation**:

**Côté Backend**:
- Vérifier que user a accès à la conversation (owner ou shared)
- Sauvegarder message user dans collection `messages`
- Appeler LLM API en mode streaming
- Générer SSE stream:
  ```python
  async def generate_sse():
      for chunk in llm_stream:
          yield f"data: {chunk}\n"
      yield "data: [DONE]\n"
  ```
- Sauvegarder réponse complète dans collection `messages`
- Mettre à jour `conversation.updatedAt` et `messageCount`
- Appliquer `promptCustomization` si fourni (system prompt)

**Côté Frontend** (déjà implémenté):
```typescript
const response = await fetch('/api/chat/stream', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'Authorization': `Bearer ${token}`,
  },
  body: JSON.stringify({ message, conversationId, promptCustomization }),
});

const reader = response.body.getReader();
const decoder = new TextDecoder();

while (true) {
  const { done, value } = await reader.read();
  if (done) break;
  
  const chunk = decoder.decode(value, { stream: true });
  const lines = chunk.split('\n');
  
  for (const line of lines) {
    if (line.startsWith('data: ')) {
      const data = line.substring(6).trim();
      if (data && data !== '[DONE]') {
        observer.next(data);  // Envoyer à NLUX
      }
    }
  }
}
```

**Intégration LLM**:
- API recommandée: OpenAI, Anthropic, ou autre LLM avec streaming
- Exemple OpenAI:
  ```python
  response = openai.ChatCompletion.create(
      model="gpt-4",
      messages=[
          {"role": "system", "content": prompt_customization},
          {"role": "user", "content": message}
      ],
      stream=True
  )
  
  for chunk in response:
      if chunk.choices[0].delta.content:
          yield chunk.choices[0].delta.content
  ```

**Gestion des erreurs**:
- Si LLM API fail: retourner erreur HTTP 500
- Si stream interrompu: logger et nettoyer ressources
- Timeout recommandé: 60 secondes

---

### 3.7 File Management

#### 3.7.1 Upload File
**POST** `/api/files/upload`

**Description**: Upload un fichier (image, document, etc.)

**Authentication**: Bearer token requis

**Permissions**: User (tous les rôles)

**Headers**:
```
Authorization: Bearer <jwt-token>
Content-Type: multipart/form-data
```

**Request Body** (multipart/form-data):
- `file` (File, required): Le fichier à uploader

**Contraintes**:
- Taille max: 10 MB par fichier
- Nombre max: 5 fichiers par requête
- Types autorisés:
  - Documents: PDF, TXT, CSV, JSON, MD
  - Images: PNG, JPEG, GIF, WebP

**Response Success**: `200 OK`
```json
{
  "file": {
    "id": "file-uuid-1",
    "name": "document.pdf",
    "size": 1024576,
    "type": "application/pdf",
    "url": "http://localhost:9000/bucket/file-uuid-1.pdf",
    "uploadedAt": "2024-01-15T12:00:00Z",
    "uploadedBy": "user-john-doe"
  }
}
```

**Response Errors**:

`400 Bad Request` - Fichier trop volumineux:
```json
{
  "error": "File size exceeds 10MB limit",
  "status": 400
}
```

`400 Bad Request` - Type non autorisé:
```json
{
  "error": "File type not allowed",
  "status": 400
}
```

`401 Unauthorized`:
```json
{
  "error": "Invalid token",
  "status": 401
}
```

**Notes d'implémentation**:
- Upload vers MinIO (S3-compatible storage)
- Générer URL publique signée (expiration recommandée: 7 jours)
- Sauvegarder métadata dans collection `files`
- `uploadedBy` = current user ID
- Calculer et valider `size` et `type`
- Nettoyer nom de fichier (sanitize)

**Exemple implémentation Python/FastAPI**:
```python
from fastapi import UploadFile, File
from minio import Minio

@app.post("/api/files/upload")
async def upload_file(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user)
):
    # Validate size
    contents = await file.read()
    if len(contents) > 10 * 1024 * 1024:
        raise HTTPException(400, "File too large")
    
    # Upload to MinIO
    file_id = str(uuid.uuid4())
    minio_client.put_object(
        "bucket",
        f"{file_id}.{file.filename.split('.')[-1]}",
        io.BytesIO(contents),
        len(contents)
    )
    
    # Generate URL
    url = minio_client.presigned_get_object("bucket", f"{file_id}")
    
    # Save to DB
    file_doc = {
        "id": file_id,
        "name": file.filename,
        "size": len(contents),
        "type": file.content_type,
        "url": url,
        "uploadedAt": datetime.utcnow(),
        "uploadedBy": current_user.id
    }
    
    return {"file": file_doc}
```

---

#### 3.7.2 Get All Files
**GET** `/api/files`

**Description**: Liste tous les fichiers uploadés par l'utilisateur

**Authentication**: Bearer token requis

**Permissions**: User (tous les rôles)

**Headers**:
```
Authorization: Bearer <jwt-token>
```

**Query Parameters**: Aucun

**Response Success**: `200 OK`
```json
{
  "files": [
    {
      "id": "file-1",
      "name": "document.pdf",
      "size": 1024576,
      "type": "application/pdf",
      "url": "http://localhost:9000/bucket/file-1.pdf",
      "uploadedAt": "2024-01-15T12:00:00Z",
      "uploadedBy": "user-john-doe"
    },
    {
      "id": "file-2",
      "name": "image.png",
      "size": 512000,
      "type": "image/png",
      "url": "http://localhost:9000/bucket/file-2.png",
      "uploadedAt": "2024-01-14T10:00:00Z",
      "uploadedBy": "user-john-doe"
    }
  ]
}
```

**Response Errors**:

`401 Unauthorized`:
```json
{
  "error": "Invalid token",
  "status": 401
}
```

**Notes d'implémentation**:
- Retourner uniquement fichiers où `uploadedBy === currentUserId`
- Trier par `uploadedAt` descendant (plus récent en premier)
- Régénérer URLs signées si expirées

---

#### 3.7.3 Delete File
**DELETE** `/api/files/{fileId}`

**Description**: Supprime un fichier uploadé

**Authentication**: Bearer token requis

**Permissions**: Owner du fichier

**Headers**:
```
Authorization: Bearer <jwt-token>
```

**Path Parameters**:
- `fileId` (string, required): ID du fichier

**Response Success**: `204 No Content`

**Response Errors**:

`401 Unauthorized`:
```json
{
  "error": "Invalid token",
  "status": 401
}
```

`403 Forbidden`:
```json
{
  "error": "You can only delete your own files",
  "status": 403
}
```

`404 Not Found`:
```json
{
  "error": "File not found",
  "status": 404
}
```

**Notes d'implémentation**:
- Vérifier que `file.uploadedBy === currentUserId`
- Supprimer de MinIO
- Supprimer metadata de collection `files`
- Action IRRÉVERSIBLE

---

### 3.8 User Settings

#### 3.8.1 Get User Settings
**GET** `/api/settings`

**Description**: Récupère les préférences utilisateur

**Authentication**: Bearer token requis

**Permissions**: User (tous les rôles)

**Headers**:
```
Authorization: Bearer <jwt-token>
```

**Query Parameters**: Aucun

**Response Success**: `200 OK`
```json
{
  "settings": {
    "promptCustomization": "Be concise and professional",
    "theme": "light",
    "language": "en"
  }
}
```

**Response Errors**:

`401 Unauthorized`:
```json
{
  "error": "Invalid token",
  "status": 401
}
```

**Notes d'implémentation**:
- Retourner settings de l'utilisateur courant
- Si pas de settings existants: retourner valeurs par défaut
- Valeurs par défaut:
  ```json
  {
    "promptCustomization": "",
    "theme": "light",
    "language": "en"
  }
  ```

---

#### 3.8.2 Update User Settings
**PUT** `/api/settings`

**Description**: Met à jour les préférences utilisateur

**Authentication**: Bearer token requis

**Permissions**: User (tous les rôles)

**Headers**:
```
Authorization: Bearer <jwt-token>
Content-Type: application/json
```

**Request Body** (tous champs optionnels):
```json
{
  "promptCustomization": "Be very concise",    // string, optional
  "theme": "dark",                              // string, optional
  "language": "fr"                              // string, optional
}
```

**Response Success**: `200 OK`
```json
{
  "settings": {
    "promptCustomization": "Be very concise",
    "theme": "dark",
    "language": "fr"
  }
}
```

**Response Errors**:

`401 Unauthorized`:
```json
{
  "error": "Invalid token",
  "status": 401
}
```

**Notes d'implémentation**:
- Upsert settings pour current user
- Merge avec settings existants (update partial autorisé)
- `promptCustomization` utilisé dans endpoint `/api/chat/stream`

---

### 3.9 Admin Operations

#### 3.9.1 Toggle Maintenance Mode
**POST** `/api/admin/maintenance`

**Description**: Active ou désactive le mode maintenance (root only)

**Authentication**: Bearer token requis

**Permissions**: Root uniquement

**Headers**:
```
Authorization: Bearer <jwt-token>
Content-Type: application/json
```

**Request Body**:
```json
{
  "enabled": true    // boolean, required
}
```

**Response Success**: `200 OK`
```json
{
  "maintenanceMode": true,
  "message": "Maintenance mode enabled"
}
```

**Response Errors**:

`401 Unauthorized`:
```json
{
  "error": "Invalid token",
  "status": 401
}
```

`403 Forbidden`:
```json
{
  "error": "Only root can toggle maintenance mode",
  "status": 403
}
```

**Notes d'implémentation**:
- Mettre à jour `system_config.maintenanceMode`
- Quand activé:
  - Seuls root users peuvent accéder à l'app
  - Tous autres users voient message maintenance
  - Sessions actives restent valides mais actions bloquées
- Retourner 503 Service Unavailable pour non-root users
- Logger dans audit log

---

## 4. Schéma Base de Données (ArangoDB)

### 4.1 Collection: users

**Structure**:
```json
{
  "_key": "user-john-doe",
  "_id": "users/user-john-doe",
  "_rev": "...",
  
  "name": "John Doe",
  "email": "john.doe@example.com",
  "role": "manager",                  // "user" | "manager" | "root"
  "status": "active",                  // "active" | "disabled"
  "groupIds": ["group-1", "group-2"],  // User groups (pas conversation groups)
  
  "createdAt": "2024-01-01T00:00:00Z",
  "lastLogin": "2024-01-15T10:00:00Z",
  
  // Mode "local" uniquement
  "passwordHash": "bcrypt-hash-here",
  
  // Mode "sso" uniquement
  "ssoId": "sso-unique-identifier"
}
```

**Indexes**:
- Primary: `_key`
- Unique: `email`
- Hash: `role`
- Hash: `status`

---

### 4.2 Collection: user_groups

**Structure**:
```json
{
  "_key": "group-1",
  "_id": "user_groups/group-1",
  "_rev": "...",
  
  "name": "Engineering Team",
  "status": "active",                  // "active" | "disabled"
  "createdAt": "2024-01-01T00:00:00Z",
  
  "managerIds": ["user-manager-1"],    // Users qui gèrent ce groupe
  "memberIds": ["user-1", "user-2", "user-3"]  // Tous users du groupe
}
```

**Indexes**:
- Primary: `_key`
- Hash: `status`

**Notes**:
- Un manager peut gérer plusieurs groupes
- Un user peut être dans plusieurs groupes
- `managerIds` ⊆ `memberIds` (managers sont aussi membres)

---

### 4.3 Collection: conversations

**Structure**:
```json
{
  "_key": "conv-1",
  "_id": "conversations/conv-1",
  "_rev": "...",
  
  "userId": "user-john-doe",           // Alias pour ownerId (legacy)
  "ownerId": "user-john-doe",          // Owner de la conversation
  "title": "Ma discussion",
  "groupId": "group-uuid-1",           // Conversation group (peut être null)
  
  "sharedWithGroupIds": ["group-1", "group-2"],  // User groups
  
  "createdAt": "2024-01-15T10:00:00Z",
  "updatedAt": "2024-01-15T11:00:00Z",
  "messageCount": 10
}
```

**Indexes**:
- Primary: `_key`
- Hash: `ownerId`
- Hash: `groupId`
- Array: `sharedWithGroupIds[*]`

**Computed fields**:
- `isShared` (calculé): `sharedWithGroupIds.length > 0`

**Notes**:
- `groupId`: Conversation group (pour organisation dans sidebar)
- `sharedWithGroupIds`: User groups (pour partage multi-users)
- Ces deux concepts sont distincts

---

### 4.4 Collection: conversation_groups

**Structure**:
```json
{
  "_key": "group-uuid-1",
  "_id": "conversation_groups/group-uuid-1",
  "_rev": "...",
  
  "name": "Travail",
  "ownerId": "user-john-doe",
  "createdAt": "2024-01-01T00:00:00Z",
  
  "conversationIds": ["conv-1", "conv-2", "conv-3"]
}
```

**Indexes**:
- Primary: `_key`
- Hash: `ownerId`

**Notes**:
- Groupes pour organiser conversations dans sidebar
- Différent de `user_groups` (qui servent au partage)
- User possède ses propres conversation groups

---

### 4.5 Collection: messages

**Structure**:
```json
{
  "_key": "msg-uuid-1",
  "_id": "messages/msg-uuid-1",
  "_rev": "...",
  
  "conversationId": "conv-1",
  "role": "user",                      // "user" | "assistant"
  "content": "Hello, how are you?",
  "timestamp": "2024-01-15T10:00:00Z"
}
```

**Indexes**:
- Primary: `_key`
- Hash: `conversationId`
- Sorted: `timestamp`

**Notes**:
- Trier par `timestamp` ascendant pour historique
- `role` = "user" pour messages utilisateur
- `role` = "assistant" pour réponses IA

---

### 4.6 Collection: files

**Structure**:
```json
{
  "_key": "file-uuid-1",
  "_id": "files/file-uuid-1",
  "_rev": "...",
  
  "name": "document.pdf",
  "size": 1024576,                     // bytes
  "type": "application/pdf",
  "url": "http://localhost:9000/bucket/file-uuid-1.pdf",
  
  "uploadedAt": "2024-01-15T12:00:00Z",
  "uploadedBy": "user-john-doe",
  
  "minioPath": "bucket/file-uuid-1.pdf"  // Chemin dans MinIO
}
```

**Indexes**:
- Primary: `_key`
- Hash: `uploadedBy`

---

### 4.7 Collection: sessions

**Structure**:
```json
{
  "_key": "session-uuid-1",
  "_id": "sessions/session-uuid-1",
  "_rev": "...",
  
  "userId": "user-john-doe",
  "token": "jwt-token-hash",           // Hash du JWT (pour revoke)
  "createdAt": "2024-01-15T10:00:00Z",
  "expiresAt": "2024-01-15T22:00:00Z",
  "active": true,
  
  "ipAddress": "192.168.1.100",
  "userAgent": "Mozilla/5.0..."
}
```

**Indexes**:
- Primary: `_key`
- Hash: `userId`
- Hash: `token`
- Hash: `active`

**Notes**:
- Nettoyer sessions expirées (cron job recommandé)
- `active === false`: session révoquée

---

### 4.8 Collection: settings

**Structure**:
```json
{
  "_key": "user-john-doe",             // userID = _key
  "_id": "settings/user-john-doe",
  "_rev": "...",
  
  "promptCustomization": "Be concise",
  "theme": "dark",
  "language": "en"
}
```

**Indexes**:
- Primary: `_key` (= userId)

---

### 4.9 Collection: system_config

**Structure**:
```json
{
  "_key": "config",                    // Document unique
  "_id": "system_config/config",
  "_rev": "...",
  
  "authMode": "local",                 // "none" | "local" | "sso"
  "allowMultiLogin": false,
  "maintenanceMode": false,
  
  "ssoConfig": {
    "tokenHeader": "X-Auth-Token",
    "nameHeader": "X-User-Name",
    "emailHeader": "X-User-Email",
    "firstNameHeader": "X-User-FirstName",
    "lastNameHeader": "X-User-LastName"
  }
}
```

**Notes**:
- Document unique (_key = "config")
- `ssoConfig` null si mode != "sso"

---

### 4.10 Collection: audit_logs (recommandé)

**Structure**:
```json
{
  "_key": "log-uuid-1",
  "_id": "audit_logs/log-uuid-1",
  "_rev": "...",
  
  "userId": "user-root",
  "action": "user_deleted",
  "targetType": "user",                // "user" | "group" | "conversation" | etc.
  "targetId": "user-1",
  "details": {
    "deletedUser": "john.doe@example.com"
  },
  "timestamp": "2024-01-15T12:00:00Z",
  "ipAddress": "192.168.1.100"
}
```

**Actions à logger** (recommandé):
- Création/modification/suppression users
- Changements de rôles
- Création/suppression groupes
- Assignments managers
- Toggle maintenance mode
- Revoke all sessions
- Login failures

---

## 5. Sécurité

### 5.1 Authentication

**Mode "local"**:
- JWT tokens avec expiration (12h recommandé)
- Signature HMAC-SHA256
- Stocker secret dans variable d'environnement
- Refresh tokens optionnel (recommandé pour production)

**Mode "sso"**:
- Validation headers configurés dans `ssoConfig`
- Pas de JWT (SSO gère l'auth)
- Vérifier présence et format des headers obligatoires

**Mode "none"**:
- Aucune validation
- User générique pour tous

### 5.2 Authorization

**Middleware de vérification**:
```python
async def check_permissions(
    required_role: str,
    current_user: User = Depends(get_current_user)
):
    if current_user.role == "root":
        return True
    if current_user.role == "manager" and required_role in ["user", "manager"]:
        return True
    if current_user.role == "user" and required_role == "user":
        return True
    raise HTTPException(403, "Insufficient permissions")
```

**Vérifications à chaque requête**:
1. Token valide (si mode local/sso)
2. User status === "active"
3. User role >= required role
4. Maintenance mode check (autoriser root uniquement)

### 5.3 Protection données

**Passwords**:
- Hash avec bcrypt ou argon2
- Minimum 8 caractères côté validation
- Jamais exposer dans responses

**Tokens**:
- Stocker hash en DB (pas token brut)
- Expiration automatique
- Revoke capability

**Files**:
- Scan antivirus recommandé
- Limites de taille strictes (10MB)
- Types MIME validés
- URLs signées avec expiration

### 5.4 Rate Limiting (recommandé)

**Endpoints critiques**:
- Login: 5 tentatives / 15 min par IP
- File upload: 10 uploads / heure par user
- Chat streaming: 100 requêtes / heure par user

**Implémentation**:
```python
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

@app.post("/api/auth/login")
@limiter.limit("5/15minutes")
async def login(...):
    ...
```

### 5.5 CORS

**Configuration FastAPI**:
```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # Frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

**Production**:
- Limiter `allow_origins` au domaine frontend uniquement
- Éviter `allow_origins=["*"]`

### 5.6 Input Validation

**Utiliser Pydantic models**:
```python
from pydantic import BaseModel, EmailStr, Field

class LoginRequest(BaseModel):
    username: EmailStr
    password: str = Field(min_length=8)

class CreateUserRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    password: str = Field(min_length=8)
    role: Literal["user", "manager", "root"] = "user"
```

**Sanitization**:
- Nettoyer noms de fichiers
- Échapper HTML dans user inputs
- Valider UUIDs

---

## 6. Performance & Optimisation

### 6.1 Caching (recommandé)

**Redis pour**:
- Sessions actives
- Config serveur
- User permissions cache

**Exemple**:
```python
import redis

redis_client = redis.Redis(host='localhost', port=6379)

# Cache user permissions
def get_user_permissions(user_id: str):
    cached = redis_client.get(f"perms:{user_id}")
    if cached:
        return json.loads(cached)
    
    # Calculate permissions
    perms = calculate_permissions(user_id)
    redis_client.setex(f"perms:{user_id}", 3600, json.dumps(perms))
    return perms
```

### 6.2 Database Optimization

**Indexes critiques**:
- `users.email` (unique)
- `conversations.ownerId`
- `messages.conversationId`
- `files.uploadedBy`

**Queries optimisées**:
- Utiliser projections (ne récupérer que champs nécessaires)
- Paginer résultats (limiter à 100 items par défaut)
- Éviter N+1 queries (join conversations + messages)

### 6.3 File Upload

**Streaming upload**:
```python
@app.post("/api/files/upload")
async def upload_file(file: UploadFile = File(...)):
    # Stream directly to MinIO (avoid loading in memory)
    file_size = 0
    async for chunk in file:
        minio_client.put_object(...)
        file_size += len(chunk)
```

### 6.4 SSE Optimization

**Chunking strategy**:
- Chunk size: 5-20 caractères
- Flush buffer régulièrement
- Timeout: 60 secondes
- Keep-alive packets toutes les 15 secondes

---

## 7. Monitoring & Logging

### 7.1 Logging Structure

**Niveaux**:
- `DEBUG`: Détails techniques
- `INFO`: Opérations normales
- `WARNING`: Situations anormales non critiques
- `ERROR`: Erreurs applicatives
- `CRITICAL`: Erreurs système critiques

**Format JSON recommandé**:
```json
{
  "timestamp": "2024-01-15T12:00:00Z",
  "level": "INFO",
  "service": "api",
  "endpoint": "/api/conversations",
  "method": "POST",
  "userId": "user-john-doe",
  "status": 201,
  "duration_ms": 45,
  "message": "Conversation created"
}
```

### 7.2 Métriques clés

**Business metrics**:
- Nombre d'utilisateurs actifs (DAU, MAU)
- Conversations créées / jour
- Messages envoyés / jour
- Files uploadés / jour
- Taux de partage de conversations

**Technical metrics**:
- Latence endpoints (p50, p95, p99)
- Taux d'erreur par endpoint
- Taux d'erreur streaming
- Upload success rate
- Database query time

**System metrics**:
- CPU usage
- Memory usage
- Database connections
- MinIO storage usage

### 7.3 Health Checks

**Endpoint recommandé**:
```python
@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "checks": {
            "database": check_database(),
            "minio": check_minio(),
            "redis": check_redis()
        }
    }
```

---

## 8. Déploiement

### 8.1 Variables d'environnement

**Fichier `.env`**:
```bash
# API
API_HOST=0.0.0.0
API_PORT=8000
API_WORKERS=4

# Database
ARANGO_HOST=localhost
ARANGO_PORT=8529
ARANGO_DATABASE=chatbot
ARANGO_USER=root
ARANGO_PASSWORD=password

# MinIO
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET=chatbot-files

# Auth
JWT_SECRET=your-secret-key-here
JWT_EXPIRATION_HOURS=12

# LLM API
OPENAI_API_KEY=sk-...
LLM_MODEL=gpt-4

# Mode
AUTH_MODE=local  # none | local | sso
ALLOW_MULTI_LOGIN=false
MAINTENANCE_MODE=false

# SSO (si AUTH_MODE=sso)
SSO_TOKEN_HEADER=X-Auth-Token
SSO_NAME_HEADER=X-User-Name
SSO_EMAIL_HEADER=X-User-Email

# Redis (optionnel)
REDIS_HOST=localhost
REDIS_PORT=6379

# Monitoring
SENTRY_DSN=https://...
LOG_LEVEL=INFO
```

### 8.2 Docker

**Dockerfile**:
```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app
COPY . .

# Expose port
EXPOSE 8000

# Run
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
```

**docker-compose.yml**:
```yaml
version: '3.8'

services:
  api:
    build: .
    ports:
      - "8000:8000"
    environment:
      - ARANGO_HOST=arangodb
      - MINIO_ENDPOINT=minio:9000
      - REDIS_HOST=redis
    depends_on:
      - arangodb
      - minio
      - redis

  arangodb:
    image: arangodb:latest
    environment:
      - ARANGO_ROOT_PASSWORD=password
    ports:
      - "8529:8529"
    volumes:
      - arangodb-data:/var/lib/arangodb3

  minio:
    image: minio/minio:latest
    command: server /data --console-address ":9001"
    ports:
      - "9000:9000"
      - "9001:9001"
    environment:
      - MINIO_ROOT_USER=minioadmin
      - MINIO_ROOT_PASSWORD=minioadmin
    volumes:
      - minio-data:/data

  redis:
    image: redis:alpine
    ports:
      - "6379:6379"

volumes:
  arangodb-data:
  minio-data:
```

### 8.3 Production Checklist

**Sécurité**:
- [ ] Changer tous les secrets/passwords
- [ ] Activer HTTPS (TLS/SSL)
- [ ] Configurer CORS restrictif
- [ ] Activer rate limiting
- [ ] Scan vulnérabilités (Snyk, Dependabot)

**Performance**:
- [ ] Activer caching (Redis)
- [ ] Optimiser indexes database
- [ ] Configurer connection pooling
- [ ] Activer compression (gzip)

**Monitoring**:
- [ ] Configurer Sentry/error tracking
- [ ] Mettre en place métriques (Prometheus)
- [ ] Configurer alerts critiques
- [ ] Logs centralisés (ELK, Datadog)

**Backup**:
- [ ] Backup automatique ArangoDB (daily)
- [ ] Backup MinIO (rsync/replication)
- [ ] Plan de disaster recovery
- [ ] Tester restoration

---

## 9. Tests

### 9.1 Tests unitaires (recommandé)

**Couverture minimale**: 70%

**Endpoints critiques à tester**:
- Authentication (login, verify, logout)
- User management (create, update, delete)
- Conversations (CRUD operations)
- File upload
- Chat streaming

**Exemple pytest**:
```python
import pytest
from fastapi.testclient import TestClient

def test_create_conversation(client: TestClient, auth_token: str):
    response = client.post(
        "/api/conversations",
        headers={"Authorization": f"Bearer {auth_token}"},
        json={"title": "Test conversation"}
    )
    assert response.status_code == 201
    assert response.json()["conversation"]["title"] == "Test conversation"

def test_unauthorized_access(client: TestClient):
    response = client.get("/api/conversations")
    assert response.status_code == 401
```

### 9.2 Tests d'intégration

**Scénarios à couvrir**:
- Workflow complet: login → create conversation → send message → logout
- Partage conversation entre users
- Manager managing users dans ses groupes
- Root operations (toggle maintenance, create groups)

### 9.3 Tests de charge (recommandé)

**Outils**: Locust, k6

**Métriques cibles**:
- Chat streaming: 100 users simultanés, <2s latency
- API endpoints: 1000 req/s, <500ms p95
- File upload: 50 uploads/min

---

## 10. Points d'attention pour le développement

### 10.1 Synchronisation frontend-backend

**Formats critiques**:
1. **Toutes les réponses doivent wrapper les objets**:
   ✅ `{"user": {...}}`
   ❌ `{...}` (objet nu)

2. **groupId dans conversations**:
   - Frontend envoie `undefined` pour dégrouper
   - Backend doit accepter `undefined` et stocker comme `null`

3. **SSE streaming format**:
   - Chaque ligne: `data: chunk\n`
   - Fin: `data: [DONE]\n`
   - PAS de JSON wrapping dans les chunks

4. **Headers obligatoires**:
   - `Authorization: Bearer <token>` pour tous endpoints protégés
   - `Content-Type: application/json` pour bodies JSON
   - `Content-Type: multipart/form-data` pour upload

### 10.2 Gestion des erreurs

**Format standard**:
```json
{
  "error": "Message d'erreur clair",
  "status": 400,
  "details": {
    "field": "email",
    "issue": "Email already exists"
  }
}
```

**Codes HTTP**:
- `200`: Succès GET/PUT
- `201`: Succès POST (création)
- `204`: Succès DELETE (no content)
- `400`: Bad request (validation error)
- `401`: Unauthorized (token invalide)
- `403`: Forbidden (permissions insuffisantes)
- `404`: Not found
- `500`: Internal server error
- `503`: Service unavailable (maintenance)

### 10.3 Timestamps

**Format ISO 8601**:
- Toujours UTC
- Format: `2024-01-15T10:30:00Z`
- Utiliser `datetime.utcnow()` (Python)
- Pas de timestamps Unix (epoch)

### 10.4 IDs et clés

**Format recommandé**:
- UUIDs v4 pour nouveaux documents
- Prefix par type: `user-`, `conv-`, `group-`, `file-`, `msg-`
- Exemple: `user-550e8400-e29b-41d4-a716-446655440000`

---

## 11. Évolutions futures

### 11.1 Phase 2

**Fonctionnalités**:
- [ ] Notifications temps réel (WebSockets)
- [ ] Export conversations (PDF, TXT, JSON)
- [ ] Search in conversations
- [ ] Conversation tags
- [ ] Voice input/output

**Techniques**:
- [ ] Database replication
- [ ] CDN pour fichiers statiques
- [ ] Horizontal scaling (load balancer)

### 11.2 Phase 3

**Fonctionnalités**:
- [ ] Mobile API (endpoints optimisés)
- [ ] Webhooks
- [ ] API publique avec rate limiting
- [ ] Analytics dashboard

**Techniques**:
- [ ] GraphQL endpoint
- [ ] gRPC pour inter-services
- [ ] Message queue (RabbitMQ/Kafka)

---

## 12. Documentation API

**OpenAPI/Swagger**:
- Auto-généré par FastAPI
- Accessible à `/docs` (Swagger UI)
- Accessible à `/redoc` (ReDoc)

**Activation**:
```python
from fastapi import FastAPI

app = FastAPI(
    title="Chatbot API",
    version="3.0",
    description="Backend API pour application chatbot",
    docs_url="/docs",
    redoc_url="/redoc"
)
```

---

## Conclusion

Cette spécification backend v3.0 fournit un contrat d'interface complet entre le frontend Next.js et le backend FastAPI.

**Points clés**:
- 40+ endpoints documentés avec formats exacts
- 3 modes d'authentification (none/local/sso)
- 3 niveaux d'autorisation (user/manager/root)
- Schéma base de données complet (9 collections)
- Streaming SSE pour chat temps réel
- Upload fichiers vers MinIO
- Sécurité, performance, monitoring

**Prochaines étapes**:
1. Implémenter les endpoints critiques (auth, conversations, chat)
2. Setup infrastructure (ArangoDB, MinIO, Redis)
3. Tests unitaires endpoints
4. Documentation OpenAPI
5. Déploiement Docker

---

**Version**: 3.0  
**Date**: 2024-01-15  
**Auteur**: Basé sur implémentation frontend et besoins métier