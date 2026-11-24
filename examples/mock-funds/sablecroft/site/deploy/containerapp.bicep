// Sablecroft Alternatives (fictional): the fund's server on Azure Container Apps. Not run in tests.
// Built from the platform's deploy/Dockerfile plus the fund's site/ (see Dockerfile.firm); the image
// holds code only. Pieces: Log Analytics (logs), Key Vault (secrets by reference), a user-assigned
// managed identity (Key Vault, ADLS, ACR pull), Azure Database for PostgreSQL Flexible Server (the
// knowledge store), an Azure Files share (the run store: a SQLite file, see FINDINGS), and the
// introspection shim as a second container in the same app (loopback only).

param location string = resourceGroup().location
param imageTag string
param tenantId string = 'b1d0c2de-7a61-4f9e-a3b2-5c0d9e8f1a22'

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'sablecroft-logs'
  location: location
  properties: { sku: { name: 'PerGB2018' }, retentionInDays: 90 }
}

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'sablecroft-qaos-identity'
  location: location
}

resource vault 'Microsoft.KeyVault/vaults@2023-07-01' existing = { name: 'sablecroft-kv' }

resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'sablecroft-env'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

resource runsShare 'Microsoft.App/managedEnvironments/storages@2024-03-01' = {
  parent: env
  name: 'runs'
  properties: {
    azureFile: {
      accountName: 'sablecroftqaos'
      shareName: 'qaos-runs'
      accessMode: 'ReadWrite'
      accountKey: 'REPLACE-with-a-secret-reference' // Azure Files over SMB takes a key; no identity option
    }
  }
}

resource qaos 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'sablecroft-qaos'
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identity.id}': {} }
  }
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      ingress: { external: true, targetPort: 8000, transport: 'http' }
      secrets: [
        { name: 'introspection-secret', keyVaultUrl: '${vault.properties.vaultUri}secrets/qaos-introspection', identity: identity.id }
        { name: 'knowledge-password', keyVaultUrl: '${vault.properties.vaultUri}secrets/qaos-pg-reader', identity: identity.id }
        { name: 'dbx-client-secret', keyVaultUrl: '${vault.properties.vaultUri}secrets/dbx-sp-secret', identity: identity.id }
      ]
    }
    template: {
      containers: [
        {
          name: 'server'
          image: 'sablecroftacr.azurecr.io/sablecroft-qaos:${imageTag}'
          resources: { cpu: json('1.0'), memory: '2Gi' }
          env: [
            // The platform's own settings (QUANT_AGENT_OS_*): a path for the run store, a URL for the
            // knowledge store, files for access and sign-in.
            { name: 'QUANT_AGENT_OS_STORE', value: 'postgresql://qaos_reader@sablecroft-pg.postgres.database.azure.com:5432/qaos?sslmode=require' }
            // Secrets the platform reads come as files under /run/secrets (NAME_FILE).
            { name: 'PGPASSWORD_FILE', value: '/run/secrets/knowledge-password' }
            { name: 'QUANT_AGENT_OS_RUNS_FILE', value: '/data/runs/runs.db' }
            { name: 'QUANT_AGENT_OS_ACCESS_FILE', value: '/config/access.toml' }
            { name: 'QUANT_AGENT_OS_SIGN_IN_FILE', value: '/config/sign-in.toml' }
            { name: 'QAOS_INTROSPECTION_SECRET_FILE', value: '/run/secrets/introspection-secret' }
            { name: 'QUANT_AGENT_OS_LOG_FORMAT', value: 'json' }
            { name: 'SAB_WAREHOUSE_KIND', value: 'databricks' }
            { name: 'DATABRICKS_SERVER_HOSTNAME', value: 'adb-1234567890123456.7.azuredatabricks.net' }
            { name: 'DATABRICKS_HTTP_PATH', value: '/sql/1.0/warehouses/sablecroft-serving' }
            { name: 'DATABRICKS_CLIENT_ID', value: '0c9f5f5e-2b7a-4a52-9d0c-1f3e6a7b8c91' }
            { name: 'DATABRICKS_CLIENT_SECRET', secretRef: 'dbx-client-secret' }
            { name: 'SAB_HOST', value: '0.0.0.0' }
          ]
          probes: [
            { type: 'Liveness', httpGet: { path: '/healthz', port: 8000 } }
            { type: 'Readiness', httpGet: { path: '/readyz', port: 8000 } }
          ]
          volumeMounts: [
            { volumeName: 'runs', mountPath: '/data/runs' }
            { volumeName: 'secrets', mountPath: '/run/secrets' }
          ]
        }
        {
          name: 'entra-introspection'
          image: 'sablecroftacr.azurecr.io/sablecroft-entra-shim:${imageTag}'
          resources: { cpu: json('0.25'), memory: '0.5Gi' }
          env: [
            { name: 'ENTRA_TENANT_ID', value: tenantId }
            { name: 'ENTRA_AUDIENCE', value: 'https://qaos.sablecroft.example/mcp' }
            { name: 'SHIM_CLIENT_SECRET', secretRef: 'introspection-secret' }
          ]
        }
      ]
      scale: { minReplicas: 1, maxReplicas: 1 } // one replica: the run store is a SQLite file
      volumes: [
        { name: 'runs', storageType: 'AzureFile', storageName: runsShare.name }
        {
          name: 'secrets'
          storageType: 'Secret'
          secrets: [
            { secretRef: 'knowledge-password', path: 'knowledge-password' }
            { secretRef: 'introspection-secret', path: 'introspection-secret' }
          ]
        }
      ]
    }
  }
}
